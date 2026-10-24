import asyncio
import sys
import unittest
from classroom.process import terminate_and_drain

class ProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelling_full_audio_pipe_does_not_hang_stop(self):
        p=await asyncio.create_subprocess_exec(sys.executable,'-c',
            'import sys,time; sys.stdout.buffer.write(b"x"*(1024*1024));sys.stdout.flush();time.sleep(30)',
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        await asyncio.sleep(.2)
        await asyncio.wait_for(terminate_and_drain(p),3)
        self.assertIsNotNone(p.returncode)

if __name__=='__main__':unittest.main()
