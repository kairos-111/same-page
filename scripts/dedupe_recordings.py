"""Remove the duplicate PCM spool of finished records whose WAV copy is byte-identical.

Records saved before 0.10.2 kept every recording twice: the PCM spool and a WAV
export of each five-minute part. This script deletes a PCM file only when the
record is finished and the WAV beside it holds exactly the same samples. It
prints what it would do unless --apply is given.

    .venv/bin/python scripts/dedupe_recordings.py [--root SESSIONS] [--apply]
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from classroom.library import wav_data_section  # noqa: E402
from classroom.paths import SESSIONS  # noqa: E402

BUSY = {'loading', 'running', 'draining'}


def digest(path, offset, length):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        handle.seek(offset)
        remaining = length
        while remaining > 0:
            data = handle.read(min(1 << 20, remaining))
            if not data:
                break
            h.update(data)
            remaining -= len(data)
    return h.hexdigest()


def finished(directory):
    try:
        metadata = json.loads((directory / 'session.json').read_text())
    except (OSError, ValueError):
        return False
    # A record still waiting for the keep/discard choice is not touched.
    if metadata.get('recording_decision') == 'pending':
        return False
    # Older records have no `saved` flag; an ended state is enough there.
    state = metadata.get('state')
    return state not in BUSY and (bool(metadata.get('saved')) or state in ('completed', 'error', 'interrupted'))


def duplicates(directory):
    """PCM files whose WAV twin has identical samples."""
    found = []
    for pcm in sorted(directory.glob('audio_*.pcm')):
        wav = pcm.with_suffix('.wav')
        if pcm.is_symlink() or wav.is_symlink() or not wav.is_file():
            continue
        section = wav_data_section(wav)
        size = pcm.stat().st_size
        if not section or section[1] != size:
            continue
        if digest(pcm, 0, size) == digest(wav, *section):
            found.append(pcm)
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--root', type=Path, default=SESSIONS)
    parser.add_argument('--apply', action='store_true', help='actually delete the duplicate PCM files')
    args = parser.parse_args()
    roots = [args.root, args.root / '.trash']
    total = 0
    for root in roots:
        if not root.is_dir():
            continue
        for directory in sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink()):
            if not finished(directory):
                continue
            files = duplicates(directory)
            if not files:
                continue
            size = sum(p.stat().st_size for p in files)
            total += size
            print(f'{directory.name}: {len(files)} 个重复 PCM，{size / 1024 ** 2:.1f} MB')
            if args.apply:
                for pcm in files:
                    pcm.unlink()
    action = '已释放' if args.apply else '可释放（加 --apply 才会删除）'
    print(f'{action} {total / 1024 ** 2:.1f} MB')


if __name__ == '__main__':
    main()
