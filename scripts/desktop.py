"""Launch the development app using the local checkout; no installer or model bundle."""
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]
BINARY=ROOT/'work/desktop-target/debug/tingke-desktop'
# A .noindex folder keeps the dev build out of Spotlight, so only the installed app shows there.
APP=ROOT/'work/desktop.noindex/Same Page 开发版.app'


def prepare_app():
    if not BINARY.exists():
        raise SystemExit('桌面开发程序尚未编译，请先运行开发说明中的编译命令。')
    resources={BINARY:'MacOS/tingke-desktop',ROOT/'desktop/Info.plist':'Info.plist',ROOT/'desktop/icons/icon.icns':'Resources/AppIcon.icns'}
    changed=False
    for source,relative in resources.items():
        target=APP/'Contents'/relative
        if not target.exists() or target.stat().st_mtime_ns < source.stat().st_mtime_ns:
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,target);changed=True
    if changed:
        subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(APP)],check=True)
    return APP


def main():
    subprocess.run(['/usr/bin/open',str(prepare_app())],check=True)
    print('已打开 Same Page 桌面开发版；不会自动开始录音。')


if __name__=='__main__':main()
