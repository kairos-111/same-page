"""Listing, pagination, metadata edits, soft delete and in-app playback.

All functions work on a session's own directory; the caller validates the id.
"""
import json
import re
import shutil
import wave
from pathlib import Path

from .store import SessionStore

TRASH = '.trash'
MAX_TITLE = 100
PAGE_LIMIT = 200


def _read_metadata(path):
    metadata_path = Path(path) / 'session.json'
    if not metadata_path.exists():
        return {}
    try:
        return json.loads(metadata_path.read_text())
    except (OSError, ValueError):
        return {}


def _write_metadata(path, metadata):
    path = Path(path)
    temporary = path / 'session.tmp'
    temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    temporary.replace(path / 'session.json')


def update_metadata(path, changes):
    """Apply a small, explicit set of user edits to session.json."""
    metadata = _read_metadata(path)
    if 'title' in changes:
        title = str(changes['title'] or '').strip()[:MAX_TITLE] or '新记录'
        metadata['title'] = title
    if 'favorite' in changes:
        metadata['favorite'] = bool(changes['favorite'])
    if 'verified' in changes:
        metadata['verified'] = bool(changes['verified'])
    if 'note' in changes:
        metadata['note'] = str(changes['note'] or '')[:2000]
    _write_metadata(path, metadata)
    return metadata


def needs_review(item):
    """A record worth checking: flagged by the integrity check, or left unfinished."""
    if (item.get('integrity') or {}).get('warning'):
        return True
    state = item.get('state')
    return bool(state and state != 'completed' and not item.get('busy'))


def list_sessions(root, query='', limit=PAGE_LIMIT, offset=0, favorite=False, review=False, busy_ids=()):
    """Return one page of sessions, newest first.

    Filtering happens before paging so that the total always matches what the
    user is looking at: a favourite on page 3 must still be counted and reachable.
    """
    root = Path(root)
    busy_ids = set(busy_ids)
    items = []
    for directory in root.iterdir() if root.exists() else []:
        if not directory.is_dir() or directory.name.startswith('.'):
            continue
        metadata_path = directory / 'session.json'
        if not metadata_path.is_file():
            continue
        try:
            metadata = json.loads(metadata_path.read_text())
        except (OSError, ValueError):
            continue
        metadata.setdefault('id', directory.name)
        metadata['has_recording'] = any(
            p.is_file() and re.fullmatch(r'audio_\d+\.(pcm|wav)', p.name)
            for p in directory.iterdir())
        metadata['busy'] = metadata['id'] in busy_ids
        items.append(metadata)
    items.sort(key=lambda item: str(item.get('started_at') or item.get('id') or ''), reverse=True)
    # 侧边栏的角标是「一共有多少条」，不受当前筛选影响，所以在筛选之前记下来。
    all_total = len(items)
    needle = str(query or '').strip().lower()
    if needle:
        items = [item for item in items
                 if needle in str(item.get('title') or '').lower()
                 or needle in str(item.get('note') or '').lower()]
    if favorite:
        items = [item for item in items if item.get('favorite')]
    elif review:
        items = [item for item in items if needs_review(item)]
    total = len(items)
    offset = max(0, int(offset or 0))
    limit = max(1, min(int(limit or PAGE_LIMIT), PAGE_LIMIT))
    return dict(items=items[offset:offset + limit], total=total, all_total=all_total, offset=offset, limit=limit)


def read_segments(path, query='', offset=0, limit=PAGE_LIMIT):
    """Return a page of segments, optionally filtered by a text query."""
    segments = SessionStore.load_segments(Path(path))
    needle = str(query or '').strip().lower()
    if needle:
        segments = [segment for segment in segments
                    if needle in str(segment.get('source') or '').lower()
                    or needle in str(segment.get('translation') or '').lower()]
    total = len(segments)
    offset = max(0, int(offset or 0))
    limit = max(1, min(int(limit or PAGE_LIMIT), PAGE_LIMIT))
    return dict(items=segments[offset:offset + limit], total=total, offset=offset, limit=limit)


def trash_path(root, session_id):
    return Path(root) / TRASH / session_id


def move_to_trash(root, session_id):
    """Move a session directory aside instead of deleting it; recoverable."""
    root = Path(root)
    source = root / session_id
    if not source.is_dir():
        raise FileNotFoundError('记录不存在')
    target = trash_path(root, session_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    shutil.move(str(source), str(target))
    return target


def restore_from_trash(root, session_id):
    root = Path(root)
    source = trash_path(root, session_id)
    if not source.is_dir():
        raise FileNotFoundError('记录不存在')
    target = root / session_id
    if target.exists():
        raise FileExistsError('同名记录已经存在')
    shutil.move(str(source), str(target))
    try:
        source.parent.rmdir()
    except OSError:
        pass
    return target


def list_trash(root):
    root = Path(root) / TRASH
    if not root.is_dir():
        return []
    items = []
    for directory in root.iterdir():
        if not directory.is_dir():
            continue
        metadata = _read_metadata(directory)
        metadata.setdefault('id', directory.name)
        items.append(metadata)
    items.sort(key=lambda item: str(item.get('started_at') or item.get('id') or ''), reverse=True)
    return items


RATE = 16000


def wav_header(rate, data_bytes, channels=1, sample_width=2):
    """A 44-byte WAV header for raw PCM, so raw recordings can be streamed as audio."""
    import struct
    block_align = channels * sample_width
    byte_rate = rate * block_align
    return (b'RIFF' + struct.pack('<I', 36 + data_bytes) + b'WAVE'
            + b'fmt ' + struct.pack('<IHHIIHH', 16, 1, channels, rate, byte_rate, block_align, sample_width * 8)
            + b'data' + struct.pack('<I', data_bytes))


def wav_data_section(path):
    """(offset, length) of the PCM samples inside a WAV file, or None if unreadable."""
    import struct
    with Path(path).open('rb') as handle:
        head = handle.read(12)
        if len(head) < 12 or head[:4] != b'RIFF' or head[8:12] != b'WAVE':
            return None
        size = Path(path).stat().st_size
        while True:
            chunk = handle.read(8)
            if len(chunk) < 8:
                return None
            kind, length = chunk[:4], struct.unpack('<I', chunk[4:])[0]
            start = handle.tell()
            if kind == b'data':
                return start, max(0, min(length, size - start)) // 2 * 2
            handle.seek(start + length + (length & 1))


def recording_parts(files):
    """The sample data of a recording, in order, as (file, offset, length).

    Raw PCM is preferred when present: it is the complete spool, while WAV files
    beside it may be older five-minute exports of the same audio.
    """
    files = sorted(Path(p) for p in files)
    pcm = [p for p in files if p.suffix == '.pcm']
    if pcm:
        return [(p, 0, p.stat().st_size // 2 * 2) for p in pcm]
    parts = []
    for p in files:
        if p.suffix != '.wav':
            continue
        section = wav_data_section(p)
        if section and section[1]:
            parts.append((p, *section))
    return parts


class RecordingStream:
    """One continuous WAV assembled from stored parts, readable by byte range."""

    def __init__(self, files, rate=RATE):
        self.parts = recording_parts(files)
        data_bytes = sum(length for _, _, length in self.parts)
        self.header = wav_header(rate, data_bytes)
        self.size = len(self.header) + data_bytes

    def iter_range(self, start, end, block=1 << 20):
        """Yield bytes start..end (inclusive) of the virtual WAV."""
        position = start
        if position < len(self.header):
            piece = self.header[position:end + 1]
            yield piece
            position += len(piece)
        cursor = len(self.header)
        for path, offset, length in self.parts:
            if position > end:
                return
            part_end = cursor + length
            if position < part_end:
                with path.open('rb') as handle:
                    handle.seek(offset + position - cursor)
                    remaining = min(part_end, end + 1) - position
                    while remaining > 0:
                        data = handle.read(min(block, remaining))
                        if not data:
                            return
                        yield data
                        position += len(data)
                        remaining -= len(data)
            cursor = part_end


def parse_range(header, size):
    """Resolve a single `bytes=` Range header to (start, end), or None for the full file.

    Raises ValueError for a range that cannot be satisfied.
    """
    if not header:
        return None
    match = re.fullmatch(r'\s*bytes=(\d*)-(\d*)\s*', header)
    if not match or not (match.group(1) or match.group(2)):
        return None
    first, last = match.groups()
    if first:
        start = int(first)
        end = min(int(last), size - 1) if last else size - 1
    else:
        start = max(0, size - int(last))
        end = size - 1
    if start >= size or start > end:
        raise ValueError('range not satisfiable')
    return start, end


def wav_chunks(path):
    """Yield (frames, rate) for each stored wav chunk in order."""
    for candidate in sorted(Path(path).glob('audio_*.wav')):
        try:
            with wave.open(str(candidate), 'rb') as source:
                yield source.readframes(source.getnframes()), source.getframerate()
        except (OSError, wave.Error):
            continue


def recording_frames(path):
    """Concatenate stored wav chunks into one stream of frames and a rate."""
    frames = bytearray()
    rate = 16000
    for chunk, chunk_rate in wav_chunks(path):
        frames += chunk
        rate = chunk_rate
    return bytes(frames), rate
