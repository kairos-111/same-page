"""Operating-system differences in one place (macOS app vs Windows app)."""
import os
from pathlib import Path
import subprocess
import sys

WINDOWS = sys.platform == 'win32'
# MLX needs Apple silicon; elsewhere recognition uses faster-whisper (CTranslate2).
# The variable lets the Windows path be exercised on a Mac during development.
ASR_BACKEND = os.environ.get('SAMEPAGE_ASR_BACKEND') or ('ct2' if WINDOWS else 'mlx')
ENGINE_ROOT = Path(__file__).resolve().parents[1]

# Directory of each recognition model, per backend. Turbo is not shipped on Windows.
ASR_MODEL_DIRS = {
    'mlx': {'small': 'whisper-small', 'turbo': 'whisper-turbo'},
    'ct2': {'small': 'whisper-small-ct2', 'turbo': 'whisper-turbo-ct2'},
}[ASR_BACKEND]
ASR_MODEL_FILES = 'weights.*' if ASR_BACKEND == 'mlx' else 'model.bin'


def asr_model_dir(models, model):
    return models / ASR_MODEL_DIRS[model]


def asr_model_available(models, model):
    return any(p.is_file() and p.stat().st_size > 0 for p in asr_model_dir(models, model).glob(ASR_MODEL_FILES))


def hidden_window():
    """Keep helper programs from flashing a console window on Windows."""
    return dict(creationflags=subprocess.CREATE_NO_WINDOW) if WINDOWS else {}


def python_script(name, *args):
    """Run one of the engine's helper scripts with the engine's own Python."""
    return [sys.executable, '-I', '-B', str(ENGINE_ROOT / 'classroom' / name), *map(str, args)]


def capture_command(capture_binary, source):
    if WINDOWS:
        return python_script('capture_windows.py', source)
    if not capture_binary.exists():
        raise RuntimeError('音频采集程序尚未构建')
    return [str(capture_binary), source]


def decode_command(path):
    if WINDOWS:
        return python_script('decode_file.py', path)
    import imageio_ffmpeg
    return [imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-v', 'error', '-i', str(path),
            '-f', 's16le', '-ac', '1', '-ar', '16000', 'pipe:1']


def llama_candidates(runtime_dir):
    """Translation servers to try, best first (GPU via Vulkan, then CPU)."""
    if WINDOWS:
        order = ['llama-vulkan', 'llama-cpu']
        found = [runtime_dir / d / 'llama-server.exe' for d in order]
        return [p for p in found if p.is_file()]
    return sorted(runtime_dir.rglob('llama-server'))


def open_folder(path):
    if WINDOWS:
        os.startfile(str(path))
    else:
        subprocess.run(['/usr/bin/open', str(path)], check=True, timeout=5)


def reveal_file(path):
    if WINDOWS:
        # explorer.exe returns 1 even when it succeeds, so do not check the code.
        subprocess.run(['explorer', '/select,', str(path)], timeout=5)
    else:
        subprocess.run(['/usr/bin/open', '-R', str(path)], check=True, timeout=5)
