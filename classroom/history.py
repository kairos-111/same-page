"""Explicit user actions for ended classes, confined to their own directory."""
import json
from pathlib import Path
import re
import shutil
import subprocess

SESSION_ID = re.compile(r'\d{8}-\d{6}-[0-9a-f]{8}')
BUSY = {'loading', 'running', 'draining'}


def session_path(root, session_id):
    if not SESSION_ID.fullmatch(session_id):
        raise FileNotFoundError('记录不存在')
    root = Path(root).resolve()
    path = root / session_id
    if path.is_symlink() or not path.is_dir() or path.resolve().parent != root:
        raise FileNotFoundError('记录不存在')
    return path


def audio_files(path):
    # Symlinks never authorize deleting or revealing a file elsewhere.
    return sorted(p for p in Path(path).iterdir()
                  if re.fullmatch(r'audio_\d+\.(pcm|wav)', p.name)
                  and p.is_file() and not p.is_symlink())


def ensure_ended(session_id, active):
    if active and active.id == session_id:
        task = getattr(active, 'orchestrator', None)
        if active.state in BUSY or (task is not None and not task.done()):
            raise ValueError('本次记录仍在处理或保存，请结束并等待保存完成')


def reveal_recording(path):
    recordings = audio_files(path)
    if not recordings:
        raise FileNotFoundError('本次没有保留录音')
    target = next((p for p in recordings if p.suffix == '.wav'), recordings[0])
    subprocess.run(['/usr/bin/open', '-R', str(target)], check=True, timeout=5)


def update_retention(path, keep):
    metadata_path=Path(path)/'session.json'
    metadata=json.loads(metadata_path.read_text())
    if not keep:
        for recording in audio_files(path):recording.unlink()
    metadata.update(recording_decision='kept' if keep else 'discarded', recording_retained=keep)
    temporary=Path(path)/'session.tmp'
    temporary.write_text(json.dumps(metadata,ensure_ascii=False,indent=2))
    temporary.replace(metadata_path)
    return metadata


def remove_class(path):
    # Caller validates the session ID, location and absence of active writes.
    shutil.rmtree(path)


def remove_unused_upload(root, uploads, metadata):
    """Remove only the app-owned upload copy if no other class still uses it."""
    upload_id=metadata.get('upload_id')
    if not isinstance(upload_id,str) or not re.fullmatch('[0-9a-f]{32}',upload_id):return
    for candidate in Path(root).glob('*/session.json'):
        try:
            other=json.loads(candidate.read_text())
        except (OSError,ValueError):
            # Unknown references: retain the copy rather than guess.
            return
        if other.get('upload_id')==upload_id and other.get('recording_retained',True):return
    target=Path(uploads)/upload_id
    if not target.is_symlink() and target.is_file():target.unlink()
