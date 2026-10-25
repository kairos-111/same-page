"""Data page figures: derived read-only from a record's events, honest about missing data."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from classroom import metrics
from classroom.app import app


def write_record(root, sid, events, **meta):
    directory = Path(root) / sid
    directory.mkdir(parents=True)
    base = dict(id=sid, title='第一讲', started_at='2026-10-25T10:00:00+07:00', state='completed', saved=True,
                source='microphone', model='small', language_mode='en', audio_seconds=120)
    base.update(meta)
    (directory / 'session.json').write_text(json.dumps(base, ensure_ascii=False))
    (directory / 'events.jsonl').write_text('\n'.join(json.dumps(e, ensure_ascii=False) for e in events) + '\n')
    return directory


def status(t, lag=0.5, idle=0.1, chinese=0, memory=2 * 1024 ** 3, state='running'):
    return dict(type='status', state=state, audio_seconds=t, asr_lag=lag, chinese_lag=chinese, input_idle=idle, memory_bytes=memory)


EVENTS = [
    dict(type='segment', id='s1', start=10, end=12, final=True),
    dict(type='segment', id='s2', start=40, end=44, final=True),
    dict(type='segment', id='s3', start=70, end=73, final=True),
    status(5), status(10), status(15, lag=18), status(20, lag=22), status(25, lag=2),
    status(30, idle=12), status(35, idle=16), status(40),
    dict(type='translation_attempt', id='s1', outcome='started', final=True),
    dict(type='translation_attempt', id='s1', outcome='applied_current', final=True, execution_seconds=0.2,
         queue_seconds=0.01, finalization_seconds=1.5, audio_end_lag=2.0),
    dict(type='translation_attempt', id='s2', outcome='applied_current', final=True, execution_seconds=6.0,
         queue_seconds=0.5, finalization_seconds=20.0, audio_end_lag=9.0),
    dict(type='translation_attempt', id='s3', outcome='error', final=True, error='模型超时'),
    dict(type='asr_quality', status='unresolved', start=50, end=55, attempts=[{'reason': 'loop', 'text': '长文本不应外泄'}]),
    dict(type='asr_quality', status='recovered', start=60, end=62, attempts=[]),
]


class ComputeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_figures_and_events_from_one_record(self):
        path = write_record(self.root, '20261025-100000-0123abcd', EVENTS,
                            first_translation_latency=[1, 2, 3, 4, 20], final_translation_latency=[2, 3, 9],
                            integrity=dict(recognition_gaps=[dict(start=50, end=55), dict(start=80, end=81)]))
        d = metrics.compute(path)
        self.assertEqual(d['first']['p50'], 3)
        self.assertEqual(d['first']['p95'], 20)
        self.assertEqual(d['first']['max'], 20)
        self.assertEqual(d['final']['p50'], 3)
        self.assertEqual(d['asr_lag']['max'], 22)
        self.assertEqual(d['gaps'], 2, 'gaps come from the saved integrity check')
        self.assertEqual(d['recovered'], 1)
        self.assertEqual(d['translation_errors'], 1)
        self.assertEqual(d['memory_peak'], 2 * 1024 ** 3)
        self.assertEqual(d['stages']['translate']['max'], 6.0)
        self.assertEqual(d['stages']['finalize']['p50'], 1.5)
        self.assertEqual([c['count'] for c in d['histogram']], [1, 2, 1, 0, 1], 'histogram uses first-appearance latency')
        kinds = [(e['kind'], e['t']) for e in d['events']]
        self.assertIn(('behind', 15.0), kinds)
        self.assertIn(('silence', 30.0), kinds)
        self.assertIn(('slow_translation', 40), kinds)
        self.assertIn(('slow_final', 40), kinds)
        self.assertIn(('translation_error', 70), kinds)
        self.assertIn(('gap', 50), kinds)
        self.assertEqual([e['t'] for e in d['events']], sorted(e['t'] for e in d['events']), 'events are in time order')
        self.assertEqual(d['series']['sentences'], [(12, 2.0), (44, 9.0)])
        self.assertTrue(d['detailed'])
        self.assertFalse(d['legacy'])
        self.assertNotIn('长文本不应外泄', json.dumps(d, ensure_ascii=False), 'recognition text never leaks into figures')

    def test_imported_files_do_not_report_silence(self):
        path = write_record(self.root, '20261025-100000-0123abcd', [status(5, idle=30), status(10, idle=40)], source='file')
        d = metrics.compute(path)
        self.assertFalse(any(e['kind'] == 'silence' for e in d['events']))
        self.assertFalse(d['live'])
        self.assertIsNone(d['first'])

    def test_old_record_is_marked_and_does_not_crash(self):
        path = write_record(self.root, '20261023-100000-0123abcd', [status(5), dict(type='segment', id='s1', start=0, end=1)],
                            first_translation_latency=[3.0])
        d = metrics.compute(path)
        self.assertTrue(d['legacy'])
        self.assertIsNone(d['stages'])
        self.assertEqual(d['first']['n'], 1)

    def test_long_series_keep_their_peaks(self):
        points = [(i, 1.0) for i in range(5000)]
        points[2345] = (2345, 50.0)
        small = metrics.downsample(points, 100)
        self.assertLessEqual(len(small), 100)
        self.assertIn((2345, 50.0), small)

    def test_cache_recomputes_after_a_change(self):
        path = write_record(self.root, '20261025-100000-0123abcd', [status(5)], first_translation_latency=[1.0])
        self.assertEqual(metrics.CACHE.get(path)['first']['p50'], 1.0)
        meta = json.loads((path / 'session.json').read_text())
        meta['first_translation_latency'] = [7.0, 7.0, 7.0]
        (path / 'session.json').write_text(json.dumps(meta))
        self.assertEqual(metrics.CACHE.get(path)['first']['p50'], 7.0)


class OverviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        write_record(self.root, '20261025-100000-0000000a', EVENTS, first_translation_latency=[1, 2], final_translation_latency=[2])
        write_record(self.root, '20261025-110000-0000000b', [status(5, lag=30)], model='turbo', first_translation_latency=[10, 12])
        write_record(self.root, '20200101-100000-0000000c', [status(5)], started_at='2020-01-01T10:00:00+07:00', first_translation_latency=[5])
        write_record(self.root, '20261025-120000-0000000d', [status(5)], state='running')
        (self.root / '.trash').mkdir()
        write_record(self.root / '.trash', '20261025-130000-0000000e', [status(5)])

    def tearDown(self):
        self.temp.cleanup()

    def test_filters_days_model_and_skips_busy_running_and_trash(self):
        everything = metrics.overview(self.root, days=0)
        self.assertEqual([r['id'] for r in everything['records']],
                         ['20261025-110000-0000000b', '20261025-100000-0000000a', '20200101-100000-0000000c'])
        recent = metrics.overview(self.root, days=30)
        self.assertNotIn('20200101-100000-0000000c', [r['id'] for r in recent['records']])
        turbo = metrics.overview(self.root, days=0, model='turbo')
        self.assertEqual([r['id'] for r in turbo['records']], ['20261025-110000-0000000b'])
        busy = metrics.overview(self.root, days=0, busy_ids=('20261025-110000-0000000b',))
        self.assertNotIn('20261025-110000-0000000b', [r['id'] for r in busy['records']])

    def test_summary_pools_sentences_and_rows_stay_light(self):
        data = metrics.overview(self.root, days=0)
        s = data['summary']
        self.assertEqual(s['records'], 3)
        self.assertEqual(s['first']['n'], 5)
        self.assertEqual(s['asr_lag_peak'], 30)
        row = data['records'][0]
        for heavy in ('series', 'events', 'first_values', 'final_values'):
            self.assertNotIn(heavy, row)
        self.assertIn('spark', row)

    def test_http_endpoints(self):
        async def run():
            with patch('classroom.app.SESSIONS', self.root):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8767') as client:
                    overview = await client.get('/api/metrics?days=0')
                    bad = await client.get('/api/metrics?model=large')
                    one = await client.get('/api/sessions/20261025-100000-0000000a/metrics')
                    missing = await client.get('/api/sessions/20261025-100000-ffffffff/metrics')
                    invalid = await client.get('/api/sessions/..%2F..%2Fetc/metrics')
                    return overview, bad, one, missing, invalid
        overview, bad, one, missing, invalid = asyncio.run(run())
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.json()['summary']['records'], 3)
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(one.status_code, 200)
        self.assertIn('events', one.json())
        self.assertNotIn('first_values', one.json())
        self.assertEqual(missing.status_code, 404)
        self.assertIn(invalid.status_code, (404, 422))


if __name__ == '__main__':
    unittest.main()
