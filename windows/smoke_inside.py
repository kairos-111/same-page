"""Runs inside Wine with the staged Windows Python: imports, engine, file import end to end."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

APP = Path(r'Z:\app')
ENGINE = APP / 'engine'
PY = ENGINE / 'work' / 'runtime' / 'python' / 'python.exe'
sample = Path(sys.argv[1])
t = time.time()
import numpy, onnxruntime, ctranslate2, faster_whisper, av, fastapi, uvicorn, httpx, psutil, opencc, soundcard  # noqa
print('imports ok', round(time.time() - t, 1), 's')
sys.path.insert(0, str(ENGINE))
from classroom.system import WINDOWS, ASR_BACKEND
print('platform', WINDOWS, ASR_BACKEND, 'torch' in sys.modules)

data = Path(tempfile.mkdtemp(prefix='samepage-'))
env = dict(os.environ, TINGKE_DATA_ROOT=str(data), TINGKE_API_PORT='8877', TINGKE_LLAMA_PORT='8878',
           PYTHONUTF8='1', PYTHONNOUSERSITE='1')
log = open(data / 'engine.log', 'w', encoding='utf-8')
engine = subprocess.Popen([str(PY), '-I', '-B', '-u', str(ENGINE / 'scripts' / 'serve.py')], cwd=str(data), env=env,
                          stdout=log, stderr=log)
B = 'http://127.0.0.1:8877'


def get(p, timeout=30):
    return json.load(urllib.request.urlopen(B + p, timeout=timeout))


for _ in range(240):
    try:
        state = get('/api/state', 3)
        break
    except Exception:
        time.sleep(1)
        if engine.poll() is not None:
            print('ENGINE EXITED');print((data / 'engine.log').read_text(encoding='utf-8')[-3000:]);sys.exit(1)
print('engine up', round(time.time() - t, 1), 's', 'ready', state['ready'], 'version', state['version'])
token = state['token']


def post(p, body=None, raw=None, ctype='application/json', timeout=900):
    req = urllib.request.Request(B + p, data=raw if raw is not None else json.dumps(body or {}).encode(), method='POST',
                                 headers={'x-session-token': token, 'content-type': ctype})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


system = get('/api/system')
print('models', [(m['id'], m['available']) for m in system['models']], system['platform'])
t = time.time()
try:
    started = post('/api/models/all/start', {'recognition': 'small'})
    print('models start', [(m['id'], m['loaded']) for m in started['models']], round(time.time() - t, 1), 's')
except Exception as exc:
    print('MODEL START FAILED', exc, getattr(exc, 'read', lambda: b'')().decode('utf-8', 'replace')[:500])
llama_log = data / 'work' / 'llama-server.log'
if llama_log.exists():
    text = llama_log.read_text(encoding='utf-8', errors='replace')
    print('llama servers tried:', [l.strip() for l in text.splitlines() if l.startswith('--- starting')])
b = uuid.uuid4().hex
raw = (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="t.wav"\r\nContent-Type: audio/wav\r\n\r\n').encode() \
    + sample.read_bytes() + f'\r\n--{b}--\r\n'.encode()
up = post('/api/uploads', raw=raw, ctype='multipart/form-data; boundary=' + b)
post('/api/sessions', {'source': 'file', 'upload_id': up['id'], 'model': 'small', 'language_mode': 'en', 'title': 'wine'})
t = time.time()
for _ in range(1200):
    st = get('/api/state')
    s = st['session']
    if s and s['state'] in ('completed', 'error', 'stopped') and s.get('saved'):
        break
    time.sleep(1)
print('session', s['state'], repr(s.get('error')), 'took', round(time.time() - t, 1), 's')
for seg in st['segments']:
    print(' ', seg.get('final'), '|', seg.get('source'), '=>', seg.get('translation'))
sessions = list((data / 'outputs' / 'sessions').glob('*/session.json'))
print('saved records', len(sessions), [p.name for p in sessions[0].parent.iterdir()] if sessions else [])
# capture helper starts and reports a clear error/ready (no audio devices in a container)
r = subprocess.run([str(PY), '-I', '-B', str(ENGINE / 'classroom' / 'capture_windows.py'), 'microphone'],
                   capture_output=True, timeout=60)
print('capture helper:', r.returncode, r.stderr.decode('utf-8', 'replace').strip()[:300])
engine.terminate()
