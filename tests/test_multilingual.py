"""Multilingual modes, routing and persistence with deterministic fakes only."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,AsyncMock,patch
import httpx
from classroom.languages import chinese_passthrough,language_units,source_language
from classroom.language_detection import SpeechLanguageRouter
from classroom.asr_quality import GuardedDecoder,text_issue
from classroom.subtitles import SegmentComposer,Word
from classroom.translation import LocalTranslator,Glossary,critical_signature,preview_due,apply_translation
from classroom.session import ClassSession
from classroom.store import export_session


class LanguageTests(unittest.TestCase):
    def test_chinese_spacing_punctuation_and_labels(self):
        c=SegmentComposer('mixed')
        words=[Word(i*.2,(i+1)*.2,w) for i,w in enumerate(['今','天','讨论','linear','regression','。'])]
        result=c.ingest(words)[0]
        self.assertEqual(result['source'],'今天讨论 linear regression。')
        self.assertTrue(result['final']);self.assertEqual(result['language'],'mixed')
        self.assertEqual(source_language('Сегодня урок.'),'ru')
        self.assertTrue(chinese_passthrough('今天是2026年，不能增加5毫克。'))
        self.assertFalse(chinese_passthrough('今天讨论AI'))
        self.assertFalse(chinese_passthrough('今天讨论مرحبا'))
        self.assertEqual(language_units('今天 not 5 мг'),['今','天','not','5','мг'])

    def test_cjk_repetition_and_material_changes(self):
        self.assertEqual(text_issue('这是测试'*20),'repetition_loop')
        self.assertEqual(text_issue('你好 '+'・'*100),'symbol_loop')
        self.assertEqual(text_issue('你好 '+'・ '*100),'symbol_loop')
        self.assertEqual(text_issue('Я люблю тебя. '+'ㄱ'*120),'symbol_loop')
        self.assertEqual(text_issue('Я люблю тебя. '+'ㄱ '*120),'symbol_loop')
        self.assertIsNone(text_issue('等等……然后继续。'))
        self.assertIsNone(text_issue('对'*24))
        self.assertNotEqual(critical_signature('Если не больше 5 мг'),critical_signature('Если больше 5 мг'))
        self.assertNotEqual(critical_signature('可以增加5毫克'),critical_signature('不可以增加5毫克'))
        self.assertTrue(preview_due(dict(source='今天学习机器学习',final=False),None,0))

    def test_fixed_language_and_recovery_never_translate_to_english(self):
        bad=dict(segments=[dict(start=0,end=10,text='the minimize '*40,words=[])])
        good=dict(language='ru',segments=[dict(start=0,end=1,text='Привет',words=[dict(start=0,end=1,word='Привет')])])
        model=Mock(side_effect=[bad,good])
        d=GuardedDecoder(model,'local','Русская речь.',language='ru')
        d(range(160000))
        for call in model.call_args_list:
            self.assertEqual(call.kwargs['task'],'transcribe');self.assertEqual(call.kwargs['language'],'ru')
        self.assertEqual(model.call_args.kwargs['initial_prompt'],'')

    def test_recent_language_detection_can_switch_and_uncertainty_does_not_force_guess(self):
        probe=Mock(side_effect=[{'en':.9,'ru':.05,'zh':.05},{'en':.05,'ru':.9,'zh':.05},{'en':.05,'ru':.05,'zh':.9}])
        router=SpeechLanguageRouter(probe)
        self.assertEqual(router.choose(list(range(32000)),0)[0],'en')
        router.frontier=2
        self.assertEqual(router.choose(list(range(64000)),0)[0],'ru')
        router.frontier=4
        self.assertEqual(router.choose(list(range(96000)),0)[0],'zh')
        self.assertLessEqual(len(probe.call_args.args[0]),6*16000)
        router.reset();router.detector=lambda _:dict(fr=.95,en=.02,ru=.02,zh=.01)
        selected,info=router.choose(range(32000),0)
        self.assertIsNone(selected);self.assertTrue(info['language_uncertain'])

    def test_mixed_acoustic_spans_keep_languages_times_and_reuse_only_identical_audio(self):
        import numpy as np
        wave=np.concatenate([np.ones(16000,dtype=np.float32)*.1,np.zeros(2080,dtype=np.float32),np.ones(16000,dtype=np.float32)*.2,np.zeros(2080,dtype=np.float32),np.ones(16000,dtype=np.float32)*.3])
        def detect(audio):
            peak=float(audio.max())
            language='ru' if peak<.15 else 'en' if peak<.25 else 'zh'
            return {language:.99}
        def decode(audio,**kwargs):
            text={'ru':'Сегодня','en':'regression','zh':'回归'}[kwargs['language']]
            return dict(language=kwargs['language'],segments=[dict(start=0,end=len(audio)/16000,text=text,words=[dict(start=0,end=len(audio)/16000,word=text)])])
        model=Mock(side_effect=decode);router=SpeechLanguageRouter(detect)
        result=router.decode(model,wave,10,temperature=0,word_timestamps=True,path_or_hf_repo='local')
        self.assertEqual([s['text'] for s in result['segments']],['Сегодня','regression','回归'])
        self.assertEqual(model.call_count,3)
        self.assertTrue(all(c.kwargs['task']=='transcribe' for c in model.call_args_list))
        self.assertEqual(result['segments'][0]['start'],0)
        self.assertAlmostEqual(result['segments'][-1]['end'],len(wave)/16000)
        router.decode(model,wave,10,temperature=0,word_timestamps=True,path_or_hf_repo='local')
        self.assertEqual(model.call_count,3)
        router.decode(model,wave,10,temperature=.4,word_timestamps=True,path_or_hf_repo='local')
        self.assertEqual(model.call_count,6)
        router.reset();self.assertEqual(router.decode_cache,{})

    def test_uncertain_regions_never_use_unrestricted_auto_decode(self):
        import numpy as np
        router=SpeechLanguageRouter(lambda _:dict(it=.96,en=.02,ru=.01,zh=.01))
        model=Mock()
        result=router.decode(model,np.ones(32000,dtype=np.float32)*.1,0)
        model.assert_not_called()
        self.assertEqual(result['_quality_issue'],'language_uncertain')
        self.assertEqual(result['_language_windows'][0]['rejected'],'language_uncertain')
        decoder=GuardedDecoder(model,'local',language=None,language_router=router)
        from classroom.asr_quality import RejectedRecognition
        with self.assertRaises(RejectedRecognition):decoder(np.ones(32000,dtype=np.float32)*.1)
        self.assertEqual(decoder.events[-1]['status'],'unresolved')
        self.assertEqual(decoder.events[-1]['end'],2)
        model.assert_not_called()

    def test_supported_hint_does_not_allow_unsupported_output(self):
        import numpy as np
        for lang,text in [('en','هلاشكا'),('ko','hello')]:
            router=SpeechLanguageRouter(lambda _:dict(en=.99))
            model=Mock(return_value=dict(language=lang,segments=[dict(start=0,end=1,
                text=text,words=[dict(start=0,end=1,word=text)])]))
            result=router.decode(model,np.ones(32000,dtype=np.float32)*.1,0)
            self.assertIn(result['_quality_issue'],('unsupported_script','unsupported_language'))

    def test_terms_are_opt_in_per_language_and_legacy_csv_still_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'terms.csv';p.write_text('english,chinese,asr_enabled\nregression,回归,true\nрегрессия,回归,true\n')
            g=Glossary(p)
            self.assertEqual(g.asr_hint('en'),'regression')
            self.assertEqual(g.asr_hint('ru'),'регрессия')
            self.assertIn('регрессия',g.asr_hint('mixed'))
            self.assertEqual(g.matching('讨论регрессия模型'),[('регрессия','回归')])


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_chinese_bypasses_model_and_mixed_preserves_chinese_exactly(self):
        with tempfile.TemporaryDirectory() as directory:
            t=LocalTranslator('http://unused',Glossary(Path(directory)/'none'))
            t._request=AsyncMock(side_effect=['今天','机器学习'])
            try:
                source='今天不能增加5毫克。'
                self.assertEqual(await t.translate(source),source);t._request.assert_not_awaited()
                mixed='Сегодня讨论 machine learning 的基本概念。'
                result=await t.translate(mixed)
                self.assertEqual(result,'今天讨论 机器学习 的基本概念。')
                self.assertEqual([c.args[0] for c in t._request.await_args_list],['Сегодня','machine learning'])
                self.assertTrue(all(c.kwargs['source_context']==mixed for c in t._request.await_args_list))
            finally:await t.close()

    async def test_direct_chinese_revisions_clear_queue_and_reject_inflight_old_result(self):
        with tempfile.TemporaryDirectory() as directory,patch('classroom.session.SESSIONS',Path(directory)):
            s=ClassSession(None,'file','small','test',language_mode='mixed')
            try:
                old=dict(type='segment',id='s1',start=0,end=1,source='This is not true.',revision=1,final=False)
                s.on_segment(old);self.assertIn('s1',s.queue.pending)
                new=dict(old,source='这不是真的。',revision=2,final=True)
                s.on_segment(new);latest=s.segments['s1']
                self.assertEqual(latest['translation'],new['source'])
                self.assertEqual(latest['translated_revision'],2)
                self.assertEqual(latest['translation_origin'],'source_zh')
                self.assertFalse(s.queue.pending)
                self.assertIsNone(apply_translation(latest,old,'这是旧译文。'))
                self.assertEqual(s.audit_translations(),[])
                s.store.export()
                self.assertIn('英俄中混合',(s.store.path/'双语记录.txt').read_text())
                self.assertIn(new['source'],(s.store.path/'双语字幕.srt').read_text())
                self.assertEqual(s.store.metadata['language'],'mul')
            finally:await s.translator.close();s.store.close()

    async def test_api_validates_mode_before_creating_record(self):
        from classroom.app import app,TOKEN
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8767') as c:
            with patch('classroom.app.ClassSession') as session:
                r=await c.post('/api/sessions',headers={'x-session-token':TOKEN},json={'language_mode':'invalid'})
                self.assertEqual(r.status_code,400);session.assert_not_called()

    async def test_new_ui_responses_are_not_cached(self):
        from classroom.app import app
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8767') as c:
            for path in ('/?v=0.10.2','/static/app.js?v=0.10.2'):
                response=await c.get(path)
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.headers['cache-control'],'no-store')

    async def test_api_passes_each_mode_and_defaults_legacy_requests_to_english(self):
        from classroom.app import app,TOKEN
        from types import SimpleNamespace
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8767') as c:
            for mode in ('en','ru','mixed',None):
                fake=SimpleNamespace(id='fake',run=AsyncMock())
                with patch('classroom.app.active',None),patch('classroom.app.ClassSession',return_value=fake) as ctor:
                    body={} if mode is None else {'language_mode':mode}
                    response=await c.post('/api/sessions',headers={'x-session-token':TOKEN},json=body)
                    self.assertEqual(response.status_code,200)
                    self.assertEqual(ctor.call_args.kwargs['language_mode'],mode or 'en')
                    await fake.orchestrator

    async def test_direct_russian_request_targets_chinese(self):
        with tempfile.TemporaryDirectory() as directory:
            t=LocalTranslator('http://unused',Glossary(Path(directory)/'none'))
            seen=[]
            def request(req):
                body=json.loads(req.content);seen.append(body)
                return httpx.Response(200,json={'choices':[dict(finish_reason='stop',message=dict(content='今天学习线性回归。'))]})
            await t.client.aclose();t.client=httpx.AsyncClient(transport=httpx.MockTransport(request),base_url='http://unused')
            try:
                self.assertEqual(await t.translate('Сегодня изучаем линейную регрессию.'),'今天学习线性回归。')
                self.assertIn('简体中文',seen[0]['messages'][0]['content'])
                self.assertIn('Сегодня',seen[0]['messages'][0]['content'])
            finally:await t.close()
