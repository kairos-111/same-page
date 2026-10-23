"""Install exclusively from verified local artifacts after download completion."""
from pathlib import Path
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tarfile

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'work';D=WORK/'downloads'
sys.path.insert(0,str(WORK))
from download_models_parallel import MODELS

def sha(path):
    digest=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(8*1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()

manifest=[]
for folder,repo,revision,name,size,digest in MODELS:
    p=D/'models'/folder/name
    if not p.exists() or p.stat().st_size!=size or sha(p)!=digest:
        raise SystemExit('模型缺失或校验失败：'+str(p))
    manifest.append(dict(path=str(p.relative_to(ROOT)),sha256=digest,repo=repo,revision=revision,bytes=size))
assets=json.loads((D/'runtime-assets.json').read_text())
for a in assets:
    p=D/'runtime'/a['filename']
    if not p.exists() or sha(p)!=a['sha256'].removeprefix('sha256:'):
        raise SystemExit('运行环境缺失或校验失败：'+str(p))
    manifest.append(dict(path=str(p.relative_to(ROOT)),sha256=a['sha256'],bytes=a['size']))
    with tarfile.open(p) as tar:tar.extractall(WORK/'runtime',filter='data')
for p in sorted((D/'wheels').glob('*.whl')):
    manifest.append(dict(path=str(p.relative_to(ROOT)),sha256=sha(p),bytes=p.stat().st_size))
(D/'verified-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
python=WORK/'runtime'/'python'/'bin'/'python3.12'
uv=shutil.which('uv')
if not uv:raise SystemExit('未找到 uv')
env=dict(os.environ,UV_CACHE_DIR=str(WORK/'uv-cache'),UV_PYTHON_DOWNLOADS='never')
subprocess.run([uv,'venv',str(ROOT/'.venv'),'--python',str(python)],check=True,env=env)
subprocess.run([uv,'pip','install','--python',str(ROOT/'.venv/bin/python'),'--offline','--no-index',
                '--find-links',str(D/'wheels'),'-r',str(D/'requirements.in')],check=True,env=env)
subprocess.run([uv,'pip','check','--python',str(ROOT/'.venv/bin/python')],check=True,env=env)
with (ROOT/'requirements.lock').open('w') as out:
    subprocess.run([uv,'pip','freeze','--python',str(ROOT/'.venv/bin/python')],stdout=out,check=True,env=env)
subprocess.run([str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/build_native.py')],check=True)
config={'python':str(python),'models_verified':True,'installed_offline':True,'manifest':'work/downloads/verified-manifest.json'}
(WORK/'deployment.json').write_text(json.dumps(config,indent=2))
print('准备完成：环境离线安装成功，模型文件已校验。')
