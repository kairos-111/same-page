"""Conservative, model-free ASR gates. Never repair a hypothesis by deleting words."""
import math
import time
import re
from dataclasses import dataclass
from .languages import language_units,HAN_RE

MAX_WORDS = 4096
MAX_RECOVERY_SECONDS = 30


def token_limit(duration):
    """Most tokens a window of speech can need. Fast speech is ~5 tokens a second;
    a repetition loop otherwise runs to Whisper's 224-token ceiling and costs seconds."""
    return int(min(224, 24 + math.ceil(duration * 12)))


def text_issue(text):
    if len(text) > 32768:
        return 'output_limit'
    # Whisper can also loop on non-word symbols (e.g. Japanese middle dots).
    # Preserve the rejected hypothesis in the existing audit path; never trim it.
    for match in re.finditer(r'([^\s\d])(?:\s*\1){23,}', text):
        if not HAN_RE.fullmatch(match[1]):
            return 'symbol_loop'
    tokens = language_units(text.casefold())
    if len(tokens) > MAX_WORDS:
        return 'output_limit'
    # Require both eight consecutive cycles and 24 words. Ordinary emphasis,
    # stutters and lists with changing members are deliberately left alone.
    for width in range(1, 9):
        run = width
        for i in range(width, len(tokens)):
            run = run + 1 if tokens[i] == tokens[i-width] else width
            if run >= max(48 if all(HAN_RE.fullmatch(t) for t in tokens[i-width:i+1]) else 24, width * 8):
                return 'repetition_loop'
    return None


def words_issue(words, duration=None):
    if len(words) > MAX_WORDS:
        return 'output_limit'
    previous = -1.0
    collapsed = 0
    for w in words:
        start, end = w.start, w.end
        if not (math.isfinite(start) and math.isfinite(end)):
            return 'invalid_timestamp'
        if start < -.05 or end < start or start < previous - .08:
            return 'invalid_timestamp'
        if duration is not None and end > duration + .25:
            return 'timestamp_outside_audio'
        previous = end
        collapsed += end - start < .005
    if len(words) >= 12 and collapsed >= 8 and collapsed / len(words) > .5:
        return 'collapsed_timestamps'
    return text_issue(' '.join(w.text for w in words))


@dataclass
class CheckedWord:
    start: float
    end: float
    text: str


def segments_issue(segments, duration):
    words = []
    for s in segments:
        try:
            start, end = s['start'], s['end']
            if not (math.isfinite(start) and math.isfinite(end)) or start < -.05 or end < start or end > duration + .25:
                return 'invalid_segment_timestamp'
            if s.get('text', '').strip() and not s.get('words') and s.get('no_speech_prob', 0) <= .9:
                return 'missing_word_timestamps'
            words.extend(CheckedWord(w['start'], w['end'], w['word']) for w in s.get('words', []))
        except (KeyError, TypeError, ValueError):
            return 'malformed_result'
    return text_issue(' '.join(s.get('text', '') for s in segments)) or words_issue(words, duration)


class RecognitionPending(Exception):
    """Keep the unconfirmed audio until bounded additional context arrives."""


class RejectedRecognition(Exception):
    pass


class GuardedDecoder:
    """At most two quality passes; mixed passes decode up to four distinct spans."""
    def __init__(self, model, model_path, prompt='An English lecture.', language='en', language_router=None):
        self.model, self.model_path, self.prompt = model, model_path, prompt
        self.language,self.language_router=language,language_router
        self.neutral_prompt='An English lecture.' if language=='en' else ''
        self.events = []
        self.offset = 0.0
        self.allow_recovery = True
        self.wait_for_context = False
        self.finalizing = False
        self.pending_end = None
        self.frontier = 0.0

    def __call__(self, audio, init_prompt=''):
        duration = len(audio) / 16000
        attempts = []
        language,language_info=self.language,{}
        for temperature in (0.0, .4):
            # Refuse expensive recovery of unusually large upstream buffers.
            if attempts and (duration > MAX_RECOVERY_SECONDS or not self.allow_recovery):
                break
            prompt = (self.prompt + ' ' + init_prompt)[-1600:] if not attempts else self.neutral_prompt
            started = time.monotonic()
            try:
                kwargs=dict(language=language,task='transcribe',initial_prompt=prompt,
                    word_timestamps=True,condition_on_previous_text=False,
                    temperature=temperature,path_or_hf_repo=self.model_path,
                    sample_len=token_limit(duration))
                if self.language_router:
                    kwargs.pop('language')
                    result=self.language_router.decode(self.model,audio,self.offset,**kwargs)
                else:result=self.model(audio,**kwargs)
                segments = result.get('segments', [])
                self.events.append(dict(type='asr_decode',start=self.offset,end=self.offset+duration,
                    seconds=time.monotonic()-started,audio_seconds=duration,temperature=temperature,
                    recovery=bool(attempts),language=result.get('language',language),
                    language_windows=result.get('_language_windows',[]),
                    language_probes=result.get('_language_probes',[]),**language_info,words=sum(len(s.get('words',[])) for s in segments),
                    characters=sum(len(s.get('text','')) for s in segments)))
                issue = result.get('_quality_issue') or segments_issue(segments, duration)
                if attempts and not any(s.get('words') and s.get('no_speech_prob', 0) <= .9 for s in segments):
                    issue = issue or 'empty_recovery'
            except Exception as exc:
                if not attempts:
                    raise
                issue, segments = 'recovery_error:' + type(exc).__name__, []
            if not issue:
                if self.pending_end is not None:
                    self.events.append(dict(type='asr_quality',status='recovered',
                        start=max(self.offset,self.frontier),end=self.offset+duration,
                        recovery='additional_context'))
                self.pending_end=None
                if attempts:
                    self.events.append(dict(type='asr_quality', status='recovered',
                        start=self.offset, end=self.offset+duration, attempts=attempts,
                        recovery_temperature=temperature))
                return segments
            raw = ' '.join(s.get('text', '') for s in segments)
            attempts.append(dict(reason=issue, temperature=temperature,
                text=raw[:4000], text_characters=len(raw), text_truncated=len(raw)>4000))
            # A second temperature cannot add missing language/acoustic context.
            # Wait for at most two seconds of NEW audio, without clearing WLK.
            if (self.wait_for_context and not self.finalizing and self.allow_recovery
                    and duration<=MAX_RECOVERY_SECONDS and len(attempts)==1):
                end=self.offset+duration
                if self.pending_end is None:self.pending_end=end
                if end-self.pending_end<2:
                    self.events.append(dict(type='asr_waiting',start=max(self.offset,self.frontier),
                        end=end,reason=issue,context_seconds=end-self.pending_end,attempt=attempts[-1]))
                    raise RecognitionPending()

        self.pending_end=None
        self.events.append(dict(type='asr_quality', status='unresolved', start=max(self.offset,self.frontier),
            end=self.offset+duration, attempts=attempts, recovery='reimport_audio',
            recovery_skipped=duration>MAX_RECOVERY_SECONDS or not self.allow_recovery))
        raise RejectedRecognition()
