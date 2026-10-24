'use strict';
const $ = id => document.getElementById(id);
const ACTIVE = new Set(['loading', 'running', 'draining']);
const SOURCE_NAME = {microphone: '麦克风', system: '电脑声音', file: '导入录音'};
const SOURCE_HINT = {
  microphone: '使用 Mac 当前的默认麦克风。第一次用时，系统会询问麦克风权限，点允许即可。',
  system: '会听到电脑上所有正在播放的声音。第一次用时，macOS 可能要求「屏幕与系统音频录制」权限，不会保存任何画面。',
  file: '选一个本机的录音或视频，按原速读取并生成字幕。'
};
const SIZES = [13, 15, 17, 19, 22, 26, 30, 36];
const MAX_LINES = 500;

const pref = {
  zh: clampSize(Number(localStorage.getItem('tk.zhSize') ?? 2)),
  en: clampSize(Number(localStorage.getItem('tk.enSize') ?? 2)),
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
let viewingHistory = false;
let openingHistory = 0;
let current = null;       // 当前会话状态
let view = 'start';
let pending = '';         // 'upload' | 'start' | 'stop'
let startNote = {text: '', error: false};
let transient = null;     // 临时提示 {text, kind, until}
let following = true;
let stopArmed = null;
const rows = new Map();
const recordingPrompted = new Set();
let recordingDialogId = null;

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
  else { finishAll(); renderLive(); requestAnimationFrame(() => { if (viewingHistory) { following = false; scroller.scrollTop = 0; } else toEnd(false); }); }
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
  $('last-meta').textContent = duration(s.audio_seconds) + ' · ' + rows.size + ' 句 · ' + (s.state === 'completed' ? (s.integrity?.warning ? '已保存，待核查' : '已保存') : s.state === 'error' ? '出错，已收到的内容已保存' : '中断，已收到的内容已保存');
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
  const title = document.createElement('button');
  title.type = 'button'; title.className = 'h-title h-open';
  title.onclick = () => openHistory(item.id, !!item.busy);
  title.textContent = item.title || '课堂记录';
  const meta = document.createElement('span');
  meta.className = 'h-meta';
  const d = new Date(item.started_at);
  const when = Number.isNaN(d.getTime()) ? String(item.id || '').slice(0, 13) : d.toLocaleString('zh-CN', {month: 'numeric', day: 'numeric', weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false});
  meta.textContent = when + ' · ' + duration(item.audio_seconds) + ' · ' + (SOURCE_NAME[item.source] || '音频');
  const isLive = item.busy || (current && item.id === current.id && ACTIVE.has(current.state));
  const odd = !isLive && ((item.state && item.state !== 'completed') || item.integrity?.warning);
  if (isLive || odd) {
    const tag = document.createElement('span');
    tag.className = 'warn';
    tag.textContent = isLive ? ' · 正在进行' : item.integrity?.warning ? ' · 已保存，待核查' : item.state === 'error' ? ' · 出错' : ' · 未正常结束';
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
  const actions = document.createElement('div');
  actions.className = 'h-actions';
  const recording = document.createElement('button');
  recording.type = 'button'; recording.textContent = item.has_recording ? '查看录音 ↗' : '未保留录音';
  recording.disabled = isLive || !item.has_recording;
  recording.title = '在访达中显示录音，可用系统播放器打开';
  recording.onclick = async () => {
    recording.disabled = true;
    try { await post('/api/sessions/' + encodeURIComponent(item.id) + '/recording/reveal'); }
    catch (e) { flash(e.message); }
    finally { recording.disabled = isLive || !item.has_recording; }
  };
  const remove = document.createElement('button');
  remove.type = 'button'; remove.className = 'danger'; remove.textContent = '删除'; remove.disabled = !!isLive;
  remove.onclick = async () => {
    if (!window.confirm('删除「' + (item.title || '课堂记录') + '」？\n历史记录、字幕和本课录音将一起永久删除，无法撤销。')) return;
    remove.disabled = true;
    try {
      await post('/api/sessions/' + encodeURIComponent(item.id) + '/delete', {confirm: true});
      const data = await getJSON('/api/state');
      if (data.session) snapshot(data.session, data.segments); else clearSession();
      renderStart();
      await loadHistory();
    } catch (e) { flash(e.message); remove.disabled = false; }
  };
  const read = document.createElement('button');
  read.type = 'button'; read.textContent = '查看字幕';
  read.onclick = () => openHistory(item.id, !!isLive);
  actions.append(read, recording);
  if (!isLive && item.has_recording && item.recording_decision === 'pending') {
    const choice = document.createElement('button');
    choice.type = 'button'; choice.textContent = '保留录音？';
    choice.onclick = () => openRecordingChoice(item.id);
    actions.append(choice);
  }
  actions.append(remove); li.append(actions);
  return li;
}

async function openHistory(id, busy = false) {
  const request = ++openingHistory;
  try {
    const data = await getJSON(busy ? '/api/state' : '/api/sessions/' + encodeURIComponent(id));
    if (request !== openingHistory) return;
    viewingHistory = !busy;
    snapshot(data.session, data.segments);
    show('live');
  } catch (e) { flash(e.message || '无法打开这堂课的字幕'); }
}
async function leaveHistory() {
  if (!viewingHistory) { show('start'); return; }
  try {
    const data = await getJSON('/api/state');
    viewingHistory = false;
    if (data.session) snapshot(data.session, data.segments); else clearSession();
    show('start');
  } catch (e) { flash(e.message || '暂时无法返回，请稍后重试'); }
}

/* ---------- 课后录音选择 ---------- */
function maybePromptRecording(s) {
  if (!s || s.historical || ACTIVE.has(s.state) || !s.saved || s.recording_decision !== 'pending' || !num(s.audio_seconds)) return;
  if (recordingPrompted.has(s.id) || $('recording-dialog').open) return;
  openRecordingChoice(s.id);
}
function openRecordingChoice(id) {
  recordingPrompted.add(id); recordingDialogId = id;
  $('recording-error').textContent = '';
  $('recording-dialog').showModal();
}
async function chooseRecording(keep) {
  const id = recordingDialogId;
  if (!id) return;
  const buttons = ['recording-keep', 'recording-discard', 'recording-later'].map($);
  buttons.forEach(b => { b.disabled = true; });
  try {
    await post('/api/sessions/' + encodeURIComponent(id) + '/recording', {keep});
    $('recording-dialog').close();
    const data = await getJSON('/api/state');
    if (viewingHistory && current?.id === id) {
      const historical = await getJSON('/api/sessions/' + encodeURIComponent(id));
      current = historical.session; renderLive();
    } else if (data.session && !viewingHistory) applyStatus(data.session);
    await loadHistory();
  } catch (e) { $('recording-error').textContent = e.message; }
  finally { buttons.forEach(b => { b.disabled = false; }); }
}
$('recording-keep').onclick = () => chooseRecording(true);
$('recording-discard').onclick = () => chooseRecording(false);
$('recording-later').onclick = () => $('recording-dialog').close();
$('recording-choice').onclick = () => { if (current) openRecordingChoice(current.id); };

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
  $('live-state').textContent = viewingHistory ? '历史字幕' : STATE_LABEL[s.state] || '—';
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
  $('i-execution').textContent = active ? num(s.translation_execution_age).toFixed(1) + ' 秒' : '—';
  $('i-chinese').textContent = s.chinese_pending == null ? '—' : num(s.chinese_pending) + ' 句 / ' + num(s.chinese_lag).toFixed(1) + ' 秒';
  $('i-finalization').textContent = active ? num(s.finalization_wait).toFixed(1) + ' 秒' : '—';
  $('i-mem').textContent = s.memory_bytes ? (num(s.memory_bytes) / 1024 ** 3).toFixed(1) + ' GB' : '—';
  $('i-model').textContent = modelName(s.model);
  $('i-source').textContent = SOURCE_NAME[s.source] || '—';
  $('i-count').textContent = rows.size + ' 句' + (s.final_count != null ? '，已确认 ' + s.final_count : '') + (num(s.asr_unresolved) ? '，识别异常 ' + num(s.asr_unresolved) + ' 处' : '');
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
    $('done-title').textContent = s.state === 'completed' ? (s.integrity?.warning ? '已保存，有内容待核查' : '这堂课已保存') : s.state === 'error' ? '这堂课出错停止了' : '这堂课中断了';
    $('done-sub').textContent = duration(s.audio_seconds) + ' · ' + rows.size + ' 句 · ' + (s.recording_retained === false ? '字幕已保存在这台电脑上，录音未保留' : '录音和字幕都保存在这台电脑上') + (num(s.asr_unresolved) ? '；有 ' + num(s.asr_unresolved) + ' 处识别异常，需定位录音补处理' : '');
  }
  $('recording-choice').hidden = active || !s.saved || s.recording_decision !== 'pending' || !num(s.audio_seconds);
  maybePromptRecording(s);
  $('new-class').textContent = viewingHistory ? '返回历史记录' : '开始新的一堂课';
  $('jump').textContent = viewingHistory ? '↓ 到最后' : '↓ 回到最新';
  renderBanner();
}
function renderBanner() {
  const b = $('banner');
  const s = current;
  let text = '', kind = '';
  if (transient && transient.until > Date.now()) ({text, kind} = transient);
  else if (!connected) { text = '和本机服务的连接断了，正在重连。录音不受影响，会继续在后台进行。'; kind = 'error'; }
  else if (s?.error) { text = s.error + (s.integrity?.warning ? ' ' + s.integrity.warning : ''); kind = 'error'; }
  else if (s?.integrity?.warning) { text = s.integrity.warning; kind = 'warn'; }
  else if (s?.state === 'draining') { text = '已停止收音，正在把最后几句处理完并保存，请稍等，不要关闭启动程序。'; kind = 'info'; }
  else if (s?.state === 'interrupted') { text = '会话中断了。已收到的声音保存在本机，可以用「已有录音」重新处理。'; kind = 'error'; }
  else if (s?.state === 'running' && num(s.input_idle) > 5) { text = '已经 ' + Math.round(num(s.input_idle)) + ' 秒没收到声音了。检查一下声音有没有在播放、输入设备或系统权限。'; kind = 'warn'; }
  else if (s?.state === 'running' && (num(s.asr_lag) > 30 || Math.max(num(s.translation_wait), num(s.chinese_lag)) > 30)) { text = '处理落后比较多，正在追。原声正在保存，识别完整性需在结束后核查。'; kind = 'warn'; }
  else if (s?.state === 'running' && (num(s.asr_lag) > 15 || Math.max(num(s.translation_wait), num(s.chinese_lag)) > 15)) { text = '字幕稍慢，正在追上…'; kind = 'warn'; }
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

/* ---------- 字幕：几句合成一段，逐字出现，修正时慢慢淡入 ---------- */
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
let animate = true;            // 快照恢复时关闭动画，直接显示
const typing = new Set();
let typeTimer = null;
function commonPrefix(a, b) { let i = 0; while (i < a.length && i < b.length && a[i] === b[i]) i++; return i; }
class Typer {
  constructor(el, onSettle) {
    this.el = el; this.shown = ''; this.target = ''; this.busy = false; this.gen = 0; this.onSettle = onSettle;
  }
  set(text) {
    this.target = text;
    if (!animate || reduceMotion || document.hidden) { this.finish(); return; }
    if (!this.busy && text !== this.shown) { typing.add(this); if (!typeTimer) typeTimer = setInterval(tick, 40); }
  }
  finish() {
    this.gen++; this.busy = false; typing.delete(this);
    if (this.shown !== this.target || this.el.childNodes.length !== 1) { this.shown = this.target; this.el.textContent = this.target; }
    this.onSettle?.(this.target);
  }
  step() {
    if (this.busy) return;
    if (this.target === this.shown) { typing.delete(this); this.onSettle?.(this.target); return; }
    if (!this.target.startsWith(this.shown)) { this.correct(); return; }
    const left = this.target.length - this.shown.length;
    this.shown = this.target.slice(0, this.shown.length + Math.max(1, Math.ceil(left / 12)));
    this.el.textContent = this.shown;
  }
  // 修正：保留相同开头，只把变化的部分淡出再慢慢淡入；过渡期间锁定，不再切换
  correct() {
    this.busy = true;
    const gen = ++this.gen;
    const keep = commonPrefix(this.shown, this.target);
    const head = document.createTextNode(this.shown.slice(0, keep));
    const tail = document.createElement('span');
    tail.className = 'fix';
    tail.textContent = this.shown.slice(keep);
    this.el.replaceChildren(head, tail);
    requestAnimationFrame(() => tail.classList.add('out'));
    setTimeout(() => {
      if (gen !== this.gen) return;
      const k = commonPrefix(this.shown, this.target);
      head.textContent = this.target.slice(0, k);
      tail.textContent = this.target.slice(k);
      tail.classList.remove('out');
      tail.classList.add('in');
      this.shown = this.target;
      setTimeout(() => {
        if (gen !== this.gen) return;
        this.el.textContent = this.shown;
        this.busy = false;
        this.step();
        if (!typing.has(this) && this.shown !== this.target) typing.add(this);
      }, 1400);
    }, 500);
  }
}
function tick() {
  for (const t of [...typing]) t.step();
  if (!typing.size) { clearInterval(typeTimer); typeTimer = null; }
}
function finishAll() { for (const e of rows.values()) { e.en.finish(); e.zh.finish(); } }

const paras = [];
const PARA_GAP = 4, PARA_SENTENCES = 6, PARA_CHARS = 420, PARA_SECONDS = 50;
function newPara(start) {
  const el = document.createElement('section');
  el.className = 'para';
  el.innerHTML = '<span class="t"></span><p class="en" lang="en"></p><p class="zh" lang="zh-CN"></p>';
  el.querySelector('.t').textContent = clock(start);
  $('lines').append(el);
  const p = {el, en: el.querySelector('.en'), zh: el.querySelector('.zh'), ids: [], start};
  paras.push(p);
  return p;
}
function paraFor(data) {
  const p = paras[paras.length - 1];
  if (!p) return newPara(data.start);
  const prev = rows.get(p.ids[p.ids.length - 1]);
  if (!prev) return p;
  const chars = p.ids.reduce((n, id) => n + (rows.get(id)?.data.source.length || 0), 0);
  const ended = prev.data.final && /[.!?]["')\]]*$/.test(prev.data.source || '');
  if (num(data.start) - num(prev.data.end) > PARA_GAP && prev.data.final) return newPara(data.start);
  if (ended && (p.ids.length >= PARA_SENTENCES || chars >= PARA_CHARS || num(data.start) - p.start >= PARA_SECONDS)) return newPara(data.start);
  if (chars >= PARA_CHARS * 1.6) return newPara(data.start);
  return p;
}
function upsertLine(data) {
  if (!data.id) return;
  let entry = rows.get(data.id);
  if (!entry) {
    const para = paraFor(data);
    const enEl = document.createElement('span');
    const zhEl = document.createElement('span');
    const zhText = document.createElement('span');
    const btn = document.createElement('button');
    const stateLabel = document.createElement('small');
    stateLabel.className = 'translation-state';
    enEl.className = zhEl.className = 's';
    enEl.dataset.id = zhEl.dataset.id = data.id;
    btn.type = 'button'; btn.className = 'retry'; btn.hidden = true; btn.textContent = '↻ 重新翻译';
    zhEl.append(zhText, stateLabel, btn);
    para.en.append(enEl);
    para.zh.append(zhEl);
    para.ids.push(data.id);
    entry = {data, para, enEl, zhEl, btn, stateLabel, shownZh: ''};
    entry.en = new Typer(enEl);
    entry.zh = new Typer(zhText, text => { if (text && current && ACTIVE.has(current.state)) $('sr-live').textContent = text; });
    btn.onclick = () => retry(data.id, btn);
    rows.set(data.id, entry);
    while (!viewingHistory && rows.size > MAX_LINES) dropOldest();
  }
  entry.data = data;
  paint(entry);
  updateCaret();
  $('live-empty').hidden = true;
}
function dropOldest() {
  const [key, old] = rows.entries().next().value;
  old.en.finish(); old.zh.finish();
  old.enEl.remove(); old.zhEl.remove();
  rows.delete(key);
  old.para.ids = old.para.ids.filter(id => id !== key);
  if (!old.para.ids.length) { old.para.el.remove(); paras.splice(paras.indexOf(old.para), 1); }
}
// 新识别出的文字没有译文时，保留上一次的中文，绝不退回“翻译中”
function paint(entry) {
  const {data} = entry;
  if (data.asr_status === 'unresolved') {
    if (entry.stateLabel) entry.stateLabel.textContent = '';
    entry.shownZh = data.source || '[识别异常：请定位录音补处理]';
    // Withdraw a previous preview immediately, including in Chinese-only mode.
    entry.en.target = data.source || ''; entry.en.finish();
    entry.zh.target = entry.shownZh; entry.zh.finish();
    entry.zhEl.classList.add('failed');
    entry.zhEl.title = '识别恢复失败；此区间未确认，原始录音已保留';
    entry.btn.hidden = true;
    return;
  }
  const zhText = typeof data.translation === 'string' ? data.translation.trim() : '';
  if (zhText) entry.shownZh = zhText;
  const failed = !!data.translation_error;
  const behind = data.translated_revision !== data.revision;
  entry.stateLabel.textContent = failed ? '（翻译未完成）' : behind ? (entry.shownZh ? '（旧译文 · 中文待更新）' : '（等待中文）') : '';
  entry.stateLabel.title = behind ? '英文版本 ' + data.revision + '；中文版本 ' + (data.translated_revision ?? '无') : '';
  entry.en.set(data.source || '');
  entry.zh.set(entry.shownZh);
  entry.zhEl.classList.toggle('failed', failed);
  entry.zhEl.title = data.translation_error ? '翻译失败：' + data.translation_error : '';
  entry.btn.hidden = !failed || current?.state !== 'running';
  if (entry.btn.hidden) { entry.btn.disabled = false; entry.btn.textContent = '↻ 重新翻译'; }
}
function repaintAll() { for (const e of rows.values()) paint(e); }
// 最后一段还有句子在等中文时，在中文末尾显示一个闪烁光标，代替“翻译中”
const caret = document.createElement('i');
caret.className = 'caret';
caret.setAttribute('aria-hidden', 'true');
function updateCaret() {
  const p = paras[paras.length - 1];
  const waiting = p && current && ACTIVE.has(current.state) && p.ids.some(id => !rows.get(id)?.shownZh);
  if (waiting) { if (caret.parentNode !== p.zh) p.zh.append(caret); }
  else caret.remove();
}
// 鼠标指向一句时，中英对应一起高亮
$('lines').addEventListener('mouseover', e => {
  const s = e.target.closest('.s');
  document.querySelectorAll('.s.hl').forEach(x => x.classList.remove('hl'));
  if (s) document.querySelectorAll('.s[data-id="' + s.dataset.id + '"]').forEach(x => x.classList.add('hl'));
});
$('lines').addEventListener('mouseleave', () => document.querySelectorAll('.s.hl').forEach(x => x.classList.remove('hl')));
async function retry(id, btn) {
  btn.disabled = true;
  btn.textContent = '已提交…';
  try { await post('/api/retry/' + encodeURIComponent(id)); btn.textContent = '已重新排队'; }
  catch (e) { btn.disabled = false; btn.textContent = '↻ 重新翻译'; flash(e.message); }
}
function resetLines() {
  for (const e of rows.values()) { e.en.finish(); e.zh.finish(); }
  rows.clear();
  paras.length = 0;
  caret.remove();
  $('lines').replaceChildren();
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
}
// 内容一长高（逐字出现时也是），跟随状态下就滚到底
new ResizeObserver(() => {
  if (view !== 'live') return;
  if (following) toEnd(false); else if (rows.size) $('jump').hidden = false;
}).observe($('lines'));
scroller.addEventListener('scroll', () => {
  if (Date.now() < autoScrollUntil) return;
  following = nearEnd();
  $('jump').hidden = following;
}, {passive: true});
$('jump').onclick = () => toEnd(true);

/* ---------- 状态与数据 ---------- */
function applyStatus(s) {
  const prev = current;
  if (prev && prev.id !== s.id) resetLines();
  current = s;
  const nowActive = ACTIVE.has(s.state);
  if (nowActive && view !== 'live') show('live');
  if (!prev || prev.state !== s.state) repaintAll();
  if (prev && ACTIVE.has(prev.state) && !nowActive) { disarmStop(); loadHistory(); }
  renderLive();
  updateCaret();
  if (view === 'start') renderStart();
}
function snapshot(status, segments) {
  resetLines();
  current = null;
  animate = false;
  if (status) applyStatus(status);
  for (const seg of segments || []) upsertLine(seg);
  animate = true;
  if (current) renderLive();
  if (view === 'live' && !viewingHistory) toEnd(false);
  else if (view === 'start') renderStart();
}
function clearSession() {
  current = null;
  resetLines();
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
$('new-class').onclick = async () => { startNote = {text: '', error: false}; await leaveHistory(); $('title').focus(); };
$('last-open').onclick = () => show('live');
$('back').onclick = leaveHistory;

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
  const root = document.documentElement.style;
  root.setProperty('--zh', SIZES[pref.zh] + 'px');
  root.setProperty('--en', SIZES[pref.en] + 'px');
  $('lines').classList.toggle('zh-only', pref.zhOnly);
  $('zh-only').setAttribute('aria-pressed', String(pref.zhOnly));
  for (const k of ['zh', 'en']) {
    $(k + '-val').textContent = SIZES[pref[k]];
    $(k + '-down').disabled = pref[k] === 0;
    $(k + '-up').disabled = pref[k] === SIZES.length - 1;
  }
  $('en-down').disabled ||= pref.zhOnly;
  $('en-up').disabled ||= pref.zhOnly;
}
function setSize(k, delta) {
  pref[k] = clampSize(pref[k] + delta);
  localStorage.setItem(k === 'zh' ? 'tk.zhSize' : 'tk.enSize', String(pref[k]));
  applyPrefs();
}
for (const k of ['zh', 'en']) {
  $(k + '-down').onclick = () => setSize(k, -1);
  $(k + '-up').onclick = () => setSize(k, 1);
}
$('zh-only').onclick = () => {
  pref.zhOnly = !pref.zhOnly;
  localStorage.setItem('tk.zhOnly', pref.zhOnly ? '1' : '0');
  applyPrefs();
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
  if (e.key === '+' || e.key === '=') setSize('zh', 1);
  else if (e.key === '-') setSize('zh', -1);
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
    if (viewingHistory) return; // Live events must not overwrite an archived class.
    if (data.type === 'snapshot') { if (data.status) snapshot(data.status, data.segments); else { clearSession(); renderStart(); loadHistory(); } }
    else if (data.type === 'segment') { upsertLine(data); if (current) renderLive(); }
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
    if (!viewingHistory) {
      if (data.session) snapshot(data.session, data.segments);
      else clearSession();
    }
    if (first) show(current && ACTIVE.has(current.state) ? 'live' : 'start');
    if (!ws || ws.readyState > WebSocket.OPEN) connect();
    else setConnected(true);
  } catch {
    if (first) show('start');
    scheduleReconnect();
  }
}
document.addEventListener('visibilitychange', () => {
  finishAll();
  if (!document.hidden && (!ws || ws.readyState === WebSocket.CLOSED) && !reconnectTimer) init();
});

applyPrefs();
selectSource(pref.source);
init(true);
