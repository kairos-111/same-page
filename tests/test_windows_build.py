"""Windows build pieces that can be checked on macOS (no Windows APIs needed)."""
from pathlib import Path
import py_compile
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CT2_SMALL = ROOT / 'work' / 'win' / 'models' / 'whisper-small-ct2'


class NumpyVADTests(unittest.TestCase):
    def test_matches_the_torch_wrapper_frame_by_frame(self):
        try:
            import torch
            from whisperlivekit.silero_vad_iterator import OnnxWrapper, load_onnx_session
        except ImportError:
            self.skipTest('reference wrapper needs torch')
        from classroom.ct2_asr import NumpyVAD
        reference, ours = OnnxWrapper(load_onnx_session()), NumpyVAD()
        rng = np.random.default_rng(0)
        t = np.arange(512 * 40) / 16000
        audio = (0.3 * np.sin(2 * np.pi * 220 * t) * (1 + np.sin(2 * np.pi * 3 * t))
                 + 0.02 * rng.standard_normal(len(t))).astype(np.float32)
        for start in range(0, len(audio), 512):
            frame = audio[start:start + 512]
            expected = float(reference(torch.from_numpy(frame), 16000).item())
            self.assertAlmostEqual(float(ours(frame, 16000).item()), expected, places=5)

    def test_rejects_wrong_frame_size(self):
        from classroom.ct2_asr import NumpyVAD
        with self.assertRaises(ValueError):
            NumpyVAD()(np.zeros(256, dtype=np.float32))


class TranscribeShapeTests(unittest.TestCase):
    def test_results_have_the_mlx_shape_and_token_limit(self):
        from classroom.ct2_asr import transcribe_fn
        seen = {}
        word = SimpleNamespace(start=0.1, end=0.4, word=' Hello', probability=0.9)
        segment = SimpleNamespace(id=0, seek=0, start=0.0, end=0.5, text=' Hello', tokens=[1, 2], temperature=0.0,
                                  avg_logprob=-0.1, compression_ratio=1.0, no_speech_prob=0.01, words=[word])

        class Model:
            def transcribe(self, audio, **options):
                seen.update(options)
                return iter([segment]), SimpleNamespace(language='en')

        result = transcribe_fn(Model())(np.zeros(16000, np.float32), language='en', initial_prompt='',
                                         sample_len=40, path_or_hf_repo='ignored')
        self.assertEqual(result['language'], 'en')
        self.assertEqual(result['segments'][0]['words'], [dict(start=0.1, end=0.4, word=' Hello', probability=0.9)])
        self.assertEqual(seen['max_new_tokens'], 40)
        self.assertIsNone(seen['initial_prompt'], 'empty prompt is not sent')
        self.assertFalse(seen['vad_filter'])

    def test_language_detector_returns_a_probability_dict(self):
        from classroom.ct2_asr import language_detector
        model = SimpleNamespace(detect_language=lambda audio: ('ru', .8, [('ru', .8), ('en', .15)]))
        self.assertEqual(language_detector(model)(np.zeros(16000)), {'ru': .8, 'en': .15})


class SystemHelperTests(unittest.TestCase):
    def test_windows_commands_use_the_engine_python(self):
        import classroom.system as system
        with patch.object(system, 'WINDOWS', True):
            capture = system.capture_command(Path('/missing'), 'system')
            self.assertEqual(capture[0], sys.executable)
            self.assertTrue(capture[-2].endswith('capture_windows.py'))
            self.assertEqual(capture[-1], 'system')
            self.assertTrue(system.decode_command('in.mp4')[-2].endswith('decode_file.py'))

    def test_llama_prefers_vulkan_then_cpu(self):
        import classroom.system as system
        with tempfile.TemporaryDirectory() as tmp, patch.object(system, 'WINDOWS', True):
            root = Path(tmp)
            for flavour in ('llama-cpu', 'llama-vulkan'):
                (root / flavour).mkdir()
                (root / flavour / 'llama-server.exe').write_bytes(b'x')
            self.assertEqual([p.parent.name for p in system.llama_candidates(root)], ['llama-vulkan', 'llama-cpu'])

    def test_mac_reveal_and_folders_still_use_open(self):
        import classroom.system as system
        with patch.object(system, 'WINDOWS', False), patch('classroom.system.subprocess.run') as run:
            system.reveal_file(Path('/tmp/a.wav'))
            self.assertEqual(run.call_args.args[0], ['/usr/bin/open', '-R', '/tmp/a.wav'])


class LlamaFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_cpu_server_is_used_when_the_gpu_server_fails(self):
        from classroom import runtime as runtime_module
        r = runtime_module.Runtime()
        started = []

        async def start(binary, model):
            started.append(binary.name)
            r.llama = SimpleNamespace(returncode=1 if binary.name == 'gpu' else None, terminate=lambda: None)

        async def wait():
            if r.llama.returncode is not None:
                raise RuntimeError('翻译服务启动失败')

        async def stop():
            r.llama = None

        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / 'hy-mt' / 'Hy-MT2-1.8B-Q4_K_M.gguf'
            model.parent.mkdir()
            model.write_bytes(b'x')
            with patch.object(runtime_module, 'MODELS', Path(tmp)), \
                 patch.object(r, 'llama_candidates', return_value=[Path('gpu'), Path('cpu')]), \
                 patch.object(r, '_start_llama', start), patch.object(r, '_wait_for_llama', wait), \
                 patch.object(r, 'stop_translation', stop):
                await r.ensure_translation()
        self.assertEqual(started, ['gpu', 'cpu'])
        r.executor.shutdown(wait=False)


class OverviewTests(unittest.TestCase):
    def test_windows_hides_a_model_that_is_not_shipped(self):
        import classroom.desktop as desktop
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'whisper-small-ct2').mkdir()
            (root / 'whisper-small-ct2' / 'model.bin').write_bytes(b'x')
            with patch.object(desktop, 'WINDOWS', True), patch.object(desktop, 'MODELS', root), \
                 patch.object(desktop, 'ASR_MODEL_FILES', 'model.bin'), \
                 patch.object(desktop, 'asr_model_dir', lambda models, m: models / f'whisper-{m}-ct2'), \
                 patch.object(desktop, 'asr_model_available', lambda models, m: (models / f'whisper-{m}-ct2' / 'model.bin').exists()):
                data = desktop.overview(SimpleNamespace(llama=None, asr_models={}, model=None))
        self.assertEqual([m['id'] for m in data['models']], ['small', 'hy-mt'])
        self.assertTrue(data['models'][0]['available'])
        self.assertEqual(data['platform'], 'windows')


class ScriptsCompileTests(unittest.TestCase):
    def test_windows_scripts_are_valid_python(self):
        for path in (ROOT / 'windows' / 'launcher.pyw', ROOT / 'classroom' / 'capture_windows.py',
                     ROOT / 'classroom' / 'decode_file.py', ROOT / 'windows' / 'build_windows.py'):
            py_compile.compile(str(path), doraise=True, cfile=tempfile.mktemp())

    def test_vendored_processor_matches_upstream(self):
        try:
            import whisperlivekit
        except ImportError:
            self.skipTest('whisperlivekit not installed')
        upstream = (Path(whisperlivekit.__file__).parent / 'local_agreement' / 'online_asr.py').read_text()
        upstream = upstream.replace('from whisperlivekit.timed_objects import', 'from .timed_objects import')
        self.assertEqual((ROOT / 'classroom' / 'wlk' / 'online_asr.py').read_text(), upstream)


@unittest.skipUnless((CT2_SMALL / 'model.bin').is_file(), 'Windows recognition model not downloaded')
class CT2RecognitionTests(unittest.TestCase):
    def test_adapter_recognises_speech_without_torch_or_mlx(self):
        import wave
        from classroom.asr import WhisperAdapter
        from classroom.ct2_asr import load_model
        sample = ROOT / 'tests' / 'fixtures' / 'hello_en.wav'
        if not sample.is_file():
            self.skipTest('speech fixture missing')
        with wave.open(str(sample)) as w:
            pcm = w.readframes(w.getnframes())
        adapter = WhisperAdapter(CT2_SMALL, '', 'en', weights=load_model(CT2_SMALL))
        words = []
        pcm += b'\0\0' * 32000
        for i in range(0, len(pcm), 32000):
            words += [w.text for w in adapter.process(pcm[i:i + 32000], (i + len(pcm[i:i + 32000])) / 32000)[0]]
        words += [w.text for w in adapter.finish()[0]]
        self.assertIn('internet', ''.join(words).lower())


if __name__ == '__main__':
    unittest.main()
