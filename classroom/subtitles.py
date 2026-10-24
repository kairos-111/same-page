"""Turn incremental ASR tokens into revisable, stable-ID subtitle segments."""
from dataclasses import dataclass
import re

@dataclass
class Word:
    start: float
    end: float
    text: str

class SegmentComposer:
    def __init__(self):
        self.index = 1
        self.words = []
        self.previous = {}

    @staticmethod
    def text(words):
        text=re.sub(r"\s+", " ", " ".join(w.text.strip() for w in words)).strip()
        return re.sub(r"\s+([,.;:!?%])",r"\1",text)

    def emit(self, words, final):
        if not words:
            return None
        text = self.text(words)
        if not text:
            return None
        key = f"s{self.index:06}"
        old = self.previous.get(key)
        if old and old["source"] == text and old["final"] == final:
            return None
        event = dict(type="segment", id=key, source=text, start=words[0].start,
                     end=words[-1].end, final=final, revision=(old["revision"]+1 if old else 1),
                     language="en", translation="", translated_revision=None)
        self.previous[key] = event
        if len(self.previous) > 4:
            self.previous.pop(next(iter(self.previous)))
        return event

    def ingest(self, committed, partial=(), flush=False):
        events = []
        for word in committed:
            self.words.append(word)
            duration = word.end-self.words[0].start
            clause = duration >= 4 and re.search(r"[,;:][\"')]*$", word.text.strip())
            enough = len(self.words) >= 18 and duration >= 12
            if re.search(r"[.!?][\"')]*$", word.text.strip()) or clause or enough:
                event = self.emit(self.words, True)
                if event:
                    events.append(event)
                self.words = []
                self.index += 1
        if flush:
            self.words.extend(partial)
            event = self.emit(self.words, True)
            if event:
                events.append(event)
            if self.words:
                self.index += 1
            self.words = []
        else:
            event = self.emit(self.words + list(partial), False)
            if event:
                events.append(event)
        return events
