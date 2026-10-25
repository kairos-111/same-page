import unittest
from unittest.mock import Mock
from types import SimpleNamespace
import numpy as np
from classroom.asr_quality import GuardedDecoder,RecognitionPending,RejectedRecognition
from classroom.language_detection import SpeechLanguageRouter
from classroom.session import ClassSession
from classroom.asr import WhisperAdapter
from test_asr_coverage import adapter,pcm


def result(text='Normal sentence.'):
    return dict(language='en',segments=[dict(start=0,end=.8,text=text,
        words=[dict(start=0,end=.8,word=text)])])


class ContextTests(unittest.TestCase):
    def test_absolute_language_probability_is_not_transcription_rejection(self):
        r=SpeechLanguageRouter(lambda _:dict(fr=.6,en=.23,ru=.03,zh=.02))
        model=Mock(return_value=result())
        out=r.decode(model,np.ones(32000,dtype=np.float32)*.1,0)
        self.assertIsNone(out['_quality_issue'])
        self.assertTrue(out['_language_windows'][0]['uncertain'])
        self.assertEqual(model.call_args.kwargs['language'],'en')
        self.assertEqual(model.call_args.kwargs['task'],'transcribe')

    def test_prior_is_used_only_when_supported_and_cannot_override_clear_switch(self):
        hint,info=SpeechLanguageRouter.classify(dict(fr=.85,en=.06,ru=.05,zh=.04),'en')
        self.assertEqual(hint,'en');self.assertEqual(info['hint_strategy'],'stable_prior')
        self.assertEqual(SpeechLanguageRouter.classify(dict(ru=.9,en=.06,zh=.04),'en')[0],'ru')
        self.assertIsNone(SpeechLanguageRouter.classify(dict(ar=.999,en=.0001,ru=.0001,zh=.0001),'en')[0])

    def test_weak_split_merges_with_neighbour_for_actual_decode_context(self):
        def detect(audio):
            return dict(fr=.95,en=.02,ru=.02,zh=.01) if float(audio.max())<.15 else dict(en=.99)
        router=SpeechLanguageRouter(detect)
        audio=np.r_[np.full(16000,.1,dtype=np.float32),np.zeros(2080,dtype=np.float32),np.full(32000,.2,dtype=np.float32)]
        model=Mock(return_value=result())
        out=router.decode(model,audio,10)
        self.assertIsNone(out['_quality_issue']);self.assertEqual(model.call_count,1)
        self.assertEqual(len(model.call_args.args[0]),len(audio))
        self.assertEqual(model.call_args.kwargs['language'],'en')

    def test_merged_weak_region_retains_the_last_confident_acoustic_prior(self):
        def detect(audio):
            peak=float(audio.max())
            if peak<.15:return dict(en=.9)
            if peak<.25:return dict(fr=.7,en=.1,ru=.15,zh=.05)
            return dict(ru=.95)
        router=SpeechLanguageRouter(detect)
        router.language='ru'
        audio=np.r_[np.full(16000,.1,dtype=np.float32),np.zeros(2080,dtype=np.float32),
            np.full(16000,.2,dtype=np.float32),np.zeros(2080,dtype=np.float32),np.full(32000,.3,dtype=np.float32)]
        router.decode(Mock(side_effect=lambda a,**k:dict(result(),language=k['language'])),audio,0)
        self.assertEqual(router.language,'ru')

    def test_bad_short_output_waits_and_new_audio_can_recover_without_gap(self):
        model=Mock(side_effect=[result('the value of '*20),result('A valid sentence.')])
        decoder=GuardedDecoder(model,'local');decoder.wait_for_context=True
        with self.assertRaises(RecognitionPending):decoder(np.ones(16000))
        self.assertEqual(model.call_count,1)
        self.assertEqual(decoder.events[-1]['type'],'asr_waiting')
        self.assertEqual(decoder(np.ones(32000))[0]['text'],'A valid sentence.')
        self.assertIsNone(decoder.pending_end)
        self.assertFalse(any(e.get('status')=='unresolved' for e in decoder.events))
        self.assertEqual(decoder.events[-1]['recovery'],'additional_context')

    def test_context_wait_is_bounded_and_finish_backlog_never_defer(self):
        decoder=GuardedDecoder(Mock(return_value=result('the value of '*20)),'local')
        decoder.wait_for_context=True
        with self.assertRaises(RecognitionPending):decoder(np.ones(16000))
        with self.assertRaises(RejectedRecognition):decoder(np.ones(49600))
        self.assertEqual(decoder.model.call_count,3)
        for finalizing,allow in ((True,True),(False,False)):
            decoder=GuardedDecoder(Mock(return_value=result('the value of '*20)),'local')
            decoder.wait_for_context=True;decoder.finalizing=finalizing;decoder.allow_recovery=allow
            with self.assertRaises(RejectedRecognition):decoder(np.ones(16000))
            self.assertFalse(any(e['type']=='asr_waiting' for e in decoder.events))

    def test_adapter_keeps_audio_and_session_does_not_flush_pending(self):
        a=adapter(.9)
        a.online.process_iter=Mock(side_effect=RecognitionPending)
        original_init=a.online.init;a.online.init=Mock(wraps=original_init)
        out=a.process(pcm(1,500),1)
        self.assertTrue(out[3]['waiting']);self.assertFalse(out[3]['rejected'])
        self.assertTrue(a.active);self.assertEqual(len(a.online.audio_buffer),16000)
        a.online.init.assert_called_once_with(offset=0)
        session=ClassSession.__new__(ClassSession);session.composer=Mock();session.event=Mock()
        session.accept_asr_result(out);session.composer.ingest.assert_not_called()
