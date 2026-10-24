"""Desktop overview and fixed folder actions use no model or audio hardware."""
import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import httpx
from classroom.app import app,TOKEN
from classroom.desktop import overview

class DesktopTests(unittest.TestCase):
    def test_model_files_are_not_claimed_to_be_loaded(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root);models=path/'models';sessions=path/'sessions'
            (models/'whisper-small').mkdir(parents=True);sessions.mkdir()
            (models/'whisper-small'/'weights.safetensors').write_bytes(b'synthetic')
            (sessions/'class').mkdir();(sessions/'class'/'session.json').write_text('{}')
            with patch('classroom.desktop.MODELS',models),patch('classroom.desktop.SESSIONS',sessions):
                data=overview(SimpleNamespace(model=None,asr=None))
            self.assertTrue(data['models'][0]['available'])
            self.assertFalse(data['models'][1]['available'])
            self.assertFalse(data['engine_loaded'])
            self.assertEqual(data['course_count'],1)
    def test_folder_actions_require_auth_and_allow_only_fixed_directories(self):
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8767') as client:
                self.assertEqual((await client.post('/api/folders/logs')).status_code,403)
                headers={'x-session-token':TOKEN}
                self.assertEqual((await client.post('/api/folders/arbitrary',headers=headers)).status_code,404)
                with patch('classroom.app.subprocess.run') as run:
                    self.assertEqual((await client.post('/api/folders/logs',headers=headers)).status_code,200)
                    self.assertEqual(run.call_args.args[0][0],'/usr/bin/open')
                data=(await client.get('/api/system')).json()
                self.assertTrue(data['local_only'])
                self.assertNotIn('token',data)
        asyncio.run(check())
