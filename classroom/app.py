from contextlib import asynccontextmanager
import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import re
import secrets
import subprocess
import time
import uuid
from fastapi import FastAPI,HTTPException,Request,UploadFile,WebSocket,WebSocketDisconnect
from fastapi.responses import FileResponse,JSONResponse
from fastapi.staticfiles import StaticFiles
from .paths import WORK,OUTPUTS,SESSIONS,STATIC
from .runtime import Runtime
from .desktop import overview,FOLDERS
from .session import ClassSession
from .store import SessionStore,export_session
from .history import session_path,audio_files,ensure_ended,reveal_recording,remove_class,update_retention,remove_unused_upload

runtime=Runtime()
active=None
session_lock=asyncio.Lock()
TOKEN=secrets.token_urlsafe(32)
ALLOWED_ORIGINS={'http://127.0.0.1:8767','http://localhost:8767'}

@asynccontextmanager
async def lifespan(app):
    OUTPUTS.mkdir(exist_ok=True);SESSIONS.mkdir(exist_ok=True)
    (WORK/'uploads').mkdir(parents=True,exist_ok=True)
    yield
    if active and active.state in ('loading','running','draining'):await active.stop()
    await runtime.close()

app=FastAPI(title='本地课堂翻译',lifespan=lifespan,docs_url=None,redoc_url=None)

@app.middleware('http')
async def local_only(request,call_next):
    host=request.headers.get('host','').split(':')[0]
    if host not in ('127.0.0.1','localhost'):return JSONResponse({'detail':'仅限本机访问'},status_code=403)
    if request.method not in ('GET','HEAD'):
        origin=request.headers.get('origin')
        if origin and origin not in ALLOWED_ORIGINS:return JSONResponse({'detail':'来源不允许'},status_code=403)
        if request.headers.get('x-session-token')!=TOKEN:return JSONResponse({'detail':'请从本机页面操作'},status_code=403)
    return await call_next(request)

@app.get('/')
def index():return FileResponse(STATIC/'index.html')

@app.get('/api/state')
def state():
    return dict(token=TOKEN,session=active.status() if active else None,
                segments=list(active.segments.values())[-500:] if active else [],
                ready=(WORK/'deployment.json').exists())

@app.get('/api/system')
def system_overview():
    return overview(runtime)

@app.post('/api/folders/{kind}')
async def open_folder(kind:str):
    path=FOLDERS.get(kind)
    if path is None or not path.is_dir():raise HTTPException(404,'目录不存在')
    try:await asyncio.to_thread(subprocess.run,['/usr/bin/open',str(path)],check=True,timeout=5)
    except Exception:raise HTTPException(500,'无法打开文件夹')
    return dict(ok=True)

@app.post('/api/uploads')
async def upload(file:UploadFile):
    name=uuid.uuid4().hex
    p=WORK/'uploads'/name
    count=0
    with p.open('wb') as f:
        while data:=await file.read(1024*1024):
            count+=len(data)
            if count>1024**3:
                f.close();p.unlink(missing_ok=True);raise HTTPException(413,'单个录音最多 1 GB')
            f.write(data)
    if not count:p.unlink(missing_ok=True);raise HTTPException(400,'录音文件为空')
    return dict(id=name,name=file.filename,bytes=count)

@app.post('/api/sessions')
async def start(request:Request):
    global active
    body=await request.json()
    source=body.get('source','microphone');model=body.get('model','small')
    if source not in ('microphone','system','file') or model not in ('turbo','small'):raise HTTPException(400,'音源或模型无效')
    title=str(body.get('title','课堂记录')).strip()[:100] or '课堂记录'
    upload_path=None
    if source=='file':
        file_id=str(body.get('upload_id',''))
        if not re.fullmatch('[0-9a-f]{32}',file_id):raise HTTPException(400,'请先选择录音文件')
        upload_path=WORK/'uploads'/file_id
        if not upload_path.exists():raise HTTPException(400,'录音文件不存在')
    async with session_lock:
        task=getattr(active,'orchestrator',None) if active else None
        if active and (active.state in ('loading','running','draining') or (task is not None and not task.done())):raise HTTPException(409,'请先结束当前会话并等待保存完成')
        active=ClassSession(runtime,source,model,title,upload_path,realtime=bool(body.get('realtime',True)))
        active.orchestrator=asyncio.create_task(active.run())
    return dict(id=active.id)

@app.post('/api/stop')
async def stop():
    if active:await active.stop()
    return dict(ok=True)

@app.post('/api/retry/{segment_id}')
async def retry(segment_id:str):
    if not active or active.state not in ('running','draining') or active.queue.closed:raise HTTPException(409,'会话已结束，可导入录音重新处理')
    active.retry(segment_id);return dict(ok=True)

@app.get('/api/sessions')
def sessions():
    items=[]
    for p in sorted(SESSIONS.glob('*/session.json'),reverse=True):
        try:
            directory=session_path(SESSIONS,p.parent.name)
            item=json.loads(p.read_text())
            item['has_recording']=bool(audio_files(directory))
            task=getattr(active,'orchestrator',None) if active and active.id==item['id'] else None
            item['busy']=bool(active and active.id==item['id'] and (active.state in ('loading','running','draining') or (task is not None and not task.done())))
            items.append(item)
        except (OSError,ValueError,KeyError):pass
    return items[:100]

def ended_class(session_id):
    try:
        path=session_path(SESSIONS,session_id)
        ensure_ended(session_id,active)
        return path
    except FileNotFoundError as exc:
        raise HTTPException(404,str(exc))
    except ValueError as exc:
        raise HTTPException(409,str(exc))

@app.get('/api/sessions/{session_id}')
def read_class(session_id:str):
    path=ended_class(session_id)
    try:
        metadata=json.loads((path/'session.json').read_text())
        segments=SessionStore.load_segments(path)
    except (OSError,ValueError):raise HTTPException(404,'课堂记录无法读取')
    # Presentation-only normalization for interrupted legacy metadata; never rewrite it.
    status=dict(metadata,historical=True,has_recording=bool(audio_files(path)),
                final_count=sum(bool(x.get('final')) for x in segments))
    if status.get('state') in ('loading','running','draining'):status['state']='interrupted'
    return dict(session=status,segments=segments)

@app.post('/api/sessions/{session_id}/recording/reveal')
async def reveal(session_id:str):
    async with session_lock:
        path=ended_class(session_id)
        try:await asyncio.to_thread(reveal_recording,path)
        except FileNotFoundError as exc:raise HTTPException(404,str(exc))
        except Exception:raise HTTPException(500,'未能打开访达，请稍后重试')
    return dict(ok=True)

@app.post('/api/sessions/{session_id}/recording')
async def recording_choice(session_id:str,request:Request):
    body=await request.json()
    keep=body.get('keep')
    if type(keep) is not bool:raise HTTPException(400,'请选择是否保存录音')
    async with session_lock:
        path=ended_class(session_id)
        metadata=json.loads((path/'session.json').read_text())
        if keep and not audio_files(path):raise HTTPException(409,'录音已不存在，不能恢复保存')
        metadata=await asyncio.to_thread(update_retention,path,keep)
        if not keep:await asyncio.to_thread(remove_unused_upload,SESSIONS,WORK/'uploads',metadata)
        if active and active.id==session_id:
            active.store.metadata.update(metadata)
            active.event(active.status(),persist=False)
    return dict(ok=True,recording_decision=metadata['recording_decision'],recording_retained=keep)

@app.post('/api/sessions/{session_id}/delete')
async def delete_class(session_id:str,request:Request):
    global active
    body=await request.json()
    if body.get('confirm') is not True:raise HTTPException(400,'请确认删除整堂课及其字幕和录音')
    async with session_lock:
        path=ended_class(session_id)
        metadata=json.loads((path/'session.json').read_text())
        await asyncio.to_thread(remove_class,path)
        await asyncio.to_thread(remove_unused_upload,SESSIONS,WORK/'uploads',metadata)
        if active and active.id==session_id:active=None
    return dict(ok=True)

@app.get('/api/export/{session_id}/{kind}')
def export(session_id:str,kind:str):
    if not re.fullmatch(r'\d{8}-\d{6}-[0-9a-f]{8}',session_id):raise HTTPException(404)
    p=SESSIONS/session_id
    if not p.is_dir():raise HTTPException(404)
    names={'txt':'双语记录.txt','srt':'双语字幕.srt','events':'events.jsonl'}
    if kind not in names:raise HTTPException(404)
    target=export_session(p)[kind] if kind in ('txt','srt') else p/names[kind]
    return FileResponse(target,filename=target.name)

@app.websocket('/ws')
async def updates(ws:WebSocket):
    if ws.headers.get('origin') not in ALLOWED_ORIGINS or ws.query_params.get('token')!=TOKEN:
        await ws.close(code=1008);return
    await ws.accept()
    bound=None
    queue=asyncio.Queue(maxsize=200)
    try:
        while True:
            if active is not bound:
                if bound:bound.clients.discard(queue)
                bound=active
                while not queue.empty():queue.get_nowait()
                if bound:
                    bound.clients.add(queue)
                    await ws.send_json(dict(type='snapshot',status=bound.status(),segments=list(bound.segments.values())[-500:]))
                else:await ws.send_json(dict(type='snapshot',status=None,segments=[]))
            try:
                event=await asyncio.wait_for(queue.get(),1)
                if event.get('type')=='resync':
                    await ws.send_json(dict(type='snapshot',status=bound.status(),segments=list(bound.segments.values())[-500:]))
                    bound.clients.add(queue)
                else:await ws.send_json(event)
            except asyncio.TimeoutError:
                await ws.send_json(dict(type='ping'))
    except (WebSocketDisconnect,RuntimeError):pass
    finally:
        if bound:bound.clients.discard(queue)

app.mount('/static',StaticFiles(directory=STATIC),name='static')
