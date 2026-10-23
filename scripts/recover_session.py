"""Rebuild exports and WAV files after interruption, without running any AI model."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from classroom.store import AudioJournal,export_session
from classroom.paths import SESSIONS

if len(sys.argv)!=2:
    raise SystemExit('用法：python scripts/recover_session.py 会话目录名')
path=(SESSIONS/sys.argv[1]).resolve()
if path.parent!=SESSIONS.resolve() or not path.is_dir():raise SystemExit('会话不存在')
export_session(path)
audio=AudioJournal(path);audio.close();audio.export_wav()
print('已恢复：',path)
