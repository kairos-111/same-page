"""Cross-feature regressions using local fakes only; no inference or service."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace
import numpy as np
from classroom.asr_quality import GuardedDecoder, RejectedRecognition
from classroom.asr_recovery import RecoveryGuard
from classroom.session import ClassSession
from classroom.store import check_integrity
from classroom.subtitles import SegmentComposer, Word
from test_asr_coverage import FakeOnline, Token
from test_asr_quality import hypothesis


class IntegrationTests(unittest.TestCase):
    def test_gap_is_not_translation_backlog_and_is_in_integrity(self):
        with tempfile.TemporaryDirectory() as root, patch('classroom.session.SESSIONS', Path(root)):
            session=ClassSession(None,'file','small','fake')
            session.on_segment(session.composer.reject(2,5,'repetition_loop'))
            state=session.status()
            self.assertEqual(state['asr_unresolved'],1)
            self.assertEqual(state['chinese_pending'],0)
            self.assertEqual(state['finalization_wait'],0)
            integrity=check_integrity(session.store.path,5,5)
            self.assertEqual(integrity['state'],'needs_review')
            self.assertEqual(integrity['recognition_gaps'][0]['start'],2)
            self.assertEqual(integrity['unmatched_translation_ids'],[])
            import asyncio
            asyncio.run(session.translator.close())
            session.store.close()

    def test_quality_rejection_inside_coverage_recovery_has_no_nested_retry(self):
        model=Mock(return_value=hypothesis('the value of '*30))
        decoder=GuardedDecoder(model,'local')
        decoder.offset=90
        online=FakeOnline(reset=True)
        online.asr=SimpleNamespace(transcribe=decoder,ts_words=lambda segments: [])
        online.init(offset=100)
        online.insert_audio_chunk(np.zeros(16000*11,dtype=np.float32))
        recovery=RecoveryGuard(online)
        words,_=recovery.process_iter()
        self.assertEqual(words,[])
        self.assertEqual(model.call_count,1)
        self.assertTrue(decoder.allow_recovery)
        self.assertEqual(decoder.offset,90)
        self.assertEqual(decoder.events[-1]['start'],100)
        self.assertEqual(recovery.drain_events()[-1]['action'],'gap')

    def test_backlog_disables_quality_retry(self):
        model=Mock(return_value=hypothesis('the minimize '*35))
        decoder=GuardedDecoder(model,'local')
        decoder.allow_recovery=False
        with self.assertRaises(RejectedRecognition):decoder(np.zeros(16000*10))
        self.assertEqual(model.call_count,1)
        self.assertTrue(decoder.events[-1]['recovery_skipped'])

    def test_rejection_interval_includes_unfinalized_committed_words(self):
        composer=SegmentComposer()
        composer.ingest([Word(1,2,'Earlier')], [Word(2,3,'preview')])
        gap=composer.reject(4,7,'bad_window')
        self.assertEqual(gap['start'],1)
        self.assertFalse(gap['final'])

    def test_end_audit_reads_evicted_segments_from_disk(self):
        with tempfile.TemporaryDirectory() as root, patch('classroom.session.SESSIONS', Path(root)):
            session=ClassSession(None,'file','small','fake')
            session.on_segment(dict(type='segment',id='old',revision=1,source='Final.',final=True,start=0,end=1))
            session.segments.clear()
            self.assertEqual(session.audit_translations(),['old'])
            import asyncio
            asyncio.run(session.translator.close())
            session.store.close()
