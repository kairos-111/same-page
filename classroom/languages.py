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

# Characters a supported language can contain: Latin (with accents), Cyrillic,
# Chinese, digits, whitespace and ordinary punctuation. Anything else in a
# transcript (stray Hangul jamo, kana dots, Arabic, music notes, box drawing...)
# is a known Whisper failure mode in these modes, not speech.
_ALLOWED = re.compile(r"[A-Za-zÀ-ɏЀ-ԯ" + HAN + r"0-9\s.,!?;:'\"()\[\]%&+\-/…–—，。！？；：“”‘’（）、《》·]")
_MEANINGFUL = re.compile(r"[A-Za-zÀ-ɏЀ-ԯ" + HAN + r"0-9]")


def clean_asr_text(text):
    """Return (cleaned_text, is_garbage) for one recognised sentence.

    Removes characters no supported language uses and runs of four or more of the
    same punctuation mark. Words are never removed. A sentence left with no Chinese
    character and fewer than two letters or digits is garbage: not shown or translated.
    """
    text = str(text or '')
    kept = ''.join(c for c in text if _ALLOWED.fullmatch(c))
    kept = re.sub(r'([^\w\s])(?:\s*\1){3,}', '', kept)
    kept = re.sub(r'\s+', ' ', kept).strip()
    # One Chinese character is a real reply (好, 嗯); Latin/Cyrillic need two.
    return kept, not HAN_RE.search(kept) and len(_MEANINGFUL.findall(kept)) < 2
