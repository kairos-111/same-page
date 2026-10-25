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
            self.assertEqual([m['id'] for m in data['models']],['small','turbo','hy-mt'])
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
                with patch('classroom.system.subprocess.run') as run:
                    self.assertEqual((await client.post('/api/folders/logs',headers=headers)).status_code,200)
                    self.assertEqual(run.call_args.args[0][0],'/usr/bin/open')
                data=(await client.get('/api/system')).json()
                self.assertTrue(data['local_only'])
                self.assertNotIn('token',data)
        asyncio.run(check())

class ModelControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_controls_require_auth_validate_and_reject_active_class(self):
        import classroom.app as api
        from unittest.mock import AsyncMock
        fake=SimpleNamespace(control_models=AsyncMock(),model=None,asr=None)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8767') as c:
            headers={'x-session-token':TOKEN}
            self.assertEqual((await c.post('/api/models/all/start',json={})).status_code,403)
            self.assertEqual((await c.post('/api/models/unknown/start',headers=headers,json={})).status_code,400)
            for status in ('loading','running','draining'):
                with patch.object(api,'active',SimpleNamespace(state=status,orchestrator=None)),patch.object(api,'runtime',fake):
                    self.assertEqual((await c.post('/api/models/all/stop',headers=headers,json={})).status_code,409)
            fake.control_models.assert_not_awaited()
            with patch.object(api,'active',None),patch.object(api,'runtime',fake):
                self.assertEqual((await c.post('/api/models/all/start',headers=headers,json={'recognition':'turbo'})).status_code,200)
                fake.control_models.assert_awaited_once_with('all','start','turbo')
                async with api.session_lock:
                    self.assertEqual((await c.post('/api/models/all/start',headers=headers,json={})).status_code,409)

    async def test_all_loads_both_recognizers_and_stop_is_targeted(self):
        from classroom.runtime import Runtime
        from unittest.mock import AsyncMock, call
        runtime=Runtime()
        runtime.ensure_translation=AsyncMock()
        runtime.preload_asr=AsyncMock()
        runtime.stop_asr=AsyncMock()
        runtime.stop_translation=AsyncMock()
        try:
            await runtime.control_models('all','start','turbo')
            runtime.ensure_translation.assert_awaited_once()
            self.assertEqual(runtime.preload_asr.await_args_list,[call('small'),call('turbo')])
            await runtime.control_models('turbo','stop')
            runtime.stop_asr.assert_awaited_once_with('turbo')
            runtime.stop_asr.reset_mock()
            await runtime.control_models('all','stop')
            runtime.stop_asr.assert_awaited_once_with()
            runtime.stop_translation.assert_awaited_once()
            self.assertIsNone(runtime.model_operation)
        finally:runtime.executor.shutdown(wait=False)

    async def test_failed_model_control_keeps_error_and_releases_busy(self):
        from classroom.runtime import Runtime
        from unittest.mock import AsyncMock
        runtime=Runtime();runtime.preload_asr=AsyncMock();runtime.ensure_translation=AsyncMock(side_effect=RuntimeError('synthetic load failure'))
        try:
            with self.assertRaises(RuntimeError):await runtime.control_models('all','start')
            self.assertIsNone(runtime.model_operation)
            self.assertEqual(runtime.model_error,'synthetic load failure')
        finally:runtime.executor.shutdown(wait=False)


class ResidentWhisperTests(unittest.IsolatedAsyncioTestCase):
    async def test_cached_weights_survive_fresh_sessions_and_selective_unload(self):
        import sys
        from unittest.mock import Mock
        from classroom.runtime import Runtime
        holder=SimpleNamespace(model=None,model_path=None)
        mx=SimpleNamespace(float16='fp16',clear_cache=Mock())
        loader=Mock(side_effect=lambda *a,**kw:object())
        modules={'mlx':SimpleNamespace(core=mx),'mlx.core':mx,
                 'mlx_whisper':SimpleNamespace(),
                 'mlx_whisper.load_models':SimpleNamespace(load_model=loader),
                 'mlx_whisper.transcribe':SimpleNamespace(ModelHolder=holder)}
        runtime=Runtime()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                for name in ('whisper-small','whisper-turbo'):
                    (root/name).mkdir();(root/name/'weights.safetensors').write_bytes(b'fake')
                with patch.dict(sys.modules,modules),patch('classroom.runtime.MODELS',root),patch('classroom.asr.WhisperAdapter',side_effect=lambda *a:SimpleNamespace()):
                    await runtime.preload_asr('small');small=runtime.asr_models['small']
                    await runtime.preload_asr('turbo');turbo=runtime.asr_models['turbo']
                    await runtime.preload_asr('small')
                    self.assertEqual(loader.call_count,2)
                    first=await runtime.load_asr('small','term')
                    self.assertIs(holder.model,small)
                    second=await runtime.load_asr('turbo','')
                    self.assertIsNot(first,second)
                    self.assertIs(holder.model,turbo)
                    self.assertEqual(loader.call_count,2)
                    await runtime.stop_asr('small')
                    self.assertNotIn('small',runtime.asr_models)
                    self.assertIs(runtime.asr_models['turbo'],turbo)
                    self.assertIs(holder.model,turbo)
                    self.assertIs(runtime.asr,second)
                    await runtime.stop_asr('turbo')
                    self.assertIsNone(runtime.asr);self.assertIsNone(runtime.model)
                    self.assertIsNone(holder.model);self.assertEqual(runtime.asr_models,{})
                    # A stopped recognizer can be loaded again; stop-all clears both.
                    await runtime.preload_asr('small');await runtime.preload_asr('turbo')
                    await runtime.stop_asr();self.assertEqual(runtime.asr_models,{})
        finally:runtime.executor.shutdown(wait=True)

    async def test_failed_load_does_not_claim_ready_or_discard_other_weights(self):
        import sys
        from unittest.mock import Mock
        from classroom.runtime import Runtime
        runtime=Runtime();small=object();runtime.asr_models['small']=small
        mx=SimpleNamespace(float16='fp16')
        modules={'mlx':SimpleNamespace(core=mx),'mlx.core':mx,
                 'mlx_whisper':SimpleNamespace(),
                 'mlx_whisper.load_models':SimpleNamespace(load_model=Mock(side_effect=RuntimeError('synthetic failure')))}
        try:
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);(root/'whisper-turbo').mkdir();(root/'whisper-turbo/weights.npz').write_bytes(b'fake')
                with patch.dict(sys.modules,modules),patch('classroom.runtime.MODELS',root):
                    with self.assertRaisesRegex(RuntimeError,'synthetic failure'):await runtime.preload_asr('turbo')
                    self.assertEqual(runtime.asr_models,{'small':small})
        finally:runtime.executor.shutdown(wait=True)


class EngineVersionTests(unittest.TestCase):
    def test_state_reports_the_engine_version_matching_the_desktop_shell(self):
        # The desktop app refuses a leftover engine of another version, so both must agree.
        import asyncio, re
        import httpx
        from classroom.app import app
        from classroom.desktop import VERSION
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8767') as client:
                return (await client.get('/api/state')).json()
        self.assertEqual(asyncio.run(run())['version'], VERSION)
        cargo = (Path(__file__).resolve().parents[1] / 'desktop/Cargo.toml').read_text()
        self.assertEqual(re.search(r'^version = "([^"]+)"', cargo, re.M).group(1), VERSION)
