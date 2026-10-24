"""Append-only session events and bounded-memory audio journal."""
import json
from pathlib import Path
import wave

RATE = 16000
CHUNK_BYTES = RATE * 2 * 300

def srt_time(seconds):
    ms = max(0, round(seconds * 1000))
    s, ms = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

class AudioJournal:
    """Five-minute PCM files serve as durable spool; unread audio never queues in RAM."""
    def __init__(self, directory):
        self.path = Path(directory)
        self.path.mkdir(parents=True, exist_ok=True)
        self.bytes = 0
        self.handle = None
        self.closed = False

    @property
    def seconds(self):
        return self.bytes / (RATE * 2)

    def append(self, data):
        if self.closed:
            raise RuntimeError("Audio journal is closed")
        if len(data) % 2:
            raise ValueError("PCM must contain complete int16 samples")
        view = memoryview(data)
        while view:
            i, offset = divmod(self.bytes, CHUNK_BYTES)
            if self.handle is None:
                self.handle = (self.path / f"audio_{i:04}.pcm").open("ab", buffering=0)
            take = min(len(view), CHUNK_BYTES - offset)
            self.handle.write(view[:take])
            self.bytes += take
            view = view[take:]
            if self.bytes % CHUNK_BYTES == 0:
                self.handle.close()
                self.handle = None

    def read(self, start_sample, max_samples):
        pos = start_sample * 2
        remaining = min(max_samples * 2, self.bytes - pos)
        chunks = []
        while remaining > 0:
            i, offset = divmod(pos, CHUNK_BYTES)
            count = min(remaining, CHUNK_BYTES - offset)
            with (self.path / f"audio_{i:04}.pcm").open("rb") as f:
                f.seek(offset)
                data = f.read(count)
            if not data:
                break
            chunks.append(data)
            pos += len(data)
            remaining -= len(data)
        return b"".join(chunks)

    def close(self):
        self.closed = True
        if self.handle:
            self.handle.close()
            self.handle = None

    def export_wav(self):
        for p in sorted(self.path.glob("audio_*.pcm")):
            with p.open("rb") as src, wave.open(str(p.with_suffix(".wav")), "wb") as dest:
                dest.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
                while data := src.read(1024 * 1024):
                    dest.writeframesraw(data)

class SessionStore:
    def __init__(self, directory, metadata):
        self.path = Path(directory)
        self.path.mkdir(parents=True, exist_ok=False)
        self.metadata = metadata
        self.save_metadata()
        self.events = (self.path / "events.jsonl").open("a", encoding="utf-8", buffering=1)
        self.audio = AudioJournal(self.path)

    def save_metadata(self):
        tmp = self.path / "session.tmp"
        tmp.write_text(json.dumps(self.metadata, ensure_ascii=False, indent=2))
        tmp.replace(self.path / "session.json")

    def append(self, event):
        self.events.write(json.dumps(event, ensure_ascii=False) + "\n")

    @staticmethod
    def load_segments(path):
        segments = {}
        events = Path(path) / "events.jsonl"
        if events.exists():
            with events.open() as f:
                for line in f:
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("type") == "segment":
                        segments[obj["id"]] = obj
        return sorted(segments.values(), key=lambda s: (s["start"], s["id"]))

    def export(self):
        export_session(self.path)

    def close(self):
        self.audio.close()
        self.events.close()

def export_session(path):
    path = Path(path)
    segments = SessionStore.load_segments(path)
    text, srt = [], []
    index = 0
    for s in segments:
        final = s.get("final", False)
        translated = s.get("translation", "")
        label = "已确认" if final else "未确认"
        text.append(f"[{srt_time(s['start'])} — {srt_time(s['end'])}] {label}\n{s['source']}\n{translated or '[待翻译]'}\n")
        if final and translated and s.get("translated_revision") == s["revision"]:
            index += 1
            srt.append(f"{index}\n{srt_time(s['start'])} --> {srt_time(max(s['end'],s['start']+.2))}\n{translated}\n{s['source']}\n")
    (path / "双语记录.txt").write_text("\n".join(text))
    (path / "双语字幕.srt").write_text("\n".join(srt))
