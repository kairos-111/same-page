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

def check_integrity(path, audio_seconds, processed_seconds, asr_flushed=True):
    """End-of-run bookkeeping, never a claim that every spoken word was heard."""
    path = Path(path)
    segments = SessionStore.load_segments(path)
    unmatched = [s['id'] for s in segments if not (
        s.get('asr_status') == 'unresolved' or
        (s.get('final') and s.get('translation') and s.get('translated_revision') == s.get('revision')))]
    gaps = [dict(start=s['start'], end=s['end'], reason=s.get('asr_reason', 'asr_unresolved'))
            for s in segments if s.get('asr_status') == 'unresolved']
    counts = {}
    with (path/'events.jsonl').open() as events:
        for line in events:
            try:event = json.loads(line)
            except json.JSONDecodeError:continue
            if event.get('type') != 'asr_coverage':continue
            action = event['action']
            counts[action] = counts.get(action, 0)+1
            if action == 'gap':
                gaps.append({k:event[k] for k in ('start', 'end', 'reason')})
    if processed_seconds < audio_seconds-1/16000:
        gaps.append(dict(start=processed_seconds, end=audio_seconds, reason='unprocessed_audio'))
    if not asr_flushed and audio_seconds>0:
        # Even a fully advanced read cursor can leave hypotheses inside the ASR.
        tail=max((s['end'] for s in segments if s.get('final')),default=0)
        gaps.append(dict(start=min(tail,audio_seconds),end=audio_seconds,reason='asr_finalize_incomplete'))
    warning = ''
    if gaps or unmatched:
        warning = f'已保存，但有 {len(gaps)} 个待核查识别区间、{len(unmatched)} 句未完成匹配译文；请查看双语文本和原声。'
    return dict(state='needs_review' if warning else 'checked', warning=warning,
                recognition_gaps=gaps, unmatched_translation_ids=unmatched,
                asr_flushed=asr_flushed,coverage_events=counts, scope='流程核对，不证明逐词完整或识别准确')


def export_session(path):
    path = Path(path)
    segments = SessionStore.load_segments(path)
    metadata_path = path/'session.json'
    metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
    integrity = metadata.get('integrity')
    note = (integrity.get('warning') or '流程核对完成；不证明逐词完整或识别准确。') if integrity else '此记录尚无结束完整性核对；不能据此证明字幕完整。'
    text, srt = [note+'\n'], []
    if integrity:
        for gap in integrity.get('recognition_gaps', []):
            text.append(f"[待核查识别区间 {srt_time(gap['start'])} — {srt_time(gap['end'])}] {gap['reason']}")
        if integrity.get('unmatched_translation_ids'):
            text.append('未完成匹配译文：'+', '.join(integrity['unmatched_translation_ids'])+'\n')
    index = 0
    for s in segments:
        final = s.get("final", False)
        translated = s.get("translation", "")
        unresolved = s.get('asr_status') == 'unresolved'
        matched = bool(translated) and s.get('translated_revision') == s['revision']
        source_label = '原文已定稿' if final else '原文未定稿'
        target_label = ('译文版本匹配' if final else '译文预览匹配') if matched else ('译文过期' if translated else '译文缺失')
        label = '识别异常／未确认，需定位录音补处理' if unresolved else source_label+' / '+target_label
        pending = '[识别未恢复，未送翻译]' if unresolved else '[待翻译]'
        text.append(f"[{srt_time(s['start'])} — {srt_time(s['end'])}] {label}\n{s['source']}\n{translated or pending}\n")
        if not unresolved and final and translated and s.get("translated_revision") == s["revision"]:
            index += 1
            srt.append(f"{index}\n{srt_time(s['start'])} --> {srt_time(max(s['end'],s['start']+.2))}\n{translated}\n{s['source']}\n")
    # Legacy exports remain evidence. Download a separately named current rendering.
    legacy = not integrity and ((path/'双语记录.txt').exists() or (path/'双语字幕.srt').exists())
    suffix = '-核对版' if legacy else ''
    files = dict(txt=path/f'双语记录{suffix}.txt',srt=path/f'双语字幕{suffix}.srt')
    files['txt'].write_text('\n'.join(text))
    files['srt'].write_text('\n'.join(srt))
    return files
