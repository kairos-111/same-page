"""Separate read-only application assets from writable classroom data."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(os.environ.get('TINGKE_DATA_ROOT', str(ROOT))).expanduser().resolve()
WORK = DATA_ROOT / 'work'
OUTPUTS = DATA_ROOT / 'outputs'
MODELS = Path(os.environ['TINGKE_MODELS']).expanduser().resolve() if os.environ.get('TINGKE_MODELS') else ROOT / 'work' / 'downloads' / 'models'
RUNTIME = ROOT / 'work' / 'runtime'
CAPTURE = ROOT / 'outputs' / 'native' / 'ClassroomAudio.app' / 'Contents' / 'MacOS' / 'ClassroomAudio'
DEPLOYMENT = ROOT / 'work' / 'deployment.json'
SESSIONS = OUTPUTS / 'sessions'
STATIC = ROOT / 'classroom' / 'static'
API_PORT = int(os.environ.get('TINGKE_API_PORT', '8767'))
LLAMA_PORT = int(os.environ.get('TINGKE_LLAMA_PORT', '8768'))
LLAMA_URL = f'http://127.0.0.1:{LLAMA_PORT}'
SAMPLE_RATE = 16000
BYTES_PER_SECOND = SAMPLE_RATE * 2
