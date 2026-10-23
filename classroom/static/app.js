'use strict';
const $ = id => document.getElementById(id);
const ACTIVE = new Set(['loading', 'running', 'draining']);
const SOURCE_NAME = {microphone: '麦克风', system: '电脑声音', file: '导入录音'};
const SOURCE_HINT = {
  microphone: '使用 Mac 当前的默认麦克风。第一次用时，系统会询问麦克风权限，点允许即可。',
  system: '会听到电脑上所有正在播放的声音。第一次用时，macOS 可能要求「屏幕与系统音频录制」权限，不会保存任何画面。',
  file: '选一个本机的录音或视频，按原速读取并生成字幕。'
};
const SIZES = [22, 26, 30, 36, 44, 54];
const MAX_LINES = 500;

const pref = {
  size: clampSize(Number(localStorage.getItem('tk.size') ?? 2)),
  zhOnly: localStorage.getItem('tk.zhOnly') === '1',
  model: localStorage.getItem('tk.model') === 'turbo' ? 'turbo' : 'small',
  source: ['microphone', 'system', 'file'].includes(localStorage.getItem('tk.source')) ? localStorage.getItem('tk.source') : 'microphone'
};
let token = '';
let ready = false;
let connected = false;
let everConnected = false;
let ws = null;
let reconnectTimer = null;
let reconnectDelay = 1500;
let current = null;       // 当前会话状态
let view = 'start';
let pending = '';         // 'upload' | 'start' | 'stop'
let startNote = {text: '', error: false};
let transient = null;     // 临时提示 {text, kind, until}
let following = true;
let stopArmed = null;
const rows = new Map();

function clampSize(i) { return Math.min(SIZES.length - 1, Math.max(0, Number.isFinite(i) ? i : 2)); }
const num = v => Number.isFinite(Number(v)) ? Number(v) : 0;
const clock = sec => {
  const s = Math.max(0, Math.floor(num(sec)));
  const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60;
  return (h ? h + ':' + String(m).padStart(2, '0') : String(m).padStart(2, '0')) + ':' + String(s % 60).padStart(2, '0');
};
const duration = sec => {
  const s = Math.round(num(sec));
  if (s < 60) return s + ' 秒';
  const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
  return h ? h + ' 小时 ' + m + ' 分' : m + ' 分钟';
};
const modelName = m => m === 'turbo' ? 'Whisper Turbo' : 'Whisper Small';

async function getJSON(path, options = {}) {
  const res = await fetch(path, {...options, cache: 'no-store'});
  let data;
  try { data = await res.json(); } catch { data = {}; }
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '操作没有成功（' + res.status + '）');
  return data;
}
function post(path, body) {
  const form = body instanceof FormData;
  return getJSON(path, {
    method: 'POST',
    headers: {'x-session-token': token, ...(form ? {} : {'Content-Type': 'application/json'})},
    body: form ? body : JSON.stringify(body || {})
  });
}

/* ---------- 视图切换 ---------- */
function show(next) {
  view = next;
  $('start-view').hidden = next !== 'start';
  $('live-view').hidden = next !== 'live';
  document.title = next === 'live' && current ? (current.title || '课堂') + ' · 听课' : '听课';
  if (next === 'start') { renderStart(); loadHistory(); }
  else { renderLive(); requestAnimationFrame(() => toEnd(false)); }
}

/* ---------- 开始页 ---------- */
function selectSource(value) {
  pref.source = value;
  localStorage.setItem('tk.source', value);
  document.querySelectorAll('.source').forEach(el => el.setAttribute('aria-checked', String(el.dataset.source === value)));
  $('file-box').hidden = value !== 'file';
  $('source-hint').textContent = SOURCE_HINT[value];
  renderStart();
}
function chosenFile() { return $('file').files[0] || null; }
function showFile() {
  const f = chosenFile();
  $('file-box').classList.toggle('has-file', !!f);
  $('file-text').textContent = f ? f.name + ' · ' + (f.size / 1024 ** 2).toFixed(1) + ' MB' : '点这里选文件，或把文件拖进来';
  renderStart();
}
function renderStart() {
  const conn = $('conn');
  const lost = !connected && (everConnected || failures >= 2);
  conn.className = 'conn ' + (connected && ready ? 'ok' : lost || (connected && !ready) ? 'bad' : '');
  $('conn-text').textContent = connected ? (ready ? '本机已就绪' : '模型还没准备好') : everConnected ? '连接断开，正在重连…' : lost ? '连不上本机服务' : '正在连接…';

  const busy = current && ACTIVE.has(current.state);
  const needFile = pref.source === 'file' && !chosenFile();
  const start = $('start');
  start.disabled = !connected || !ready || !!pending || busy || needFile;
  start.textContent = pending === 'upload' ? '正在导入…' : pending === 'start' ? '正在启动…' : '开始听课';
  document.querySelectorAll('.source').forEach(el => { el.disabled = !!pending; });

  const note = $('start-note');
  let text = startNote.text, error = startNote.error;
  if (!text) {
    if (lost) { text = '连不上本机服务。请双击 outputs 里的「启动课堂翻译.command」，再刷新这个页面。'; error = true; }
    else if (connected && !ready) { text = '识别和翻译模型还没部署完成，暂时不能开始。'; error = true; }
    else if (busy) text = '另一个窗口正在上课，可以直接去看字幕。';
    else if (needFile) text = '先选一个录音文件。';
  }
  note.textContent = text;
  note.className = 'start-note' + (error ? ' error' : '');
  if (busy && !pending) {
    note.append(' ');
    const a = document.createElement('a');
    a.href = '#'; a.textContent = '打开字幕 →';
    a.onclick = e => { e.preventDefault(); show('live'); };
    note.append(a);
  }
  renderLast();
}
function renderLast() {
  const box = $('last');
  const s = current;
  if (!s || ACTIVE.has(s.state) || !rows.size) { box.hidden = true; return; }
  box.hidden = false;
  $('last-title').textContent = s.title || '课堂记录';
  $('last-meta').textContent = duration(s.audio_seconds) + ' · ' + rows.size + ' 句 · ' + (s.state === 'completed' ? '已保存' : s.state === 'error' ? '出错，已收到的内容已保存' : '中断，已收到的内容已保存');
}

async function loadHistory() {
  try {
    const list = await getJSON('/api/sessions');
    const items = [...list].sort((a, b) => String(b.started_at || b.id).localeCompare(String(a.started_at || a.id))).slice(0, 20);
    $('history').replaceChildren(...items.map(historyRow));
    $('history-empty').hidden = items.length > 0;
  } catch {
    $('history-empty').hidden = false;
    $('history-empty').textContent = '暂时读不到以前的记录。';
  }
}
function historyRow(item) {
  const li = document.createElement('li');
  const main = document.createElement('div');
  main.className = 'h-main';
  const title = document.createElement('span');
  title.className = 'h-title';
  title.textContent = item.title || '课堂记录';
  const meta = document.createElement('span');
  meta.className = 'h-meta';
  const d = new Date(item.started_at);
  const when = Number.isNaN(d.getTime()) ? String(item.id || '').slice(0, 13) : d.toLocaleString('zh-CN', {month: 'numeric', day: 'numeric', weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false});
  meta.textContent = when + ' · ' + duration(item.audio_seconds) + ' · ' + (SOURCE_NAME[item.source] || '音频');
  const isLive = current && item.id === current.id && ACTIVE.has(current.state);
  const odd = !isLive && item.state && item.state !== 'completed';
  if (isLive || odd) {
    const tag = document.createElement('span');
    tag.className = 'warn';
    tag.textContent = isLive ? ' · 正在进行' : item.state === 'error' ? ' · 出错' : ' · 未正常结束';
    meta.append(tag);
  }
  main.append(title, meta);
  li.append(main);
  for (const [kind, label] of [['txt', '双语文本'], ['srt', '字幕 SRT']]) {
    const a = document.createElement('a');
    a.href = '/api/export/' + encodeURIComponent(item.id) + '/' + kind;
    a.textContent = label;
    a.setAttribute('aria-label', '下载「' + (item.title || '课堂记录') + '」' + label);
    li.append(a);
  }
  return li;
}

/* ---------- 上课页 ---------- */
const STATE_LABEL = {loading: '准备中', running: '正在听', draining: '正在收尾', completed: '已结束', error: '出错了', interrupted: '已中断'};
const EMPTY_TEXT = {
  loading: '正在加载本地模型，第一次可能要十几秒…',
  running: '正在听。识别到英语后，字幕会出现在这里。',
  draining: '正在处理最后一段…',
  completed: '这堂课没有识别到英语内容。',
  error: '这堂课出了问题，没有生成字幕。',
  interrupted: '这堂课中断了，没有生成字幕。'
};
function renderLive() {
  const s = current;
  if (!s) return;
  const active = ACTIVE.has(s.state);
  $('rec').className = 'rec ' + ({running: 'on', loading: 'busy', draining: 'busy', completed: 'ok'}[s.state] || 'bad');
  $('live-state').textContent = STATE_LABEL[s.state] || '—';
  $('live-clock').textContent = clock(s.audio_seconds);
  $('live-title').textContent = s.title || '课堂记录';
  if (view === 'live') document.title = (s.title || '课堂') + ' · 听课';

  const lit = s.state === 'running' ? Math.min(5, Math.round(num(s.level) * 30)) : 0;
  const bars = $('live-level');
  bars.classList.toggle('lit', lit > 0);
  [...bars.children].forEach((b, i) => { b.style.height = (i < lit ? 5 + i * 2.2 : 3) + 'px'; });

  const stop = $('stop');
  stop.hidden = !active;
  stop.disabled = !connected || pending === 'stop' || s.state === 'draining';
  stop.textContent = s.state === 'draining' || pending === 'stop' ? '保存中…' : stopArmed ? '确定结束？' : '结束';
  stop.classList.toggle('armed', !!stopArmed);

  $('i-lag').textContent = active ? num(s.asr_lag).toFixed(1) + ' 秒' : '—';
  $('i-queue').textContent = active ? num(s.translation_queue) + (s.translation_busy ? ' 句（1 句翻译中）' : ' 句') : '—';
  $('i-wait').textContent = active && num(s.translation_queue) ? num(s.translation_wait).toFixed(1) + ' 秒' : '—';
  $('i-mem').textContent = s.memory_bytes ? (num(s.memory_bytes) / 1024 ** 3).toFixed(1) + ' GB' : '—';
  $('i-model').textContent = modelName(s.model);
  $('i-source').textContent = SOURCE_NAME[s.source] || '—';
  $('i-count').textContent = rows.size + ' 句' + (s.final_count != null ? '，已确认 ' + s.final_count : '');
  for (const [id, kind] of [['dl-txt', 'txt'], ['dl-srt', 'srt'], ['dl-txt-live', 'txt'], ['dl-srt-live', 'srt']]) {
    $(id).href = '/api/export/' + encodeURIComponent(s.id) + '/' + kind;
  }

  $('live-empty').hidden = rows.size > 0;
  $('live-empty-text').textContent = EMPTY_TEXT[s.state] || '';
  $('live-empty').querySelector('.pulse').hidden = !active;

  $('back').hidden = active;
  const done = $('done');
  done.hidden = active;
  if (!active) {
    $('done-title').textContent = s.state === 'completed' ? '这堂课已保存' : s.state === 'error' ? '这堂课出错停止了' : '这堂课中断了';
    $('done-sub').textContent = duration(s.audio_seconds) + ' · ' + rows.size + ' 句 · 录音和字幕都保存在这台电脑上';
  }
  renderBanner();
}
function renderBanner() {
  const b = $('banner');
  const s = current;
  let text = '', kind = '';
  if (transient && transient.until > Date.now()) ({text, kind} = transient);
  else if (!connected) { text = '和本机服务的连接断了，正在重连。录音不受影响，会继续在后台进行。'; kind = 'error'; }
  else if (s?.error) { text = s.error; kind = 'error'; }
  else if (s?.state === 'draining') { text = '已停止收音，正在把最后几句处理完并保存，请稍等，不要关闭启动程序。'; kind = 'info'; }
  else if (s?.state === 'interrupted') { text = '会话中断了。已收到的声音保存在本机，可以用「已有录音」重新处理。'; kind = 'error'; }
  else if (s?.state === 'running' && num(s.input_idle) > 5) { text = '已经 ' + Math.round(num(s.input_idle)) + ' 秒没收到声音了。检查一下声音有没有在播放、输入设备或系统权限。'; kind = 'warn'; }
  else if (s?.state === 'running' && (num(s.asr_lag) > 30 || num(s.translation_wait) > 30)) { text = '处理落后比较多，正在追。声音都在保存，不会丢。'; kind = 'warn'; }
  else if (s?.state === 'running' && (num(s.asr_lag) > 15 || num(s.translation_wait) > 15)) { text = '字幕稍慢，正在追上…'; kind = 'warn'; }
  b.hidden = !text;
  b.textContent = text;
  b.className = 'banner' + (kind && kind !== 'warn' ? ' ' + kind : '');
}
function flash(text, kind = 'error', ms = 6000) {
  transient = {text, kind, until: Date.now() + ms};
  if (view === 'live') renderBanner();
  else { startNote = {text, error: kind === 'error'}; renderStart(); }
  setTimeout(() => {
    if (transient && transient.until <= Date.now()) { transient = null; renderBanner(); }
    if (startNote.text === text) { startNote = {text: '', error: false}; renderStart(); }
  }, ms + 50);
}

/* ---------- 字幕行 ---------- */
function upsertLine(data) {
  if (!data.id) return;
  let entry = rows.get(data.id);
  if (!entry) {
    const li = document.createElement('li');
    li.className = 'line';
    li.innerHTML = '<span class="t"></span><div class="en" lang="en"></div><div class="zh" lang="zh-CN"></div><button type="button" class="retry" hidden>重新翻译这句</button>';
    li.querySelector('.retry').onclick = () => retry(data.id, li.querySelector('.retry'));
    $('lines').append(li);
    entry = {li, data};
    rows.set(data.id, entry);
    while (rows.size > MAX_LINES) {
      const [key, old] = rows.entries().next().value;
      old.li.remove();
      rows.delete(key);
    }
  }
  entry.data = data;
  paint(entry);
  ageLines();
  $('live-empty').hidden = true;
}
function paint({li, data}) {
  const zhText = typeof data.translation === 'string' ? data.translation.trim() : '';
  const matched = !!zhText && data.translated_revision != null && data.translated_revision === data.revision;
  const failed = !!data.translation_error && !matched;
  const settled = data.final && matched;
  li.className = 'line' + (!zhText ? (failed ? ' failed' : ' waiting') : settled ? '' : ' draft') + (li.classList.contains('old') ? ' old' : '');
  li.querySelector('.t').textContent = clock(data.start);
  li.querySelector('.en').textContent = data.source || '';
  li.querySelector('.zh').textContent = zhText || (failed ? '这句没翻译出来' : '翻译中…');
  li.title = failed ? '翻译失败：' + data.translation_error : '';
  const btn = li.querySelector('.retry');
  btn.hidden = !failed || current?.state !== 'running';
  btn.disabled = false;
  btn.textContent = '重新翻译这句';
}
function repaintAll() { for (const e of rows.values()) paint(e); }
// 跟随最新内容时，较早的句子稍微变淡，让视线落在最新几句上
function ageLines() {
  const dim = following && current && ACTIVE.has(current.state);
  const list = [...rows.values()];
  list.forEach(({li}, i) => li.classList.toggle('old', dim && i < list.length - 4));
}
async function retry(id, btn) {
  btn.disabled = true;
  btn.textContent = '已提交…';
  try { await post('/api/retry/' + encodeURIComponent(id)); btn.textContent = '已重新排队'; }
  catch (e) { btn.disabled = false; btn.textContent = '重新翻译这句'; flash(e.message); }
}

/* ---------- 滚动：默认跟随，往上翻就暂停，一键回来 ---------- */
let autoScrollUntil = 0;
const scroller = $('scroller');
function nearEnd() { return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 120; }
function toEnd(smooth) {
  following = true;
  $('jump').hidden = true;
  autoScrollUntil = Date.now() + (smooth ? 600 : 80);
  scroller.scrollTo({top: scroller.scrollHeight, behavior: smooth ? 'smooth' : 'auto'});
  ageLines();
}
function keepFollowing() { if (following && view === 'live') toEnd(false); else if (!following) $('jump').hidden = false; }
scroller.addEventListener('scroll', () => {
  if (Date.now() < autoScrollUntil) return;
  const was = following;
  following = nearEnd();
  $('jump').hidden = following;
  if (was !== following) ageLines();
}, {passive: true});
$('jump').onclick = () => toEnd(true);

/* ---------- 状态与数据 ---------- */
function applyStatus(s) {
  const prev = current;
  if (prev && prev.id !== s.id) { rows.clear(); $('lines').replaceChildren(); }
  current = s;
  const nowActive = ACTIVE.has(s.state);
  if (nowActive && view !== 'live') show('live');
  if (!prev || prev.state !== s.state) { repaintAll(); ageLines(); }
  if (prev && ACTIVE.has(prev.state) && !nowActive) { disarmStop(); loadHistory(); }
  renderLive();
  if (view === 'start') renderStart();
}
function snapshot(status, segments) {
  rows.clear();
  $('lines').replaceChildren();
  current = null;
  if (status) applyStatus(status);
  for (const seg of segments || []) upsertLine(seg);
  if (current) renderLive();
  if (view === 'live') toEnd(false);
  else renderStart();
}
function clearSession() {
  current = null;
  rows.clear();
  $('lines').replaceChildren();
  if (view === 'live') show('start');
}

/* ---------- 操作 ---------- */
function disarmStop() { clearTimeout(stopArmed); stopArmed = null; }
$('stop').onclick = async () => {
  if (pending || !current || !ACTIVE.has(current.state)) return;
  // 防误触：第一次点只是确认，3 秒内再点才真正结束
  if (!stopArmed) {
    stopArmed = setTimeout(() => { stopArmed = null; renderLive(); }, 3000);
    renderLive();
    return;
  }
  disarmStop();
  pending = 'stop';
  renderLive();
  try {
    await post('/api/stop');
    const data = await getJSON('/api/state');
    if (data.session) applyStatus(data.session);
  } catch (e) { flash(e.message || '结束没有成功，请再试一次。'); }
  finally { pending = ''; renderLive(); }
};

$('start').onclick = async () => {
  if (pending || !connected || !ready) return;
  startNote = {text: '', error: false};
  try {
    let uploadId;
    if (pref.source === 'file') {
      const f = chosenFile();
      if (!f) throw new Error('先选一个录音文件。');
      if (!f.size) throw new Error('这个文件是空的，换一个试试。');
      if (f.size > 1024 ** 3) throw new Error('文件超过 1 GB，换一个小一点的。');
      pending = 'upload'; renderStart();
      const form = new FormData();
      form.append('file', f);
      uploadId = (await post('/api/uploads', form)).id;
    }
    pending = 'start'; renderStart();
    await post('/api/sessions', {source: pref.source, model: pref.model, title: $('title').value.trim(), upload_id: uploadId, realtime: true});
    const data = await getJSON('/api/state');
    pending = '';
    if (data.session) snapshot(data.session, data.segments);
    $('title').value = '';
    if (pref.source === 'file') { $('file').value = ''; showFile(); }
    show('live');
  } catch (e) {
    pending = '';
    startNote = {text: e.message || '没能开始，请再试一次。', error: true};
    renderStart();
  }
};
$('new-class').onclick = () => { startNote = {text: '', error: false}; show('start'); $('title').focus(); };
$('last-open').onclick = () => show('live');
$('back').onclick = () => show('start');

document.querySelectorAll('.source').forEach(el => { el.onclick = () => selectSource(el.dataset.source); });
$('file').onchange = showFile;
// 把文件拖到开始页任意位置都能导入
const startView = $('start-view');
startView.addEventListener('dragover', e => {
  if (pending || !e.dataTransfer?.types.includes('Files')) return;
  e.preventDefault();
  if (pref.source !== 'file') selectSource('file');
  $('file-box').classList.add('drag');
});
startView.addEventListener('dragleave', e => { if (!startView.contains(e.relatedTarget)) $('file-box').classList.remove('drag'); });
startView.addEventListener('drop', e => {
  e.preventDefault();
  $('file-box').classList.remove('drag');
  const f = e.dataTransfer?.files?.[0];
  if (!f || pending) return;
  const dt = new DataTransfer();
  dt.items.add(f);
  $('file').files = dt.files;
  showFile();
});
window.addEventListener('dragover', e => e.preventDefault());
window.addEventListener('drop', e => e.preventDefault());

document.querySelectorAll('input[name="model"]').forEach(el => {
  el.checked = el.value === pref.model;
  el.onchange = () => { pref.model = el.value; localStorage.setItem('tk.model', el.value); showModel(); };
});
function showModel() { $('model-tag').textContent = pref.model === 'turbo' ? '更准 · Turbo' : '快速 · Small'; }
showModel();

/* ---------- 阅读设置 ---------- */
function applyPrefs() {
  document.documentElement.style.setProperty('--zh', SIZES[pref.size] + 'px');
  $('lines').classList.toggle('zh-only', pref.zhOnly);
  $('zh-only').setAttribute('aria-pressed', String(pref.zhOnly));
  $('size-down').disabled = pref.size === 0;
  $('size-up').disabled = pref.size === SIZES.length - 1;
}
function setSize(delta) {
  pref.size = clampSize(pref.size + delta);
  localStorage.setItem('tk.size', String(pref.size));
  applyPrefs();
  if (following) toEnd(false);
}
$('size-down').onclick = () => setSize(-1);
$('size-up').onclick = () => setSize(1);
$('zh-only').onclick = () => {
  pref.zhOnly = !pref.zhOnly;
  localStorage.setItem('tk.zhOnly', pref.zhOnly ? '1' : '0');
  applyPrefs();
  if (following) toEnd(false);
};
function togglePop(open) {
  const want = open ?? $('info').hidden;
  $('info').hidden = !want;
  $('info-btn').setAttribute('aria-expanded', String(want));
}
$('info-btn').onclick = e => { e.stopPropagation(); togglePop(); };
document.addEventListener('click', e => { if (!$('info').hidden && !e.target.closest('.pop-wrap')) togglePop(false); });
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { togglePop(false); if (stopArmed) { disarmStop(); renderLive(); } }
  if (view !== 'live' || e.target.closest('input, textarea, select') || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === '+' || e.key === '=') setSize(1);
  else if (e.key === '-') setSize(-1);
  else if (e.key === 'End') toEnd(true);
});

/* ---------- 连接 ---------- */
let failures = 0;
function setConnected(value) {
  connected = value;
  if (value) { everConnected = true; failures = 0; }
  if (view === 'live') renderLive(); else renderStart();
}
function scheduleReconnect() {
  if (reconnectTimer) return;
  failures++;
  setConnected(false);
  reconnectTimer = setTimeout(() => { reconnectTimer = null; init(); }, reconnectDelay);
  reconnectDelay = Math.min(10000, reconnectDelay * 1.5);
}
function connect() {
  const socket = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws?token=' + encodeURIComponent(token));
  ws = socket;
  socket.onopen = () => { if (ws === socket) { reconnectDelay = 1500; setConnected(true); } };
  socket.onmessage = ev => {
    if (ws !== socket) return;
    let data;
    try { data = JSON.parse(ev.data); } catch { socket.close(); return; }
    if (data.type === 'snapshot') snapshot(data.status, data.segments);
    else if (data.type === 'segment') { upsertLine(data); keepFollowing(); if (current) renderLive(); }
    else if (data.type === 'status') applyStatus(data);
    else if (data.type === 'error') flash(data.message || '本机处理出了点问题。');
  };
  socket.onclose = () => { if (ws === socket) scheduleReconnect(); };
  socket.onerror = () => socket.close();
}
async function init(first = false) {
  try {
    const data = await getJSON('/api/state', {signal: AbortSignal.timeout(10000)});
    token = data.token;
    ready = !!data.ready;
    if (data.session) snapshot(data.session, data.segments);
    else clearSession();
    if (first) show(current && ACTIVE.has(current.state) ? 'live' : 'start');
    if (!ws || ws.readyState > WebSocket.OPEN) connect();
    else setConnected(true);
  } catch {
    if (first) show('start');
    scheduleReconnect();
  }
}
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && (!ws || ws.readyState === WebSocket.CLOSED) && !reconnectTimer) init();
});

applyPrefs();
selectSource(pref.source);
init(true);
