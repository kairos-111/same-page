"""Assemble the Windows x64 build of Same Page on this Mac, then make the installer.

Inputs (downloaded once into work/win/, see windows/README.md):
  work/win/dl/python.tar.gz                         python-build-standalone 3.12 x86_64-pc-windows-msvc
  work/win/dl/llama-<tag>-bin-win-{vulkan,cpu}-x64.zip  llama.cpp release
  work/win/models/whisper-small-ct2/                Systran/faster-whisper-small
  work/downloads/models/hy-mt/                      Hy-MT2 1.8B GGUF (same as macOS)
Output:
  work/win/stage/                                   installed layout
  work/win/out/SamePage-<version>-windows-x64-setup.exe

Never touches user data, the macOS app or the development environment.
Usage: work/runtime/python/bin/python3.12 windows/build_windows.py [--skip-installer]
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
WIN = ROOT / 'work' / 'win'
STAGE = WIN / 'stage'
OUT = WIN / 'out'
ENGINE = STAGE / 'engine'
PY = ENGINE / 'work' / 'runtime' / 'python'
SITE = PY / 'Lib' / 'site-packages'
LLAMA_TAG = 'b11669'
VERSION = '0.10.2'
EXPECTED = {
    WIN / 'models' / 'whisper-small-ct2' / 'model.bin': '3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671',
    ROOT / 'work' / 'downloads' / 'models' / 'hy-mt' / 'Hy-MT2-1.8B-Q4_K_M.gguf': 'dc5f44fcf1fa496ee7ad725982c0c8c553a4de00259b53af84c4b89fb0c06699',
}
MSVC_DLLS = ('msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll', 'msvcp140_atomic_wait.dll',
             'msvcp140_codecvt_ids.dll', 'concrt140.dll', 'vcomp140.dll', 'vcruntime140_threads.dll')
NSIS_LIMIT = 2 * 1024 ** 3


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def step(text):
    print('==>', text, flush=True)


def verify_inputs():
    step('verify inputs')
    for path, digest in EXPECTED.items():
        if not path.is_file():
            raise SystemExit(f'missing input: {path}')
        if sha(path) != digest:
            raise SystemExit(f'checksum mismatch: {path}')
    for name in ('python.tar.gz', f'llama-{LLAMA_TAG}-bin-win-vulkan-x64.zip', f'llama-{LLAMA_TAG}-bin-win-cpu-x64.zip'):
        if not (WIN / 'dl' / name).is_file():
            raise SystemExit(f'missing input: {WIN / "dl" / name}')


def python_runtime():
    step('Windows Python runtime')
    with tarfile.open(WIN / 'dl' / 'python.tar.gz') as tar:
        tar.extractall(ENGINE / 'work' / 'runtime', filter='data')
    for junk in list(PY.rglob('*.pdb')) + [PY / 'include', PY / 'libs', PY / 'Lib' / 'test', PY / 'Lib' / 'idlelib',
                                           PY / 'Lib' / 'ensurepip', PY / 'Lib' / 'lib2to3']:
        if junk.is_dir():
            shutil.rmtree(junk)
        elif junk.exists():
            junk.unlink()


def packages():
    step('Windows wheels (win_amd64, cp312)')
    pip = ROOT / 'work' / 'runtime' / 'python' / 'bin' / 'python3.12'
    subprocess.run([str(pip), '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', '--no-compile',
                    '--target', str(SITE), '--platform', 'win_amd64', '--python-version', '3.12',
                    '--implementation', 'cp', '--abi', 'cp312', '--only-binary=:all:',
                    '-r', str(ROOT / 'windows' / 'requirements.txt')], check=True)
    # msvc-runtime drops the Visual C++ DLLs into the target root/Scripts; they belong
    # next to python.exe (and llama-server.exe) where Windows looks for them.
    for name in MSVC_DLLS:
        source = SITE / name
        if source.is_file():
            shutil.copy2(source, PY / name)
    shutil.rmtree(SITE / 'Scripts', ignore_errors=True)
    shutil.rmtree(SITE / 'bin', ignore_errors=True)
    for name in MSVC_DLLS + ('vccorlib140.dll', 'vcamp140.dll'):
        (SITE / name).unlink(missing_ok=True)


def engine_code():
    step('engine code')
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store', 'startup-error.html')
    shutil.copytree(ROOT / 'classroom', ENGINE / 'classroom', ignore=ignore)
    (ENGINE / 'scripts').mkdir(parents=True)
    shutil.copy2(ROOT / 'scripts' / 'serve.py', ENGINE / 'scripts' / 'serve.py')
    (ENGINE / 'outputs').mkdir()
    shutil.copy2(ROOT / 'outputs' / '课程术语.csv', ENGINE / 'outputs' / '课程术语.csv')
    (ENGINE / 'bundle.json').write_text(json.dumps(dict(version=VERSION, local_only=True, api_port=8877,
                                                        llama_port=8878, platform='windows')), encoding='utf-8')
    (ENGINE / 'work' / 'deployment.json').write_text(json.dumps(dict(local_bundle=True, models_verified=True,
                                                                     version=VERSION, platform='windows')), encoding='utf-8')
    shutil.copy2(ROOT / 'windows' / 'launcher.pyw', STAGE / 'launcher.pyw')


def llama():
    step('llama.cpp (Vulkan + CPU)')
    for flavour in ('vulkan', 'cpu'):
        dest = ENGINE / 'work' / 'runtime' / f'llama-{flavour}'
        dest.mkdir(parents=True)
        with zipfile.ZipFile(WIN / 'dl' / f'llama-{LLAMA_TAG}-bin-win-{flavour}-x64.zip') as z:
            for info in z.infolist():
                name = Path(info.filename).name
                keep = name == 'llama-server.exe' or name.startswith('LICENSE') or (
                    name.endswith('.dll') and not name.endswith('-impl.dll')) or name == 'llama-server-impl.dll'
                if keep and not info.is_dir():
                    (dest / name).write_bytes(z.read(info))
        if not (dest / 'llama-server.exe').is_file():
            raise SystemExit('llama-server.exe missing from ' + flavour)
        for name in MSVC_DLLS:
            if (PY / name).is_file() and not (dest / name).exists():
                shutil.copy2(PY / name, dest / name)


def models():
    step('models')
    target = ENGINE / 'work' / 'downloads' / 'models'
    small = target / 'whisper-small-ct2'
    small.mkdir(parents=True)
    for name in ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt', 'README.md'):
        shutil.copy2(WIN / 'models' / 'whisper-small-ct2' / name, small / name)
    hy = target / 'hy-mt'
    hy.mkdir()
    for name in ('Hy-MT2-1.8B-Q4_K_M.gguf', 'LICENSE.txt', 'README.md'):
        shutil.copy2(ROOT / 'work' / 'downloads' / 'models' / 'hy-mt' / name, hy / name)


def icon():
    step('icon')
    source = ROOT / 'desktop' / 'icons' / 'icon.png'
    images = []
    tmp = WIN / 'icon-tmp'
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    for size in (16, 24, 32, 48, 64, 128, 256):
        png = tmp / f'{size}.png'
        subprocess.run(['/usr/bin/sips', '-z', str(size), str(size), str(source), '--out', str(png)],
                       check=True, capture_output=True)
        images.append((size, png.read_bytes()))
    header = struct.pack('<HHH', 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b'', b''
    for size, data in images:
        entries += struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(data), offset + len(blobs))
        blobs += data
    (STAGE / 'samepage.ico').write_bytes(header + entries + blobs)
    shutil.rmtree(tmp)


def licenses():
    step('third-party notices')
    text = """Same Page 第三方组件与许可

- Python 3.12 (python-build-standalone) — PSF License, engine/work/runtime/python/LICENSE.txt
- OpenAI Whisper small 权重（faster-whisper 转换版 Systran/faster-whisper-small）— MIT
- Hy-MT2-1.8B-GGUF（腾讯）— Apache License 2.0，engine/work/downloads/models/hy-mt/LICENSE.txt
- llama.cpp / ggml — MIT，engine/work/runtime/llama-*/LICENSE*
- CTranslate2、faster-whisper — MIT
- ONNX Runtime — MIT；Silero VAD — MIT
- WhisperLiveKit（LocalAgreement，engine/classroom/wlk）— Apache License 2.0
- PyAV — BSD；其内置 FFmpeg 为 LGPL 2.1 构建
- FastAPI、Starlette、Uvicorn、HTTPX、Pydantic、NumPy、psutil、soundcard、cffi — MIT/BSD 系许可
- opencc-python-reimplemented — Apache License 2.0
- Microsoft Visual C++ 运行库（msvc-runtime）— 按微软可再发行组件条款随附
各 Python 包的完整许可见 engine/work/runtime/python/Lib/site-packages/*.dist-info。
"""
    (STAGE / '第三方许可.txt').write_text(text, encoding='utf-8')
    shutil.copy2(ROOT / 'classroom' / 'wlk' / 'LICENSE-WhisperLiveKit', STAGE / 'LICENSE-WhisperLiveKit.txt')


def precompile():
    step('precompile bytecode')
    py = ROOT / 'work' / 'runtime' / 'python' / 'bin' / 'python3.12'
    # unchecked-hash: used as-is regardless of file times; the app runs Python with -B.
    subprocess.run([str(py), '-m', 'compileall', '-q', '-j', '0', '--invalidation-mode', 'unchecked-hash',
                    str(PY / 'Lib'), str(ENGINE / 'classroom'), str(ENGINE / 'scripts')],
                   check=False, capture_output=True)


def manifest():
    step('manifest')
    files = sorted(p for p in STAGE.rglob('*') if p.is_file())
    total = sum(p.stat().st_size for p in files)
    big = [dict(path=str(p.relative_to(STAGE)), bytes=p.stat().st_size, sha256=sha(p))
           for p in files if p.stat().st_size > 20 << 20]
    (WIN / 'stage-manifest.json').write_text(json.dumps(dict(version=VERSION, files=len(files), bytes=total, large=big),
                                                        ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'    {len(files)} files, {total / 1024 ** 3:.2f} GB', flush=True)
    return total


def installer():
    step('NSIS installer (docker)')
    OUT.mkdir(parents=True, exist_ok=True)
    exe = OUT / f'SamePage-{VERSION}-windows-x64-setup.exe'
    exe.unlink(missing_ok=True)
    shutil.copy2(ROOT / 'windows' / 'installer.nsi', WIN / 'installer.nsi')
    subprocess.run(['docker', 'run', '--rm', '-e', 'LANG=C.UTF-8', '-e', 'LC_ALL=C.UTF-8', '-v', f'{WIN}:/w', '-w', '/w', 'samepage-nsis',
                    'makensis', '-V2', f'-DVERSION={VERSION}', f'-DOUTFILE=out/{exe.name}', 'installer.nsi'], check=True)
    size = exe.stat().st_size
    if size >= NSIS_LIMIT:
        raise SystemExit('installer exceeds the 2 GB NSIS limit')
    digest = sha(exe)
    (OUT / (exe.name + '.sha256')).write_text(f'{digest}  {exe.name}\n')
    print(f'    {exe} {size / 1024 ** 3:.2f} GB sha256 {digest}', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-installer', action='store_true')
    args = parser.parse_args()
    verify_inputs()
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    python_runtime()
    packages()
    engine_code()
    llama()
    models()
    icon()
    licenses()
    precompile()
    manifest()
    if not args.skip_installer:
        installer()


if __name__ == '__main__':
    main()
