import asyncio
from collections import OrderedDict
import csv
from pathlib import Path
import re
import time
import httpx

def apply_translation(latest, item, text):
    """A prefix translation may fill a preview, but can never replace a newer result."""
    if latest and latest.get('asr_status') == 'unresolved':
        return None
    if not latest or item['revision']<(latest.get('translated_revision') or 0):
        return None
    current=item['revision']==latest['revision']
    prefix=latest['source'].startswith(item['source'])
    boundary=latest['source'][len(item['source']):len(item['source'])+1]
    if not current and (item['final'] or not prefix or (boundary and boundary.isalnum())):
        return None
    return dict(latest,translation=text,translated_revision=item['revision'],translation_error='')

def critical_signature(text):
    # Preserve order/count: changing a number, polarity, condition or unit is material.
    return re.findall(r"[+-]?\d+(?:[.,]\d+)*(?:e[+-]?\d+)?|(?<![a-z])(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion|half|quarter|negative|positive|neither|nor|no|not|never|without|cannot|unless|if|only|except|less|more|before|after|must|may|mg|g|kg|ml|l|mm|cm|m|km|ms|s|hz|khz|mhz|percent|milligrams?|grams?|kilograms?|milliliters?|liters?|meters?|seconds?|minutes?|hours?|degrees?|millimet(?:er|re)s?|centimet(?:er|re)s?|metres?|volts?|watts?|pa|kpa|mpa)(?![a-z])|n['’]t|[%°µμ<>=]", text.lower())


def preview_due(item, previous, now):
    if item['final']:
        return True
    text=item['source']
    if previous is None:
        return len(text.split()) >= 4 or bool(critical_signature(text))
    if text == previous['source']:
        return False
    if critical_signature(text) != critical_signature(previous['source']):
        return True
    old=previous['source'].split()
    new=text.split()
    common=0
    for a,b in zip(old,new):
        if a != b:break
        common+=1
    # Revisions count changed suffix words; continuous small edits get a bounded refresh.
    elapsed=now-previous['dispatched_at']
    return (len(new)-common >= 4 and elapsed >= 1) or elapsed >= 5


def validate_translation(content):
    if not isinstance(content,str):
        raise RuntimeError('本地翻译模型返回无效内容')
    if '<think>' in content and '</think>' not in content:
        raise RuntimeError('本地翻译模型返回未完成的思考内容')
    content=re.sub(r'<think>.*?</think>','',content,flags=re.S).strip()
    if not content or not re.search(r'[\w\u4e00-\u9fff]',content):
        raise RuntimeError('本地翻译模型返回空内容')
    return content


class RevisionQueue:
    """Only the latest pending revision per segment; confirmed work gets priority."""
    def __init__(self):
        self.pending = OrderedDict()
        self.wake = asyncio.Event()
        self.closed = False

    def put(self, item):
        if self.closed or item.get('asr_status') == 'unresolved':
            return
        item=item.copy()
        item['queued_at']=time.monotonic()
        self.pending[item['id']] = item
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
        self.asr_terms = []
        if Path(path).exists():
            with Path(path).open(encoding='utf-8-sig', newline='') as f:
                for row in csv.DictReader(f):
                    en,zh = row.get('english','').strip(),row.get('chinese','').strip()
                    if en and zh and len(en)<120 and len(zh)<120:
                        self.terms.append((en,zh))
                        # Legacy CSVs remain useful for translation, but no longer
                        # assert that every example word is a topic of this class.
                        if len(self.terms)<=1000 and row.get('asr_enabled','').strip().lower() in ('1','true','yes'):
                            self.asr_terms.append(en)
        self.terms = self.terms[:1000]

    def matching(self, text):
        return [(en,zh) for en,zh in self.terms if re.search(r'(?<!\w)'+re.escape(en)+r'(?!\w)',text,re.I)][:20]

    def asr_hint(self):
        return ', '.join(self.asr_terms)[:800]

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
        choice = r.json()['choices'][0]
        if choice.get('finish_reason') != 'stop':
            raise RuntimeError('本地翻译未完整结束：'+str(choice.get('finish_reason')))
        return validate_translation(choice.get('message',{}).get('content'))

    async def close(self):
        await self.client.aclose()
