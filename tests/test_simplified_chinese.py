import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from classroom.chinese import simplified, simplified_segment
from classroom.subtitles import SegmentComposer, Word
from classroom.translation import LocalTranslator, Glossary, validate_translation
from classroom.session import ClassSession
from classroom.store import SessionStore, export_session


class ScriptTests(unittest.TestCase):
    def test_phrases_and_foreign_spans(self):
        self.assertEqual(simplified('這次訪談對我來說真的是意義非凡。頭髮發展，乾淨。'),
                         '这次访谈对我来说真的是意义非凡。头发发展，干净。')
        text='Hello, Сегодня linear regression, 20°C!'
        self.assertEqual(simplified(text),text)
        self.assertEqual(simplified('English 這是線性回歸，Привет。'), 'English 这是线性回归，Привет。')
        self.assertEqual(validate_translation('這是譯文。'),'这是译文。')

    def test_composer_normalizes_before_revision_comparison_and_retains_raw(self):
        c=SegmentComposer('mixed')
        event=c.emit([Word(0,1,'這次訪談。')],False)
        self.assertEqual(event['source'],'这次访谈。')
        self.assertEqual(event['source_raw'],'這次訪談。')
        self.assertIsNone(c.emit([Word(0,1,'这次访谈。')],False))
        self.assertEqual(event['start'],0)
        self.assertEqual(event['revision'],1)

    def test_history_is_read_projection_and_downloads_are_simplified(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            event=dict(type='segment',id='s1',source='這次訪談。',translation='這次訪談。',
                       start=0,end=1,revision=2,translated_revision=2,final=True)
            events=path/'events.jsonl';events.write_text(json.dumps(event,ensure_ascii=False)+'\n')
            (path/'session.json').write_text('{}')
            raw=events.read_bytes()
            view=SessionStore.load_segments(path)[0]
            self.assertEqual(view['source'], '这次访谈。')
            self.assertEqual(view['translation'], '这次访谈。')
            self.assertEqual(view['source_raw'],event['source'])
            files=export_session(path)
            for file in files.values():
                self.assertIn('这次访谈。',file.read_text())
                self.assertNotIn('這次訪談',file.read_text())
            self.assertEqual(events.read_bytes(),raw)
            self.assertEqual(simplified_segment(view),view)


class ScriptAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_chinese_and_mixed_still_avoid_chinese_model_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            t=LocalTranslator('http://unused',Glossary(Path(directory)/'none'))
            t._request=AsyncMock(return_value='線性回歸')
            try:
                self.assertEqual(await t.translate('這次訪談對我來說意義非凡。'),'这次访谈对我来说意义非凡。')
                t._request.assert_not_awaited()
                self.assertEqual(await t.translate('今天討論 linear regression 的意義。'),'今天讨论 线性回归 的意义。')
                t._request.assert_awaited_once()
            finally:await t.close()

    async def test_session_saves_simplified_passthrough_with_matching_version(self):
        with tempfile.TemporaryDirectory() as directory,patch('classroom.session.SESSIONS',Path(directory)):
            s=ClassSession(None,'file','small','test',language_mode='mixed')
            try:
                s.on_segment(dict(type='segment',id='s1',source='這是線性回歸。',start=0,end=1,
                                  final=True,revision=1,translation='',translated_revision=None))
                event=s.segments['s1']
                self.assertEqual(event['source'],'这是线性回归。')
                self.assertEqual(event['translation'],event['source'])
                self.assertEqual(event['translation_origin'],'source_zh')
                self.assertEqual(event['translated_revision'],event['revision'])
                self.assertFalse(s.queue.pending)
            finally:await s.translator.close();s.store.close()
