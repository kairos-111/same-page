"""Recording kept once, served as one seekable WAV, and fast file import."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

import httpx

from classroom import library, store
from classroom.app import app
from classroom.session import ClassSession


def samples(count, start=0):
    """Distinct 16-bit samples so misplaced bytes are detectable."""
    return b''.join(((start + i) % 30000).to_bytes(2, 'little', signed=True) for i in range(count))


def write_wav(path, data):
    with wave.open(str(path), 'wb') as handle:
        handle.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        handle.writeframes(data)


class ExportTests(unittest.TestCase):
    def test_export_merges_parts_into_one_wav_and_drops_the_spool(self):
        with tempfile.TemporaryDirectory() as root, patch.object(store, 'CHUNK_BYTES', 3200):
            journal = store.AudioJournal(Path(root))
            data = samples(4000)  # 8000 bytes -> three spool parts of 3200 bytes
            journal.append(data)
            journal.close()
            self.assertEqual(len(list(Path(root).glob('audio_*.pcm'))), 3)
            target = journal.export_wav()
            self.assertEqual(target.name, 'audio_0000.wav')
            self.assertEqual(sorted(p.name for p in Path(root).iterdir()), ['audio_0000.wav'])
            with wave.open(str(target), 'rb') as handle:
                self.assertEqual(handle.getnframes(), 4000)
                self.assertEqual(handle.readframes(4000), data)

    def test_export_replaces_older_per_part_wavs(self):
        with tempfile.TemporaryDirectory() as root, patch.object(store, 'CHUNK_BYTES', 3200):
            journal = store.AudioJournal(Path(root))
            journal.append(samples(3000))
            journal.close()
            write_wav(Path(root) / 'audio_0001.wav', samples(10))  # stale export of the same audio
            journal.export_wav()
            self.assertEqual(sorted(p.name for p in Path(root).iterdir()), ['audio_0000.wav'])

    def test_export_without_audio_does_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            journal = store.AudioJournal(Path(root))
            journal.close()
            self.assertIsNone(journal.export_wav())
            self.assertEqual(list(Path(root).iterdir()), [])


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.parts = [samples(1000, 0), samples(700, 1000), samples(333, 1700)]

    def tearDown(self):
        self.temp.cleanup()

    def full(self, stream):
        return b''.join(stream.iter_range(0, stream.size - 1))

    def test_old_five_minute_wavs_become_one_wav_with_one_header(self):
        files = []
        for index, data in enumerate(self.parts):
            files.append(self.root / f'audio_{index:04}.wav')
            write_wav(files[-1], data)
        stream = library.RecordingStream(files)
        body = self.full(stream)
        self.assertEqual(len(body), stream.size)
        self.assertEqual(body.count(b'RIFF'), 1)
        self.assertEqual(body[44:], b''.join(self.parts))
        out = self.root / 'joined.wav'
        out.write_bytes(body)
        with wave.open(str(out), 'rb') as handle:
            self.assertEqual(handle.getnframes(), 2033)

    def test_pcm_spool_wins_over_wav_copies_so_audio_is_not_doubled(self):
        files = []
        for index, data in enumerate(self.parts):
            (self.root / f'audio_{index:04}.pcm').write_bytes(data)
            write_wav(self.root / f'audio_{index:04}.wav', data)
            files += [self.root / f'audio_{index:04}.pcm', self.root / f'audio_{index:04}.wav']
        stream = library.RecordingStream(files)
        self.assertEqual(self.full(stream)[44:], b''.join(self.parts))

    def test_any_byte_range_matches_the_whole_file(self):
        files = []
        for index, data in enumerate(self.parts):
            files.append(self.root / f'audio_{index:04}.pcm')
            files[-1].write_bytes(data)
        stream = library.RecordingStream(files)
        whole = self.full(stream)
        for start, end in [(0, 0), (0, 43), (40, 50), (44, 2043), (2043, 2044), (2000, 3500), (stream.size - 1, stream.size - 1)]:
            self.assertEqual(b''.join(stream.iter_range(start, end)), whole[start:end + 1], (start, end))

    def test_parse_range(self):
        self.assertIsNone(library.parse_range(None, 100))
        self.assertIsNone(library.parse_range('items=1-2', 100))
        self.assertEqual(library.parse_range('bytes=0-', 100), (0, 99))
        self.assertEqual(library.parse_range('bytes=10-19', 100), (10, 19))
        self.assertEqual(library.parse_range('bytes=90-500', 100), (90, 99))
        self.assertEqual(library.parse_range('bytes=-10', 100), (90, 99))
        with self.assertRaises(ValueError):
            library.parse_range('bytes=100-', 100)


class EndpointTests(unittest.TestCase):
    sid = '20261025-120000-0123abcd'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        directory = self.root / self.sid
        directory.mkdir()
        (directory / 'session.json').write_text(json.dumps(dict(id=self.sid, state='completed', saved=True)))
        (directory / 'events.jsonl').write_text('')
        self.data = samples(5000)
        write_wav(directory / 'audio_0000.wav', self.data[:6000])
        write_wav(directory / 'audio_0001.wav', self.data[6000:])

    def tearDown(self):
        self.temp.cleanup()

    def request(self, method, headers=None):
        async def run():
            with patch('classroom.app.SESSIONS', self.root):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8767') as client:
                    return await client.request(method, f'/api/sessions/{self.sid}/recording', headers=headers or {})
        return asyncio.run(run())

    def test_full_and_partial_requests(self):
        whole = self.request('GET')
        self.assertEqual(whole.status_code, 200)
        self.assertEqual(whole.headers['accept-ranges'], 'bytes')
        self.assertEqual(len(whole.content), 44 + len(self.data))
        self.assertEqual(whole.content[44:], self.data)
        part = self.request('GET', {'Range': 'bytes=5000-6099'})
        self.assertEqual(part.status_code, 206)
        self.assertEqual(part.headers['content-range'], f'bytes 5000-6099/{44 + len(self.data)}')
        self.assertEqual(part.content, whole.content[5000:6100])

    def test_head_and_unsatisfiable_range(self):
        head = self.request('HEAD')
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.headers['content-length'], str(44 + len(self.data)))
        bad = self.request('GET', {'Range': 'bytes=999999-'})
        self.assertEqual(bad.status_code, 416)


class FastImportTests(unittest.IsolatedAsyncioTestCase):
    def session(self, root, source, realtime):
        with patch('classroom.session.SESSIONS', Path(root)):
            return ClassSession(object(), source, 'small', 'fake', realtime=realtime)

    async def test_only_files_without_pacing_are_fast_imports(self):
        with tempfile.TemporaryDirectory() as root:
            cases = [(self.session(root, 'file', False), True), (self.session(root, 'file', True), False),
                     (self.session(root, 'microphone', False), False)]
            for session, expected in cases:
                self.assertEqual(session.fast_import, expected)
                session.store.close()

    async def test_stopping_a_fast_import_ends_recognition_where_it_stands(self):
        with tempfile.TemporaryDirectory() as root:
            session = self.session(root, 'file', False)
            session.store.audio.append(samples(16000 * 3))
            session.processed = 1.0
            session.state = 'running'
            done = asyncio.get_running_loop().create_future()
            done.set_result(None)
            session.source_task = done
            session.orchestrator = asyncio.create_task(asyncio.sleep(0))
            await session.stop()
            self.assertEqual(session.recognize_limit, 32000)
            self.assertEqual(session.recognition_end(), 32000)
            status = session.status()
            self.assertTrue(status['fast_import'])
            self.assertTrue(status['input_done'])
            self.assertEqual(status['processed_seconds'], 1.0)
            session.store.close()

    async def test_live_stop_still_drains_all_received_audio(self):
        with tempfile.TemporaryDirectory() as root:
            session = self.session(root, 'microphone', True)
            session.store.audio.append(samples(16000))
            session.state = 'running'
            done = asyncio.get_running_loop().create_future()
            done.set_result(None)
            session.source_task = done
            session.orchestrator = asyncio.create_task(asyncio.sleep(0))
            await session.stop()
            self.assertIsNone(session.recognize_limit)
            self.assertEqual(session.recognition_end(), 32000)
            session.store.close()


if __name__ == '__main__':
    unittest.main()
