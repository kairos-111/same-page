import asyncio
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from classroom.store import AudioJournal, SessionStore, srt_time, export_session
from classroom.subtitles import SegmentComposer, Word

class CoreTests(unittest.TestCase):
    def test_audio_spills_across_file_boundary_without_loss(self):
        with tempfile.TemporaryDirectory() as d, patch('classroom.store.CHUNK_BYTES',32):
            journal=AudioJournal(d)
            data=bytes(range(100))
            journal.append(data[:18]);journal.append(data[18:])
            self.assertEqual(journal.read(0,100),data)
            self.assertEqual(journal.read(12,24),data[24:72])
            journal.close()

    def test_revisions_keep_id_and_confirm_only_committed_words(self):
        c=SegmentComposer()
        words=[Word(0,1,'It'),Word(1,2,' does'),Word(2,3,' not')]
        draft=c.ingest([],words)[0]
        self.assertFalse(draft['final'])
        more=words+[Word(3,4,' increase.')]
        confirmed=c.ingest(more)[0]
        self.assertEqual(draft['id'],confirmed['id'])
        self.assertTrue(confirmed['final'])
        self.assertIn('not',confirmed['source'])
        self.assertGreater(confirmed['revision'],draft['revision'])

    def test_two_sentences_from_one_asr_update_have_unique_ids(self):
        c=SegmentComposer()
        events=c.ingest([Word(0,1,'First.'),Word(1,2,'Second.')])
        self.assertEqual(len(events),2)
        self.assertNotEqual(events[0]['id'],events[1]['id'])

    def test_long_sentence_splits_at_clause_instead_of_next_clause_first_word(self):
        c=SegmentComposer()
        words=[Word(i*.6,(i+1)*.6,' word') for i in range(14)]
        words[-1].text=' you,'
        words.append(Word(8.4,9,' ask'))
        events=c.ingest(words)
        self.assertTrue(events[0]['source'].endswith('you,'))
        self.assertEqual(events[1]['source'],'ask')

    def test_srt_time_carries_milliseconds(self):
        self.assertEqual(srt_time(59.9999),'00:01:00,000')

    def test_word_timestamps_preserve_decimal_without_inserting_space(self):
        words=[Word(0,.2,' 3'),Word(.2,.4,'.5'),Word(.4,.6,' milligrams.')]
        self.assertEqual(SegmentComposer.text(words),'3.5 milligrams.')

    def test_recovery_ignores_truncated_event_and_stale_translation(self):
        with tempfile.TemporaryDirectory() as root:
            s=SessionStore(Path(root)/'session',{'name':'test'})
            s.append(dict(type='segment',id='s1',start=0,end=1,source='Not true.',final=True,revision=2,translation='旧译文',translated_revision=1))
            s.events.write('{broken');s.close()
            export_session(s.path)
            self.assertEqual((s.path/'双语字幕.srt').read_text(),'')
            self.assertIn('Not true.',(s.path/'双语记录.txt').read_text())

if __name__=='__main__':unittest.main()
