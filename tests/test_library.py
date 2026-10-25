"""Tests for the record library: paging, edits, trash, search and export."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
import httpx
from classroom import library
from classroom.app import app, TOKEN


def make_session(root, sid, title='新记录', **extra):
    directory = Path(root) / sid
    directory.mkdir(parents=True)
    metadata = dict(id=sid, title=title, started_at='2026-10-25T10:00:00',
                    audio_seconds=60, language_mode='en', **extra)
    metadata.setdefault('state', 'completed')
    (directory / 'session.json').write_text(json.dumps(metadata, ensure_ascii=False))
    (directory / 'events.jsonl').write_text(json.dumps({'type': 'segment', 'id': 's1', 'start': 0.0,
                                                       'end': 1.0, 'source': 'Hello world',
                                                       'translation': '你好世界'}) + '\n')
    return directory


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'sessions'
        self.root.mkdir()
        self.ids = []
        for index in range(3):
            sid = f'2026102{index}-120000-0123abcd'
            self.ids.append(sid)
            make_session(self.root, sid, title=f'第{index}节课')

    def tearDown(self):
        self.temp.cleanup()

    def test_list_sessions_paginates_and_totals(self):
        page = library.list_sessions(self.root, limit=2, offset=0)
        self.assertEqual(page['total'], 3)
        self.assertEqual(len(page['items']), 2)
        second = library.list_sessions(self.root, limit=2, offset=2)
        self.assertEqual(len(second['items']), 1)

    def test_list_sessions_filters_by_title(self):
        page = library.list_sessions(self.root, query='第1节')
        self.assertEqual(page['total'], 1)
        self.assertEqual(page['items'][0]['title'], '第1节课')

    def test_list_sessions_filters_favourites_before_paging(self):
        # A favourite must be reachable and counted even when it is not on page 1.
        make_session(self.root, '20261099-120000-0123abcd', title='收藏在第三页', favorite=True)
        page = library.list_sessions(self.root, favorite=True)
        self.assertEqual(page['total'], 1)
        self.assertEqual(page['items'][0]['title'], '收藏在第三页')
        self.assertEqual(library.list_sessions(self.root, favorite=True, limit=1, offset=1)['items'], [])

    def test_list_sessions_search_total_matches_the_filter(self):
        page = library.list_sessions(self.root, query='第1节')
        self.assertEqual(page['total'], 1)
        self.assertEqual(len(page['items']), 1)

    def test_list_sessions_review_filter_skips_finished_records(self):
        make_session(self.root, '20261098-120000-0123abcd', title='有完整性告警',
                     state='completed', integrity={'warning': '未识别区间'})
        page = library.list_sessions(self.root, review=True)
        self.assertEqual(page['total'], 1)
        self.assertEqual(page['items'][0]['title'], '有完整性告警')
        self.assertEqual(library.list_sessions(self.root)['total'], 4)

    def test_list_sessions_review_filter_skips_busy_records(self):
        # An unfinished record needs review, unless it is still running.
        make_session(self.root, '20261097-120000-0123abcd', title='还在跑', state='running')
        self.assertEqual(library.list_sessions(self.root, review=True)['total'], 1)
        page = library.list_sessions(self.root, review=True, busy_ids=('20261097-120000-0123abcd',))
        self.assertEqual(page['total'], 0)

    def test_list_sessions_reports_the_unfiltered_total(self):
        # The sidebar badge says how many records exist in all, so a filter must
        # not change it.
        make_session(self.root, '20261096-120000-0123abcd', title='收藏的一条', favorite=True)
        page = library.list_sessions(self.root, favorite=True)
        self.assertEqual(page['total'], 1)
        self.assertEqual(page['all_total'], 4)

    def test_update_metadata_is_explicit_and_bounded(self):
        directory = self.root / self.ids[0]
        metadata = library.update_metadata(directory, {'title': '  数学课  ', 'favorite': True})
        self.assertEqual(metadata['title'], '数学课')
        self.assertTrue(metadata['favorite'])
        # A field that was not requested is untouched.
        self.assertEqual(metadata['language_mode'], 'en')
        stored = json.loads((directory / 'session.json').read_text())
        self.assertEqual(stored['title'], '数学课')

    def test_trash_move_and_restore(self):
        target = library.move_to_trash(self.root, self.ids[0])
        self.assertTrue(target.is_dir())
        self.assertFalse((self.root / self.ids[0]).exists())
        self.assertEqual(len(library.list_trash(self.root)), 1)
        library.restore_from_trash(self.root, self.ids[0])
        self.assertTrue((self.root / self.ids[0]).exists())
        self.assertEqual(library.list_trash(self.root), [])

    def test_trash_leaves_other_records_alone(self):
        library.move_to_trash(self.root, self.ids[0])
        for sid in self.ids[1:]:
            self.assertTrue((self.root / sid).exists())

    def test_read_segments_search(self):
        page = library.read_segments(self.root / self.ids[0], '你好')
        self.assertEqual(page['total'], 1)
        self.assertEqual(page['items'][0]['translation'], '你好世界')
        self.assertEqual(library.read_segments(self.root / self.ids[0], 'nothing')['total'], 0)

    def test_wav_header_is_valid(self):
        header = library.wav_header(16000, 32000)
        self.assertEqual(header[:4], b'RIFF')
        self.assertEqual(header[8:12], b'WAVE')


class LibraryApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'sessions'
        self.root.mkdir()
        self.sid = '20261025-120000-0123abcd'
        make_session(self.root, self.sid, title='物理课')

    def tearDown(self):
        self.temp.cleanup()

    def client(self):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8767')

    def test_meta_edit_via_http(self):
        import asyncio
        from unittest.mock import patch

        async def run():
            with patch('classroom.app.SESSIONS', self.root):
                async with self.client() as client:
                    response = await client.post(f'/api/sessions/{self.sid}/meta',
                                                json={'title': '改过的标题', 'favorite': True},
                                                headers={'x-session-token': TOKEN})
                    self.assertEqual(response.status_code, 200)
                    stored = json.loads((self.root / self.sid / 'session.json').read_text())
                    self.assertEqual(stored['title'], '改过的标题')
                    self.assertTrue(stored['favorite'])
        asyncio.run(run())

    def test_delete_moves_to_trash_and_can_be_restored(self):
        import asyncio
        from unittest.mock import patch

        async def run():
            with patch('classroom.app.SESSIONS', self.root):
                async with self.client() as client:
                    response = await client.post(f'/api/sessions/{self.sid}/delete', json={'confirm': True},
                                                headers={'x-session-token': TOKEN})
                    self.assertEqual(response.status_code, 200)
                    self.assertFalse((self.root / self.sid).exists())
                    listing = await client.get('/api/trash')
                    self.assertEqual(len(listing.json()['items']), 1)
                    restored = await client.post(f'/api/trash/{self.sid}/restore',
                                                headers={'x-session-token': TOKEN})
                    self.assertEqual(restored.status_code, 200)
                    self.assertTrue((self.root / self.sid).exists())
        asyncio.run(run())

    def test_delete_requires_confirmation(self):
        import asyncio
        from unittest.mock import patch

        async def run():
            with patch('classroom.app.SESSIONS', self.root):
                async with self.client() as client:
                    response = await client.post(f'/api/sessions/{self.sid}/delete', json={},
                                                headers={'x-session-token': TOKEN})
                    self.assertEqual(response.status_code, 400)
                    self.assertTrue((self.root / self.sid).exists())
        asyncio.run(run())
