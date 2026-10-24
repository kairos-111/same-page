"""Launch the development app using the local checkout; no installer or model bundle."""
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]
BINARY=ROOT/'work/desktop-target/debug/tingke-desktop'
APP=ROOT/'work/desktop/听课开发版.app'

def main():
    if not BINARY.exists():
        raise SystemExit('桌面开发程序尚未编译，请先运行开发说明中的编译命令。')
    target=APP/'Contents/MacOS/tingke-desktop'
    # A development .app gives macOS a stable app identity; no distributable DMG.
    if not target.exists() or target.stat().st_mtime < BINARY.stat().st_mtime:
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(BINARY,target)
        shutil.copy2(ROOT/'desktop/Info.plist',APP/'Contents/Info.plist')
        subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(APP)],check=True)
    subprocess.run(['/usr/bin/open',str(APP)],check=True)
    print('已打开听课桌面开发版；不会自动开始录音。')

if __name__=='__main__':main()
