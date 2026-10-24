from pathlib import Path
import shutil
import subprocess

root = Path(__file__).resolve().parents[1]
app = root / 'outputs' / 'native' / 'ClassroomAudio.app'
binary = app / 'Contents' / 'MacOS' / 'ClassroomAudio'
binary.parent.mkdir(parents=True, exist_ok=True)
shutil.copy(root / 'native' / 'Info.plist', app / 'Contents' / 'Info.plist')
subprocess.run(['/usr/bin/xcrun','swiftc','-swift-version','5','-O',str(root/'native'/'AudioCapture.swift'),'-o',str(binary)],check=True)
subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(app)],check=True)
subprocess.run([str(binary),'--check'],check=True)
