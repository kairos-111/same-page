"""Own the local translation server and the single MLX worker."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import functools
import os
from pathlib import Path
import time
import httpx
from .paths import WORK,MODELS,LLAMA_URL

class Runtime:
    def __init__(self):
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='asr')
        self.asr=None
        self.model=None
        self.llama=None
        self.llama_log=None
        self.api_key=''

    async def thread(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self.executor,functools.partial(fn,*args))

    def find_llama(self):
        candidates=list((WORK/'runtime').rglob('llama-server'))
        if not candidates:
            raise RuntimeError('llama.cpp 尚未部署，请先完成下载和环境准备')
        return candidates[0]

    async def ensure_translation(self):
        if self.llama and self.llama.returncode is not None:
            self.llama=None
        if not self.llama:
            binary=self.find_llama()
            model=MODELS/'hy-mt'/'Hy-MT2-1.8B-Q4_K_M.gguf'
            if not model.exists():raise RuntimeError('翻译模型尚未下载完成')
            self.llama_log=(WORK/'llama-server.log').open('a')
            import secrets
            self.api_key=secrets.token_urlsafe(32)
            key_file=WORK/'llama-key'
            key_file.write_text(self.api_key)
            key_file.chmod(0o600)
            self.llama=await asyncio.create_subprocess_exec(str(binary),'-m',str(model),'--host','127.0.0.1',
                '--port','8768','-ngl','99','-c','4096','--parallel','1','--jinja','-lv','4','--api-key-file',str(key_file),
                stdout=self.llama_log,stderr=self.llama_log)
        async with httpx.AsyncClient(trust_env=False) as c:
            for _ in range(90):
                if self.llama.returncode is not None:raise RuntimeError('翻译服务启动失败，请查看诊断日志')
                try:
                    r=await c.get(LLAMA_URL+'/health',timeout=2)
                    if r.status_code==200:return
                except httpx.HTTPError:pass
                await asyncio.sleep(1)
        raise RuntimeError('翻译模型加载超时')

    async def load_asr(self, model, hint):
        from .asr import WhisperAdapter
        path=MODELS/('whisper-turbo' if model=='turbo' else 'whisper-small')
        if not any(path.glob('weights.*')):raise RuntimeError('识别模型尚未下载完成')
        # A fresh streaming adapter is needed per class; the MLX holder reuses loaded weights.
        self.asr=None
        def load():
            import gc
            import mlx.core as mx
            gc.collect();mx.clear_cache()
            return WhisperAdapter(path,hint)
        self.asr=await self.thread(load)
        self.model=model
        return self.asr

    async def close(self):
        if self.llama and self.llama.returncode is None:
            self.llama.terminate()
            try:await asyncio.wait_for(self.llama.wait(),5)
            except asyncio.TimeoutError:self.llama.kill();await self.llama.wait()
        if self.llama_log:self.llama_log.close()
        self.executor.shutdown(wait=False,cancel_futures=True)
