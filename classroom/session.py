import asyncio
from collections import OrderedDict,deque
from datetime import datetime
import json
import math
from pathlib import Path
import time
import uuid
import psutil
import httpx
from .paths import OUTPUTS,SESSIONS,LLAMA_URL
from .store import SessionStore
from .subtitles import SegmentComposer
from .translation import Glossary,LocalTranslator,RevisionQueue,apply_translation,preview_due,validate_translation,critical_signature
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
        self.dispatched={}
        self.first_latency=[]
        self.first_seen=set()
        self.final_latency=[]
        self.stats_history=deque(maxlen=5400)
        self.source_process=None
        self.tasks=[]
        self.orchestrator=None
        self.glossary=Glossary(OUTPUTS/'课程术语.csv')
        self.translator=LocalTranslator(LLAMA_URL,self.glossary)
        self.metrics_finished_at=None
        self.last_segment=None
        self.inflight=None
        self.asr_unresolved=0
        self.inflight_item=None
        self.inflight_started=None

    def event(self,obj,persist=True):
        obj={**obj,'session_id':self.id,'session_elapsed':time.monotonic()-self.clock,'wall_time':time.time()}
        if persist:self.store.append(obj)
        for q in tuple(self.clients):
            if q.full():
                # Slow browsers receive a fresh snapshot on reconnect; inference must not wait on UI.
                self.clients.remove(q)
                while not q.empty():q.get_nowait()
                q.put_nowait({'type':'resync'})
            else:q.put_nowait(obj)

    def status(self):
        now=self.metrics_finished_at or time.monotonic()
        oldest=min((x.get('queued_at',now) for x in self.queue.pending.values()),default=now)
        behind=[x for x in self.segments.values() if x.get('translated_revision')!=x['revision']]
        processes=[psutil.Process()]
        try:processes+=processes[0].children(recursive=True)
        except psutil.Error:pass
        rss=0
        for p in processes:
            try:rss+=p.memory_info().rss
            except psutil.Error:pass
        return dict(type='status',id=self.id,title=self.title,state=self.state,error=self.error,
            audio_seconds=self.store.audio.seconds,asr_lag=max(0,self.store.audio.seconds-self.processed),
            translation_queue=len(self.queue.pending),translation_wait=max(0,now-oldest),
            translation_busy=self.inflight is not None,
            translation_execution_age=max(0,now-self.inflight_started) if self.inflight_started is not None else 0,
            translation_inflight_revision=self.inflight_item['revision'] if self.inflight_item else None,
            translation_inflight_id=self.inflight,
            chinese_pending=len(behind),
            chinese_lag=max((now-x['unmatched_since'] for x in behind),default=0),
            finalization_wait=max((now-x['first_seen_at'] for x in self.segments.values() if not x['final']),default=0),
            level=round(self.level,4),input_idle=round(time.monotonic()-self.last_input,1),
            memory_bytes=rss,source=self.source,model=self.model,
            asr_unresolved=self.asr_unresolved,
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
            self.audit_translations()
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
            self.audit_translations()
            self.metrics_finished_at=time.monotonic()
            self.store.audio.close()
            await self.translator.close()
            self.event(self.status())
            self.store.metadata.update(state=self.state,error=self.error,audio_seconds=self.store.audio.seconds,
                processed_seconds=self.processed,asr_unresolved=self.asr_unresolved,first_translation_latency=self.first_latency[-1000:],
                final_translation_latency=self.final_latency[-1000:],finished_at=datetime.now().astimezone().isoformat())
            self.store.save_metadata()
            self.store.export()
            await asyncio.to_thread(self.store.audio.export_wav)
            self.store.close()
            self.asr=None

    def audit_translations(self):
        incomplete=[x['id'] for x in self.segments.values() if x['final'] and (not x.get('translation') or x.get('translated_revision')!=x['revision'])]
        self.store.metadata['incomplete_translation_ids']=incomplete
        if incomplete and not self.error:
            self.error=f'{len(incomplete)} 句最终中文未完成；原声与修订已保留，可导入录音补处理'
        return incomplete

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
            self.accept_asr_result(result)
        result=await self.runtime.thread(self.asr.finish)
        self.accept_asr_result(result)
        self.queue.close()

    def accept_asr_result(self, result):
        # Fourth field is additive; legacy/test adapters may still return three.
        quality = result[3] if len(result)>3 else {}
        for event in quality.get('events', []):self.event(event)
        if quality.get('rejected'):
            failure = quality['events'][-1]
            self.on_segment(self.composer.reject(failure['start'], failure['end'],
                failure['attempts'][-1]['reason']))
            return
        for event in self.composer.ingest(*result[:2], flush=result[2]):self.on_segment(event)

    def on_segment(self,event):
        key=event['id']
        if event.get('asr_status') == 'unresolved':
            self.asr_unresolved+=1
            self.queue.pending.pop(key, None)
            self.segments[key]=event
            self.last_segment=event
            self.event(event)
            return
        old=self.segments.get(key)
        reuse=False
        if old and old.get('translation') and event['source'].startswith(old['source']):
            event['translation']=old['translation']
            event['translated_revision']=old.get('translated_revision')
            if event['final'] and event['source']==old['source'] and old.get('translated_revision')==old['revision']:
                event['translated_revision']=event['revision'];reuse=True
        now=time.monotonic()
        event['first_seen_at']=old['first_seen_at'] if old else now
        event['source_updated_at']=now
        event['unmatched_since']=old.get('unmatched_since',now) if old and old.get('translated_revision')!=old['revision'] else now
        if event['final']:
            event['finalized_at']=old.get('finalized_at',now) if old else now
            event['finalization_seconds']=event['finalized_at']-event['first_seen_at']
        # Keep readable evidence even when ASR diverges, with an explicit version mismatch.
        if old and old.get('translation') and not event.get('translation'):
            event['translation']=old['translation']
            event['translated_revision']=old.get('translated_revision')
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
        # Any queued revision is replaced only by the current eligible revision.
        self.schedule(event)
        if len(self.dispatched)>1000:
            self.dispatched={k:v for k,v in self.dispatched.items() if k in self.segments}

    def schedule(self,event):
        key=event['id']
        replaced=self.queue.pending.pop(key,None)
        if replaced and replaced['revision']!=event['revision']:
            self.event(dict(type='translation_attempt',id=key,revision=replaced['revision'],current_revision=event['revision'],outcome='superseded_pending',queue_seconds=time.monotonic()-replaced['queued_at']))
        now=time.monotonic()
        if event.get('translated_revision')==event['revision']:
            return
        if event['final'] or preview_due(event,self.dispatched.get(key),now):
            # Never build speculative work behind a slow request. Finals and critical
            # corrections are retained; ordinary previews can refresh on the next update.
            overloaded=(self.inflight_started is not None and now-self.inflight_started>=5) or any(x['final'] for x in self.queue.pending.values())
            previous=self.dispatched.get(key)
            critical=critical_signature(event['source']) != critical_signature(previous['source'] if previous else '')
            overloaded=overloaded or self.store.audio.seconds-self.processed>=15 or any(now-x['queued_at']>=15 for x in self.queue.pending.values())
            if event['final'] or critical or not overloaded:
                self.queue.put(event)

    async def translate(self):
        while (item:=await self.queue.get()) is not None:
            latest=self.segments.get(item['id'])
            if not latest or latest['revision']!=item['revision']:
                self.event(dict(type='translation_attempt',id=item['id'],revision=item['revision'],outcome='superseded_before_start'))
                continue
            if latest.get('translated_revision')==latest['revision']:
                continue
            started=time.monotonic()
            if not item.get('retry') and not preview_due(item,self.dispatched.get(item['id']),started):
                continue
            self.inflight=item['id']
            self.inflight_item=item
            self.inflight_started=started
            self.dispatched[item['id']]=dict(item,dispatched_at=started)
            timing=dict(id=item['id'],revision=item['revision'],final=item['final'],
                queue_seconds=max(0,started-item['queued_at']),
                revision_wait_seconds=max(0,started-item['source_updated_at']),
                finalization_seconds=item.get('finalization_seconds'),
                finalization_audio_lag=max(0,item['finalized_at']-self.clock-item['end']) if item['final'] and self.realtime else None)
            self.event(dict(type='translation_attempt',**timing,outcome='started'))
            outcome='error';message=''
            try:
                text=validate_translation(await self.translator.translate(item['source'],list(self.context)))
                previous=self.segments.get(item['id'])
                latest=apply_translation(previous,item,text)
                if latest is None:
                    outcome='stale_discarded'
                    continue
                first=not previous.get('translation')
                latest=dict(latest,translation_seconds=time.monotonic()-started)
                latest['translation_audio_end']=item['end']
                self.segments[item['id']]=latest
                latency=max(0,time.monotonic()-self.clock-item['end']) if self.realtime else None
                if latency is not None:
                    if first and item['id'] not in self.first_seen:
                        self.first_latency.append(latency);self.first_seen.add(item['id'])
                    if latest['final'] and latest.get('translated_revision')==latest['revision']:self.final_latency.append(latency)
                if item['final']:self.context.append(item['source'])
                outcome='applied_current' if latest['revision']==item['revision'] else 'applied_prefix'
                self.event(latest)
            except asyncio.CancelledError:
                outcome='client_cancelled_compute_unconfirmed'
                raise
            except Exception as e:
                message=str(e) or type(e).__name__
                if isinstance(e,httpx.TimeoutException):
                    outcome='request_timeout_compute_unconfirmed'
                    message='翻译请求超时；未确认模型计算已停止'
                latest=self.segments.get(item['id'])
                if latest and latest['revision']==item['revision']:
                    latest=dict(latest,translation_error=message);self.segments[item['id']]=latest;self.event(latest)
            finally:
                self.event(dict(type='translation_attempt',**timing,outcome=outcome,error=message,
                    execution_seconds=time.monotonic()-started,
                    current_revision=self.segments.get(item['id'],{}).get('revision'),
                    revision_age_seconds=time.monotonic()-item['source_updated_at'],
                    audio_end_lag=max(0,time.monotonic()-self.clock-item['end']) if self.realtime else None))
                self.inflight=None
                self.inflight_item=None
                self.inflight_started=None

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
        if item and item.get('asr_status') != 'unresolved':self.queue.put(dict(item,retry=True))
