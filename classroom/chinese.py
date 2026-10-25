"""Offline Chinese script normalization; no translation or model call."""
from opencc import OpenCC
from .languages import HAN_RE

# Load the small phrase/character dictionaries once; convert() only reads them.
_CONVERTER = OpenCC('t2s')


def simplified(text):
    return _CONVERTER.convert(text) if HAN_RE.search(text) else text


def simplified_segment(segment):
    """Return a display/save projection, retaining changed model text for audit."""
    result = dict(segment)
    if result.get('asr_status') == 'unresolved':
        return result
    for key in ('source', 'translation'):
        text = result.get(key)
        if isinstance(text, str):
            converted = simplified(text)
            if converted != text:
                result.setdefault(key + '_raw', text)
                result[key] = converted
    return result
