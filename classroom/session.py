import asyncio
from collections import OrderedDict,deque
from datetime import datetime
import json
import math
from pathlib import Path
import time
import uuid
import psutil
from .paths import OUTPUTS,SESSIONS,LLAMA_URL
from .store import SessionStore
from .subtitles import SegmentComposer
from .translation import Glossary,LocalTranslator,RevisionQueue,apply_translation
from .process import terminate_and_drain

class ClassSession:
    def __init__(self,runtime,source,model,title,upload=None,realtime=True):
        self.runtime=runtime
        self.id=datetime.now().strftime('%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:8]
        self.source,self.model,self.title=source,model,title
        self.upload=upload
        self.realtime=realtime
        self.started=time.time()
        self.clock=time.monotonic()
        self.state='loading'
        self.error=''
        self.store=SessionStore(SESSIONS/self.id,dict(id=self.id,title=title,source=source,model=model,
            started_at=datetime.now().astimezone().isoformat(),state=self.state,language='en',target='zh'))
        self.queue=RevisionQueue()
        self.segments=OrderedDict()
        self.context=deque(maxlen=2)
        self.composer=SegmentComposer()
        self.clients=set()
        self.input_done=False
        self.processed=0
        self.level=0
        self.last_input=time.monotonic()
        self.preview_times={}
        self.first_latency=[]
        self.first_seen=set()
        self.final_latency=[]
        self.stats_history=deque(maxlen=5400)
        self.source_process=None
        self.tasks=[]
        self.orchestrator=None
        self.glossary=Glossary(OUTPUTS/'课程术语.csv')
        self.translator=LocalTranslator(LLAMA_URL,self.glossary)
        self.last_segment=None
        self.inflight=None

    def event(self,obj,persist=True):
        obj={**obj,'wall_time':time.time()}
        if persist:self.store.append(obj)
        for q in tuple(self.clients):
            if q.full():
                # Slow browsers receive a fresh snapshot on reconnect; inference must not wait on UI.
                self.clients.remove(q)
                while not q.empty():q.get_nowait()
                q.put_nowait({'type':'resync'})
            else:q.put_nowait(obj)

    def status(self):
        oldest=min((x.get('queued_at',time.monotonic()) for x in self.queue.pending.values()),default=time.monotonic())
        processes=[psutil.Process()]
        try:processes+=processes[0].children(recursive=True)
        except psutil.Error:pass
        rss=0
        for p in processes:
            try:rss+=p.memory_info().rss
            except psutil.Error:pass
        return dict(type='status',id=self.id,title=self.title,state=self.state,error=self.error,
            audio_seconds=self.store.audio.seconds,asr_lag=max(0,self.store.audio.seconds-self.processed),
            translation_queue=len(self.queue.pending),translation_wait=max(0,time.monotonic()-oldest),
            translation_busy=self.inflight is not None,
            level=round(self.level,4),input_idle=round(time.monotonic()-self.last_input,1),
            memory_bytes=rss,source=self.source,model=self.model,
            final_count=sum(s['final'] for s in self.segments.values()))

    async def run(self):
        try:
            await self.runtime.ensure_translation()
            self.asr=await self.runtime.load_asr(self.model,self.glossary.asr_hint())
            self.state='running'
            self.clock=time.monotonic()
            self.store.metadata['audio_started_at']=time.time()
            self.event(self.status())
            self.source_task=asyncio.create_task(self.capture())
            self.asr_task=asyncio.create_task(self.recognize())
            self.translation_task=asyncio.create_task(self.translate())
            self.monitor_task=asyncio.create_task(self.monitor())
            self.tasks=[self.source_task,self.asr_task,self.translation_task,self.monitor_task]
            await asyncio.gather(self.source_task,self.asr_task)
            self.queue.close()
            await self.translation_task
            self.state='completed' if not self.error else 'error'
        except asyncio.CancelledError:
            self.state='interrupted'
        except Exception as e:
            self.error=str(e);self.state='error'
            self.event(dict(type='error',message=self.error))
        finally:
            self.input_done=True
            for t in self.tasks:
                if not t.done():t.cancel()
            if self.tasks:await asyncio.gather(*self.tasks,return_exceptions=True)
            if self.source_process:
                # FFmpeg may have filled stdout while paced playback was cancelled.
                # Drain both pipes; wait() alone can hang even after the child exits.
                await terminate_and_drain(self.source_process)
            self.store.audio.close()
            await self.translator.close()
            self.event(self.status())
            self.store.metadata.update(state=self.state,error=self.error,audio_seconds=self.store.audio.seconds,
                processed_seconds=self.processed,first_translation_latency=self.first_latency[-1000:],
                final_translation_latency=self.final_latency[-1000:],finished_at=datetime.now().astimezone().isoformat())
            self.store.save_metadata()
            self.store.export()
            await asyncio.to_thread(self.store.audio.export_wav)
            self.store.close()
            self.asr=None

    async def stop(self):
        if self.state=='loading':
            self.orchestrator.cancel()
            await asyncio.gather(self.orchestrator,return_exceptions=True)
            return
        if self.state not in ('running','draining'):return
        self.state='draining'
        self.input_done=True
        if self.source_process and self.source_process.returncode is None:self.source_process.terminate()
        if not self.source_task.done():self.source_task.cancel()
        try:await asyncio.wait_for(asyncio.shield(self.orchestrator),60)
        except asyncio.TimeoutError:
            self.error='停止等待超时；原始音频已保存，未完成区间可通过导入录音重新处理'
            self.orchestrator.cancel()
            await asyncio.gather(self.orchestrator,return_exceptions=True)

    def append_audio(self,data):
        if not data:return
        self.store.audio.append(data)
        import numpy as np
        audio=np.frombuffer(data,dtype='<i2').astype(np.float32)/32768
        self.level=float(np.sqrt(np.mean(audio*audio))) if len(audio) else 0
        self.last_input=time.monotonic()

    async def capture(self):
        try:
            if self.source=='file':
                import imageio_ffmpeg
                cmd=[imageio_ffmpeg.get_ffmpeg_exe(),'-nostdin','-v','error','-i',str(self.upload),'-f','s16le','-ac','1','-ar','16000','pipe:1']
            else:
                binary=OUTPUTS/'native'/'ClassroomAudio.app'/'Contents'/'MacOS'/'ClassroomAudio'
                if not binary.exists():raise RuntimeError('音频采集程序尚未构建')
                cmd=[str(binary),self.source]
            self.source_process=await asyncio.create_subprocess_exec(*cmd,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            async def errors():
                while line:=await self.source_process.stderr.readline():
                    message=line.decode(errors='replace').strip()
                    try:
                        obj=json.loads(message)
                        if obj.get('type')=='error':self.error=obj['message']
                    except json.JSONDecodeError:pass
                    self.event(dict(type='capture',message=message))
            stderr_task=asyncio.create_task(errors())
            odd=b''
            try:
                while not self.input_done:
                    data=await self.source_process.stdout.read(6400)
                    if not data:break
                    data=odd+data
                    odd=data[len(data)//2*2:]
                    self.append_audio(data[:len(data)//2*2])
                    if self.source=='file' and self.realtime:
                        delay=self.clock+self.store.audio.seconds-time.monotonic()
                        if delay>0:await asyncio.sleep(delay)
                if self.source_process.returncode is None and self.input_done:self.source_process.terminate()
                code=await self.source_process.wait()
                await stderr_task
                if code and not self.input_done:raise RuntimeError(self.error or f'音频采集退出，代码 {code}')
            finally:
                if not stderr_task.done():stderr_task.cancel()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.error=str(e);self.event(dict(type='error',message=self.error))
        finally:self.input_done=True

    async def recognize(self):
        cursor=0
        while not self.input_done or cursor*2<self.store.audio.bytes:
            available=self.store.audio.bytes//2-cursor
            if available<16000 and not self.input_done:
                await asyncio.sleep(.1);continue
            if available<=0:break
            # Catch up from disk, never by dropping unread audio.
            data=self.store.audio.read(cursor,min(48000,available))
            cursor+=len(data)//2
            result=await self.runtime.thread(self.asr.process,data,cursor/16000)
            self.processed=cursor/16000
            for e in self.composer.ingest(*result[:2],flush=result[2]):self.on_segment(e)
        result=await self.runtime.thread(self.asr.finish)
        for e in self.composer.ingest(*result[:2],flush=True):self.on_segment(e)
        self.queue.close()

    def on_segment(self,event):
        key=event['id']
        old=self.segments.get(key)
        reuse=False
        if old and old.get('translation') and event['source'].startswith(old['source']):
            event['translation']=old['translation']
            event['translated_revision']=old.get('translated_revision')
            if event['final'] and event['source']==old['source'] and old.get('translated_revision')==old['revision']:
                event['translated_revision']=event['revision'];reuse=True
        event['queued_at']=time.monotonic()
        self.segments[key]=event
        self.last_segment=event
        while len(self.segments)>500:
            k,previous=next(iter(self.segments.items()))
            if previous.get('translated_revision')==previous['revision'] and k not in self.queue.pending and k!=self.inflight:
                self.segments.pop(k)
            else:break
        self.event(event)
        if reuse:
            self.context.append(event['source'])
            if self.realtime:self.final_latency.append(max(0,time.monotonic()-self.clock-event['end']))
            self.queue.pending.pop(key,None)
            return
        enough=len(event['source'].split())>=4
        now=time.monotonic()
        lag=max(0,self.store.audio.seconds-self.processed)
        wait=max((now-s.get('queued_at',now) for s in self.queue.pending.values()),default=0)
        if event['final'] or (enough and lag<15 and wait<15 and now-self.preview_times.get(key,0)>=1):
            self.preview_times[key]=now
            self.queue.put(event)
        if len(self.preview_times)>1000:self.preview_times={k:v for k,v in self.preview_times.items() if k in self.segments}

    async def translate(self):
        while (item:=await self.queue.get()) is not None:
            started=time.monotonic()
            self.inflight=item['id']
            try:
                text=await self.translator.translate(item['source'],list(self.context))
                previous=self.segments.get(item['id'])
                latest=apply_translation(previous,item,text)
                if latest is None:continue
                first=not previous.get('translation')
                latest=dict(latest,translation_seconds=time.monotonic()-started)
                latest['translation_audio_end']=item['end']
                self.segments[item['id']]=latest
                # Wall-clock lag is meaningful for real-time sources only.
                latency=max(0,time.monotonic()-self.clock-item['end']) if self.realtime else None
                if latency is not None:
                    if first and item['id'] not in self.first_seen:
                        self.first_latency.append(latency);self.first_seen.add(item['id'])
                    if item['final']:self.final_latency.append(latency)
                if item['final']:self.context.append(item['source'])
                self.event(latest)
            except Exception as e:
                latest=self.segments.get(item['id'])
                if latest and latest['revision']==item['revision']:
                    latest=dict(latest,translation_error=str(e));self.segments[item['id']]=latest;self.event(latest)
            finally:self.inflight=None

    async def monitor(self):
        tick=0
        while True:
            status=self.status()
            self.stats_history.append((time.monotonic(),status['asr_lag']))
            self.event(status,persist=False)
            if tick%5==0:self.store.append(dict(status,wall_time=time.time()))
            tick+=1
            await asyncio.sleep(1)

    def retry(self,segment_id):
        item=self.segments.get(segment_id)
        if item:self.queue.put(item)
