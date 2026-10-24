import asyncio
from collections import OrderedDict
import csv
from pathlib import Path
import re
import time
import httpx

def apply_translation(latest, item, text):
    """A prefix translation may fill a preview, but can never replace a newer result."""
    if not latest or item['revision']<(latest.get('translated_revision') or 0):
        return None
    current=item['revision']==latest['revision']
    prefix=latest['source'].startswith(item['source'])
    boundary=latest['source'][len(item['source']):len(item['source'])+1]
    if not current and (item['final'] or not prefix or (boundary and boundary.isalnum())):
        return None
    return dict(latest,translation=text,translated_revision=item['revision'],translation_error='')

class RevisionQueue:
    """Only the latest pending revision per segment; confirmed work gets priority."""
    def __init__(self):
        self.pending = OrderedDict()
        self.wake = asyncio.Event()
        self.closed = False

    def put(self, item):
        if self.closed:
            return
        self.pending[item['id']] = item.copy()
        self.wake.set()

    async def get(self):
        while True:
            if self.pending:
                key = next((k for k,v in self.pending.items() if v['final']), next(iter(self.pending)))
                return self.pending.pop(key)
            if self.closed:
                return None
            self.wake.clear()
            await self.wake.wait()

    def close(self):
        self.closed = True
        self.wake.set()

class Glossary:
    def __init__(self, path):
        self.terms = []
        if Path(path).exists():
            with Path(path).open(encoding='utf-8-sig', newline='') as f:
                for row in csv.DictReader(f):
                    en,zh = row.get('english','').strip(),row.get('chinese','').strip()
                    if en and zh and len(en)<120 and len(zh)<120:
                        self.terms.append((en,zh))
        self.terms = self.terms[:1000]

    def matching(self, text):
        return [(en,zh) for en,zh in self.terms if re.search(r'(?<!\w)'+re.escape(en)+r'(?!\w)',text,re.I)][:20]

    def asr_hint(self):
        return ', '.join(en for en,_ in self.terms)[:800]

class LocalTranslator:
    def __init__(self, url, glossary):
        self.client = httpx.AsyncClient(base_url=url, timeout=httpx.Timeout(35, connect=3),trust_env=False)
        self.glossary = glossary

    async def translate(self, text, context=()):
        terms = self.glossary.matching(text)
        prompt = ''
        if terms:
            prompt += '参考下面的术语翻译：\n'+'\n'.join(f'{en} 翻译成 {zh}' for en,zh in terms)+'\n'
        # Short unfinished fragments easily invite a small model to repeat the context.
        # Restrict context to complete, substantive sentences and separate it explicitly.
        if context and len(text.split())>=10 and re.search(r'[.!?]$',text.strip()):
            prompt += '【背景信息，仅供理解，不要翻译】\n'+'\n'.join(context)[-1200:]+'\n'
        prompt += '将【待翻译文本】翻译为简体中文，只输出该文本的译文，不要解释、续写或重复背景信息。文本可能尚未结束，请保留其不完整性。\n【待翻译文本】\n'+text
        from .paths import WORK
        key_file=WORK/'llama-key'
        headers={'Authorization':'Bearer '+key_file.read_text().strip()} if key_file.exists() else {}
        r = await self.client.post('/v1/chat/completions', headers=headers, json={
            'messages':[{'role':'user','content':prompt}], 'temperature':0.1,
            'top_p':0.6,'top_k':20,'repeat_penalty':1.05,
            'max_tokens':min(384,max(64,len(text))), 'stream':False,
        })
        r.raise_for_status()
        content = r.json()['choices'][0]['message']['content']
        content = re.sub(r'<think>.*?</think>','',content or '',flags=re.S).strip()
        if not content:
            raise RuntimeError('本地翻译模型返回空内容')
        return content

    async def close(self):
        await self.client.aclose()
