"""Assemble this Mac's self-contained app. Never start it or invoke a model.

Uses installed local artifacts, not the development venv interpreter/symlinks.
Existing source material and existing output bundles are never overwritten.
"""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
VERSION='0.10.2'

def copy_tree(source,dest,extra=()):
    shutil.copytree(source,dest,symlinks=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc','.DS_Store','*.ranges',*extra))

def idle_development():
    try:
        with urllib.request.urlopen('http://127.0.0.1:8767/api/state',timeout=2) as response:state=json.load(response)
    except OSError:return
    if state.get('session') and state['session']['state'] in ('loading','running','draining'):
        raise SystemExit('正在翻译或保存，暂不复制历史资料，请结束后再构建。')

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(8*1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def migrate(data):
    idle_development()
    sessions=data/'outputs/sessions';sessions.mkdir(parents=True,exist_ok=True)
    uploads=data/'work/uploads';uploads.mkdir(parents=True,exist_ok=True)
    count=0
    for meta in sorted((ROOT/'outputs/sessions').glob('*/session.json')):
        info=json.loads(meta.read_text())
        dest=sessions/meta.parent.name
        if dest.exists():continue
        temporary=sessions/('.copy-'+meta.parent.name)
        if temporary.exists():raise RuntimeError('历史复制暂存目录已存在，请人工核对：'+str(temporary))
        copy_tree(meta.parent,temporary)
        temporary.rename(dest);count+=1
        upload_id=info.get('upload_id')
        if isinstance(upload_id,str) and len(upload_id)==32 and all(c in '0123456789abcdef' for c in upload_id) and info.get('recording_retained',True):
            source=ROOT/'work/uploads'/upload_id
            if source.is_file() and not source.is_symlink() and not (uploads/upload_id).exists():shutil.copy2(source,uploads/upload_id)
    terms=data/'outputs/课程术语.csv'
    if not terms.exists():shutil.copy2(ROOT/'outputs/课程术语.csv',terms)
    return count

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--migrate-history',action='store_true')
    parser.add_argument('--dmg',action='store_true')
    args=parser.parse_args()
    dest=args.output.expanduser().resolve()
    if dest.exists():raise SystemExit('输出已存在，未覆盖：'+str(dest))
    dest.parent.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(dest.parent).free<12*1024**3:raise SystemExit('请至少预留12GB打包空间')
    idle_development()
    stamp=datetime.now().strftime('%Y%m%d-%H%M%S')
    stage=ROOT/'work/local-bundles'/stamp
    stage.mkdir(parents=True)
    app=stage/'Same Page.app';contents=app/'Contents';macos=contents/'MacOS';macos.mkdir(parents=True)
    engine=contents/'Resources/engine';engine.mkdir(parents=True)
    print('复制独立运行时与依赖…',flush=True)
    python=engine/'work/runtime/python'
    copy_tree(ROOT/'work/runtime/python',python)
    installed=ROOT/'.venv/lib/python3.12/site-packages'
    site=python/'lib/python3.12/site-packages'
    for source in installed.iterdir():
        if source.name.startswith('_virtualenv') or source.name=='__pycache__':continue
        target=site/source.name
        if target.exists():raise RuntimeError('运行时包重名，未覆盖：'+source.name)
        if source.is_dir():copy_tree(source,target)
        elif source.is_symlink():target.symlink_to(os.readlink(source))
        else:shutil.copy2(source,target)
    print('复制三个模型、推理引擎和音频组件…',flush=True)
    copy_tree(ROOT/'work/downloads/models',engine/'work/downloads/models',('.cache',))
    copy_tree(ROOT/'work/runtime/llama-b11669',engine/'work/runtime/llama-b11669')
    copy_tree(ROOT/'classroom',engine/'classroom')
    (engine/'scripts').mkdir();shutil.copy2(ROOT/'scripts/serve.py',engine/'scripts/serve.py')
    copy_tree(ROOT/'outputs/native',engine/'outputs/native')
    shutil.copy2(ROOT/'native/Info.plist',engine/'outputs/native/ClassroomAudio.app/Contents/Info.plist')
    shutil.copy2(ROOT/'outputs/课程术语.csv',engine/'outputs/课程术语.csv')
    shutil.copy2(ROOT/'requirements.lock',engine/'requirements.lock')
    (engine/'work/deployment.json').write_text(json.dumps(dict(local_bundle=True,models_verified=False,version=VERSION)))
    (engine/'bundle.json').write_text(json.dumps(dict(version=VERSION,local_only=True,api_port=8877,llama_port=8878)))
    # No outward links to the original checkout, venv, weights or user data.
    for path in app.rglob('*'):
        if path.is_symlink() and not path.resolve().is_relative_to(app.resolve()):
            raise RuntimeError('发现包外符号链接：'+str(path))
    print('编译独立桌面程序（不启动应用）…',flush=True)
    env=dict(os.environ,CARGO_HOME=str(ROOT/'work/devtools/cargo'),RUSTUP_HOME=str(ROOT/'work/devtools/rustup'),CARGO_TARGET_DIR=str(ROOT/'work/desktop-target'),TAURI_CONFIG=(ROOT/'desktop/tauri.local.conf.json').read_text())
    env['PATH']=str(ROOT/'work/devtools/cargo/bin')+os.pathsep+env.get('PATH','')
    with (stage/'compile.log').open('wb') as log:
        subprocess.run(['cargo','build','--offline','--release','--manifest-path',str(ROOT/'desktop/Cargo.toml')],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    shutil.copy2(ROOT/'work/desktop-target/release/tingke-desktop',macos/'tingke-desktop')
    info=plistlib.loads((ROOT/'desktop/Info.plist').read_bytes())
    info.update(CFBundleName='Same Page',CFBundleDisplayName='Same Page',CFBundleIdentifier='app.tingke.local',CFBundleShortVersionString=VERSION,CFBundleVersion='12',NSMicrophoneUsageDescription='获取所选音源，在本机生成原文字幕与译文。',NSScreenCaptureUsageDescription='获取电脑播放的声音，在本机生成字幕；不保存屏幕画面。')
    # Generate a native icon from the existing project artwork.
    iconset=stage/'AppIcon.iconset';iconset.mkdir()
    for size in (16,32,128,256,512):
        for scale in (1,2):
            pixels=size*scale;name=f'icon_{size}x{size}'+('@2x' if scale==2 else '')+'.png'
            subprocess.run(['/usr/bin/sips','-z',str(pixels),str(pixels),str(ROOT/'desktop/icons/icon.png'),'--out',str(iconset/name)],check=True,stdout=subprocess.DEVNULL)
    subprocess.run(['/usr/bin/iconutil','-c','icns',str(iconset),'-o',str(contents/'Resources/AppIcon.icns')],check=True)
    info['CFBundleIconFile']='AppIcon.icns'
    (contents/'Info.plist').write_bytes(plistlib.dumps(info))
    print('核对资源校验值并作本机临时签名…',flush=True)
    models=[]
    for path in sorted((engine/'work/downloads/models').rglob('*')):
        if path.is_file():models.append(dict(path=str(path.relative_to(engine)),bytes=path.stat().st_size,sha256=sha(path)))
    expected={m['path']:m['sha256'].removeprefix('sha256:') for m in json.loads((ROOT/'work/downloads/verified-manifest.json').read_text()) if m.get('repo')}
    actual={m['path']:m['sha256'] for m in models}
    for path,digest in expected.items():
        if actual.get(path)!=digest:raise RuntimeError('模型校验不匹配：'+path)
    (engine/'model-manifest.json').write_text(json.dumps(models,ensure_ascii=False,indent=2))
    (engine/'work/deployment.json').write_text(json.dumps(dict(local_bundle=True,models_verified=True,version=VERSION)))
    subprocess.run(['/usr/bin/codesign','--force','--deep','--sign','-',str(app)],check=True)
    subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(app)],check=True)
    # Move across folders on the same APFS volume without duplicating the app.
    shutil.move(str(app),str(dest))
    report=dict(app=str(dest),version=VERSION,models_bytes=sum(x['bytes'] for x in models),app_bytes=sum(p.stat().st_size for p in dest.rglob('*') if p.is_file() and not p.is_symlink()),launched=False,model_tests=False,signature='local-ad-hoc')
    if args.migrate_history:
        data=Path.home()/'Library/Application Support/听课'
        report['copied_classes']=migrate(data);report['data_root']=str(data)
    if args.dmg:
        print('制作DMG外壳（不会启动或安装应用）…',flush=True)
        image_source=stage/'disk-image';image_source.mkdir()
        subprocess.run(['/bin/cp','-cR',str(dest),str(image_source/'Same Page.app')],check=True)
        (image_source/'Applications').symlink_to('/Applications')
        (image_source/'安装说明.txt').write_text('将“Same Page.app”拖入Applications，然后打开。\n这是本机整合版，未作公开分发签名公证。\n程序、Python、Small/Turbo/Hy-MT模型及音频组件已包含。\n记录保存在 ~/Library/Application Support/听课/outputs/sessions，不在App包内。\n不会自动录音。首次使用音源时可能需要授权。\n不要同时在开发版和独立版中启动模型；两者会争用算力。\n')
        dmg=dest.parent/'SamePage-本机版-0.10.2-arm64.dmg'
        if dmg.exists():raise RuntimeError('DMG已存在，未覆盖')
        subprocess.run(['/usr/bin/hdiutil','create','-volname','Same Page 本机版','-srcfolder',str(image_source),'-format','UDZO','-ov','-o',str(dmg)],check=True)
        report['dmg']=str(dmg);report['dmg_bytes']=dmg.stat().st_size;report['dmg_sha256']=sha(dmg)
    (dest.parent/'本机应用构建记录.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
