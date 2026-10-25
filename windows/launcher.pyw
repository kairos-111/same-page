"""Same Page for Windows: start the local engine, show the app window, clean up on close.

Run with the bundled pythonw.exe (no console). Layout under the install folder:
    launcher.pyw
    engine/                    classroom/, scripts/, work/runtime/{python,llama-*}, work/downloads/models/
Data lives in %LOCALAPPDATA%\\Same Page (records, logs, settings), separate from the program.

Lifecycle
- Every process the engine starts (llama-server, audio capture) is put in a Windows job
  object that is killed when this launcher exits, so nothing is left running.
- The page is shown in an Edge (or Chrome) app window with its own profile. When that
  window closes, an active recording is stopped and saved first, then models are released.
"""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

APP = 'Same Page'
PORT = 8877
LLAMA_PORT = 8878
HERE = Path(__file__).resolve().parent
ENGINE = HERE / 'engine'
PYTHON = ENGINE / 'work' / 'runtime' / 'python' / 'python.exe'
DATA = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / APP
LOGS = DATA / 'work'
BASE = f'http://127.0.0.1:{PORT}'
VERSION = json.loads((ENGINE / 'bundle.json').read_text(encoding='utf-8'))['version']

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
user32 = ctypes.WinDLL('user32', use_last_error=True)


def log(message):
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        with open(LOGS / 'launcher.log', 'a', encoding='utf-8') as f:
            f.write(time.strftime('%Y-%m-%d %H:%M:%S ') + message + '\n')
    except OSError:
        pass


def message_box(text, error=True):
    flags = 0x10 if error else 0x40  # MB_ICONERROR / MB_ICONINFORMATION
    user32.MessageBoxW(None, text, APP, flags | 0x10000)  # MB_SETFOREGROUND


# ---- engine HTTP -------------------------------------------------------------

def get(path, timeout=3):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.load(r)


def post(path, token, body=None, timeout=10):
    req = urllib.request.Request(BASE + path, data=json.dumps(body or {}).encode(), method='POST',
                                 headers={'content-type': 'application/json', 'x-session-token': token})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def engine_state():
    try:
        state = get('/api/state')
    except Exception:
        return None
    return state if 'token' in state and 'segments' in state else None


def port_in_use(port):
    import socket
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(('127.0.0.1', port)) == 0


# ---- job object: children die with the launcher -------------------------------

class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD),
                ('SchedulingClass', wintypes.DWORD)]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in ('ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                                               'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [('BasicLimitInformation', JOBOBJECT_BASIC_LIMIT_INFORMATION), ('IoInfo', IO_COUNTERS),
                ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]


def kill_on_close_job():
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = kernel32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info))
    return job if ok else None


def assign(job, process):
    if not job:
        return False
    PROCESS_ALL_ACCESS = 0x1F0FFF
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, process.pid)
    if not handle:
        return False
    try:
        return bool(kernel32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(handle)))
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(handle))


# ---- single instance ---------------------------------------------------------

def first_instance():
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel32.CreateMutexW(None, False, 'Local\\SamePageLauncher')
    first = ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS
    return handle, first


# ---- splash ----------------------------------------------------------------

class Splash:
    """The startup window runs as its own pythonw process (Tk needs its main thread)."""

    def __init__(self):
        self.process = None
        script = HERE / 'splash.pyw'
        pythonw = PYTHON.with_name('pythonw.exe')
        try:
            if script.is_file() and pythonw.is_file():
                self.process = subprocess.Popen([str(pythonw), '-I', '-B', str(script)])
        except OSError as exc:
            log(f'splash unavailable: {exc!r}')

    def close(self):
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
            except OSError:
                pass
        self.process = None


# ---- engine process ----------------------------------------------------------

def start_engine(job):
    LOGS.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME')}
    env.update(TINGKE_DATA_ROOT=str(DATA), TINGKE_API_PORT=str(PORT), TINGKE_LLAMA_PORT=str(LLAMA_PORT),
               PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1')
    out = open(LOGS / 'desktop-engine.log', 'a', encoding='utf-8')
    out.write(f'\n--- {time.strftime("%Y-%m-%d %H:%M:%S")} Same Page {VERSION}\n')
    out.flush()
    process = subprocess.Popen([str(PYTHON), '-I', '-B', '-u', str(ENGINE / 'scripts' / 'serve.py')],
                               cwd=str(DATA), env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    if not assign(job, process):
        log('engine could not be added to the job object; it is stopped explicitly on exit')
    return process


def wait_for_engine(process, limit=180):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        state = engine_state()
        if state:
            return state
        if process.poll() is not None:
            return None
        time.sleep(0.3)
    return None


# ---- app window --------------------------------------------------------------

def browser():
    candidates = []
    for base in (os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles'), os.environ.get('LOCALAPPDATA')):
        if base:
            candidates += [Path(base) / 'Microsoft' / 'Edge' / 'Application' / 'msedge.exe',
                           Path(base) / 'Google' / 'Chrome' / 'Application' / 'chrome.exe']
    try:
        import winreg
        for name in ('msedge.exe', 'chrome.exe'):
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths' + '\\' + name) as key:
                        candidates.insert(0, Path(winreg.QueryValue(key, None)))
                except OSError:
                    pass
    except ImportError:
        pass
    return next((p for p in candidates if p.is_file()), None)


def open_window():
    url = f'{BASE}/?v={VERSION}&shell=windows'
    test_command = os.environ.get('SAMEPAGE_BROWSER_CMD')  # smoke tests only: a fake window
    exe = browser()
    if exe is None and not test_command:
        log('no Edge/Chrome found; opening the default browser')
        try:
            os.startfile(url)
        except OSError as exc:
            log(f'could not open a browser: {exc!r}')
            message_box('没有找到 Microsoft Edge 或 Chrome。请用浏览器打开：\n' + url, error=False)
        return None
    profile = DATA / 'window'
    head = json.loads(test_command) if test_command else [str(exe)]
    args = head + [f'--app={url}', f'--user-data-dir={profile}', '--no-first-run', '--no-default-browser-check',
            '--disable-features=msEdgeStartupBoost,Translate,msUndersideButton', '--window-size=1200,820',
            '--disable-background-mode', '--disable-sync', '--lang=zh-CN']
    log(f'opening window with {head[0]}')
    return subprocess.Popen(args)


# ---- shutdown ----------------------------------------------------------------

def save_and_release():
    """Stop an active recording (saving it), then release models. Best effort."""
    state = engine_state()
    if not state:
        return
    token = state['token']
    session = state.get('session') or {}
    if session.get('state') in ('loading', 'running', 'draining'):
        log('window closed during a translation; stopping and saving it')
        try:
            post('/api/stop', token, timeout=600)
        except Exception as exc:
            log(f'stop failed: {exc!r}')
    try:
        post('/api/models/all/stop', token, {'recognition': 'small'}, timeout=60)
    except Exception as exc:
        log(f'model stop failed: {exc!r}')


def watch(window, engine):
    """Return when the app window is gone (or the engine died)."""
    opened = time.monotonic()
    if window is not None:
        while window.poll() is None:
            if engine.poll() is not None:
                return 'engine-exited'
            time.sleep(0.5)
        if time.monotonic() - opened > 5:
            return 'window-closed'
        log('browser handed the window to another process; following page heartbeats instead')
    # No owned window process: the page pings every 20 s while open. Minimised windows
    # are throttled, so allow a generous gap and never end an active translation.
    while engine.poll() is None:
        time.sleep(5)
        try:
            shell = get('/api/shell')
        except Exception:
            continue
        age = shell.get('ping_age')
        if age is not None and age > 180 and not shell.get('busy'):
            return 'heartbeat-lost'
    return 'engine-exited'


def main():
    mutex, first = first_instance()
    if not first:
        # Already running: just bring up another window on the same engine.
        if engine_state():
            open_window()
        return 0
    if not PYTHON.is_file():
        message_box('安装不完整：找不到内置运行环境。请重新安装 Same Page。')
        return 1
    splash = Splash()
    job = kill_on_close_job()
    engine = None
    state = engine_state()
    if state and state.get('version') != VERSION:
        splash.close()
        message_box(f'端口 {PORT} 上仍在运行旧版本的 Same Page 引擎（{state.get("version")}）。\n'
                    '请在任务管理器中结束残留的 python 进程后重新打开。')
        return 1
    if state is None:
        if port_in_use(PORT):
            splash.close()
            message_box(f'本地端口 {PORT} 已被其他程序占用，Same Page 无法启动。\n请关闭占用该端口的程序后重试。')
            return 1
        engine = start_engine(job)
        state = wait_for_engine(engine)
        if state is None:
            splash.close()
            message_box('本地引擎启动失败。\n日志位置：' + str(LOGS / 'desktop-engine.log'))
            if engine.poll() is None:
                engine.kill()
            return 1
    window = open_window()
    # The splash closes itself shortly after the engine answers; do not cut it off.
    if engine is None:
        # Another launcher owns the engine; this one only opened a window.
        return 0
    reason = watch(window, engine)
    log(f'closing: {reason}')
    if reason != 'engine-exited':
        save_and_release()
    if engine.poll() is None:
        engine.terminate()
        try:
            engine.wait(10)
        except subprocess.TimeoutExpired:
            engine.kill()
    if mutex:
        kernel32.CloseHandle(wintypes.HANDLE(mutex))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:  # last resort: never fail silently
        log(f'launcher crashed: {exc!r}')
        message_box('Same Page 启动时出错：' + repr(exc) + '\n日志：' + str(LOGS / 'launcher.log'))
        sys.exit(1)
