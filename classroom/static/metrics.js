'use strict';
/* 运行数据：所有数字都来自本机记录的事件日志，只读，只计处理时间。 */
const M = {days: 30, model: 'all', data: null, detail: null, request: 0, filter: 'all'};
const SVG_NS = 'http://www.w3.org/2000/svg';
const LV_OK = 4, LV_BEHIND = 15;
const KIND = {
  gap: {label: '识别未确认', level: 'warn'},
  recovered: {label: '自动恢复', level: 'ok'},
  slow_final: {label: '定稿等待长', level: 'warn'},
  slow_translation: {label: '翻译慢', level: 'warn'},
  translation_error: {label: '翻译失败', level: 'bad'},
  behind: {label: '处理落后', level: 'bad'},
  silence: {label: '没有声音', level: 'info'},
};
const MODE_SHORT = {en: '英语', ru: '俄语', mixed: '混合', mul: '混合'};

function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k.startsWith('on')) node[k] = v;
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const child of children.flat()) if (child != null) node.append(child);
  return node;
}
function s(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) node.setAttribute(k, v);
  for (const child of children.flat()) if (child != null) node.append(child);
  return node;
}
const sec = v => v == null ? '—' : v < 10 ? v.toFixed(1) : String(Math.round(v));
const secText = v => v == null ? '—' : sec(v) + ' 秒';
const level = v => v == null ? 'none' : v < LV_OK ? 'ok' : v < LV_BEHIND ? 'mid' : 'bad';
const bytes = n => n >= 1024 ** 2 ? (n / 1024 ** 2).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB';
function when(iso, withYear = false) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString('zh-CN', {...(withYear ? {year: 'numeric'} : {}), month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false});
}
function longDuration(seconds) {
  if (num(seconds) < 3600) return duration(seconds);
  const m = Math.round(num(seconds) / 60);
  return m < 60 ? m + ' 分钟' : Math.floor(m / 60) + ' 小时' + (m % 60 ? ' ' + (m % 60) + ' 分' : '');
}

/* 数字滚动入场；系统开启“减少动态效果”时直接显示。 */
function countUp(node, value, format) {
  if (value == null) { node.textContent = '—'; return; }
  if (reduceMotion) { node.textContent = format(value); return; }
  const start = performance.now(), duration = 700;
  const step = now => {
    const t = Math.min(1, (now - start) / duration), eased = 1 - Math.pow(1 - t, 3);
    node.textContent = format(value * eased);
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/* 一个共用的悬浮提示。 */
const tip = h('div', {class: 'm-tip', role: 'tooltip', hidden: true});
document.body.append(tip);
function showTip(event, lines) {
  tip.textContent = lines.filter(Boolean).join('\n');
  tip.hidden = false;
  const x = Math.min(event.clientX + 14, innerWidth - tip.offsetWidth - 12);
  const y = Math.max(12, event.clientY - tip.offsetHeight - 12);
  tip.style.transform = `translate(${x}px, ${y}px)`;
}
function hideTip() { tip.hidden = true; }

/* ---------- 加载 ---------- */
// 从诊断页跳去看字幕，回来时停在原来那条记录上；其他情况刷新总览。
function metricsShown() {
  if (M.returnToDetail && M.detail) { M.returnToDetail = false; return; }
  M.returnToDetail = false;
  loadMetrics();
}
async function loadMetrics() {
  const request = ++M.request;
  $('metrics-detail').hidden = true;
  $('metrics-overview').hidden = false;
  if (!M.data) $('m-hero').replaceChildren(h('p', {class: 'm-loading', text: '正在汇总本机记录…'}));
  try {
    const data = await getJSON('/api/metrics?days=' + M.days + '&model=' + M.model);
    if (request !== M.request) return;
    M.data = data;
    renderOverview(data);
  } catch (e) {
    if (request === M.request) $('m-hero').replaceChildren(h('p', {class: 'm-loading', text: '暂时读不到运行数据：' + e.message}));
  }
}
document.querySelectorAll('.m-seg button').forEach(button => {
  button.onclick = () => {
    M.days = Number(button.dataset.days);
    document.querySelectorAll('.m-seg button').forEach(b => b.setAttribute('aria-checked', String(b === button)));
    loadMetrics();
  };
});
$('m-model').onchange = () => { M.model = $('m-model').value; loadMetrics(); };

/* ---------- 总览 ---------- */
function renderOverview(data) {
  const sum = data.summary;
  $('m-count').textContent = sum.records ? sum.records + ' 条' : '';
  if (!sum.records) {
    $('m-hero').replaceChildren(h('div', {class: 'm-empty'}, h('strong', {text: '这段时间还没有记录'}), h('p', {text: '完成一次翻译后，这里会显示字幕跟得多快、哪里卡过。'})));
    $('m-trend').replaceChildren();
    $('m-table').replaceChildren();
    return;
  }
  $('m-hero').replaceChildren(heroCard(sum));
  renderTrend([...data.records].reverse());
  $('m-table').replaceChildren(...data.records.map(recordRow));
}

function heroCard(sum) {
  const big = h('b', {class: 'lv-' + level(sum.first?.p50)});
  countUp(big, sum.first?.p50, sec);
  const main = h('div', {class: 'm-hero-main'},
    h('span', {class: 'm-label', text: '出字延迟 · 中位'}),
    h('div', {class: 'm-big'}, big, h('small', {text: '秒'})),
    h('p', {class: 'm-sub', text: sum.first ? '说完一句到出现中文。95% 的句子在 ' + secText(sum.first.p95) + '内，最慢 ' + secText(sum.first.max) + '。' : '这段时间只有导入文件，不计实时延迟。'}),
    distribution(sum.histogram));
  const side = h('div', {class: 'm-hero-side'},
    tile('定稿延迟', sum.final?.p50, '95% 在 ' + secText(sum.final?.p95) + '内'),
    tile('识别落后峰值', sum.asr_lag_peak, sum.behind_seconds ? '落后超过 15 秒共 ' + secText(sum.behind_seconds) : '没有落后超过 15 秒'),
    tile('待核查区间', sum.gaps, '自动恢复 ' + sum.recovered + ' 次 · 翻译失败 ' + sum.translation_errors + ' 次', true));
  const foot = h('footer', {class: 'm-hero-foot', text: sum.records + ' 条记录 · 合计 ' + longDuration(sum.audio_seconds) + ' · ' + sum.sentences.toLocaleString('zh-CN') + ' 句 · 日志 ' + bytes(sum.log_bytes)});
  return h('div', {class: 'm-hero-grid'}, main, side, foot);
}
function tile(label, value, note, isCount = false) {
  const number = h('b', {class: isCount ? (value ? 'lv-mid' : 'lv-ok') : 'lv-' + level(value)});
  countUp(number, value, isCount ? v => String(Math.round(v)) : sec);
  return h('div', {class: 'm-tile'}, h('span', {class: 'm-label', text: label}),
    h('div', {class: 'm-num'}, number, h('small', {text: isCount ? '处' : '秒'})), h('p', {class: 'm-note', text: note}));
}
function distribution(bins) {
  const total = bins.reduce((n, b) => n + b.count, 0);
  if (!total) return null;
  const names = bins.map(b => b.high == null ? b.low + ' 秒以上' : b.low + '–' + b.high + ' 秒');
  const bar = h('div', {class: 'm-dist', role: 'img', 'aria-label': '出字延迟分布：' + bins.map((b, i) => names[i] + ' ' + Math.round(b.count / total * 100) + '%').join('，')});
  const legend = h('div', {class: 'm-dist-legend'});
  bins.forEach((b, i) => {
    const pct = b.count / total * 100;
    const seg = h('span', {class: 'm-dist-seg bin-' + i});
    seg.style.setProperty('--w', pct + '%');
    seg.onmousemove = e => showTip(e, [names[i], b.count + ' 句 · ' + pct.toFixed(1) + '%']);
    seg.onmouseleave = hideTip;
    bar.append(seg);
    legend.append(h('span', {}, h('i', {class: 'm-key bin-' + i}), names[i] + ' ', h('b', {text: Math.round(pct) + '%'})));
  });
  requestAnimationFrame(() => bar.classList.add('ready'));
  return h('div', {}, bar, legend);
}

/* 每次记录一个点：圆点为中位，竖线到 95%。 */
function renderTrend(records) {
  const box = $('m-trend');
  const points = records.filter(r => r.first);
  if (!points.length) { box.replaceChildren(h('p', {class: 'm-note', text: '这段时间的记录没有实时延迟数据。'})); return; }
  const width = Math.max(320, box.clientWidth || 720), height = 190, pad = {l: 34, r: 14, t: 14, b: 26};
  // 刻度按多数记录定，个别极慢的记录封顶并画箭头，免得把其余的点压成一条线。
  const p95s = points.map(p => p.first.p95).sort((a, b) => a - b);
  const top = Math.min(30, Math.max(LV_BEHIND + 3, Math.ceil(p95s[Math.max(0, Math.ceil(p95s.length * .8) - 1)] * 1.25 / 5) * 5));
  const x = i => pad.l + (points.length === 1 ? (width - pad.l - pad.r) / 2 : i * (width - pad.l - pad.r) / (points.length - 1));
  const y = v => pad.t + (1 - Math.min(v, top) / top) * (height - pad.t - pad.b);
  const svg = s('svg', {viewBox: `0 0 ${width} ${height}`, width: '100%', height, class: 'm-svg', role: 'img', 'aria-label': '每次记录的出字延迟中位数与 95% 范围'});
  for (const [v, label, cls] of [[0, '0', 'm-grid'], [LV_OK, '4 秒', 'm-grid ok'], [LV_BEHIND, '15 秒', 'm-grid bad'], [top, top + ' 秒', 'm-grid faint']]) {
    svg.append(s('line', {x1: pad.l, x2: width - pad.r, y1: y(v), y2: y(v), class: cls}), s('text', {x: pad.l - 6, y: y(v) + 4, class: 'm-axis', 'text-anchor': 'end'}, label));
  }
  const plot = s('g', {class: 'm-rise'});
  points.forEach((p, i) => {
    const group = s('g', {class: 'm-point', tabindex: '0', role: 'button', 'aria-label': p.title + '，中位 ' + secText(p.first.p50)});
    group.append(s('line', {x1: x(i), x2: x(i), y1: y(p.first.p50), y2: y(p.first.p95), class: 'm-whisker-line'}));
    if (p.first.p95 > top) group.append(s('path', {d: `M ${x(i) - 4} ${y(top) + 5} L ${x(i)} ${y(top) - 1} L ${x(i) + 4} ${y(top) + 5}`, class: 'm-cap'}));
    group.append(s('rect', {x: x(i) - 9, y: pad.t - 4, width: 18, height: height - pad.t - pad.b + 8, class: 'm-hit'}),
      s('circle', {cx: x(i), cy: y(p.first.p50), r: 5.5, class: 'm-dot-svg lv-' + level(p.first.p50)}));
    const lines = [p.title, when(p.started_at) + ' · ' + longDuration(p.audio_seconds), '中位 ' + secText(p.first.p50) + ' · 95% ' + secText(p.first.p95) + ' · 最慢 ' + secText(p.first.max)];
    group.onmousemove = e => showTip(e, lines);
    group.onmouseleave = hideTip;
    group.onclick = () => { hideTip(); openMetricsDetail(p.id); };
    group.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openMetricsDetail(p.id); } };
    plot.append(group);
  });
  svg.append(plot);
  const labelEvery = Math.max(1, Math.ceil(points.length / Math.max(2, Math.floor(width / 90))));
  points.forEach((p, i) => {
    if (i % labelEvery && i !== points.length - 1) return;
    const d = new Date(p.started_at);
    svg.append(s('text', {x: x(i), y: height - 6, class: 'm-axis', 'text-anchor': 'middle'}, Number.isNaN(d.getTime()) ? '' : (d.getMonth() + 1) + '/' + d.getDate()));
  });
  box.replaceChildren(svg);
  requestAnimationFrame(() => plot.classList.add('ready'));
}
new ResizeObserver(() => { if (M.data && !$('metrics-view').hidden && !$('metrics-overview').hidden) renderTrend([...M.data.records].reverse()); }).observe($('m-trend'));

function spark(points, peak) {
  if (!points?.length) return h('span', {class: 'm-note', text: '—'});
  const w = 96, hgt = 26, top = Math.max(LV_OK * 2, peak || 0);
  const end = points[points.length - 1][0] || 1;
  const path = points.map(([t, v], i) => (i ? 'L' : 'M') + (t / end * w).toFixed(1) + ' ' + (hgt - 2 - Math.min(v, top) / top * (hgt - 4)).toFixed(1)).join(' ');
  return s('svg', {viewBox: `0 0 ${w} ${hgt}`, width: w, height: hgt, class: 'm-spark lv-' + level(peak), 'aria-hidden': 'true'},
    s('path', {d: path + ` L ${w} ${hgt} L 0 ${hgt} Z`, class: 'm-spark-area'}), s('path', {d: path, class: 'm-spark-line'}));
}
function badges(r) {
  const list = [];
  if (r.legacy) list.push(['旧记录', 'info']);
  else if (!r.first && r.live) list.push([num(r.audio_seconds) < 60 ? '时长过短' : '无句子计时', 'info']);
  if (r.behind_seconds) list.push(['落后 ' + secText(r.asr_lag?.max), 'bad']);
  if (r.translation_errors) list.push(['翻译失败 ' + r.translation_errors, 'bad']);
  if (r.gaps) list.push(['待核查 ' + r.gaps, 'warn']);
  if (r.recovered) list.push(['恢复 ' + r.recovered, 'ok']);
  if (!list.length) list.push(['正常', 'ok']);
  return h('div', {class: 'm-badges'}, list.map(([text, lv]) => h('span', {class: 'm-badge ' + lv, text})));
}
function recordRow(r) {
  const meta = [when(r.started_at), longDuration(r.audio_seconds), SOURCE_NAME[r.source] || '', r.model === 'turbo' ? 'Turbo' : 'Small', MODE_SHORT[r.language_mode] || ''].filter(Boolean).join(' · ');
  const latency = r.first
    ? h('div', {class: 'm-lat'}, h('b', {class: 'lv-' + level(r.first.p50), text: secText(r.first.p50)}), h('small', {text: '95% ' + secText(r.first.p95)}))
    : h('div', {class: 'm-lat'}, h('small', {text: r.live ? '无数据' : '导入文件'}));
  return h('button', {type: 'button', class: 'm-row', onclick: () => openMetricsDetail(r.id), 'aria-label': '查看「' + r.title + '」的运行数据'},
    h('div', {class: 'm-row-main'}, h('strong', {text: r.title}), h('span', {text: meta})),
    latency, h('div', {class: 'm-row-spark'}, spark(r.spark, r.asr_lag?.max)), badges(r), h('span', {class: 'm-chev', 'aria-hidden': 'true', text: '›'}));
}

/* ---------- 单条记录 ---------- */
async function openMetricsDetail(id) {
  const request = ++M.request;
  hideTip();
  try {
    const data = await getJSON('/api/sessions/' + encodeURIComponent(id) + '/metrics');
    if (request !== M.request) return;
    M.detail = data;
    M.filter = 'all';
    renderDetail(data);
    $('metrics-overview').hidden = true;
    $('metrics-detail').hidden = false;
    $('metrics-view').scrollTop = 0;
    window.scrollTo(0, 0);
  } catch (e) { flash(e.message || '读不到这条记录的数据'); }
}
function backToOverview() {
  $('metrics-detail').hidden = true;
  $('metrics-overview').hidden = false;
  if (M.data) renderTrend([...M.data.records].reverse());
}
async function jumpTo(id, seconds) {
  M.returnToDetail = true;
  // 等字幕页完成首次布局（它会先滚到顶部）再定位。
  if (await openHistory(id, false, 'metrics')) setTimeout(() => revealTime(seconds), 80);
}

function renderDetail(d) {
  const back = h('button', {type: 'button', class: 'm-back', onclick: backToOverview, text: '‹ 运行数据'});
  const chips = [longDuration(d.audio_seconds), SOURCE_NAME[d.source], d.model === 'turbo' ? 'Whisper Turbo' : 'Whisper Small', LANGUAGE_MODES[d.language_mode]].filter(Boolean);
  const header = h('header', {class: 'm-detail-head'},
    h('div', {}, back, h('p', {class: 'eyebrow', text: when(d.started_at, true)}), h('h1', {text: d.title}),
      h('div', {class: 'm-chips'}, chips.map(c => h('span', {class: 'm-chip', text: c})))),
    h('div', {class: 'm-actions'},
      h('button', {type: 'button', class: 'btn-ghost', onclick: () => jumpTo(d.id, 0), text: '打开字幕'}),
      h('a', {class: 'btn-ghost', href: '/api/export/' + encodeURIComponent(d.id) + '/events', text: '导出日志'}),
      h('button', {type: 'button', class: 'btn-ghost', onclick: e => copySummary(d, e.currentTarget), text: '复制摘要'})));
  const kpis = h('div', {class: 'm-kpis'},
    kpi('出字延迟', d.first?.p50, d.first ? '95% ' + secText(d.first.p95) + ' · 最慢 ' + secText(d.first.max) : (d.live ? '无数据' : '导入文件不计')),
    kpi('定稿延迟', d.final?.p50, d.final ? '95% ' + secText(d.final.p95) : '无数据'),
    kpi('翻译一句', d.stages?.translate?.p50, d.stages?.translate ? '最慢 ' + secText(d.stages.translate.max) : '旧记录无此数据', true),
    kpi('识别落后峰值', d.asr_lag?.max, d.asr_lag ? '中位 ' + secText(d.asr_lag.p50) : '无数据'),
    h('div', {class: 'm-kpi'}, h('span', {class: 'm-label', text: '内存峰值'}), h('div', {class: 'm-num'}, h('b', {text: d.memory_peak ? (d.memory_peak / 1024 ** 3).toFixed(1) : '—'}), h('small', {text: 'GB'})), h('p', {class: 'm-note', text: '本应用及其子进程，估算'})));
  const timeline = h('section', {class: 'm-card'}, h('div', {class: 'm-card-head'}, h('h2', {text: '整场时间线'}),
    h('span', {class: 'm-legend'}, h('i', {class: 'm-key lag'}), '识别落后 ', h('i', {class: 'm-dot lv-ok'}), '每句出中文延迟 ', h('i', {class: 'm-tick'}), '事件')),
    timelineChart(d));
  const lower = h('div', {class: 'm-split'}, stagesCard(d), histogramCard(d));
  M.eventsBox = h('div', {class: 'm-events'});
  const events = h('section', {class: 'm-card'}, h('div', {class: 'm-card-head'}, h('h2', {text: '异常与事件'}),
    h('span', {class: 'm-note', text: d.event_total > d.events.length ? '显示前 ' + d.events.length + ' 条，共 ' + d.event_total + ' 条' : ''})),
    eventFilters(d), M.eventsBox);
  renderEvents(d);
  $('metrics-detail').replaceChildren(header, kpis, timeline, lower, events, logCard(d));
}
function kpi(label, value, note, precise = false) {
  const number = h('b', {class: 'lv-' + (precise ? 'ok' : level(value))});
  countUp(number, value, precise ? v => v.toFixed(2) : sec);
  return h('div', {class: 'm-kpi'}, h('span', {class: 'm-label', text: label}), h('div', {class: 'm-num'}, number, h('small', {text: '秒'})), h('p', {class: 'm-note', text: note}));
}

/* 时间线：上方热力条（每 5 秒一格，按落后程度着色）和事件刻度，下方为曲线。 */
function timelineChart(d) {
  const box = h('div', {class: 'm-chart m-timeline'});
  const draw = () => {
    const lag = d.series.lag, asr = d.series.asr, sentences = d.series.sentences;
    const end = Math.max(num(d.audio_seconds), ...lag.map(p => p[0]), ...sentences.map(p => p[0]), 1);
    const width = Math.max(320, box.clientWidth || 760), height = 230;
    const pad = {l: 34, r: 14}, stripY = 18, stripH = 12, chartTop = 46, chartBottom = height - 24;
    const top = Math.min(60, Math.max(LV_OK * 2, Math.ceil(Math.max(0, ...asr.map(p => p[1]), ...sentences.map(p => p[1])) / 5) * 5));
    const x = t => pad.l + t / end * (width - pad.l - pad.r);
    const y = v => chartTop + (1 - Math.min(v, top) / top) * (chartBottom - chartTop);
    const svg = s('svg', {viewBox: `0 0 ${width} ${height}`, width: '100%', height, class: 'm-svg', role: 'img', 'aria-label': '整场识别落后与每句出中文延迟随时间的变化'});
    if (!lag.length && !sentences.length) { box.replaceChildren(h('p', {class: 'm-note', text: '这条记录没有保存运行中的计时数据。'})); return; }
    const strip = s('g', {class: 'm-strip'});
    lag.forEach(([t, v], i) => {
      const next = lag[i + 1] ? lag[i + 1][0] : end;
      strip.append(s('rect', {x: x(t), y: stripY, width: Math.max(1, x(next) - x(t) + .5), height: stripH, class: 'cell lv-' + level(v)}));
    });
    svg.append(s('text', {x: pad.l - 6, y: stripY + 10, class: 'm-axis', 'text-anchor': 'end'}, '状态'), strip);
    for (const e of d.events) {
      const info = KIND[e.kind] || {level: 'info'};
      svg.append(s('path', {d: `M ${x(e.t)} ${stripY - 3} l -4 -7 h 8 z`, class: 'm-marker ' + info.level}));
    }
    for (const [v, label, cls] of [[0, '0', 'm-grid'], [LV_OK, '4 秒', 'm-grid ok'], [LV_BEHIND, '15 秒', 'm-grid bad']]) {
      if (v > top) continue;
      svg.append(s('line', {x1: pad.l, x2: width - pad.r, y1: y(v), y2: y(v), class: cls}), s('text', {x: pad.l - 6, y: y(v) + 4, class: 'm-axis', 'text-anchor': 'end'}, label));
    }
    if (asr.length) {
      const line = asr.map(([t, v], i) => (i ? 'L' : 'M') + x(t).toFixed(1) + ' ' + y(v).toFixed(1)).join(' ');
      svg.append(s('path', {d: line + ` L ${x(asr[asr.length - 1][0])} ${y(0)} L ${x(asr[0][0])} ${y(0)} Z`, class: 'm-area'}), s('path', {d: line, class: 'm-line'}));
    }
    const dots = s('g', {class: 'm-rise'});
    for (const [t, v] of sentences) dots.append(s('circle', {cx: x(t), cy: y(v), r: 3.4, class: 'm-dot-svg lv-' + level(v)}));
    svg.append(dots);
    const ticks = Math.max(2, Math.min(8, Math.floor(width / 110)));
    for (let i = 0; i <= ticks; i++) {
      const t = end * i / ticks;
      svg.append(s('text', {x: x(t), y: height - 6, class: 'm-axis', 'text-anchor': i === 0 ? 'start' : i === ticks ? 'end' : 'middle'}, clock(t)));
    }
    const cursor = s('line', {y1: stripY - 2, y2: chartBottom, class: 'm-cursor', visibility: 'hidden'});
    const hit = s('rect', {x: pad.l, y: 0, width: width - pad.l - pad.r, height: chartBottom, fill: 'transparent'});
    hit.onmousemove = event => {
      const rect = svg.getBoundingClientRect();
      const t = Math.max(0, Math.min(end, ((event.clientX - rect.left) * width / rect.width - pad.l) / (width - pad.l - pad.r) * end));
      cursor.setAttribute('x1', x(t)); cursor.setAttribute('x2', x(t)); cursor.setAttribute('visibility', 'visible');
      const near = (list, span) => list.reduce((best, p) => Math.abs(p[0] - t) < Math.abs((best?.[0] ?? Infinity) - t) ? p : best, null);
      const a = near(asr), sNear = near(sentences);
      const ev = d.events.find(e => Math.abs(e.t - t) <= end / 120);
      showTip(event, [clock(t), a ? '识别落后 ' + secText(a[1]) : '', sNear && Math.abs(sNear[0] - t) < end / 60 ? '这句出中文用了 ' + secText(sNear[1]) : '', ev ? '● ' + ev.text : '', '点击跳到这里的字幕']);
    };
    hit.onmouseleave = () => { cursor.setAttribute('visibility', 'hidden'); hideTip(); };
    hit.onclick = event => {
      const rect = svg.getBoundingClientRect();
      const t = Math.max(0, ((event.clientX - rect.left) * width / rect.width - pad.l) / (width - pad.l - pad.r) * end);
      hideTip(); jumpTo(d.id, t);
    };
    svg.append(cursor, hit);
    box.replaceChildren(svg);
    requestAnimationFrame(() => { dots.classList.add('ready'); strip.classList.add('ready'); });
  };
  requestAnimationFrame(draw);
  new ResizeObserver(() => { if (!$('metrics-detail').hidden && box.isConnected) draw(); }).observe(box);
  return box;
}

/* 一句话的时间花在哪：等待定稿 → 排队 → 翻译（中位数）。 */
function stagesCard(d) {
  const card = h('section', {class: 'm-card'}, h('div', {class: 'm-card-head'}, h('h2', {text: '一句话的时间花在哪'}), h('span', {class: 'm-note', text: '中位数'})));
  const st = d.stages;
  if (!st) { card.append(h('p', {class: 'm-note', text: '这条记录较早，没有分阶段计时。'})); return card; }
  const parts = [['等待定稿', st.finalize?.p50, 'finalize', '字幕出现后，等原文确认'], ['排队', st.queue?.p50, 'queue', '等前面的句子翻译完'], ['翻译', st.translate?.p50, 'translate', '本地模型翻译这一句']];
  const total = parts.reduce((n, p) => n + num(p[1]), 0) || 1;
  const bar = h('div', {class: 'm-stack'});
  const legend = h('div', {class: 'm-stack-legend'});
  for (const [label, value, cls, note] of parts) {
    const seg = h('span', {class: 'm-stack-seg ' + cls});
    seg.style.setProperty('--w', Math.max(value ? 2 : 0, num(value) / total * 100) + '%');
    seg.onmousemove = e => showTip(e, [label + ' ' + secText(value), note]);
    seg.onmouseleave = hideTip;
    bar.append(seg);
    legend.append(h('div', {}, h('i', {class: 'm-key ' + cls}), h('span', {text: label}), h('b', {text: value == null ? '—' : value < 1 ? value.toFixed(2) + ' 秒' : secText(value)}), h('small', {text: note})));
  }
  requestAnimationFrame(() => bar.classList.add('ready'));
  const biggest = parts.reduce((a, b) => num(b[1]) > num(a[1]) ? b : a);
  card.append(bar, legend, h('p', {class: 'm-insight', text: '主要时间在「' + biggest[0] + '」。' + (biggest[2] === 'finalize' ? '这是识别在等一句话说完，不是翻译慢。' : biggest[2] === 'translate' ? '翻译模型是这次的主要耗时。' : '翻译排队较多，说话很密时会出现。')}));
  return card;
}
function histogramCard(d) {
  const card = h('section', {class: 'm-card'}, h('div', {class: 'm-card-head'}, h('h2', {text: '出字延迟分布'}), h('span', {class: 'm-note', text: d.first ? d.first.n + ' 句' : ''})));
  const total = d.histogram.reduce((n, b) => n + b.count, 0);
  if (!total) { card.append(h('p', {class: 'm-note', text: d.live ? '没有可统计的句子。' : '导入文件不计实时延迟。'})); return card; }
  const peak = Math.max(...d.histogram.map(b => b.count));
  const bars = h('div', {class: 'm-hist'});
  d.histogram.forEach((b, i) => {
    const name = b.high == null ? b.low + '+ 秒' : b.low + '–' + b.high;
    const col = h('div', {class: 'm-hist-col'}, h('small', {text: Math.round(b.count / total * 100) + '%'}), h('span', {class: 'm-hist-bar bin-' + i}), h('em', {text: name}));
    col.querySelector('.m-hist-bar').style.setProperty('--h', (b.count / peak * 100) + '%');
    col.onmousemove = e => showTip(e, [name + (b.high == null ? '' : ' 秒'), b.count + ' 句']);
    col.onmouseleave = hideTip;
    bars.append(col);
  });
  requestAnimationFrame(() => bars.classList.add('ready'));
  card.append(bars);
  return card;
}

function eventFilters(d) {
  const counts = {};
  for (const e of d.events) counts[e.kind] = (counts[e.kind] || 0) + 1;
  const kinds = Object.keys(KIND).filter(k => counts[k]);
  const box = h('div', {class: 'm-filter-chips', role: 'group', 'aria-label': '按类型筛选事件'});
  if (!kinds.length) return box;
  const make = (key, label, n) => h('button', {type: 'button', class: 'chip', 'aria-pressed': String(M.filter === key), onclick: e => {
    M.filter = key;
    box.querySelectorAll('.chip').forEach(c => c.setAttribute('aria-pressed', String(c === e.currentTarget)));
    renderEvents(d);
  }}, label + ' ' + n);
  box.append(make('all', '全部', d.events.length), ...kinds.map(k => make(k, KIND[k].label, counts[k])));
  return box;
}
function renderEvents(d) {
  const list = d.events.filter(e => M.filter === 'all' || e.kind === M.filter);
  if (!list.length) {
    M.eventsBox.replaceChildren(h('p', {class: 'm-calm', text: d.detailed || d.events.length ? '这一场没有记录到异常。' : '这条记录较早，没有保存逐条事件。'}));
    return;
  }
  M.eventsBox.replaceChildren(...list.map(e => {
    const info = KIND[e.kind] || {label: e.kind, level: 'info'};
    return h('button', {type: 'button', class: 'm-event', onclick: () => jumpTo(d.id, e.t)},
      h('span', {class: 'm-event-time', text: clock(e.t) + (e.end && e.end - e.t >= 1 ? '–' + clock(e.end) : '')}),
      h('i', {class: 'm-dot ' + info.level}), h('span', {class: 'm-event-kind', text: info.label}), h('span', {class: 'm-event-text', text: e.text}),
      h('span', {class: 'm-event-go', text: '跳到字幕 ›'}));
  }));
}
function logCard(d) {
  const c = d.counts || {};
  const items = [['识别调用', c.asr_attempt], ['翻译尝试', c.translation_attempt], ['字幕修订', c.segment], ['状态快照', c.status], ['覆盖审计', c.asr_coverage], ['质量审计', c.asr_quality]].filter(([, n]) => n);
  return h('section', {class: 'm-card m-log'}, h('div', {class: 'm-card-head'}, h('h2', {text: '运行日志'}), h('span', {class: 'm-note', text: bytes(d.log_bytes)})),
    h('div', {class: 'm-log-grid'}, items.map(([label, n]) => h('div', {}, h('b', {text: n.toLocaleString('zh-CN')}), h('span', {text: label})))),
    h('p', {class: 'm-note', text: '日志随这条记录保存在本机，删除记录时一起删除。导出的日志为 JSON Lines 格式，可能包含字幕文字，分享前请先查看。'}));
}
async function copySummary(d, button) {
  const lines = [d.title + '（' + when(d.started_at, true) + '，' + longDuration(d.audio_seconds) + '，' + (d.model === 'turbo' ? 'Turbo' : 'Small') + '，' + (LANGUAGE_MODES[d.language_mode] || '') + '）',
    '出字延迟：中位 ' + secText(d.first?.p50) + '，95% ' + secText(d.first?.p95) + '，最慢 ' + secText(d.first?.max),
    '定稿延迟：中位 ' + secText(d.final?.p50) + '，95% ' + secText(d.final?.p95),
    '识别落后：峰值 ' + secText(d.asr_lag?.max) + '；翻译一句中位 ' + (d.stages?.translate ? d.stages.translate.p50.toFixed(2) + ' 秒' : '—'),
    '待核查区间 ' + d.gaps + '，自动恢复 ' + d.recovered + '，翻译失败 ' + d.translation_errors,
    ...d.events.slice(0, 20).map(e => '  ' + clock(e.t) + ' ' + e.text)];
  try { await navigator.clipboard.writeText(lines.join('\n')); button.textContent = '已复制'; setTimeout(() => { button.textContent = '复制摘要'; }, 1600); }
  catch { flash('复制没有成功。', 'error', 3000); }
}
