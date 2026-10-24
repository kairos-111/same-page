"""Read-only desktop overview; no inference, download or audio capture."""
import shutil
from .paths import ROOT,WORK,MODELS,SESSIONS,OUTPUTS

VERSION='0.6.0'

def overview(runtime):
    specs=[('small','Whisper Small','默认识别 · 英语',MODELS/'whisper-small','weights.*'),
           ('hy-mt','Hy-MT2 1.8B','本地中文翻译',MODELS/'hy-mt','*.gguf'),
           ('turbo','Whisper Turbo','可选识别 · 需要更多算力',MODELS/'whisper-turbo','weights.*')]
    models=[]
    for key,name,description,directory,pattern in specs:
        available=any(p.is_file() and p.stat().st_size>0 for p in directory.glob(pattern))
        size=sum(p.stat().st_size for p in directory.rglob('*') if p.is_file()) if directory.exists() else 0
        models.append(dict(id=key,name=name,description=description,available=available,bytes=size))
    count=sum(1 for _ in SESSIONS.glob('*/session.json'))
    return dict(version=VERSION,models=models,course_count=count,free_bytes=shutil.disk_usage(ROOT).free,
                recognition_model=runtime.model,engine_loaded=runtime.asr is not None,
                local_only=True,development=True)

FOLDERS={'courses':SESSIONS,'models':MODELS,'logs':WORK,'terms':OUTPUTS}
