"""Short stop/export regression against the running local service."""
import json
from pathlib import Path
import time
import requests

ROOT=Path(__file__).resolve().parents[1]
base='http://127.0.0.1:8767'
for _ in range(20):
    try:initial=requests.get(base+'/api/state',timeout=2).json();break
    except requests.RequestException:time.sleep(.5)
headers={'x-session-token':initial['token']}
if initial.get('session',{} ) and initial['session']['state'] in ('loading','running','draining'):
    raise SystemExit('当前会话正在使用，取消自动测试以免打断。')
with (ROOT/'work/technical-sample.aiff').open('rb') as file:
    r=requests.post(base+'/api/uploads',headers=headers,files={'file':('technical.aiff',file)},timeout=10)
    r.raise_for_status();fid=r.json()['id']
r=requests.post(base+'/api/sessions',headers=headers,json={'source':'file','title':'停止与保存验收短测','upload_id':fid},timeout=10)
r.raise_for_status();sid=r.json()['id'];print('START',sid,flush=True)
for _ in range(120):
    state=requests.get(base+'/api/state',timeout=5).json()['session']
    if state['state']=='error':raise RuntimeError(state['error'])
    if state['state']=='running' and state['audio_seconds']>=5:break
    time.sleep(.5)
else:raise RuntimeError('加载或采集超时')
started=time.monotonic()
r=requests.post(base+'/api/stop',headers=headers,timeout=30);r.raise_for_status()
elapsed=time.monotonic()-started
state=requests.get(base+'/api/state',timeout=5).json()['session']
assert state['state']=='completed' and not state['error'],state
directory=ROOT/'outputs/sessions'/sid
metadata=json.loads((directory/'session.json').read_text())
assert metadata['state']=='completed' and metadata.get('finished_at'),metadata
assert list(directory.glob('audio_*.wav')),'WAV export missing'
for kind in ('txt','srt'):
    r=requests.get(base+'/api/export/'+sid+'/'+kind,timeout=5);r.raise_for_status();assert r.content
assert requests.post(base+'/api/sessions',json={'source':'microphone'},timeout=5).status_code==403
result={'passed':True,'session_id':sid,'stop_seconds':elapsed,'state':state,'wav_exported':True,'metadata_finalized':True}
(ROOT/'work/stop-export-test.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print('PASS stop/export',round(elapsed,3),'seconds',flush=True)
