"""WhisperLiveKit LocalAgreement adapter; all calls run on one dedicated thread."""
from .asr_recovery import RecoveryGuard
from .asr_quality import GuardedDecoder, RejectedRecognition, RecognitionPending

BACKLOG_NO_RETRY = 5  # seconds of unrecognised audio
BUFFER_SECONDS = 6
END_PAUSE = 1.0           # pause that ends a sentence
END_PAUSE_PUNCTUATED = .5  # shorter pause once the words already end a sentence
LONG_RUN = 6.0            # a sentence this long ends at any short breath
LONG_RUN_PAUSE = .3
SENTENCE_END = ('.', '?', '!', '。', '？', '！', '…')

class WhisperAdapter:
    def __init__(self, model_path, hint='', language_mode='en', weights=None):
        import numpy as np
        from .languages import MODES
        from .language_detection import SpeechLanguageRouter
        if language_mode not in MODES:raise ValueError('识别模式无效')
        self.language_mode=language_mode
        language=language_mode if language_mode!='mixed' else None
        if weights is not None:
            # faster-whisper (Windows): same result shapes as MLX, no torch/mlx.
            from .ct2_asr import CT2Whisper,NumpyVAD,NumpyBridge,language_detector,online_processor_class
            OnlineASRProcessor=online_processor_class()
            self.language_router=SpeechLanguageRouter(language_detector(weights)) if language_mode=='mixed' else None
            self.asr = CT2Whisper(weights,lan=language or 'auto',model_dir=model_path)
            vad=NumpyVAD();torch=NumpyBridge()
        else:
            import mlx.core as mx
            import torch
            from whisperlivekit.local_agreement.backends import MLXWhisper
            from whisperlivekit.local_agreement.online_asr import OnlineASRProcessor
            from whisperlivekit.silero_vad_iterator import load_onnx_session, OnnxWrapper
            from .language_detection import mlx_language_detector
            torch.set_num_threads(2)
            if not mx.metal.is_available():
                raise RuntimeError('MLX 未检测到 Apple GPU')
            self.language_router=SpeechLanguageRouter(mlx_language_detector(str(model_path.resolve()))) if language_mode=='mixed' else None
            self.asr = MLXWhisper(lan=language or 'auto',model_dir=str(model_path))
            vad=OnnxWrapper(load_onnx_session())
        self.asr.tokenizer = None
        self.asr.confidence_validation = False
        self.asr.buffer_trimming = 'segment'
        # Each step re-decodes the unconfirmed buffer; a shorter buffer keeps long
        # lectures from falling behind (was 10 s).
        self.asr.buffer_trimming_sec = BUFFER_SECONDS
        prompt='An English lecture.' if language_mode=='en' else 'Русская речь.' if language_mode=='ru' else ''
        if hint:prompt+=' Topics include '+hint.rstrip(', .')+'.'
        self.decoder = GuardedDecoder(self.asr.model, self.asr.model_size_or_path, prompt, language, self.language_router)
        self.decoder.wait_for_context = language_mode=='mixed'
        self.asr.transcribe = self.decoder
        self.online = OnlineASRProcessor(self.asr)
        self.recovery = RecoveryGuard(self.online)
        original_words = self.asr.ts_words
        def audited_words(segments):
            for segment in segments:
                if segment.get('no_speech_prob', 0) > .9:
                    offset = getattr(self.online, 'recovery_offset', self.online.buffer_time_offset)
                    self.recovery.record('decoder_filtered', offset+segment.get('start', 0),
                        offset+segment.get('end', 0), reason='no_speech_prob_gt_0.9', had_words=bool(segment.get('words')))
            return original_words(segments)
        self.asr.ts_words = audited_words
        self.preroll = b''
        self.end_seconds = 0
        self.run_start = 0
        self.run_frontier = 0
        self.last_speech_end = 0
        self.vad = vad
        self.active = False
        self.silence = 0.0
        self.np = np
        self.torch = torch
        # Compile the common MLX path before starting the classroom clock.
        self.asr.model(np.zeros(16000,dtype=np.float32),language=language,task='transcribe',temperature=0.0,
            word_timestamps=True,path_or_hf_repo=self.asr.model_size_or_path)

    def set_backlog(self, seconds):
        # Called only between completed worker calls. Once more than a few seconds
        # behind, keep an explicit review gap instead of spending extra decodes that
        # would push every following sentence later.
        self.recovery.allow_recovery = seconds < BACKLOG_NO_RETRY
        self.decoder.allow_recovery = seconds < BACKLOG_NO_RETRY

    def process(self, data, end_seconds):
        np = self.np
        start_seconds = end_seconds-len(data)/32000
        self.end_seconds = end_seconds
        audio = np.frombuffer(data,dtype='<i2').astype(np.float32)/32768.0
        probability = 0.0
        speech = False
        speech_end = start_seconds
        for start in range(0,len(audio),512):
            frame = audio[start:start+512]
            if len(frame)<512:frame=np.pad(frame,(0,512-len(frame)))
            frame_probability = float(self.vad(self.torch.from_numpy(frame),16000).item())
            probability = max(probability, frame_probability)
            # Lower-confidence VAD needs energy in the same 32 ms frame.
            candidate = frame_probability > .5 or (frame_probability >= .2 and float(np.sqrt(np.mean(frame*frame))) >= .002)
            if candidate:
                speech = True
                speech_end = start_seconds+min(start+512,len(audio))/16000
        rms = float(np.sqrt(np.mean(audio*audio))) if len(audio) else 0
        if not self.active and not speech:
            self.recovery.record('vad_skip', start_seconds, end_seconds,
                reason='vad_no_speech', max_probability=probability, rms=rms,
                preroll_seconds=min(.75, len(self.preroll+data)/32000))
            self.preroll = (self.preroll+data)[-24000:]
            return [],[],False
        if not self.active:
            router=getattr(self,'language_router',None)
            if router:router.reset()
            data = self.preroll+data
            self.run_start = start_seconds-len(self.preroll)/32000
            self.run_frontier = self.recovery.frontier
            self.online.init(offset=self.run_start)
            self.online.transcript_buffer.last_committed_time = max(self.run_start,self.recovery.frontier)
            if start_seconds > self.run_start:
                self.recovery.record('preroll_replay',self.run_start,start_seconds,reason='speech_onset_context')
            self.recovery.attempts = 0
            self.preroll = b''
            audio = np.frombuffer(data,dtype='<i2').astype(np.float32)/32768.0
        self.online.insert_audio_chunk(audio)
        self.active = True
        if speech:self.last_speech_end=speech_end
        # Pause since the last voiced frame, not since the last fully silent chunk.
        self.silence=max(0.0,end_seconds-max(self.last_speech_end,self.run_start))
        try:
            self.decoder.offset = self.online.buffer_time_offset
            self.decoder.frontier = getattr(self.recovery,'frontier',self.decoder.offset)
            self.decoder.finalizing = self.silence>=END_PAUSE
            router=getattr(self,'language_router',None)
            if router:router.frontier=self.recovery.frontier
            committed,_ = self.recovery.process_iter()
        except RecognitionPending:
            return self._result([], [], False, waiting=True)
        except RejectedRecognition:
            return self._reject(end_seconds)
        if (self.silence>=END_PAUSE
                or (self.silence>=END_PAUSE_PUNCTUATED and self._ends_sentence(committed))
                or (self.silence>=LONG_RUN_PAUSE and end_seconds-self.run_start>=LONG_RUN)):
            rest,_=self.online.finish()
            committed += self.recovery.accept(rest)
            self._close_run(end_seconds)
            self.online.init(offset=end_seconds)
            return self._result(committed, [], True)
        partial=[w for w in self.online.transcript_buffer.buffer if w.end>self.recovery.frontier+1e-6]
        self.online.committed=self.online.committed[-300:]
        return self._result(committed, partial, False)

    def _ends_sentence(self, committed):
        buffer=getattr(self.online,'transcript_buffer',None)
        words=list(committed)+list(getattr(buffer,'buffer',[]) or [])
        if not words and getattr(self.online,'committed',None):words=self.online.committed[-1:]
        return bool(words) and str(words[-1].text).strip().endswith(SENTENCE_END)

    def _close_run(self, end):
        if self.recovery.frontier <= self.run_frontier:
            self.recovery.record('gap', self.run_start, end, reason='speech_candidate_without_words')
        elif self.last_speech_end-self.recovery.frontier > 1.5:
            self.recovery.record('gap', self.recovery.frontier, self.last_speech_end,
                reason='speech_candidate_tail_without_words')
        self.active=False
        self.silence=0

    def drain_events(self):
        return self.recovery.drain_events()

    def finish(self):
        # Inactive preroll has already been audited as VAD skip, not missing speech.
        if not self.active:
            self.preroll=b''
            return [],[],True
        try:
            self.decoder.offset = self.online.buffer_time_offset
            self.decoder.frontier = getattr(self.recovery,'frontier',self.decoder.offset)
            self.decoder.finalizing = True
            committed,_=self.recovery.process_iter()
        except RejectedRecognition:
            return self._reject(self.end_seconds)
        rest,_=self.online.finish()
        committed += self.recovery.accept(rest)
        self._close_run(self.end_seconds)
        return self._result(committed, [], True)

    def _result(self, committed, partial, flush, rejected=False, waiting=False):
        events, self.decoder.events = self.decoder.events, []
        return committed, partial, flush, dict(events=events, rejected=rejected, waiting=waiting)

    def _reject(self, end_seconds):
        # Quality rejection has its own bounded retry; never launch coverage retries too.
        self.online.init(offset=end_seconds)
        self.decoder.pending_end=None
        self.active=False
        self.silence=0
        self.preroll=b''
        return self._result([], [], False, rejected=True)
