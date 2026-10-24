import asyncio
from dataclasses import dataclass, replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from classroom.asr import WhisperAdapter
from classroom.asr_recovery import RecoveryGuard
from classroom.session import ClassSession
from classroom.store import SessionStore, check_integrity, export_session


@dataclass
class Token:
    start: float
    end: float
    text: str

    def with_offset(self, offset):
        return replace(self, start=self.start+offset, end=self.end+offset)


class FakeASR:
    def __init__(self, outputs=None):
        self.outputs = list(outputs or [])
        self.calls = 0
        self.sizes = []

    def transcribe(self, audio, init_prompt=''):
        self.calls += 1
        self.sizes.append(len(audio))
        return self.outputs.pop(0) if self.outputs else []

    def ts_words(self, result):
        return result


class FakeOnline:
    def __init__(self, asr=None, reset=True):
        self.asr = asr or FakeASR()
        self.reset = reset
        self.next_words = []
        self.init()

    def init(self, offset=0):
        self.audio_buffer = np.array([], dtype=np.float32)
        self.buffer_time_offset = offset
        self.transcript_buffer = SimpleNamespace(buffer=[], last_committed_time=offset)
        self.committed = []

    def get_audio_buffer_end_time(self):
        return self.buffer_time_offset+len(self.audio_buffer)/16000

    def insert_audio_chunk(self, audio):
        self.audio_buffer = np.append(self.audio_buffer,audio)

    def process_iter(self):
        end = self.get_audio_buffer_end_time()
        if self.reset:
            self.init(offset=end)
            return [], end
        words, self.next_words = self.next_words, []
        if hasattr(self,'auto_partial'):
            self.transcript_buffer.buffer=list(self.auto_partial)
        return words, end

    def finish(self):
        words, self.transcript_buffer.buffer = self.transcript_buffer.buffer, []
        return words,self.get_audio_buffer_end_time()


def adapter(probability=.0, online=None):
    a = WhisperAdapter.__new__(WhisperAdapter)
    a.np=np
    a.torch=SimpleNamespace(from_numpy=lambda frame: frame)
    a.vad=lambda frame,rate: SimpleNamespace(item=lambda: probability)
    a.online=online or FakeOnline(reset=False)
    a.decoder=SimpleNamespace(events=[], offset=0, allow_recovery=True)
    a.recovery=RecoveryGuard(a.online)
    a.active=False
    a.silence=0
    a.preroll=b''
    a.end_seconds=a.run_start=a.run_frontier=a.last_speech_end=0
    return a


def pcm(seconds, value=0):
    return np.full(round(seconds*16000),value,dtype='<i2').tobytes()


class RecoveryTests(unittest.TestCase):
    def test_installed_online_processor_reset_with_fake_decoder(self):
        # Exercise installed WLK reset/offset behavior, without constructing a model.
        from whisperlivekit.local_agreement.online_asr import OnlineASRProcessor
        tokens=[Token(.1,1,'Recovered.')]
        asr=FakeASR([[],tokens,tokens])
        asr.tokenizer=None
        asr.confidence_validation=False
        asr.buffer_trimming='segment'
        asr.buffer_trimming_sec=10
        asr.sep=' '
        online=OnlineASRProcessor(asr)
        online.init(offset=100)
        guard=RecoveryGuard(online)
        online.insert_audio_chunk(np.zeros(16000*11,dtype=np.float32))
        words,_=guard.process_iter()
        self.assertEqual([w.text for w in words],['Recovered.'])
        self.assertAlmostEqual(words[0].start,100.1)
        self.assertEqual(online.buffer_time_offset,111)
        self.assertEqual(asr.calls,3)
        self.assertEqual(guard.drain_events()[-1]['action'],'recovered')

    def test_reset_recovers_before_destroy_and_repeated_submission_is_deduplicated(self):
        tokens=[Token(0,1,'quiet'),Token(1,2,'start.')]
        online=FakeOnline(FakeASR([tokens,tokens]))
        guard=RecoveryGuard(online)
        online.insert_audio_chunk(np.ones(16000*11))
        words,_=guard.process_iter()
        self.assertEqual([w.text for w in words],['quiet','start.'])
        self.assertEqual(guard.accept(words),[])
        self.assertEqual([e['action'] for e in guard.drain_events()],['reset','recovery_attempt','recovered'])
        self.assertEqual(online.buffer_time_offset,11)
        self.assertEqual(len(online.audio_buffer),0)

    def test_window_budget_and_failures_are_bounded_with_absolute_intervals(self):
        online=FakeOnline()
        guard=RecoveryGuard(online)
        for _ in range(3):
            online.insert_audio_chunk(np.ones(16000*20))
            guard.process_iter()
        events=guard.drain_events()
        self.assertEqual(online.asr.calls,4)
        self.assertLessEqual(max(online.asr.sizes),16000*15)
        self.assertTrue(any(e.get('reason')=='recovery_attempt_limit' and e['end']==60 for e in events))
        self.assertTrue(any(e.get('reason')=='recovery_window_limit' and e['start']==0 and e['end']==5 for e in events))
        self.assertFalse(guard.running)

    def test_backlog_skips_extra_decodes_but_leaves_localizable_gap(self):
        online=FakeOnline()
        a=adapter(online=online)
        a.set_backlog(15)
        online.insert_audio_chunk(np.ones(16000*11))
        self.assertEqual(a.recovery.process_iter()[0],[])
        self.assertEqual(online.asr.calls,0)
        self.assertEqual(a.drain_events()[-1]['reason'],'recovery_skipped_for_backlog')
        a.set_backlog(1)
        self.assertTrue(a.recovery.allow_recovery)

    def test_unstable_decode_does_not_confirm_partial_words(self):
        online=FakeOnline(FakeASR([[Token(0,1,'not')],[Token(0,1,'now')]]))
        guard=RecoveryGuard(online)
        online.insert_audio_chunk(np.ones(16000*11))
        self.assertEqual(guard.process_iter()[0],[])
        self.assertEqual(guard.drain_events()[-1]['reason'],'recovery_failed')

    def test_normal_progress_replenishes_budget_and_trimming_does_not_trigger_recovery(self):
        online=FakeOnline(reset=False)
        guard=RecoveryGuard(online)
        guard.attempts=2
        online.next_words=[Token(0,1,'word')]
        online.insert_audio_chunk(np.ones(16000))
        self.assertEqual(len(guard.process_iter()[0]),1)
        self.assertEqual(guard.attempts,0)
        self.assertEqual(online.asr.calls,0)
        self.assertEqual(guard.drain_events(),[])

    def test_silence_preroll_onset_pause_and_repeated_finish(self):
        a=adapter()
        a.process(pcm(2),2)
        self.assertEqual(len(a.preroll),24000)
        self.assertFalse(any(e['action']=='gap' for e in a.drain_events()))
        a.vad=lambda f,r: SimpleNamespace(item=lambda: .3)
        a.online.next_words=[Token(1.3,1.5,'Soft')]
        committed=a.process(pcm(1,100),3)[0]
        self.assertEqual(committed[0].text,'Soft')
        self.assertEqual(a.online.buffer_time_offset,1.25)
        self.assertEqual(len(a.online.audio_buffer),28000)
        self.assertTrue(any(e['action']=='preroll_replay' for e in a.drain_events()))
        a.online.transcript_buffer.buffer=[Token(2.5,2.9,'voice.')]
        a.vad=lambda f,r: SimpleNamespace(item=lambda: 0)
        self.assertFalse(a.process(pcm(.5),3.5)[2])
        words,_,flush=a.process(pcm(.5),4)[:3]
        self.assertTrue(flush)
        self.assertEqual([w.text for w in words],['voice.'])
        self.assertEqual(a.finish(),([],[],True))
        self.assertFalse(any(e['action']=='gap' for e in a.drain_events()))

    def test_noise_without_vad_candidate_stays_skip_and_empty_speech_is_reviewable(self):
        a=adapter(.1)
        a.process(pcm(1,2000),1)
        self.assertFalse(a.active)
        self.assertEqual(a.drain_events()[0]['action'],'vad_skip')
        a.vad=lambda f,r: SimpleNamespace(item=lambda: .8)
        a.process(pcm(1,100),2)
        a.finish()
        self.assertTrue(any(e.get('reason')=='speech_candidate_without_words' for e in a.drain_events()))

    def test_silent_tail_inside_speech_chunk_is_not_a_recognition_gap(self):
        a=adapter()
        calls=iter([.8]+[0]*100)
        a.vad=lambda f,r: SimpleNamespace(item=lambda: next(calls,0))
        a.online.auto_partial=[Token(0,.032,'Short.')]
        a.process(pcm(3,100),3)
        a.finish()
        self.assertFalse(any(e['action']=='gap' for e in a.drain_events()))

    def test_stop_flush_accepts_tail_once(self):
        a=adapter(.8)
        a.process(pcm(1,100),1)
        a.online.transcript_buffer.buffer=[Token(.1,.8,'Tail.')]
        self.assertEqual([w.text for w in a.finish()[0]],['Tail.'])
        self.assertEqual(a.finish()[0],[])


class IntegrityTests(unittest.TestCase):
    def test_all_disk_segments_checked_and_txt_srt_share_matching_rule(self):
        with tempfile.TemporaryDirectory() as root:
            store=SessionStore(Path(root)/'s',{})
            for i,(final,translation,revision) in enumerate([(True,'旧',1),(True,'好',2),(False,'预览',2),(True,'',None)]):
                store.append(dict(type='segment',id=f's{i}',start=i,end=i+1,source='Word.',revision=2,
                    final=final,translation=translation,translated_revision=revision))
            store.append(dict(type='asr_coverage',action='vad_skip',start=0,end=1))
            store.append(dict(type='asr_coverage',action='gap',start=1,end=2,reason='recovery_failed'))
            integrity=check_integrity(store.path,6,5)
            self.assertEqual(integrity['state'],'needs_review')
            self.assertEqual(len(integrity['recognition_gaps']),2)
            self.assertEqual(integrity['unmatched_translation_ids'],['s0','s2','s3'])
            store.metadata['integrity']=integrity
            store.save_metadata()
            store.export()
            txt=(store.path/'双语记录.txt').read_text()
            srt=(store.path/'双语字幕.srt').read_text()
            self.assertIn('译文过期',txt)
            self.assertIn('译文预览匹配',txt)
            self.assertIn('译文缺失',txt)
            self.assertNotIn('已确认',txt)
            self.assertEqual(srt.count('-->'),1)
            self.assertIn('好',srt)
            store.close()

    def test_advanced_cursor_without_asr_finalization_is_incomplete(self):
        with tempfile.TemporaryDirectory() as root:
            store=SessionStore(Path(root)/'s',{})
            integrity=check_integrity(store.path,2,2,asr_flushed=False)
            self.assertEqual(integrity['state'],'needs_review')
            self.assertEqual(integrity['recognition_gaps'][0]['reason'],'asr_finalize_incomplete')
            store.close()

    def test_history_export_does_not_invent_integrity_metadata(self):
        with tempfile.TemporaryDirectory() as root:
            store=SessionStore(Path(root)/'old',{'state':'completed'})
            before=(store.path/'session.json').read_bytes()
            (store.path/'双语记录.txt').write_text('legacy evidence')
            files=export_session(store.path)
            self.assertEqual((store.path/'双语记录.txt').read_text(),'legacy evidence')
            self.assertEqual(files['txt'].name,'双语记录-核对版.txt')
            self.assertEqual(before,(store.path/'session.json').read_bytes())
            self.assertIn('尚无结束完整性核对',files['txt'].read_text())
            store.close()


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_end_to_end_fake_capture_stop_drain_and_export(self):
        class Runtime:
            async def ensure_translation(self):pass
            async def load_asr(self,*args):
                a=adapter(.8)
                a.online.auto_partial=[Token(.1,.8,'Tail.')]
                return a
            async def thread(self,fn,*args):return fn(*args)
        class Translator:
            def __init__(self,*args):pass
            async def translate(self,*args):return '中文'
            async def close(self):pass
        with tempfile.TemporaryDirectory() as root, patch('classroom.session.SESSIONS',Path(root)), patch('classroom.session.LocalTranslator',Translator):
            session=ClassSession(Runtime(),'file','small','fake',realtime=False)
            async def capture():
                session.append_audio(pcm(1,100))
                session.input_done=True
            session.capture=capture
            await session.run()
            metadata=json.loads((session.store.path/'session.json').read_text())
            self.assertEqual(metadata['state'],'completed')
            self.assertEqual(metadata['integrity']['state'],'checked')
            self.assertEqual(metadata['processed_seconds'],1)
            self.assertEqual((session.store.path/'双语字幕.srt').read_text().count('-->'),1)
            self.assertTrue((session.store.path/'audio_0000.wav').exists())
