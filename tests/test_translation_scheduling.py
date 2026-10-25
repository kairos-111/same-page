"""Deterministic local fakes: no model, audio device, HTTP service or real recording."""
import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import httpx
from classroom.session import ClassSession
from classroom.translation import LocalTranslator, Glossary, preview_due, critical_signature


def segment(revision, text, final=False, key='s1'):
    return dict(type='segment',id=key,revision=revision,source=text,final=final,start=0,end=1)


class GateTranslator:
    def __init__(self):
        self.calls=[]
        self.started=asyncio.Queue()
        self.release=asyncio.Queue()

    async def close(self):
        pass

    async def translate(self,text,context,final=True):
        self.calls.append(text)
        self.started.put_nowait(text)
        return await self.release.get()


class SchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        with patch('classroom.session.SESSIONS',Path(self.temp.name)):
            self.s=ClassSession(None,'file','small','fake only')
        await self.s.translator.close()
        self.fake=GateTranslator()
        self.s.translator=self.fake
        self.worker=None

    async def asyncTearDown(self):
        if self.worker and not self.worker.done():
            self.worker.cancel()
            await asyncio.gather(self.worker,return_exceptions=True)
        self.s.store.audio.close()
        self.s.store.close()
        self.temp.cleanup()

    async def start(self):
        self.worker=asyncio.create_task(self.s.translate())
        await asyncio.wait_for(self.fake.started.get(),1)

    async def drain(self):
        self.s.queue.close()
        await asyncio.wait_for(self.worker,1)

    def logs(self):
        self.s.store.events.flush()
        import json
        return [json.loads(x) for x in (self.s.store.path/'events.jsonl').read_text().splitlines()]

    async def test_asr_timing_resets_after_failure_and_records_outcome(self):
        async def thread(fn,*args):
            self.assertGreater(self.s.status()['asr_execution_age'],0)
            return fn(*args)
        self.s.runtime=SimpleNamespace(thread=thread)
        def fail(*args):raise RuntimeError('synthetic')
        with self.assertRaises(RuntimeError):
            await self.s.process_asr(fail,b'audio',3.0)
        self.assertIsNone(self.s.asr_started)
        event=self.logs()[-1]
        self.assertEqual(event['type'],'asr_attempt')
        self.assertEqual(event['outcome'],'error')
        self.assertEqual(event['audio_end'],3.0)
        self.assertEqual(await self.s.process_asr(lambda: 'finished'),'finished')
        self.assertTrue(self.logs()[-1]['flush'])

    async def test_slow_stale_request_latest_final_drains_and_is_traceable(self):
        self.s.on_segment(segment(1,'The dose is 35 mg'))
        await self.start()
        # The gate remains closed: elapsed time is controlled, no long sleeps.
        self.s.inflight_started-=20
        for rev in range(2,30):
            self.s.on_segment(segment(rev,'The dose is not 3.5 mg '+('word '*rev)))
        self.s.on_segment(segment(30,'The dose is not 3.5 mg.',True))
        status=self.s.status()
        self.assertEqual(status['translation_queue'],1)
        self.assertGreaterEqual(status['translation_execution_age'],20)
        self.assertEqual(status['chinese_pending'],1)
        self.assertEqual(status['translation_inflight_revision'],1)
        self.fake.release.put_nowait('旧剂量')
        await asyncio.wait_for(self.fake.started.get(),1)
        self.fake.release.put_nowait('剂量不是3.5毫克。')
        await self.drain()
        self.assertEqual(len(self.fake.calls),2)
        self.assertEqual(self.s.segments['s1']['translated_revision'],30)
        self.assertEqual(self.s.status()['chinese_pending'],0)
        self.assertFalse(self.s.audit_translations())
        outcomes=[x for x in self.logs() if x['type']=='translation_attempt']
        discarded=next(x for x in outcomes if x['outcome']=='stale_discarded')
        self.assertIn('execution_seconds',discarded)
        self.assertEqual(discarded['session_id'],self.s.id)
        self.assertEqual(discarded['revision'],1)
        self.assertTrue(any(x['outcome']=='superseded_pending' for x in outcomes))

    async def test_final_priority_and_no_ordinary_previews_behind_slow_work(self):
        self.s.on_segment(segment(1,'First sentence is pending'))
        await self.start()
        self.s.inflight_started-=6
        self.s.on_segment(segment(1,'Another ordinary preview sentence',key='preview'))
        self.assertNotIn('preview',self.s.queue.pending)
        self.s.on_segment(segment(1,'A final sentence.',True,key='final'))
        self.fake.release.put_nowait('第一句')
        self.assertEqual(await asyncio.wait_for(self.fake.started.get(),1),'A final sentence.')
        self.fake.release.put_nowait('最终句。')
        await self.drain()

    async def test_empty_final_is_error_and_closure_reports_incomplete(self):
        self.s.on_segment(segment(1,'Complete sentence.',True))
        await self.start()
        self.fake.release.put_nowait('')
        await self.drain()
        self.assertTrue(self.s.segments['s1']['translation_error'])
        self.assertEqual(self.s.audit_translations(),['s1'])
        self.assertIn('未完成',self.s.error)

    async def test_failed_preview_can_be_explicitly_retried(self):
        self.s.on_segment(segment(1,'This has four words'))
        await self.start()
        self.fake.release.put_nowait('')
        await asyncio.sleep(0)
        self.s.retry('s1')
        await asyncio.wait_for(self.fake.started.get(),1)
        self.fake.release.put_nowait('重试中文')
        await self.drain()
        self.assertEqual(self.s.segments['s1']['translation'],'重试中文')
        self.assertFalse(self.s.segments['s1']['translation_error'])

    async def test_timeout_is_not_reported_as_compute_cancellation(self):
        async def fail(*args):raise httpx.ReadTimeout('')
        self.fake.translate=fail
        self.s.on_segment(segment(1,'Complete sentence.',True))
        self.worker=asyncio.create_task(self.s.translate())
        await self.drain()
        self.assertIn('未确认',self.s.segments['s1']['translation_error'])
        self.assertTrue(any(x.get('outcome')=='request_timeout_compute_unconfirmed' for x in self.logs()))

    async def test_run_end_preserves_incomplete_metadata_and_error_state(self):
        async def noop(*args):pass
        async def finish():
            self.s.on_segment(segment(1,'Final sentence.',True))
        self.s.runtime=SimpleNamespace(ensure_translation=noop,load_asr=noop)
        self.s.capture=noop
        self.s.recognize=finish
        self.worker=asyncio.create_task(self.s.run())
        await asyncio.wait_for(self.fake.started.get(),1)
        self.fake.release.put_nowait('')
        await asyncio.wait_for(self.worker,1)
        import json
        metadata=json.loads((self.s.store.path/'session.json').read_text())
        self.assertEqual(metadata['state'],'error')
        self.assertEqual(metadata['incomplete_translation_ids'],['s1'])
        self.assertIn('未完成',metadata['error'])
        self.assertEqual((self.s.store.path/'双语字幕.srt').read_text(),'')
        frozen=self.s.status()['chinese_lag']
        await asyncio.sleep(0)
        self.assertEqual(self.s.status()['chinese_lag'],frozen)

    async def test_same_source_final_reuses_complete_preview(self):
        self.s.on_segment(segment(1,'This is a sentence.'))
        await self.start()
        self.fake.release.put_nowait('这是一句话。')
        await asyncio.sleep(0)
        self.s.on_segment(segment(2,'This is a sentence.',True))
        await self.drain()
        self.assertEqual(len(self.fake.calls),1)
        self.assertEqual(self.s.segments['s1']['translated_revision'],2)
        self.assertIn('finalization_seconds',self.s.segments['s1'])

    async def test_queue_wait_and_chinese_age_include_distinct_clocks(self):
        self.s.on_segment(segment(1,'This has four words'))
        self.s.segments['s1']['unmatched_since']-=12
        self.s.queue.pending['s1']['queued_at']-=3
        status=self.s.status()
        self.assertGreaterEqual(status['chinese_lag'],12)
        self.assertGreaterEqual(status['translation_wait'],3)
        self.assertEqual(status['translation_execution_age'],0)

    def test_incremental_and_critical_refresh_policy(self):
        old=dict(source='This is a simple',dispatched_at=10)
        self.assertFalse(preview_due(segment(2,'This is a simple example'),old,11))
        self.assertTrue(preview_due(segment(3,'This is a simple example for the class'),old,11))
        self.assertTrue(preview_due(segment(2,'This is a simple example'),old,15))
        self.assertTrue(preview_due(segment(2,'This is a simple',True),old,10))
        for a,b in [('35 mg','3.5 mg'),('5mg','5ml'),('3.5','-3.5'),('three mg','thirty mg'),('it works','it doesn’t work'),('it works','it does not work'),('it works','if it works')]:
            self.assertNotEqual(critical_signature(a),critical_signature(b))
            self.assertTrue(preview_due(segment(2,b),dict(source=a,dispatched_at=10),10))

    async def test_response_finish_reason_and_content_validation(self):
        translator=LocalTranslator('http://fake',Glossary('/nonexistent'))
        await translator.client.aclose()
        payload={}
        def respond(request):return httpx.Response(200,json=payload)
        translator.client=httpx.AsyncClient(base_url='http://fake',transport=httpx.MockTransport(respond))
        try:
            for reason,content in [('length','看似非空的截断中文'),('stop',''),('stop','<think>unfinished'),(None,'中文'),('stop','...')]:
                payload.update(choices=[dict(finish_reason=reason,message=dict(content=content))])
                with self.assertRaises(RuntimeError):await translator.translate('source')
            payload.update(choices=[dict(finish_reason='stop',message=dict(content='完整中文。'))])
            self.assertEqual(await translator.translate('source'),'完整中文。')
        finally:await translator.close()
