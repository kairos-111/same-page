"""WhisperLiveKit LocalAgreement adapter; all calls run on one dedicated thread."""
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
        self.vad = OnnxWrapper(load_onnx_session())
        self.active = False
        self.silence = 0.0
        self.np = np
        self.torch = torch
        # Compile the common MLX path before starting the classroom clock.
        self.asr.model(np.zeros(16000,dtype=np.float32),language='en',temperature=0.0,
            word_timestamps=True,path_or_hf_repo=self.asr.model_size_or_path)

    def process(self, data, end_seconds):
        np = self.np
        audio = np.frombuffer(data,dtype='<i2').astype(np.float32)/32768.0
        speech = False
        for start in range(0,len(audio),512):
            frame = audio[start:start+512]
            if len(frame)<512:frame=np.pad(frame,(0,512-len(frame)))
            speech = bool(self.vad(self.torch.from_numpy(frame),16000).item()>.5) or speech
        if not self.active and not speech:
            self.online.init(offset=end_seconds)
            return [],[],False
        self.online.insert_audio_chunk(audio)
        if speech:
            self.active=True;self.silence=0
        else:
            self.silence+=len(audio)/16000
        try:
            self.decoder.offset = self.online.buffer_time_offset
            committed,_ = self.online.process_iter()
        except RejectedRecognition:
            return self._reject(end_seconds)
        if self.silence>=1:
            rest,_=self.online.finish()
            self.online.init(offset=end_seconds)
            self.active=False;self.silence=0
            return self._result(committed+rest, [], True)
        partial=list(self.online.transcript_buffer.buffer)
        self.online.committed=self.online.committed[-300:]
        return self._result(committed, partial, False)

    def finish(self):
        if not self.active:
            return [],[],True
        end_seconds = self.online.get_audio_buffer_end_time()
        try:
            self.decoder.offset = self.online.buffer_time_offset
            committed,_=self.online.process_iter()
        except RejectedRecognition:
            return self._reject(end_seconds)
        rest,_=self.online.finish()
        self.active=False
        return self._result(committed+rest, [], True)

    def _result(self, committed, partial, flush, rejected=False):
        events, self.decoder.events = self.decoder.events, []
        return committed, partial, flush, dict(events=events, rejected=rejected)

    def _reject(self, end_seconds):
        # Nothing from this failed hypothesis reaches LocalAgreement or finish().
        # The audio remains in the session journal; reset prevents endless retries.
        self.online.init(offset=end_seconds)
        self.active=False
        self.silence=0
        return self._result([], [], False, rejected=True)
