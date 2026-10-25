"""Language modes and script-aware text handling; no model or user-data mutation."""
import re

MODES = ('en', 'ru', 'mixed')
MODE_LABELS = {'en':'英语 → 中文', 'ru':'俄语 → 中文', 'mixed':'英俄中混合 → 中文'}
HAN = r'\u3400-\u9fff\uf900-\ufaff'
FOREIGN = re.compile(r'[A-Za-z\u0400-\u052f]')
HAN_RE = re.compile('['+HAN+']')

def text_languages(text):
    found=[]
    for code,pattern in (('en',r'[A-Za-z]'),('ru',r'[\u0400-\u052f]'),('zh','['+HAN+']')):
        if re.search(pattern,text):found.append(code)
    return found

def source_language(text, fallback='en'):
    languages=text_languages(text)
    return 'mixed' if len(languages)>1 else languages[0] if languages else ('und' if fallback=='mixed' else fallback)

def has_foreign_letters(text):
    return any(c.isalpha() and not HAN_RE.fullmatch(c) for c in text)

def chinese_passthrough(text):
    # A Latin/Cyrillic name or acronym must not accidentally bypass translation.
    return bool(HAN_RE.search(text)) and not has_foreign_letters(text)

def language_units(text):
    letters=r'[^\W\d_'+HAN+']'
    return re.findall('['+HAN+']|'+letters+"+(?:['’]"+letters+r"+)?|[+-]?\d+(?:[.,]\d+)*",text)

def join_asr_words(words):
    text=re.sub(r'\s+',' ',' '.join(w.text.strip() for w in words)).strip()
    text=re.sub(r'\s+([,.;:!?%。，；：！？、）】》])',r'\1',text)
    text=re.sub('(?<=['+HAN+']) +(?=['+HAN+'])','',text)
    text=re.sub('(?<=[（【《]) +|(?<=[，。；：！？、]) +(?=['+HAN+'])','',text)
    return text
