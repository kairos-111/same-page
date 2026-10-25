"""Timing and anomaly figures for the data page, derived read-only from a record's events.

Nothing here writes to a record. Figures are process timings: they say how far
the pipeline was behind, never whether recognition or translation was right.
"""
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import threading

LAG_OK = 4            # seconds; matches the live bar's "已跟上"
LAG_BEHIND = 15       # seconds; matches the live bar's "落后"
SLOW_TRANSLATION = 5  # one sentence taking longer than this to translate
LONG_FINALIZE = 15    # a sentence waiting this long to be confirmed
LONG_SILENCE = 10     # no input for this long during live capture
BINS = ((0, 2), (2, 4), (4, 8), (8, 15), (15, None))
MAX_SERIES = 360
MAX_EVENTS = 300
FAILED = {'error', 'request_timeout_compute_unconfirmed'}
APPLIED = {'applied_current', 'applied_prefix'}


def percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def summary(values):
    values = [float(v) for v in values if isinstance(v, (int, float)) and math.isfinite(v)]
    if not values:
        return None
    return dict(n=len(values), p50=percentile(values, .5), p95=percentile(values, .95),
                max=max(values), mean=sum(values) / len(values))


def histogram(values):
    counts = [0] * len(BINS)
    for v in values:
        for i, (low, high) in enumerate(BINS):
            if v >= low and (high is None or v < high):
                counts[i] += 1
                break
    return [dict(low=low, high=high, count=c) for (low, high), c in zip(BINS, counts)]


def downsample(points, limit=MAX_SERIES):
    """Keep the shape (including peaks) of a long series within `limit` points."""
    if len(points) <= limit:
        return points
    size = math.ceil(len(points) / limit)
    out = []
    for i in range(0, len(points), size):
        chunk = points[i:i + size]
        out.append(max(chunk, key=lambda p: p[1]))
    return out


def intervals(points, predicate):
    """Merge consecutive points where `predicate(value)` holds into (start, end, peak)."""
    found, start, peak, last = [], None, 0, None
    for t, value in points:
        if predicate(value):
            if start is None:
                start, peak = t, value
            peak = max(peak, value)
            last = t
        elif start is not None:
            found.append((start, last, peak))
            start = None
    if start is not None:
        found.append((start, last, peak))
    return found


def _read_events(path):
    events = Path(path) / 'events.jsonl'
    if not events.exists():
        return
    with events.open(encoding='utf-8', errors='replace') as handle:
        for line in handle:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def compute(path):
    """All figures for one record directory."""
    path = Path(path)
    try:
        meta = json.loads((path / 'session.json').read_text())
    except (OSError, ValueError):
        meta = {}
    segments, status, sentence_lag = {}, [], []
    executions, queues, finalizes = [], [], []
    events, counts = [], {}
    has_attempts = False
    for event in _read_events(path):
        kind = event.get('type')
        counts[kind] = counts.get(kind, 0) + 1
        if kind == 'segment' and event.get('id'):
            segments[event['id']] = (event.get('start', 0), event.get('end', 0))
        elif kind == 'status':
            if event.get('state') == 'running':
                status.append(event)
        elif kind == 'translation_attempt' and event.get('outcome') != 'started':
            has_attempts = True
            outcome = event.get('outcome')
            start = segments.get(event.get('id'), (None, None))[0]
            if outcome in APPLIED:
                if isinstance(event.get('execution_seconds'), (int, float)):
                    executions.append(event['execution_seconds'])
                    if event['execution_seconds'] > SLOW_TRANSLATION and start is not None:
                        events.append(dict(t=start, kind='slow_translation', level='warn',
                                           value=event['execution_seconds'], text='这一句翻译用了 {:.1f} 秒'.format(event['execution_seconds'])))
                if isinstance(event.get('queue_seconds'), (int, float)):
                    queues.append(event['queue_seconds'])
                if event.get('final') and isinstance(event.get('finalization_seconds'), (int, float)):
                    finalizes.append(event['finalization_seconds'])
                    if event['finalization_seconds'] > LONG_FINALIZE and start is not None:
                        events.append(dict(t=start, kind='slow_final', level='warn', value=event['finalization_seconds'],
                                           text='这一句等了 {:.1f} 秒才定稿'.format(event['finalization_seconds'])))
                if event.get('final') and outcome == 'applied_current' and isinstance(event.get('audio_end_lag'), (int, float)):
                    end = segments.get(event.get('id'), (None, None))[1]
                    if end is not None:
                        sentence_lag.append((end, event['audio_end_lag']))
            elif outcome in FAILED:
                events.append(dict(t=start if start is not None else 0, kind='translation_error', level='bad',
                                   text=str(event.get('error'))[:80] if event.get('error') else '这一句没有得到译文'))
        elif kind == 'asr_quality':
            if event.get('status') == 'unresolved':
                events.append(dict(t=event.get('start', 0), end=event.get('end'), kind='gap', level='warn',
                                   text='这段语音没有形成可靠字幕，预览已撤回'))
            elif event.get('status') == 'recovered':
                events.append(dict(t=event.get('start', 0), end=event.get('end'), kind='recovered', level='ok',
                                   text='识别出现重复或时间异常，自动重试后恢复'))
    lag_points = [(float(s.get('audio_seconds', 0)), max(float(s.get('asr_lag', 0) or 0),
                  float(s.get('chinese_lag', 0) or 0))) for s in status]
    asr_points = [(float(s.get('audio_seconds', 0)), float(s.get('asr_lag', 0) or 0)) for s in status]
    for start, end, peak in intervals(lag_points, lambda v: v >= LAG_BEHIND):
        events.append(dict(t=start, end=end, kind='behind', level='bad', value=peak,
                           text='最多落后 {:.0f} 秒'.format(peak)))
    if meta.get('source') != 'file':
        idle = [(float(s.get('audio_seconds', 0)), float(s.get('input_idle', 0) or 0)) for s in status]
        for start, end, peak in intervals(idle, lambda v: v >= LONG_SILENCE):
            events.append(dict(t=start, end=end, kind='silence', level='info', value=peak,
                               text='连续 {:.0f} 秒没有收到声音'.format(peak)))
    events.sort(key=lambda e: (e['t'], e['kind']))
    integrity = meta.get('integrity') or {}
    first = meta.get('first_translation_latency') or []
    final = meta.get('final_translation_latency') or []
    memory = [s.get('memory_bytes') for s in status if isinstance(s.get('memory_bytes'), (int, float))]
    stages = None
    if finalizes or executions:
        stages = dict(finalize=summary(finalizes), queue=summary(queues), translate=summary(executions))
    return dict(
        id=meta.get('id', path.name), title=meta.get('title') or '新记录', started_at=meta.get('started_at'),
        audio_seconds=meta.get('audio_seconds', 0), source=meta.get('source'), model=meta.get('model'),
        language_mode=meta.get('language_mode') or meta.get('language') or 'en', state=meta.get('state'),
        live=bool(first or final) or meta.get('source') != 'file',
        first=summary(first), final=summary(final), asr_lag=summary([v for _, v in asr_points]),
        histogram=histogram(first or final), stages=stages,
        memory_peak=max(memory) if memory else None,
        gaps=len(integrity.get('recognition_gaps') or []) or int(meta.get('asr_unresolved') or 0),
        recovered=sum(1 for e in events if e['kind'] == 'recovered'),
        translation_errors=sum(1 for e in events if e['kind'] == 'translation_error'),
        incomplete=len(meta.get('incomplete_translation_ids') or []),
        behind_seconds=sum(max(0, b - a) for a, b, _ in intervals(lag_points, lambda v: v >= LAG_BEHIND)),
        detailed=has_attempts,
        # Records made before per-call timing was logged have none of these events.
        legacy=not any(k in counts for k in ('asr_attempt', 'translation_attempt', 'asr_quality')),
        series=dict(lag=downsample(lag_points), asr=downsample(asr_points), sentences=downsample(sentence_lag)),
        events=events[:MAX_EVENTS], event_total=len(events), counts=counts,
        log_bytes=(path / 'events.jsonl').stat().st_size if (path / 'events.jsonl').exists() else 0,
        first_values=list(first), final_values=list(final),
    )


class Cache:
    """Recompute a record only when its files change."""

    def __init__(self):
        self.items = {}
        self.lock = threading.Lock()

    def get(self, path):
        path = Path(path)
        try:
            key = tuple((p.stat().st_mtime_ns, p.stat().st_size) for p in (path / 'session.json', path / 'events.jsonl') if p.exists())
        except OSError:
            key = None
        with self.lock:
            hit = self.items.get(path)
            if hit and hit[0] == key:
                return hit[1]
        value = compute(path)
        with self.lock:
            self.items[path] = (key, value)
        return value


CACHE = Cache()
PRIVATE = ('first_values', 'final_values')


def detail(path):
    return {k: v for k, v in CACHE.get(path).items() if k not in PRIVATE}


def _started(item):
    try:
        return datetime.fromisoformat(item.get('started_at'))
    except (TypeError, ValueError):
        return None


def overview(root, days=30, model='all', busy_ids=()):
    """Summary across records plus one row per record, newest first."""
    root = Path(root)
    rows = []
    cutoff = None
    if days:
        cutoff = datetime.now().astimezone() - timedelta(days=days)
    for directory in sorted(root.iterdir() if root.exists() else [], reverse=True):
        if not directory.is_dir() or directory.name.startswith('.') or directory.name in busy_ids:
            continue
        if not (directory / 'session.json').is_file():
            continue
        item = CACHE.get(directory)
        if item.get('state') in ('loading', 'running', 'draining'):
            continue
        started = _started(item)
        if cutoff and started and started < cutoff:
            continue
        if model in ('small', 'turbo') and item.get('model') != model:
            continue
        rows.append(item)
    pooled_first, pooled_final = [], []
    for item in rows:
        pooled_first += item['first_values']
        pooled_final += item['final_values']
    lag_peaks = [r['asr_lag']['max'] for r in rows if r.get('asr_lag')]
    total = dict(
        records=len(rows), audio_seconds=sum(float(r.get('audio_seconds') or 0) for r in rows),
        sentences=len(pooled_final), first=summary(pooled_first), final=summary(pooled_final),
        histogram=histogram(pooled_first or pooled_final),
        asr_lag_peak=max(lag_peaks) if lag_peaks else None,
        gaps=sum(r['gaps'] for r in rows), recovered=sum(r['recovered'] for r in rows),
        translation_errors=sum(r['translation_errors'] for r in rows),
        behind_seconds=sum(r['behind_seconds'] for r in rows),
        log_bytes=sum(r['log_bytes'] for r in rows),
    )
    light = []
    for r in rows:
        row = {k: v for k, v in r.items() if k not in PRIVATE + ('series', 'events', 'counts', 'histogram')}
        row['spark'] = downsample(r['series']['asr'], 48)
        light.append(row)
    return dict(summary=total, records=light, thresholds=dict(ok=LAG_OK, behind=LAG_BEHIND))
