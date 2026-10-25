import tempfile
from collections import OrderedDict, deque
from pathlib import Path
import unittest
from unittest.mock import Mock
from types import SimpleNamespace

from classroom.asr import WhisperAdapter
from classroom.asr_quality import GuardedDecoder, RejectedRecognition, text_issue, words_issue, segments_issue
from classroom.subtitles import SegmentComposer, Word
from classroom.translation import Glossary, RevisionQueue, apply_translation
from classroom.session import ClassSession
from classroom.store import SessionStore, export_session


def hypothesis(text, step=.2):
    words = text.split()
    return {'segments': [dict(start=0, end=len(words)*step, text=text,
        words=[dict(start=i*step, end=(i+1)*step, word=w) for i,w in enumerate(words)])]}


class QualityTests(unittest.TestCase):
    def test_known_failure_patterns(self):
        for text in ('the minimize, '*75, 'the value of '*80,
                     'A valid introduction. '+'the minimize, '*75+' And another clause.'):
            self.assertEqual(text_issue(text), 'repetition_loop')

    def test_normal_emphasis_stutter_quotes_lists_and_decimal(self):
        for text in ('No, no, no. This is very, very important.',
                     'I I I mean that it is not true.',
                     'To be or not to be. To be or not to be.',
                     'the value of A, the value of B, the value of C, the value of D',
                     '3.5 milligrams, not 35 milligrams.',
                     ' '.join('item '+str(i) for i in range(100))):
            self.assertIsNone(text_issue(text))

    def test_threshold_boundaries_and_output_limit(self):
        self.assertIsNone(text_issue('yes '*23))
        self.assertEqual(text_issue('yes '*24), 'repetition_loop')
        self.assertEqual(text_issue('x'*32769), 'output_limit')
        self.assertIsNone(text_issue('the value of '*7))
        self.assertEqual(text_issue('the value of '*8), 'repetition_loop')
        self.assertEqual(text_issue(' '.join(str(i) for i in range(4097))), 'output_limit')

    def test_timestamp_boundaries(self):
        self.assertIsNone(words_issue([Word(0, 0, 'I'), Word(0,.3,'agree')], 1))
        for words in ([Word(float('nan'),1,'a')], [Word(1,.5,'a')],
                      [Word(0,3,'a')], [Word(0,1,'a'),Word(.8,1.2,'b')]):
            self.assertIsNotNone(words_issue(words, 2))
        collapsed=[Word(0,0,str(i)) for i in range(12)]
        self.assertEqual(words_issue(collapsed, 2), 'collapsed_timestamps')

    def test_segment_timestamps_and_missing_words(self):
        self.assertIsNotNone(segments_issue([dict(start=0,end=4,text='abc',words=[])], 2))
        self.assertEqual(segments_issue([dict(start=0,end=1,text='abc')], 2), 'missing_word_timestamps')
        self.assertIsNone(segments_issue([], 2))

    def test_good_result_costs_one_call(self):
        model=Mock(return_value=hypothesis('This is a normal sentence.'))
        decoder=GuardedDecoder(model,'local')
        result=decoder(range(320000))
        self.assertEqual(result[0]['text'], 'This is a normal sentence.')
        self.assertEqual(model.call_count, 1)
        self.assertEqual(len(decoder.events), 1)
        timing=decoder.events[0]
        self.assertEqual(timing['type'], 'asr_decode')
        self.assertEqual(timing['audio_seconds'], 20)
        self.assertGreaterEqual(timing['seconds'], 0)
        self.assertFalse(timing['recovery'])

    def test_recovery_uses_fresh_neutral_prompt_and_validates(self):
        model=Mock(side_effect=[hypothesis('the value of '*30),hypothesis('A fresh valid sentence.')])
        decoder=GuardedDecoder(model,'local','Topics include eigenvalue.')
        decoder.offset=100
        result=decoder(range(320000),'old context')
        self.assertEqual(result[0]['text'],'A fresh valid sentence.')
        first, second=model.call_args_list
        self.assertIn('eigenvalue',first.kwargs['initial_prompt'])
        self.assertEqual(second.kwargs['initial_prompt'],'An English lecture.')
        self.assertEqual(second.kwargs['temperature'],.4)
        self.assertFalse(second.kwargs['condition_on_previous_text'])
        timings=[e for e in decoder.events if e['type']=='asr_decode']
        self.assertEqual([e['recovery'] for e in timings],[False,True])
        recovery=decoder.events[-1]
        self.assertEqual(recovery['status'],'recovered')
        self.assertEqual(recovery['start'],100)
        self.assertEqual(recovery['end'],120)

    def test_recovery_exhaustion_empty_error_and_duration_cap(self):
        bad=hypothesis('the minimize '*35)
        silent=hypothesis('This is hidden by the backend.')
        silent['segments'][0]['no_speech_prob']=.99
        for recovery in (bad, {'segments':[]}, silent, RuntimeError('fake failure')):
            model=Mock(side_effect=[bad,recovery])
            decoder=GuardedDecoder(model,'local')
            with self.assertRaises(RejectedRecognition):decoder(range(320000))
            self.assertEqual(model.call_count,2)
            self.assertEqual(decoder.events[-1]['status'],'unresolved')
            self.assertEqual(len(decoder.events[-1]['attempts']),2)
        model=Mock(return_value=bad)
        decoder=GuardedDecoder(model,'local')
        with self.assertRaises(RejectedRecognition):decoder(range(16000*31))
        self.assertEqual(model.call_count,1)
        self.assertTrue(decoder.events[-1]['recovery_skipped'])

    def test_adapter_finish_never_flushes_rejected_hypothesis(self):
        adapter=WhisperAdapter.__new__(WhisperAdapter)
        adapter.decoder=GuardedDecoder(Mock(return_value=hypothesis('the minimize '*35)),'local')
        adapter.active=True
        online=Mock()
        online.buffer_time_offset=12
        online.get_audio_buffer_end_time.return_value=32
        online.process_iter.side_effect=lambda:adapter.decoder(range(320000))
        adapter.online=online
        adapter.end_seconds=32
        adapter.recovery=SimpleNamespace(process_iter=online.process_iter)
        result=adapter.finish()
        self.assertEqual(result[:3],([],[],False))
        self.assertTrue(result[3]['rejected'])
        online.finish.assert_not_called()
        online.init.assert_called_once_with(offset=32)
        self.assertFalse(adapter.active)

    def test_composer_checks_whole_update_before_punctuation_splits(self):
        c=SegmentComposer()
        c.ingest([], [Word(0,1,'A valid preview')])
        bad=[Word(i*.02,(i+1)*.02,w) for i,w in enumerate(('the minimize, '*75).split())]
        events=c.ingest(bad,flush=True)
        self.assertEqual(len(events),1)
        self.assertFalse(events[0]['final'])
        self.assertEqual(events[0]['asr_status'],'unresolved')
        self.assertEqual(events[0]['revision'],2)
        self.assertNotIn('minimize', events[0]['source'])
        fresh=c.ingest([Word(4,5,'Next sentence.')])[0]
        self.assertTrue(fresh['final'])
        self.assertNotEqual(fresh['id'],events[0]['id'])

    def test_queue_and_inflight_results_cannot_translate_rejected_gap(self):
        c=SegmentComposer()
        draft=c.ingest([], [Word(0,1,'A valid preview')])[0]
        gap=c.reject(0,10,'repetition_loop')
        q=RevisionQueue();q.put(gap)
        self.assertFalse(q.pending)
        self.assertIsNone(apply_translation(gap,draft,'stale translation'))

    def test_session_rejection_persists_evidence_cancels_pending_and_context(self):
        s=ClassSession.__new__(ClassSession)
        s.composer=SegmentComposer();s.queue=RevisionQueue()
        draft=s.composer.ingest([], [Word(0,1,'A valid preview')])[0]
        s.queue.put(draft)
        s.segments=OrderedDict({draft['id']:draft});s.context=deque();s.asr_unresolved=0
        s.event=Mock()
        failure=dict(type='asr_quality',status='unresolved',start=0,end=10,
                     attempts=[dict(reason='repetition_loop')])
        s.accept_asr_result(([],[],False,dict(events=[failure],rejected=True)))
        self.assertEqual(s.event.call_args_list[0].args[0],failure)
        self.assertFalse(s.queue.pending);self.assertFalse(s.context)
        gap=s.segments[draft['id']]
        self.assertEqual(gap['asr_status'],'unresolved')
        self.assertEqual(s.asr_unresolved,1)
        s.retry(gap['id']);self.assertFalse(s.queue.pending)

    def test_gap_export_is_explicit_and_never_srt(self):
        with tempfile.TemporaryDirectory() as root:
            store=SessionStore(Path(root)/'session',{})
            store.append(SegmentComposer().reject(3,7,'repetition_loop'))
            store.close();export_session(store.path)
            self.assertIn('识别异常', (store.path/'双语记录.txt').read_text())
            self.assertNotIn('[待翻译]', (store.path/'双语记录.txt').read_text())
            self.assertEqual((store.path/'双语字幕.srt').read_text(),'')

    def test_glossary_requires_explicit_asr_opt_in_without_deleting_terms(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'terms.csv'
            path.write_text('english,chinese\neigenvalue,特征值\n')
            g=Glossary(path)
            self.assertEqual(g.asr_hint(),'')
            self.assertEqual(g.matching('an eigenvalue'),[('eigenvalue','特征值')])
            path.write_text('english,chinese,asr_enabled\neigenvalue,特征值,\nethics,伦理,true\nlogic,逻辑,yes\n')
            g=Glossary(path)
            self.assertEqual(g.asr_hint(),'ethics, logic')
            self.assertEqual(len(g.terms),3)


if __name__=='__main__':unittest.main()
