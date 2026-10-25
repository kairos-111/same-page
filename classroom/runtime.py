"""Own the local translation server and cached Whisper weights on a single MLX worker."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import functools
import os
from pathlib import Path
import time
import httpx
from .paths import WORK,MODELS,LLAMA_URL,RUNTIME,LLAMA_PORT
from .system import WINDOWS,ASR_BACKEND,asr_model_dir,asr_model_available,hidden_window,llama_candidates

class Runtime:
    def __init__(self):
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='asr')
        self.asr=None
        self.model=None
        self.asr_models={}
        self.llama=None
        self.llama_log=None
        self.api_key=''
        self.model_operation=None
        self.model_error=''

    async def thread(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self.executor,functools.partial(fn,*args))

    def find_llama(self):
        return self.llama_candidates()[0]

    def llama_candidates(self):
        candidates=llama_candidates(RUNTIME)
        if not candidates:
            raise RuntimeError('llama.cpp 尚未部署，请先完成下载和环境准备')
        return candidates

    async def ensure_translation(self):
        if self.llama and self.llama.returncode is not None:
            self.llama=None
        if self.llama:
            await self._wait_for_llama()
            return
        model=MODELS/'hy-mt'/'Hy-MT2-1.8B-Q4_K_M.gguf'
        if not model.exists():raise RuntimeError('翻译模型尚未下载完成')
        candidates=self.llama_candidates()
        last_error=None
        # On Windows the GPU (Vulkan) server comes first; a PC without a usable GPU
        # driver falls back to the CPU server instead of failing translation.
        for binary in candidates:
            await self._start_llama(binary,model)
            try:
                await self._wait_for_llama()
                return
            except RuntimeError as exc:
                last_error=exc
                await self.stop_translation()
        raise last_error

    async def _start_llama(self, binary, model):
        self.llama_log=(WORK/'llama-server.log').open('a')
        self.llama_log.write(f'\n--- starting {binary}\n');self.llama_log.flush()
        import secrets
        self.api_key=secrets.token_urlsafe(32)
        key_file=WORK/'llama-key'
        key_file.write_text(self.api_key)
        key_file.chmod(0o600)
        model_arg,key_arg,cwd=str(model),str(key_file),None
        if WINDOWS:
            # llama.cpp reads argv in the ANSI code page, so a Chinese user name in the
            # path can break it. Paths relative to WORK stay ASCII for a default install.
            cwd=str(WORK)
            try:model_arg=os.path.relpath(model,WORK)
            except ValueError:pass  # different drive: keep the absolute path
            key_arg='llama-key'
        self.llama=await asyncio.create_subprocess_exec(str(binary),'-m',model_arg,'--host','127.0.0.1',
            '--port',str(LLAMA_PORT),'-ngl','99','-c','4096','--parallel','1','--jinja','-lv','4','--api-key-file',key_arg,
            stdout=self.llama_log,stderr=self.llama_log,cwd=cwd,**hidden_window())

    async def _wait_for_llama(self):
        async with httpx.AsyncClient(trust_env=False) as c:
            for _ in range(90):
                if self.llama.returncode is not None:raise RuntimeError('翻译服务启动失败，请查看诊断日志')
                try:
                    r=await c.get(LLAMA_URL+'/health',timeout=2)
                    if r.status_code==200:return
                except httpx.HTTPError:pass
                await asyncio.sleep(1)
        raise RuntimeError('翻译模型加载超时')

    def available_asr_models(self):
        return [m for m in ('small','turbo') if asr_model_available(MODELS,m)]

    def _preload_asr(self, model):
        if model not in ('small','turbo'):raise ValueError('识别模型无效')
        if ASR_BACKEND=='ct2':
            if model not in self.asr_models:
                from .ct2_asr import load_model as load_ct2
                self.asr_models[model]=load_ct2(asr_model_dir(MODELS,model))
            return self.asr_models[model]
        import mlx.core as mx
        from mlx_whisper.load_models import load_model
        if model not in self.asr_models:
            path=MODELS/('whisper-turbo' if model=='turbo' else 'whisper-small')
            if not any(path.glob('weights.*')):raise RuntimeError('识别模型尚未下载完成')
            # load_model evaluates weights before returning; do not retain a fake ready flag.
            self.asr_models[model]=load_model(str(path.resolve()),dtype=mx.float16)
        return self.asr_models[model]

    async def preload_asr(self, model):
        await self.thread(self._preload_asr,model)

    async def load_asr(self, model, hint, language_mode='en'):
        from .asr import WhisperAdapter
        # Streaming/VAD/context state is fresh for every recording. Only weights are shared.
        self.asr=None
        self.model=None
        def load():
            if ASR_BACKEND=='ct2':
                weights=self._preload_asr(model)
                return WhisperAdapter(asr_model_dir(MODELS,model),hint,language_mode,weights=weights)
            from mlx_whisper.transcribe import ModelHolder
            weights=self._preload_asr(model)
            path=MODELS/('whisper-turbo' if model=='turbo' else 'whisper-small')
            ModelHolder.model=weights
            ModelHolder.model_path=str(path.resolve())
            return WhisperAdapter(path,hint,language_mode)
        self.asr=await self.thread(load)
        self.model=model
        return self.asr

    async def stop_translation(self):
        if self.llama and self.llama.returncode is None:
            self.llama.terminate()
            try:await asyncio.wait_for(self.llama.wait(),8)
            except asyncio.TimeoutError:
                raise RuntimeError('翻译模型仍在停止，请稍后重试')
        self.llama=None
        if self.llama_log:self.llama_log.close();self.llama_log=None
        self.api_key=''

    async def stop_asr(self, model=None):
        def release():
            import gc
            if ASR_BACKEND=='ct2':
                if model is None or self.model==model:
                    self.asr=None
                    self.model=None
                for key in (list(self.asr_models) if model is None else [model]):
                    self.asr_models.pop(key,None)
                gc.collect()
                return
            import mlx.core as mx
            from mlx_whisper.transcribe import ModelHolder
            if model is None or self.model==model:
                self.asr=None
                self.model=None
            removed=list(self.asr_models) if model is None else [model]
            for key in removed:
                weights=self.asr_models.pop(key,None)
                if weights is not None and ModelHolder.model is weights:
                    ModelHolder.model=None
                    ModelHolder.model_path=None
            # Drop the loop's last weight reference before collecting GPU allocations.
            weights=None
            if model is None:
                ModelHolder.model=None
                ModelHolder.model_path=None
            gc.collect();mx.clear_cache()
        await self.thread(release)

    async def control_models(self, target, action, recognition='small'):
        self.model_operation=dict(target=target,action=action)
        self.model_error=''
        try:
            if action=='start':
                if target=='all':
                    for selected in self.available_asr_models():
                        await self.preload_asr(selected)
                    await self.ensure_translation()
                elif target=='hy-mt':await self.ensure_translation()
                else:await self.preload_asr(target)
            else:
                if target in ('all','hy-mt'):await self.stop_translation()
                if target=='all':await self.stop_asr()
                elif target in ('small','turbo'):await self.stop_asr(target)
        except Exception as exc:
            self.model_error=str(exc)
            raise
        finally:self.model_operation=None

    async def close(self):
        if self.llama and self.llama.returncode is None:
            self.llama.terminate()
            try:await asyncio.wait_for(self.llama.wait(),5)
            except asyncio.TimeoutError:self.llama.kill();await self.llama.wait()
        if self.llama_log:self.llama_log.close()
        self.executor.shutdown(wait=False,cancel_futures=True)
