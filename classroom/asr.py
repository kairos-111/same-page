"""WhisperLiveKit LocalAgreement adapter; all calls run on one dedicated thread."""
from .asr_recovery import RecoveryGuard
from .asr_quality import GuardedDecoder, RejectedRecognition

class WhisperAdapter:
    def __init__(self, model_path, hint=''):
        import mlx.core as mx
        import numpy as np
        import torch
        from whisperlivekit.local_agreement.backends import MLXWhisper
        from whisperlivekit.local_agreement.online_asr import OnlineASRProcessor
        from whisperlivekit.silero_vad_iterator import load_onnx_session, OnnxWrapper
        torch.set_num_threads(2)
        if not mx.metal.is_available():
            raise RuntimeError('MLX 未检测到 Apple GPU')
        self.asr = MLXWhisper(lan='en',model_dir=str(model_path))
        self.asr.tokenizer = None
        self.asr.confidence_validation = False
        self.asr.buffer_trimming = 'segment'
        self.asr.buffer_trimming_sec = 10
        prompt='An English lecture.'
        if hint:prompt+=' Topics include '+hint.rstrip(', .')+'.'
        self.decoder = GuardedDecoder(self.asr.model, self.asr.model_size_or_path, prompt)
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
        self.vad = OnnxWrapper(load_onnx_session())
        self.active = False
        self.silence = 0.0
        self.np = np
        self.torch = torch
        # Compile the common MLX path before starting the classroom clock.
        self.asr.model(np.zeros(16000,dtype=np.float32),language='en',temperature=0.0,
            word_timestamps=True,path_or_hf_repo=self.asr.model_size_or_path)

    def set_backlog(self, seconds):
        # Called only between completed worker calls. At high lag, preserve an
        # explicit gap rather than spend two additional decodes and compound lag.
        self.recovery.allow_recovery = seconds < 15
        self.decoder.allow_recovery = seconds < 15

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
        if speech:
            self.silence=0
            self.last_speech_end=speech_end
        else:self.silence+=len(audio)/16000
        try:
            self.decoder.offset = self.online.buffer_time_offset
            committed,_ = self.recovery.process_iter()
        except RejectedRecognition:
            return self._reject(end_seconds)
        if self.silence>=1:
            rest,_=self.online.finish()
            committed += self.recovery.accept(rest)
            self._close_run(end_seconds)
            self.online.init(offset=end_seconds)
            return self._result(committed, [], True)
        partial=[w for w in self.online.transcript_buffer.buffer if w.end>self.recovery.frontier+1e-6]
        self.online.committed=self.online.committed[-300:]
        return self._result(committed, partial, False)

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
            committed,_=self.recovery.process_iter()
        except RejectedRecognition:
            return self._reject(self.end_seconds)
        rest,_=self.online.finish()
        committed += self.recovery.accept(rest)
        self._close_run(self.end_seconds)
        return self._result(committed, [], True)

    def _result(self, committed, partial, flush, rejected=False):
        events, self.decoder.events = self.decoder.events, []
        return committed, partial, flush, dict(events=events, rejected=rejected)

    def _reject(self, end_seconds):
        # Quality rejection has its own bounded retry; never launch coverage retries too.
        self.online.init(offset=end_seconds)
        self.active=False
        self.silence=0
        self.preroll=b''
        return self._result([], [], False, rejected=True)
