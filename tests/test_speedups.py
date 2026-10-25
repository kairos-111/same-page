"""Latency fixes: early capture, junk text kept out of translation, bounded model work."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from classroom.asr_quality import token_limit
from classroom.languages import clean_asr_text
from classroom.session import ClassSession
from classroom.translation import token_budget


def segment(text, final=False, key='s1', revision=1):
    return dict(type='segment', id=key, revision=revision, source=text, final=final, start=0, end=1)


class JunkTextTests(unittest.TestCase):
    def test_known_whisper_junk_is_garbage(self):
        for junk in ['・・・・・・・・', 'ㄱㄱㄱㄱㄱㄱ', '______________', 'هلاشكا', '♪♪♪', 'A', '—']:
            self.assertTrue(clean_asr_text(junk)[1], junk)

    def test_real_speech_is_kept_unchanged(self):
        for text in ["Hello, what's your name?", '你好', '好', '嗯。', 'OK', 'The value is 3.5% ... right?', 'Ça va? Да!']:
            self.assertEqual(clean_asr_text(text), (text, False), text)

    def test_junk_tail_is_trimmed_but_words_stay(self):
        self.assertEqual(clean_asr_text('Я люблю тебя. ㄱㄱㄱㄱㄱㄱㄱㄱ'), ('Я люблю тебя.', False))


class BudgetTests(unittest.TestCase):
    def test_translation_budget_follows_source_length(self):
        self.assertLess(token_budget('Hello'), token_budget('Hello, what is your name today?'))
        self.assertLessEqual(token_budget('word ' * 500), 320)

    def test_decode_limit_follows_audio_length(self):
        self.assertEqual(token_limit(0), 24)
        self.assertGreater(token_limit(6), 6 * 5 + 24, 'room for fast speech')
        self.assertEqual(token_limit(60), 224, "never above Whisper's own ceiling")


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        with patch('classroom.session.SESSIONS', Path(self.temp.name)):
            self.s = ClassSession(None, 'microphone', 'small', 'fake')

    async def asyncTearDown(self):
        await self.s.translator.close()
        self.s.store.audio.close()
        self.s.store.close()
        self.temp.cleanup()

    async def test_junk_preview_is_not_shown_or_translated(self):
        self.s.on_segment(segment('・・・・・・・・'))
        self.assertNotIn('s1', self.s.segments)
        self.assertEqual(self.s.queue.pending, {})
        self.assertEqual(self.s.asr_unresolved, 0)

    async def test_junk_final_becomes_a_review_gap_and_withdraws_the_preview(self):
        self.s.on_segment(segment('Hello there friend', revision=1))
        self.s.on_segment(segment('ㄱㄱㄱㄱㄱㄱ', final=True, revision=2))
        latest = self.s.segments['s1']
        self.assertEqual(latest['asr_status'], 'unresolved')
        self.assertEqual(latest['asr_reason'], 'garbage_text')
        self.assertEqual(latest['source_raw'], 'ㄱㄱㄱㄱㄱㄱ')
        self.assertNotIn('s1', self.s.queue.pending, 'junk never reaches the translator')
        self.assertEqual(self.s.asr_unresolved, 1)

    async def test_trimmed_text_keeps_the_raw_hypothesis_for_audit(self):
        self.s.on_segment(segment('Я люблю тебя. ㄱㄱㄱㄱㄱㄱ', final=True))
        latest = self.s.segments['s1']
        self.assertEqual(latest['source'], 'Я люблю тебя.')
        self.assertEqual(latest['source_raw'], 'Я люблю тебя. ㄱㄱㄱㄱㄱㄱ')

    async def test_timed_out_final_is_retried_once(self):
        calls = []

        class Slow:
            async def translate(self, text, context, final=True):
                calls.append(final)
                if len(calls) == 1:
                    raise httpx.ReadTimeout('slow')
                return '你好'

            async def close(self):
                pass

        await self.s.translator.close()
        self.s.translator = Slow()
        self.s.on_segment(segment('Hello there friend', final=True))
        worker = asyncio.create_task(self.s.translate())
        for _ in range(50):
            if self.s.segments['s1'].get('translation') == '你好':
                break
            await asyncio.sleep(0.01)
        self.s.queue.close()
        await asyncio.wait_for(worker, 1)
        self.assertEqual(calls, [True, True])
        self.assertEqual(self.s.segments['s1']['translation'], '你好')

    async def test_capture_starts_before_models_finish_loading(self):
        order = []
        loaded = asyncio.Event()

        class Runtime:
            async def ensure_translation(self):
                order.append('translation')
                await loaded.wait()

            async def load_asr(self, *args):
                order.append('asr')
                raise RuntimeError('stop here')

        async def capture():
            order.append('capture')
            self.s.capture_ready = True
            self.s.append_audio(b'\0\0' * 1600)
            loaded.set()
            self.s.input_done = True

        self.s.runtime = Runtime()
        self.s.capture = capture
        # Model loading only finishes once capture has run, so this would hang if
        # capture still waited for the models.
        await asyncio.wait_for(self.s.run(), 2)
        self.assertLess(order.index('capture'), order.index('asr'), 'listening starts while models load')
        self.assertTrue(self.s.audio_started)
        self.assertIn('audio_started_at', self.s.store.metadata)

    async def test_status_reports_microphone_readiness(self):
        self.assertFalse(self.s.status()['capture_ready'])
        self.s.capture_ready = True
        self.assertTrue(self.s.status()['capture_ready'])


if __name__ == '__main__':
    unittest.main()
