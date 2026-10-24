"""Only disposable synthetic files; never operate on the user's saved classes."""
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import httpx
from classroom.history import session_path, audio_files, ensure_ended, update_retention, remove_class, reveal_recording, remove_unused_upload
from classroom.app import app, TOKEN

SID='20261024-120000-0123abcd'

class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.sessions=self.root/'sessions';self.sessions.mkdir()
        self.path=self.sessions/SID;self.path.mkdir()
        (self.path/'session.json').write_text(json.dumps(dict(id=SID,state='completed',recording_decision='pending')))
        for name in ('audio_0000.pcm','audio_0000.wav','events.jsonl','双语记录.txt','双语字幕.srt'):
            (self.path/name).write_text('synthetic')
    def tearDown(self):self.temp.cleanup()

    def test_path_traversal_and_symlink_rejected(self):
        for value in ('../escape','bad',SID+'/../other'):
            with self.assertRaises(FileNotFoundError):session_path(self.sessions,value)
        linked=self.sessions/'20261024-130000-0123abcd';linked.symlink_to(self.path,target_is_directory=True)
        with self.assertRaises(FileNotFoundError):session_path(self.sessions,linked.name)

    def test_active_or_finalizing_cannot_be_deleted(self):
        for state,done in [('running',True),('draining',True),('completed',False)]:
            with self.assertRaises(ValueError):ensure_ended(SID,SimpleNamespace(id=SID,state=state,orchestrator=SimpleNamespace(done=lambda:done)))
        ensure_ended(SID,SimpleNamespace(id=SID,state='completed',orchestrator=None))

    def test_discard_audio_keeps_history_subtitles_and_events(self):
        update_retention(self.path,False)
        self.assertEqual(audio_files(self.path),[])
        for name in ('session.json','events.jsonl','双语记录.txt','双语字幕.srt'):
            self.assertTrue((self.path/name).exists())
        self.assertEqual(json.loads((self.path/'session.json').read_text())['recording_decision'],'discarded')
        update_retention(self.path,False) # idempotent

    def test_keep_and_reveal_use_wav_without_shell(self):
        update_retention(self.path,True)
        with patch('classroom.history.subprocess.run') as run:
            reveal_recording(self.path)
            self.assertEqual(run.call_args.args[0],['/usr/bin/open','-R',str(self.path/'audio_0000.wav')])
        self.assertEqual(len(audio_files(self.path)),2)

    def test_delete_removes_one_class_only(self):
        outside=self.sessions/'untouched';outside.mkdir()
        (outside/'original.wav').write_text('original')
        remove_class(session_path(self.sessions,SID))
        self.assertFalse(self.path.exists())
        self.assertTrue((outside/'original.wav').exists())

    def test_uploaded_copy_not_removed_while_shared(self):
        uploads=self.root/'uploads';uploads.mkdir()
        uid='a'*32;(uploads/uid).write_text('app copy')
        metadata=dict(upload_id=uid)
        (self.path/'session.json').write_text(json.dumps(dict(upload_id=uid,recording_retained=True)))
        remove_unused_upload(self.sessions,uploads,metadata)
        self.assertTrue((uploads/uid).exists())
        update_retention(self.path,False)
        remove_unused_upload(self.sessions,uploads,metadata)
        self.assertFalse((uploads/uid).exists())

    def test_http_auth_busy_retention_and_delete(self):
        async def check():
            transport=httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1:8767') as client:
                url=f'/api/sessions/{SID}'
                self.assertEqual((await client.post(url+'/delete',json={'confirm':True})).status_code,403)
                headers={'x-session-token':TOKEN,'origin':'http://127.0.0.1:8767'}
                self.assertEqual((await client.post(url+'/delete',headers=headers,json={})).status_code,400)
                with patch('classroom.app.active',SimpleNamespace(id=SID,state='running',orchestrator=None)):
                    self.assertEqual((await client.post(url+'/delete',headers=headers,json={'confirm':True})).status_code,409)
                    self.assertEqual((await client.post(url+'/recording',headers=headers,json={'keep':False})).status_code,409)
                self.assertEqual((await client.get('/api/sessions')).json()[0]['has_recording'],True)
                self.assertEqual((await client.post(url+'/recording',headers=headers,json={'keep':False})).status_code,200)
                self.assertEqual((await client.get('/api/sessions')).json()[0]['has_recording'],False)
                self.assertEqual((await client.post(url+'/recording',headers=headers,json={'keep':True})).status_code,409)
                self.assertEqual((await client.post(url+'/recording/reveal',headers=headers)).status_code,404)
                self.assertEqual((await client.post(url+'/delete',headers=headers,json={'confirm':True})).status_code,200)
                self.assertEqual((await client.get('/api/sessions')).json(),[])
        with patch('classroom.app.SESSIONS',self.sessions),patch('classroom.app.WORK',self.root),patch('classroom.app.active',None):asyncio.run(check())


class SavingTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_completed_status_before_wav_export_finishes(self):
        import threading
        from test_asr_coverage import adapter, Token, pcm
        from classroom.session import ClassSession
        class Runtime:
            async def ensure_translation(self):pass
            async def load_asr(self,*args):
                a=adapter(.8);a.online.auto_partial=[Token(.1,.8,'Tail.')];return a
            async def thread(self,fn,*args):return fn(*args)
        class Translator:
            def __init__(self,*args):pass
            async def translate(self,*args):return '中文'
            async def close(self):pass
        entered=threading.Event();release=threading.Event()
        with tempfile.TemporaryDirectory() as root,patch('classroom.session.SESSIONS',Path(root)),patch('classroom.session.LocalTranslator',Translator):
            session=ClassSession(Runtime(),'file','small','synthetic',realtime=False)
            async def capture():
                session.append_audio(pcm(1,100));session.input_done=True
            session.capture=capture
            original=session.store.audio.export_wav
            def gated_export():
                entered.set()
                if not release.wait(3):raise RuntimeError('test gate timeout')
                original()
            session.store.audio.export_wav=gated_export
            task=asyncio.create_task(session.run())
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,2))
                self.assertEqual(session.state,'draining')
                self.assertFalse(session.status()['saved'])
                with self.assertRaises(ValueError):ensure_ended(session.id,session)
            finally:
                release.set()
                await asyncio.wait_for(task,2)
            self.assertEqual(session.state,'completed')
            self.assertTrue(session.status()['saved'])
            self.assertEqual(session.status()['recording_decision'],'pending')
