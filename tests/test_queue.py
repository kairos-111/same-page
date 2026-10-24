import asyncio
import unittest
from classroom.translation import RevisionQueue,Glossary,apply_translation

class QueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirmed_segment_preempts_latest_draft(self):
        q=RevisionQueue()
        q.put(dict(id='a',revision=1,final=False))
        q.put(dict(id='a',revision=2,final=False))
        q.put(dict(id='b',revision=1,final=True))
        self.assertEqual((await q.get())['id'],'b')
        self.assertEqual((await q.get())['revision'],2)
        q.close();self.assertIsNone(await q.get())

    async def test_waiting_consumer_wakes_on_shutdown(self):
        q=RevisionQueue()
        pending=asyncio.create_task(q.get())
        await asyncio.sleep(0)
        q.close()
        self.assertIsNone(await asyncio.wait_for(pending,1))

    async def test_final_replaces_draft_without_duplicate_work(self):
        q=RevisionQueue()
        q.put(dict(id='a',revision=1,final=False))
        q.put(dict(id='a',revision=2,final=True))
        self.assertEqual(len(q.pending),1)
        self.assertTrue((await q.get())['final'])

    async def test_prefix_preview_never_overwrites_newer_translation(self):
        current=dict(id='a',revision=3,source='It does not increase',final=False,translated_revision=2,translation='不会增加')
        older=dict(id='a',revision=1,source='It does',final=False)
        self.assertIsNone(apply_translation(current,older,'它会'))
        newer=dict(id='a',revision=2,source='It does not',final=False)
        result=apply_translation(dict(current,translated_revision=None,translation=''),newer,'它不会')
        self.assertEqual(result['translated_revision'],2)
        self.assertEqual(result['revision'],3)

    async def test_diverged_recognition_does_not_accept_stale_translation(self):
        current=dict(id='a',revision=2,source='It does not increase',final=False)
        old=dict(id='a',revision=1,source='It increases',final=False)
        self.assertIsNone(apply_translation(current,old,'它增加'))

if __name__=='__main__':unittest.main()
