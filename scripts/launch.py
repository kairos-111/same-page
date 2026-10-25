"""Start a detached local service, or reuse it; opening another page does not record audio."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
URL = 'http://127.0.0.1:8767/'

def running():
    try:
        with urllib.request.urlopen(URL+'api/state', timeout=2) as response:
            data = json.load(response)
        return isinstance(data, dict) and 'ready' in data and 'segments' in data and 'token' in data
    except (OSError, ValueError, urllib.error.URLError):
        return False

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if not running():
        with socket.socket() as probe:
            probe.settimeout(1)
            if probe.connect_ex(('127.0.0.1',8767)) == 0:
                raise SystemExit('8767 端口被其他程序占用，未启动重复服务。')
        python = ROOT/'.venv/bin/python'
        if not python.exists():
            raise SystemExit('独立 Python 环境不存在，请先恢复项目运行环境。')
        log_path = ROOT/'work/app.log'
        log_path.parent.mkdir(parents=True,exist_ok=True)
        with log_path.open('ab', buffering=0) as log:
            process = subprocess.Popen([str(python),'-u',str(ROOT/'scripts/serve.py')],
                cwd=str(ROOT),stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                start_new_session=True,close_fds=True)
        for _ in range(60):
            if running():
                break
            if process.poll() is not None:
                raise SystemExit('服务启动失败，诊断日志位于 work/app.log。')
            time.sleep(.5)
        else:
            raise SystemExit('服务启动等待超时，请稍后打开页面；诊断日志位于 work/app.log。')
    print('Same Page 已就绪：'+URL)
    print('可关闭启动窗口，服务会继续运行。打开页面不会自动开始录音。')
    if not args.no_browser:
        webbrowser.open(URL,new=2)

if __name__ == '__main__':
    main()
