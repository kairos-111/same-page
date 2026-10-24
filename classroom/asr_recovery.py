"""Bounded recovery around WLK's destructive init; no disk cursor rewinds.

Only adapter-owned process_iter calls are guarded. Intentional silence resets remain
explicit. Events use absolute audio seconds; a recovered decode is not an acoustic
accuracy/word coverage guarantee.
"""

class RecoveryGuard:
    MAX_SECONDS = 15
    MAX_ATTEMPTS = 2  # per speech run; normal LocalAgreement progress replenishes it

    def __init__(self, online):
        self.online = online
        self.events = []
        self.frontier = 0.0
        self.attempts = 0
        self.running = False
        self.lost = None
        self.allow_recovery = True
        original_init = online.init

        def guarded_init(offset=None):
            if self.running and len(online.audio_buffer):
                start = max(online.buffer_time_offset, self.frontier)
                end = online.get_audio_buffer_end_time()
                # Copy only a bounded suffix before the dependency destroys it.
                keep_start = max(start, end-self.MAX_SECONDS)
                i = max(0, round((keep_start-online.buffer_time_offset)*16000))
                self.lost = (start, keep_start, end, online.audio_buffer[i:].copy())
            return original_init(offset=offset)
        online.init = guarded_init

    def record(self, action, start, end, **details):
        if end > start:
            self.events.append(dict(type='asr_coverage', action=action,
                                    start=start, end=end, **details))

    def accept(self, words):
        accepted = []
        for word in words:
            if word.end > self.frontier+1e-6:
                accepted.append(word)
                self.frontier = max(self.frontier, word.end)
        return accepted

    def process_iter(self):
        self.lost = None
        self.running = True
        try:
            words, upto = self.online.process_iter()
        finally:
            self.running = False
        words = self.accept(words)
        if words:
            self.attempts = 0
        if self.lost:
            start, keep_start, end, audio = self.lost
            self.record('reset', start, end, reason='processor_init_during_decode')
            if keep_start > start:
                self.record('gap', start, keep_start, reason='recovery_window_limit')
            if not self.allow_recovery:
                self.record('gap', keep_start, end, reason='recovery_skipped_for_backlog')
            elif self.attempts >= self.MAX_ATTEMPTS:
                self.record('gap', keep_start, end, reason='recovery_attempt_limit')
            else:
                self.attempts += 1
                self.record('recovery_attempt', keep_start, end, attempt=self.attempts)
                try:
                    # Two repeated decodes must agree on ALL words. No recursive
                    # online processor calls, and the no-speech filter stays enabled.
                    asr = self.online.asr
                    self.online.recovery_offset = keep_start
                    decoder = asr.transcribe
                    guarded = hasattr(decoder, 'allow_recovery')
                    if guarded:
                        prior = decoder.allow_recovery, decoder.offset
                        decoder.allow_recovery = False
                        decoder.offset = keep_start
                    try:
                        first = asr.ts_words(decoder(audio, init_prompt=''))
                        second = asr.ts_words(decoder(audio, init_prompt=''))
                    finally:
                        if guarded:
                            decoder.allow_recovery, decoder.offset = prior
                    valid = bool(first) and [w.text for w in first] == [w.text for w in second]
                    if not valid:
                        raise ValueError('empty_or_unstable_decode')
                    recovered = [w.with_offset(keep_start) for w in second
                                 if 0 <= w.start < w.end <= len(audio)/16000+.05]
                    if len(recovered) != len(second):
                        raise ValueError('invalid_word_timestamps')
                    recovered = self.accept(recovered)
                    if not recovered:
                        raise ValueError('no_new_valid_words')
                    words.extend(recovered)
                    self.record('recovered', keep_start, end, words=len(recovered),
                                verification='two_decodes_agree_not_acoustic_proof')
                except Exception as exc:
                    self.record('gap', keep_start, end, reason='recovery_failed', error=str(exc))
                finally:
                    if hasattr(self.online, 'recovery_offset'):
                        del self.online.recovery_offset
            self.lost = None
        return words, upto

    def drain_events(self):
        events, self.events = self.events, []
        return events
