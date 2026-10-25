"""faster-whisper (CTranslate2) backend for computers without Apple's MLX, i.e. Windows.

It presents the same shapes the MLX path produces, so the quality gates, recovery,
language routing and LocalAgreement code run unchanged:
- `transcribe_fn(model)` behaves like `mlx_whisper.transcribe` (dict segments/words);
- `CT2Whisper` stands in for whisperlivekit's MLXWhisper;
- `NumpyVAD` is Silero VAD on onnxruntime without PyTorch.
Nothing here imports torch or mlx.
"""
import os
from pathlib import Path

CPU_THREADS = max(2, min(8, (os.cpu_count() or 4) - 2))


def load_model(path):
    from faster_whisper import WhisperModel
    path = Path(path)
    if not (path / 'model.bin').is_file():
        raise RuntimeError('识别模型尚未下载完成')
    options = dict(device='cpu', compute_type='int8', cpu_threads=CPU_THREADS, local_files_only=True)
    # CPU int8 works on every x86-64 Windows PC; GPU builds need extra CUDA files.
    if str(path).isascii():
        return WhisperModel(str(path), **options)
    # CTranslate2 opens files with narrow paths, which fail for non-ASCII Windows user
    # names; hand it the files' contents instead.
    files = {p.name: p.read_bytes() for p in path.iterdir() if p.is_file() and p.suffix in ('.bin', '.json', '.txt')}
    return WhisperModel('whisper', files=files, **options)


def _word(w):
    return dict(start=float(w.start), end=float(w.end), word=w.word, probability=float(w.probability))


def transcribe_fn(model):
    """Return a callable with mlx_whisper.transcribe's signature and result shape."""
    def transcribe(audio, language=None, task='transcribe', initial_prompt=None, word_timestamps=True,
                   condition_on_previous_text=False, temperature=0.0, path_or_hf_repo=None,
                   sample_len=None, **_):
        options = dict(language=language, task=task, initial_prompt=initial_prompt or None,
                       word_timestamps=word_timestamps, condition_on_previous_text=condition_on_previous_text,
                       temperature=temperature, beam_size=1, best_of=1, vad_filter=False)
        if sample_len:
            options['max_new_tokens'] = int(sample_len)
        segments, info = model.transcribe(audio, **options)
        result = []
        for s in segments:
            result.append(dict(id=s.id, seek=s.seek, start=float(s.start), end=float(s.end), text=s.text,
                               tokens=list(s.tokens), temperature=s.temperature, avg_logprob=s.avg_logprob,
                               compression_ratio=s.compression_ratio, no_speech_prob=s.no_speech_prob,
                               words=[_word(w) for w in (s.words or [])]))
        return dict(text=''.join(s['text'] for s in result), segments=result, language=info.language)
    return transcribe


def language_detector(model):
    def detect(audio):
        _, _, probabilities = model.detect_language(audio)
        return {language: float(p) for language, p in probabilities}
    return detect


def online_processor_class():
    # Vendored copy: importing the whisperlivekit package pulls in PyTorch.
    from .wlk.online_asr import OnlineASRProcessor
    return OnlineASRProcessor


class CT2Whisper:
    """The attributes whisperlivekit's OnlineASRProcessor uses from MLXWhisper."""
    sep = ''

    def __init__(self, model, lan='auto', model_dir=''):
        self.original_language = None if lan == 'auto' else lan
        self.transcribe_kargs = {}
        self.weights = model
        self.model = transcribe_fn(model)
        self.model_size_or_path = str(model_dir)

    def transcribe(self, audio, init_prompt=''):
        result = self.model(audio, language=self.original_language, initial_prompt=init_prompt,
                            word_timestamps=True, condition_on_previous_text=True)
        return result.get('segments', [])

    def ts_words(self, segments):
        from .wlk.timed_objects import ASRToken
        tokens = []
        for segment in segments:
            if segment.get('no_speech_prob', 0) > 0.9:
                continue
            for word in segment.get('words', []):
                tokens.append(ASRToken(word['start'], word['end'], word['word']))
        return tokens

    def segments_end_ts(self, res):
        return [s['end'] for s in res]

    def use_vad(self):
        pass


class _Probability:
    def __init__(self, value):
        self.value = value

    def item(self):
        return self.value


class NumpyVAD:
    """Silero VAD v5/v6 ONNX with per-instance state, numpy only (16 kHz, 512 samples)."""

    def __init__(self, model_path=None):
        import numpy as np
        import onnxruntime
        if model_path is None:
            model_path = Path(__file__).parent / 'wlk' / 'silero_vad.onnx'
        options = onnxruntime.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        self.session = onnxruntime.InferenceSession(str(model_path), sess_options=options,
                                                    providers=['CPUExecutionProvider'])
        self.np = np
        self.reset_states()

    def reset_states(self):
        np = self.np
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, 64), dtype=np.float32)

    def __call__(self, frame, sample_rate=16000):
        np = self.np
        x = np.asarray(frame, dtype=np.float32).reshape(1, -1)
        if x.shape[1] != 512 or sample_rate != 16000:
            raise ValueError('VAD expects 512 samples at 16 kHz')
        x = np.concatenate([self.context, x], axis=1)
        out, self.state = self.session.run(None, {'input': x, 'state': self.state,
                                                  'sr': np.array(sample_rate, dtype=np.int64)})
        self.context = x[:, -64:]
        return _Probability(float(np.asarray(out).reshape(-1)[0]))


class NumpyBridge:
    """Stands in for `torch` in WhisperAdapter.process: frames stay numpy arrays."""
    @staticmethod
    def from_numpy(array):
        return array
