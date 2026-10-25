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
from .paths import OUTPUTS,SESSIONS,LLAMA_URL,CAPTURE
from .store import SessionStore,check_integrity
from .subtitles import SegmentComposer
from .chinese import simplified_segment
from .translation import Glossary,LocalTranslator,RevisionQueue,apply_translation,preview_due,validate_translation,critical_signature
from .process import terminate_and_drain
from .languages import MODES,chinese_passthrough,source_language,text_languages,clean_asr_text

class ClassSession:
    def __init__(self,runtime,source,model,title,upload=None,realtime=True,language_mode='en'):
        if language_mode not in MODES:raise ValueError('识别模式无效')
        self.language_mode=language_mode
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
            started_at=datetime.now().astimezone().isoformat(),state=self.state,language='mul' if language_mode=='mixed' else language_mode,language_mode=language_mode,target='zh',
            recording_decision='pending',recording_retained=True,
            upload_id=upload.name if upload is not None else None))
        self.queue=RevisionQueue()
        self.segments=OrderedDict()
        self.context=deque(maxlen=2)
        self.composer=SegmentComposer(language_mode)
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
        self.integrity=None
        self.asr_flushed=False
        self.saved=False
        self.asr_started=None
        self.asr_last_seconds=0
        # Imported files are decoded as fast as possible; there is no live speaker to keep up with.
        self.fast_import=source=='file' and not realtime
        self.recognize_limit=None
        # The microphone helper can take seconds to start (e.g. Bluetooth headsets);
        # nothing is heard before it reports ready.
        self.capture_ready=False
        self.audio_started=False

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
        behind=[x for x in self.segments.values() if x.get('asr_status') != 'unresolved' and x.get('translated_revision')!=x['revision']]
        processes=[psutil.Process()]
        try:processes+=processes[0].children(recursive=True)
        except psutil.Error:pass
        rss=0
        for p in processes:
            try:rss+=p.memory_info().rss
            except psutil.Error:pass
        return dict(type='status',id=self.id,title=self.title,state=self.state,error=self.error,
            integrity=self.integrity,saved=self.saved,
            recording_decision=self.store.metadata.get("recording_decision"),
            recording_retained=self.store.metadata.get("recording_retained",True),
            audio_seconds=self.store.audio.seconds,asr_lag=max(0,self.store.audio.seconds-self.processed),
            translation_queue=len(self.queue.pending),translation_wait=max(0,now-oldest),
            asr_execution_age=max(0,now-self.asr_started) if self.asr_started is not None else 0,
            asr_last_seconds=self.asr_last_seconds,
            translation_busy=self.inflight is not None,
            translation_execution_age=max(0,now-self.inflight_started) if self.inflight_started is not None else 0,
            translation_inflight_revision=self.inflight_item['revision'] if self.inflight_item else None,
            translation_inflight_id=self.inflight,
            chinese_pending=len(behind),
            chinese_lag=max((now-x['unmatched_since'] for x in behind),default=0),
            finalization_wait=max((now-x['first_seen_at'] for x in self.segments.values() if not x['final'] and x.get('asr_status') != 'unresolved'),default=0),
            level=round(self.level,4),input_idle=round(time.monotonic()-self.last_input,1),
            memory_bytes=rss,source=self.source,model=self.model,language_mode=self.language_mode,target='zh',
            asr_unresolved=self.asr_unresolved,
            processed_seconds=self.processed,input_done=self.input_done,fast_import=self.fast_import,
            capture_ready=self.capture_ready or self.audio_started or self.source=='file',
            final_count=sum(s['final'] for s in self.segments.values()))

    async def run(self):
        try:
            # Start listening while the models load: speech is spooled to disk and
            # recognised once the models are ready, instead of being missed.
            self.source_task=asyncio.create_task(self.capture())
            self.monitor_task=asyncio.create_task(self.monitor())
            self.tasks=[self.source_task,self.monitor_task]
            await self.runtime.ensure_translation()
            args=(self.model,self.glossary.asr_hint(self.language_mode))
            if self.language_mode!='en':args+= (self.language_mode,)
            self.asr=await self.runtime.load_asr(*args)
            self.state='running'
            self.event(self.status())
            self.asr_task=asyncio.create_task(self.recognize())
            self.translation_task=asyncio.create_task(self.translate())
            self.tasks+=[self.asr_task,self.translation_task]
            await asyncio.gather(self.source_task,self.asr_task)
            self.queue.close()
            await self.translation_task
            self.audit_translations()
            self.state='draining'
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
            self.integrity=check_integrity(self.store.path,self.store.audio.seconds,self.processed,self.asr_flushed)
            self.event(dict(type='integrity',session_id=self.id,**self.integrity))
            self.store.metadata.update(integrity=self.integrity,state=self.state,error=self.error,audio_seconds=self.store.audio.seconds,
                processed_seconds=self.processed,asr_unresolved=self.asr_unresolved,first_translation_latency=self.first_latency[-1000:],
                final_translation_latency=self.final_latency[-1000:],finished_at=datetime.now().astimezone().isoformat())
            self.store.save_metadata()
            # Announce completion only after all exports and WAV files exist.
            self.store.export()
            await asyncio.to_thread(self.store.audio.export_wav)
            if self.state == 'draining':self.state='error' if self.error else 'completed'
            self.saved=True
            self.store.metadata.update(state=self.state,saved=True)
            self.store.save_metadata()
            self.event(self.status())
            self.store.close()
            self.asr=None

    def audit_translations(self):
        incomplete=[x['id'] for x in self.store.load_segments(self.store.path) if x['final'] and (not x.get('translation') or x.get('translated_revision')!=x['revision'])]
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
        if self.fast_import and self.recognize_limit is None:
            self.recognize_limit=int(self.processed*16000)*2
        if self.source_process and self.source_process.returncode is None:self.source_process.terminate()
        if not self.source_task.done():self.source_task.cancel()
        try:await asyncio.wait_for(asyncio.shield(self.orchestrator),60)
        except asyncio.TimeoutError:
            self.error='停止等待超时；原始音频已保存，未完成区间可通过导入录音重新处理'
            self.orchestrator.cancel()
            await asyncio.gather(self.orchestrator,return_exceptions=True)

    def append_audio(self,data):
        if not data:return
        if not self.audio_started:
            # Audio time zero is when sound first arrives, so delays are measured
            # from what was actually heard, not from pressing Start.
            self.audio_started=True
            self.clock=time.monotonic()-len(data)/32000
            self.store.metadata['audio_started_at']=time.time()-len(data)/32000
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
                binary=CAPTURE
                if not binary.exists():raise RuntimeError('音频采集程序尚未构建')
                cmd=[str(binary),self.source]
            self.source_process=await asyncio.create_subprocess_exec(*cmd,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            async def errors():
                while line:=await self.source_process.stderr.readline():
                    message=line.decode(errors='replace').strip()
                    try:
                        obj=json.loads(message)
                        if obj.get('type')=='error':self.error=obj['message']
                        elif obj.get('type')=='ready':
                            self.capture_ready=True
                            self.event(self.status(),persist=False)
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
        while not self.input_done or cursor*2<self.recognition_end():
            available=self.recognition_end()//2-cursor
            if available<16000 and not self.input_done:
                await asyncio.sleep(.1);continue
            if available<=0:break
            # Catch up from disk, never by dropping unread audio.
            data=self.store.audio.read(cursor,min(48000,available))
            if not data:raise RuntimeError("音频日志读取未前进；原声保留，未处理区间待核查")
            cursor+=len(data)//2
            if hasattr(self.asr,'set_backlog'):
                # A fast import is always far ahead of recognition; that is not live lag,
                # so recovery decodes stay enabled.
                self.asr.set_backlog(0 if self.fast_import else max(0,self.store.audio.seconds-cursor/16000))
            result=await self.process_asr(self.asr.process,data,cursor/16000)
            self.record_asr_events()
            self.processed=cursor/16000
            self.accept_asr_result(result)
        result=await self.process_asr(self.asr.finish)
        self.record_asr_events()
        self.accept_asr_result(result)
        self.asr_flushed=True
        self.queue.close()

    def recognition_end(self):
        # Stopping a fast import ends recognition where it stands; the rest of the file
        # stays in the recording and is reported as unprocessed by the integrity check.
        end=self.store.audio.bytes
        return end if self.recognize_limit is None else min(end,self.recognize_limit)

    async def process_asr(self, function, *args):
        self.asr_started=time.monotonic()
        outcome='completed'
        try:return await self.runtime.thread(function,*args)
        except asyncio.CancelledError:
            outcome='cancelled_wait' # Thread execution may still be running.
            raise
        except Exception:
            outcome='error'
            raise
        finally:
            self.asr_last_seconds=time.monotonic()-self.asr_started
            self.event(dict(type='asr_attempt',audio_end=args[1] if len(args)>1 else self.processed,
                seconds=self.asr_last_seconds,flush=not bool(args),outcome=outcome))
            self.asr_started=None

    def accept_asr_result(self, result):
        # Fourth field is additive; legacy/test adapters may still return three.
        quality = result[3] if len(result)>3 else {}
        for event in quality.get('events', []):self.event(event)
        if quality.get('waiting'):return
        if quality.get('rejected'):
            failure = next(e for e in reversed(quality['events']) if e.get('type') == 'asr_quality' and e.get('status') == 'unresolved')
            self.on_segment(self.composer.reject(failure['start'], failure['end'],
                failure['attempts'][-1]['reason']))
            return
        for event in self.composer.ingest(*result[:2], flush=result[2]):self.on_segment(event)

    def record_asr_events(self):
        # Drain on the same ASR worker boundary; adapter never touches the store/UI.
        for event in self.asr.drain_events() if hasattr(self.asr,'drain_events') else ():
            self.event(dict(event,session_id=self.id))

    def on_segment(self,event):
        key=event['id']
        if event.get('asr_status') != 'unresolved':
            cleaned,garbage=clean_asr_text(event.get('source',''))
            if garbage:
                if not event.get('final') and key not in self.segments:
                    return  # A junk preview is simply not shown.
                # Junk that would replace or finalise a sentence becomes a review gap.
                event={**event,'asr_status':'unresolved','asr_reason':'garbage_text','source':'','source_raw':event.get('source','')}
            elif cleaned!=event.get('source'):
                event={**event,'source':cleaned,'source_raw':event.get('source','')}
        if event.get('asr_status') == 'unresolved':
            self.asr_unresolved+=1
            self.queue.pending.pop(key, None)
            self.segments[key]=event
            self.last_segment=event
            self.event(event)
            return
        event=simplified_segment(event)
        event['language_mode']=self.language_mode
        event['language']=source_language(event['source'],self.language_mode)
        event['languages']=text_languages(event['source'])
        old=self.segments.get(key)
        passthrough=chinese_passthrough(event['source'])
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
        if passthrough:
            event.update(translation=event['source'],translated_revision=event['revision'],
                translation_origin='source_zh',translation_error='',translation_seconds=0)
            reuse=True
        self.segments[key]=event
        self.last_segment=event
        while len(self.segments)>500:
            k,previous=next(iter(self.segments.items()))
            if previous.get('translated_revision')==previous['revision'] and k not in self.queue.pending and k!=self.inflight:
                self.segments.pop(k)
            else:break
        self.event(event)
        if reuse:
            if event['final']:self.context.append(event['source'])
            if self.realtime and event['final']:self.final_latency.append(max(0,time.monotonic()-self.clock-event['end']))
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
                text=validate_translation(await self.translator.translate(item['source'],list(self.context),item['final']))
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
                    # A confirmed sentence gets one more try once the slot is free.
                    if item['final'] and not item.get('auto_retry'):
                        self.queue.put(dict(item,retry=True,auto_retry=True))
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
