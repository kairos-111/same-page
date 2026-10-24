from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work"
OUTPUTS = ROOT / "outputs"
MODELS = WORK / "downloads" / "models"
SESSIONS = OUTPUTS / "sessions"
STATIC = ROOT / "classroom" / "static"
LLAMA_URL = "http://127.0.0.1:8768"
SAMPLE_RATE = 16000
BYTES_PER_SECOND = SAMPLE_RATE * 2
