"""Read-only desktop overview; no inference, download or audio capture."""
import shutil
from .paths import ROOT,WORK,MODELS,SESSIONS,OUTPUTS,DATA_ROOT

VERSION='0.10.2'

def overview(runtime):
    specs=[('small','Whisper Small','默认识别 · 多语言',MODELS/'whisper-small','weights.*'),
           ('turbo','Whisper Turbo','可选识别 · 需要更多算力',MODELS/'whisper-turbo','weights.*'),
           ('hy-mt','Hy-MT2 1.8B','本地中文翻译',MODELS/'hy-mt','*.gguf')]
    models=[]
    for key,name,description,directory,pattern in specs:
        available=any(p.is_file() and p.stat().st_size>0 for p in directory.glob(pattern))
        size=sum(p.stat().st_size for p in directory.rglob('*') if p.is_file()) if directory.exists() else 0
        loaded=(getattr(runtime,'llama',None) is not None and runtime.llama.returncode is None) if key=='hy-mt' else (key in getattr(runtime,'asr_models',{}))
        models.append(dict(id=key,name=name,description=description,available=available,bytes=size,loaded=loaded))
    count=sum(1 for _ in SESSIONS.glob('*/session.json'))
    return dict(version=VERSION,models=models,course_count=count,free_bytes=shutil.disk_usage(DATA_ROOT).free,
                recognition_model=runtime.model,engine_loaded=bool(getattr(runtime,'asr_models',{})),
                model_operation=getattr(runtime,'model_operation',None),model_error=getattr(runtime,'model_error',''),
                local_only=True,development=DATA_ROOT==ROOT)

FOLDERS={'courses':SESSIONS,'models':MODELS,'logs':WORK,'terms':OUTPUTS}
