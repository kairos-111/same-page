'use strict';
const $ = id => document.getElementById(id);
const ACTIVE = new Set(['loading', 'running', 'draining']);
const LANGUAGE_MODES = {en:'英语 → 中文',ru:'俄语 → 中文',mixed:'英俄中混合 → 中文'};
const SOURCE_NAME = {microphone: '麦克风', system: '电脑声音', file: '导入文件'};
const SOURCE_HINT = {
  microphone: '使用 Mac 当前的默认麦克风。第一次用时，系统会询问麦克风权限，点允许即可。',
  system: '会听到电脑上所有正在播放的声音。第一次用时，macOS 可能要求「屏幕与系统音频录制」权限，不会保存任何画面。',
  file: '选一个本机的录音或视频，会尽快处理，不用等完原来的时长。'
};
const SIZES = [13, 15, 17, 19, 22, 26, 30, 36];
const MAX_LINES = 500;

const pref = {
  zh: clampSize(Number(localStorage.getItem('tk.zhSize') ?? 2)),
  en: clampSize(Number(localStorage.getItem('tk.enSize') ?? 2)),
  zhOnly: localStorage.getItem('tk.zhOnly') === '1',
  languageMode: ['en','ru','mixed'].includes(localStorage.getItem('tk.languageMode')) ? localStorage.getItem('tk.languageMode') : 'en',
  model: localStorage.getItem('tk.model') === 'turbo' ? 'turbo' : 'small',
  source: ['microphone', 'system', 'file'].includes(localStorage.getItem('tk.source')) ? localStorage.getItem('tk.source') : 'microphone'
};
let token = '';
let ready = false;
let serverLanguageModes=['en'];
let connected = false;
let everConnected = false;
let ws = null;
let reconnectTimer = null;
let reconnectDelay = 1500;
let viewingHistory = false;
let openingHistory = 0;
let historySearchTimer = null;
let historyItems = [];
let historyTotal = 0;
let historyAllTotal = 0;
let historyQuery = '';
let trashItems = [];
let current = null;       // 当前会话状态
let view = 'start';
let pending = '';         // 'upload' | 'start' | 'stop'
let startNote = {text: '', error: false};
let transient = null;     // 临时提示 {text, kind, until}
let following = true;
let stopArmed = null;
let recordQuery = '';     // 当前记录内的搜索词
let recordMatches = new Set();
let audioPlayer = null;
let audioPlayerId = null;  // 播放器对应的记录
const rows = new Map();
const recognitionGaps = new Map();
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
  document.documentElement.classList.toggle('subtitle-open', next === 'live');
  if (next === 'live') window.scrollTo(0, 0);
  $('start-view').hidden = next !== 'start';
  $('live-view').hidden = next !== 'live';
  $('library-view').hidden = next !== 'library';
  $('settings-view').hidden = next !== 'settings';
  document.querySelectorAll('[data-page]').forEach(b => b.classList.toggle('selected', b.dataset.page === (next === 'live' ? (viewingHistory ? 'library' : 'start') : next)));
  document.title = next === 'live' && current ? (current.title || '记录') + ' · Same Page' : 'Same Page';
  if (next === 'start') { renderStart(); loadHistory(); }
  else if (next === 'library') loadHistory();
  else if (next === 'settings') loadSettings();
  else { finishAll(); renderLive(); requestAnimationFrame(() => { if (viewingHistory) { following = false; scroller.scrollTop = 0; $('jump').hidden = nearEnd(); } else toEnd(false); }); }
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
const LANGUAGE_NOTES = {
  en: '英语原文，译成中文。',
  ru: '俄语原文，译成中文。',
  mixed: '试用：自动识别英语、俄语和中文，中文直接保留。语言切换处请核对原文。'
};
function languageLocked() { return !!(current && ACTIVE.has(current.state)) || !!pending; }
function renderLanguageMode() {
  const busy = current && ACTIVE.has(current.state);
  const mode = busy ? (current.language_mode || 'en') : pref.languageMode;
  const locked = languageLocked();
  for (const button of document.querySelectorAll('.lang')) {
    const on = button.dataset.mode === mode;
    button.setAttribute('aria-checked', String(on));
    button.tabIndex = on ? 0 : -1;
    button.disabled = locked || !serverLanguageModes.includes(button.dataset.mode);
  }
  $('language-mode-note').textContent = serverLanguageModes.length < 3 ? '当前引擎需重启，暂仅支持英语。' : busy ? '本次语言已锁定，结束后可切换。' : LANGUAGE_NOTES[mode];
}
function chooseLanguageMode(mode) {
  if (languageLocked()) { renderLanguageMode(); return; }
  if (!Object.hasOwn(LANGUAGE_MODES, mode) || !serverLanguageModes.includes(mode)) return;
  pref.languageMode = mode;
  localStorage.setItem('tk.languageMode', mode);
  renderLanguageMode();
}
document.querySelectorAll('.lang').forEach(button => { button.onclick = () => chooseLanguageMode(button.dataset.mode); });
// 单选组的方向键：左右切换并把焦点留在新选项上。
$('language-mode').onkeydown = e => {
  const step = {ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1}[e.key];
  if (!step || languageLocked()) return;
  e.preventDefault();
  const modes = Object.keys(LANGUAGE_MODES).filter(m => serverLanguageModes.includes(m));
  const next = modes[(modes.indexOf(pref.languageMode) + step + modes.length) % modes.length];
  chooseLanguageMode(next);
  document.querySelector('.lang[data-mode="' + next + '"]')?.focus();
};
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
  renderLanguageMode();
  const needFile = pref.source === 'file' && !chosenFile();
  const start = $('start');
  start.disabled = !connected || !ready || !!pending || busy || needFile || !serverLanguageModes.includes(pref.languageMode);
  $('start-label').textContent = pending === 'upload' ? '正在导入…' : pending === 'start' ? '正在启动…' : '开始翻译';
  document.querySelectorAll('.source').forEach(el => { el.disabled = !!pending; });
  $('start-model').disabled = !!pending || !!busy;

  const note = $('start-note');
  let text = startNote.text, error = startNote.error;
  if (!text) {
    if (lost) { text = '连不上本地引擎。请重新打开 Same Page；浏览器预览可重新运行启动入口。'; error = true; }
    else if (connected && !ready) { text = '识别和翻译模型还没部署完成，暂时不能开始。'; error = true; }
    else if (busy) text = '另一个窗口正在翻译，可以直接查看记录。';
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
  $('last-title').textContent = s.title || '新记录';
  $('last-meta').textContent = duration(s.audio_seconds) + ' · ' + rows.size + ' 句 · ' + (s.state === 'completed' ? (s.integrity?.warning ? '已保存，待核查' : '已保存') : s.state === 'error' ? '出错，已收到的内容已保存' : '中断，已收到的内容已保存');
}

async function loadHistory(append = false) {
  try {
    const params = new URLSearchParams();
    if (historyQuery) params.set('q', historyQuery);
    // 收藏/待核查在服务端过滤，否则翻页之外的记录会被漏掉，总数也会对不上。
    const filter = historyFilter();
    if (filter === 'favorite') params.set('favorite', 'true');
    else if (filter === 'review') params.set('review', 'true');
    params.set('limit', '200');
    params.set('offset', append ? String(historyItems.length) : '0');
    const data = await getJSON('/api/library?' + params.toString());
    historyTotal = data.total ?? (data.items || []).length;
    // 侧边栏角标永远是全部记录的条数，筛选时也不变。
    historyAllTotal = data.all_total ?? historyTotal;
    const items = data.items || [];
    historyItems = append ? [...historyItems, ...items] : items;
    renderHistory();
  } catch {
    if (!append) { $('history-empty').hidden = false; $('history-empty').textContent = '暂时读不到以前的记录。'; }
  }
}
function historyFilter() {
  const chip = document.querySelector('.chip[aria-pressed="true"]');
  return chip ? chip.dataset.filter : 'all';
}
function renderHistory() {
  const filter = historyFilter();
  const query = historyQuery.trim().toLowerCase();
  const items = historyItems;
  $('history').replaceChildren(...items.map(historyRow));
  $('library-count').textContent = historyAllTotal || '';
  const more = $('history-more');
  more.hidden = !(historyItems.length < historyTotal);
  more.textContent = '加载更多记录（还有 ' + Math.max(0, historyTotal - historyItems.length) + ' 条）';
  if (filter === 'trash') {
    $('history').replaceChildren(...trashItems.map(trashRow));
    $('history-summary').textContent = trashItems.length ? '废纸篓里有 ' + trashItems.length + ' 条记录，可恢复' : '废纸篓是空的';
    $('history-empty').hidden = trashItems.length > 0;
    $('history-empty').textContent = '废纸篓是空的。删除的记录会先放到这里，确认不再需要才会彻底清除。';
    more.hidden = true;
    return;
  }
  const names = {all: '共 ', favorite: '共 ', review: '共 '};
  // 总数用服务端的数字，不用已加载的条数：翻页前也必须说清楚一共有多少。
  $('history-summary').textContent = query
    ? '找到 ' + historyTotal + ' 条记录'
    : names[filter] + historyTotal + ' 条记录';
  $('history-empty').hidden = items.length > 0;
  $('history-empty').textContent = query ? '没有找到这条记录，换个关键词试试。' : '还没有记录。开始翻译后，字幕会自动保存在这里。';
}
function trashRow(item) {
  const li = document.createElement('li');
  const main = document.createElement('div');
  main.className = 'h-main';
  const title = document.createElement('span');
  title.className = 'h-title';
  title.textContent = item.title || '新记录';
  const meta = document.createElement('span');
  meta.className = 'h-meta';
  const d = new Date(item.started_at);
  meta.textContent = (Number.isNaN(d.getTime()) ? '' : d.toLocaleString('zh-CN', {month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false})) + ' · ' + duration(item.audio_seconds);
  main.append(title, meta);
  li.append(main);
  const actions = document.createElement('div');
  actions.className = 'h-actions';
  const restore = document.createElement('button');
  restore.type = 'button'; restore.textContent = '恢复';
  restore.onclick = async () => {
    restore.disabled = true;
    try { await post('/api/trash/' + encodeURIComponent(item.id) + '/restore'); await loadTrash(); await loadHistory(); }
    catch (e) { flash(e.message); restore.disabled = false; }
  };
  const purge = document.createElement('button');
  purge.type = 'button'; purge.className = 'danger'; purge.textContent = '彻底删除';
  purge.onclick = async () => {
    if (!await confirmHistoryDeletion(item, 'purge')) return;
    purge.disabled = true;
    try { await post('/api/trash/' + encodeURIComponent(item.id) + '/delete', {confirm: true}); await loadTrash(); await loadHistory(); }
    catch (e) { flash(e.message); purge.disabled = false; }
  };
  actions.append(restore, purge);
  li.append(actions);
  return li;
}
async function loadTrash() {
  try { const data = await getJSON('/api/trash'); trashItems = data.items || []; renderHistory(); }
  catch { trashItems = []; }
}
document.querySelectorAll('.chip').forEach(chip => {
  chip.onclick = async () => {
    document.querySelectorAll('.chip').forEach(c => c.setAttribute('aria-pressed', String(c === chip)));
    if (chip.dataset.filter === 'trash') { await loadTrash(); return; }
    // 筛选结果来自服务端，重新取一次，否则总数和翻页都会对不上。
    historyItems = [];
    await loadHistory();
  };
});
$('history-search').oninput = () => {
  historyQuery = $('history-search').value;
  clearTimeout(historySearchTimer);
  historySearchTimer = setTimeout(() => { historyItems = []; loadHistory(); }, 200);
};
$('history-more').onclick = () => loadHistory(true);
/* ---------- 应用内删除确认：不依赖 WebView 的 window.confirm ---------- */
let resolveHistoryDeletion = null;
function confirmHistoryDeletion(item, kind) {
  if (resolveHistoryDeletion) return Promise.resolve(false);
  const audioOnly = kind === 'recording';
  $('delete-heading').textContent = audioOnly ? '删除本次录音？' : kind === 'purge' ? '彻底删除这条记录？' : '删除这条记录？';
  $('delete-course-name').textContent = item.title || '新记录';
  $('delete-description').textContent = audioOnly
    ? '仅删除本次录音，字幕和记录会保留。删除后无法恢复录音。'
    : kind === 'purge'
      ? '这条记录将被彻底删除，无法恢复。'
      : '记录、双语字幕和本次全部录音将一起移到废纸篓，之后可以恢复。';
  $('delete-confirm').textContent = audioOnly ? '删除录音' : '删除记录';
  return new Promise(resolve => {
    resolveHistoryDeletion = resolve;
    $('delete-dialog').showModal();
    $('delete-cancel').focus();
  });
}
function completeHistoryDeletion(confirmed) {
  const resolve = resolveHistoryDeletion;
  resolveHistoryDeletion = null;
  $('delete-dialog').close();
  if (resolve) resolve(confirmed);
}
$('delete-cancel').onclick = () => completeHistoryDeletion(false);
$('delete-confirm').onclick = () => completeHistoryDeletion(true);
$('delete-dialog').addEventListener('cancel', e => { e.preventDefault(); completeHistoryDeletion(false); });
$('delete-dialog').addEventListener('close', () => {
  if (resolveHistoryDeletion) completeHistoryDeletion(false);
});

function historyRow(item) {
  const li = document.createElement('li');
  const main = document.createElement('div');
  main.className = 'h-main';
  const title = document.createElement('button');
  title.type = 'button'; title.className = 'h-title h-open';
  title.onclick = () => openHistory(item.id, !!item.busy);
  title.textContent = item.title || '新记录';
  const meta = document.createElement('span');
  meta.className = 'h-meta';
  const d = new Date(item.started_at);
  const when = Number.isNaN(d.getTime()) ? String(item.id || '').slice(0, 13) : d.toLocaleString('zh-CN', {month: 'numeric', day: 'numeric', weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false});
  const modeLabel={en:'英语→中文',ru:'俄语→中文',mixed:'英俄中混合→中文'}[item.language_mode || 'en'] || '原文→中文';
  meta.textContent = when + ' · ' + duration(item.audio_seconds) + ' · ' + (SOURCE_NAME[item.source] || '音频')+' · '+modeLabel;
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
  const actions = document.createElement('div');
  actions.className = 'h-actions';
  // The row keeps two visible actions; everything else lives in one overflow menu
  // so a long list stays readable instead of turning into a wall of buttons.
  const read = document.createElement('button');
  read.type = 'button'; read.className = 'h-primary'; read.textContent = isLive ? '回到翻译' : '查看记录';
  read.onclick = () => openHistory(item.id, !!isLive);
  const favorite = document.createElement('button');
  favorite.type = 'button'; favorite.className = 'fav'; favorite.textContent = item.favorite ? '★' : '☆';
  favorite.title = item.favorite ? '取消收藏' : '收藏这条记录';
  favorite.setAttribute('aria-pressed', String(!!item.favorite));
  favorite.setAttribute('aria-label', (item.favorite ? '取消收藏「' : '收藏「') + (item.title || '新记录') + '」');
  favorite.onclick = async () => {
    favorite.disabled = true;
    try {
      await post('/api/sessions/' + encodeURIComponent(item.id) + '/meta', {favorite: !item.favorite});
      item.favorite = !item.favorite;
      favorite.textContent = item.favorite ? '★' : '☆';
      favorite.setAttribute('aria-pressed', String(item.favorite));
    } catch (e) { flash(e.message); }
    finally { favorite.disabled = false; }
  };
  actions.append(read, favorite);

  const more = document.createElement('details');
  more.className = 'h-more';
  const moreSummary = document.createElement('summary');
  moreSummary.textContent = '⋯';
  moreSummary.title = '更多操作';
  moreSummary.setAttribute('aria-label', '「' + (item.title || '新记录') + '」的更多操作');
  more.append(moreSummary);
  const menu = document.createElement('div');
  menu.className = 'h-menu';
  for (const [kind, label] of [['txt', '下载双语文本'], ['srt', '下载字幕 SRT'], ['md', '下载 Markdown']]) {
    const a = document.createElement('a');
    a.href = '/api/export/' + encodeURIComponent(item.id) + '/' + kind;
    a.textContent = label;
    a.setAttribute('aria-label', '下载「' + (item.title || '新记录') + '」' + label);
    menu.append(a);
  }
  const recording = document.createElement('button');
  recording.type = 'button'; recording.textContent = item.has_recording ? '回听录音' : '未保留录音';
  recording.disabled = isLive || !item.has_recording;
  recording.title = '在应用内回听本次录音';
  recording.onclick = async () => {
    recording.disabled = true;
    try { await openHistory(item.id, !!item.busy); }
    catch (e) { flash(e.message); }
    finally { recording.disabled = isLive || !item.has_recording; }
  };
  menu.append(recording);
  if (item.has_recording) {
    const removeAudio=document.createElement('button');
    removeAudio.type='button';removeAudio.className='danger';removeAudio.textContent='删除录音';removeAudio.disabled=!!isLive;
    removeAudio.onclick=async()=>{
      if (!await confirmHistoryDeletion(item, 'recording')) return;
      removeAudio.disabled=true;
      try {
        await post('/api/sessions/'+encodeURIComponent(item.id)+'/recording',{keep:false});
        if (current?.id===item.id) {
          current={...current,recording_decision:'discarded',recording_retained:false,has_recording:false};renderLive();
        }
        more.open = false;
        await loadHistory();
      } catch(e) {flash(e.message);removeAudio.disabled=false;}
    };
    menu.append(removeAudio);
  }
  const remove = document.createElement('button');
  remove.type = 'button'; remove.className = 'danger'; remove.textContent = '删除记录'; remove.disabled = !!isLive;
  remove.onclick = async () => {
    if (!await confirmHistoryDeletion(item, 'course')) return;
    remove.disabled = true;
    try {
      await post('/api/sessions/' + encodeURIComponent(item.id) + '/delete', {confirm: true});
      const data = await getJSON('/api/state');
      if (data.session) snapshot(data.session, data.segments); else clearSession();
      renderStart();
      more.open = false;
      await loadHistory();
    } catch (e) { flash(e.message); remove.disabled = false; }
  };
  menu.append(remove);
  more.append(menu);
  actions.append(more);
  li.append(actions);
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
  } catch (e) { flash(e.message || '无法打开这条记录'); }
}
async function leaveHistory() {
  if (!viewingHistory) { show('library'); return; }
  try {
    const data = await getJSON('/api/state');
    viewingHistory = false;
    if (data.session) snapshot(data.session, data.segments); else clearSession();
    show('library');
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
  const buttons = ['recording-keep', 'recording-discard'].map($);
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
$('recording-dialog').addEventListener('cancel', e => e.preventDefault());
$('recording-choice').onclick = () => { if (current) openRecordingChoice(current.id); };

/* ---------- 字幕页 ---------- */
const STATE_LABEL = {loading: '准备中', running: '正在听', draining: '正在收尾', completed: '已结束', error: '出错了', interrupted: '已中断'};
const EMPTY_TEXT = {
  loading: '正在加载本地模型，第一次可能要十几秒…',
  running: '正在听。识别到语音后，原文与中文结果会出现在这里。',
  draining: '正在处理最后一段…',
  completed: '本次没有识别到可显示的内容。',
  error: '本次处理出现问题，没有生成字幕。',
  interrupted: '本次处理中断，没有生成字幕。'
};
function renderRecognitionDetails() {
  const gaps = new Map();
  for (const gap of [...recognitionGaps.values(), ...(current?.integrity?.recognition_gaps || [])]) {
    gaps.set(num(gap.start).toFixed(3) + ':' + num(gap.end).toFixed(3), gap);
  }
  const total = Math.max(num(current?.asr_unresolved), gaps.size);
  $('i-asr-gaps').textContent = total ? total + ' 段' : '无';
  const intervals = [...gaps.values()].sort((a, b) => num(a.start) - num(b.start));
  $('recognition-ranges').textContent = intervals.slice(-20).map(g => clock(g.start) + '–' + clock(g.end)).join('\n');
  $('recognition-summary').textContent = total ? '有 ' + total + ' 段语音未形成可靠字幕。' + (intervals.length > 20 ? '以下显示最近20段。' : '') : '';
  $('integrity-note').textContent = current?.integrity?.warning || '';
  $('recognition-details').hidden = !total && !current?.integrity?.warning;
}

function renderLive() {
  const s = current;
  if (!s) return;
  const active = ACTIVE.has(s.state);
  $('live-view').classList.toggle('history-reading', viewingHistory);
  $('rec').className = 'rec ' + ({running: 'on', loading: 'busy', draining: 'busy', completed: 'ok'}[s.state] || 'bad');
  $('live-state').textContent = viewingHistory ? '历史记录' : STATE_LABEL[s.state] || '—';
  // 导入文件按处理进度计时，不按已读入的长度，否则一开始就跳到全长。
  $('live-clock').textContent = clock(s.fast_import && active ? num(s.processed_seconds) : s.audio_seconds);
  $('live-title').textContent = s.title || '新记录';
  if (view === 'live') document.title = (s.title || '记录') + ' · Same Page';

  const lit = s.state === 'running' ? Math.min(5, Math.round(num(s.level) * 30)) : 0;
  const bars = $('live-level');
  bars.classList.toggle('lit', lit > 0);
  [...bars.children].forEach((b, i) => { b.style.height = (i < lit ? 5 + i * 2.2 : 3) + 'px'; });

  const stop = $('stop');
  stop.hidden = !active;
  stop.disabled = !connected || pending === 'stop' || s.state === 'draining';
  stop.textContent = s.state === 'draining' || pending === 'stop' ? '保存中…' : stopArmed ? '确定结束？' : '结束';
  stop.classList.toggle('armed', !!stopArmed);

  $('i-asr-execution').textContent = active ? num(s.asr_execution_age).toFixed(1) + ' / ' + num(s.asr_last_seconds).toFixed(1) + ' 秒' : '—';
  $('i-lag').textContent = active ? num(s.asr_lag).toFixed(1) + ' 秒' : '—';
  $('i-queue').textContent = active ? num(s.translation_queue) + (s.translation_busy ? ' 句（1 句翻译中）' : ' 句') : '—';
  $('i-wait').textContent = active && num(s.translation_queue) ? num(s.translation_wait).toFixed(1) + ' 秒' : '—';
  $('i-execution').textContent = active ? num(s.translation_execution_age).toFixed(1) + ' 秒' : '—';
  $('i-chinese').textContent = s.chinese_pending == null ? '—' : num(s.chinese_pending) + ' 句 / ' + num(s.chinese_lag).toFixed(1) + ' 秒';
  $('i-finalization').textContent = active ? num(s.finalization_wait).toFixed(1) + ' 秒' : '—';
  $('i-mem').textContent = s.memory_bytes ? (num(s.memory_bytes) / 1024 ** 3).toFixed(1) + ' GB' : '—';
  $('i-model').textContent = modelName(s.model)+' · '+(LANGUAGE_MODES[s.language_mode || 'en'] || '原文→中文');
  $('i-source').textContent = SOURCE_NAME[s.source] || '—';
  renderRecognitionDetails();
  $('i-count').textContent = rows.size + ' 句' + (s.final_count != null ? '，已确认 ' + s.final_count : '') + (num(s.asr_unresolved) ? '，识别异常 ' + num(s.asr_unresolved) + ' 处' : '');
  renderLagPill(s, active);
  for (const [id, kind] of [['dl-txt', 'txt'], ['dl-srt', 'srt'], ['dl-md', 'md']]) {
    $(id).href = '/api/export/' + encodeURIComponent(s.id) + '/' + kind;
  }
  $('live-search-box').hidden = !viewingHistory;
  renderRecordTools();

  $('live-empty').hidden = rows.size > 0;
  $('live-empty-text').textContent = EMPTY_TEXT[s.state] || '';
  $('live-empty').querySelector('.pulse').hidden = !active;

  $('back').hidden = active;
  const done = $('done');
  done.hidden = active;
  if (!active) {
    $('done-title').textContent = s.state === 'completed' ? (s.integrity?.warning ? '已保存，有内容待核查' : '本次记录已保存') : s.state === 'error' ? '本次处理出错停止了' : '本次处理中断了';
    $('done-sub').textContent = duration(s.audio_seconds) + ' · ' + rows.size + ' 句 · ' + (s.recording_retained === false ? '字幕已保存在这台电脑上，录音未保留' : '录音和字幕都保存在这台电脑上') + (num(s.asr_unresolved) ? ' · 核查信息见详情' : '');
  }
  $('recording-choice').hidden = active || !s.saved || s.recording_decision !== 'pending' || !num(s.audio_seconds);
  maybePromptRecording(s);
  $('new-class').textContent = viewingHistory ? '返回历史记录' : '开始新的翻译';
  $('jump').textContent = viewingHistory ? '↓ 到最后' : '↓ 回到最新';
  renderBanner();
}
/* 顶栏一个标签说清字幕跟得怎样；点开就是完整详情。 */
const LAG_OK = 4, LAG_BEHIND = 15;
function lagStatus(s, active) {
  const gaps = Math.max(num(s.asr_unresolved), recognitionGaps.size, (s.integrity?.recognition_gaps || []).length);
  if (!active) return gaps ? {text: '待核查 ' + gaps + ' 处', kind: 'warn'} : {text: '详情', kind: ''};
  if (s.state === 'loading') return {text: '准备中', kind: ''};
  if (s.state === 'draining') return {text: '正在收尾', kind: ''};
  if (s.fast_import) {
    const total = num(s.audio_seconds);
    return {text: s.input_done && total ? '已处理 ' + Math.floor(Math.min(1, num(s.processed_seconds) / total) * 100) + '%' : '正在读取文件', kind: ''};
  }
  const lag = Math.max(num(s.asr_lag), num(s.translation_wait), num(s.chinese_lag));
  if (lag < LAG_OK) return {text: '已跟上', kind: 'ok'};
  if (lag < LAG_BEHIND) return {text: '延迟约 ' + Math.round(lag) + ' 秒', kind: ''};
  return {text: '落后 ' + Math.round(lag) + ' 秒', kind: 'warn'};
}
function renderLagPill(s, active) {
  const {text, kind} = lagStatus(s, active);
  const pill = $('info-btn');
  pill.textContent = text;
  pill.classList.toggle('ok', kind === 'ok');
  pill.classList.toggle('warn', kind === 'warn');
  pill.title = active && s.state === 'running' && !s.fast_import
    ? '识别落后 ' + num(s.asr_lag).toFixed(1) + ' 秒，翻译排队 ' + num(s.translation_wait).toFixed(1) + ' 秒。点开查看详情。'
    : '运行详情与待核查区间';
}
function renderBanner() {
  const b = $('banner');
  const s = current;
  let text = '', kind = '';
  if (transient && transient.until > Date.now()) ({text, kind} = transient);
  else if (!connected) { text = '和本机服务的连接断了，正在重连。录音不受影响，会继续在后台进行。'; kind = 'error'; }
  else if (s?.error) { text = s.error + (s.integrity?.warning ? ' ' + s.integrity.warning : ''); kind = 'error'; }
  else if (s?.state === 'draining') { text = '已停止收音，正在把最后几句处理完并保存，请稍等，不要关闭启动程序。'; kind = 'info'; }
  else if (s?.state === 'interrupted') { text = '会话中断了。已收到的声音保存在本机，可以用「导入文件」重新处理。'; kind = 'error'; }
  else if (s?.state === 'running' && s.fast_import) {
    const total = num(s.audio_seconds), done = Math.min(total, num(s.processed_seconds));
    text = s.input_done && total
      ? '正在处理导入的文件：已识别 ' + clock(done) + ' / ' + clock(total) + '（' + Math.floor(done / total * 100) + '%）。可以随时结束，未处理的部分会在详情里注明。'
      : '正在读取导入的文件…';
    kind = 'info';
  }
  else if (s?.state === 'running' && !s.input_done && num(s.input_idle) > 5) { text = '已经 ' + Math.round(num(s.input_idle)) + ' 秒没收到声音了。检查一下声音有没有在播放、输入设备或系统权限。'; kind = 'warn'; }
  else if (s?.state === 'running' && (num(s.asr_lag) > 30 || Math.max(num(s.translation_wait), num(s.chinese_lag)) > 30)) { text = '处理落后比较多，正在追。原声正在保存，识别完整性需在结束后核查。'; kind = 'warn'; }
  b.hidden = !text;
  b.textContent = text;
  b.className = 'banner' + (kind && kind !== 'warn' ? ' ' + kind : '');
}
function flash(text, kind = 'error', ms = 6000) {
  transient = {text, kind, until: Date.now() + ms};
  if (view === 'live') renderBanner();
  else if (view === 'library' || view === 'settings') { $('shell-notice').textContent = text; $('shell-notice').hidden = false; }
  else { startNote = {text, error: kind === 'error'}; renderStart(); }
  setTimeout(() => {
    if (transient && transient.until <= Date.now()) { transient = null; renderBanner(); }
    if ($('shell-notice').textContent === text) $('shell-notice').hidden = true;
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
  // 每段自带一个复制按钮：整段取走原文加译文，不必再翻整页。
  const copy = document.createElement('button');
  copy.type = 'button'; copy.className = 'para-copy'; copy.textContent = '复制';
  copy.setAttribute('aria-label', '复制这一段的原文与译文');
  el.append(copy);
  $('lines').append(el);
  const p = {el, en: el.querySelector('.en'), zh: el.querySelector('.zh'), ids: [], start, copy};
  copy.onclick = () => copyPara(p);
  paras.push(p);
  return p;
}
async function copyPara(para) {
  const text = paraText(para);
  if (!text) { flash('这一段还没有内容。', 'error', 2500); return; }
  try {
    await navigator.clipboard.writeText(text);
    para.copy.textContent = '已复制';
    setTimeout(() => { para.copy.textContent = '复制'; }, 1600);
  } catch {
    flash('复制没有成功，可以改用「下载双语文本」。', 'error', 4000);
  }
}
function paraText(para) {
  const parts = [];
  for (const id of para.ids) {
    const entry = rows.get(id);
    if (!entry) continue;
    const source = (entry.data.source || '').trim();
    const translation = (entry.data.translation || '').trim();
    if (!source && !translation) continue;
    parts.push(source + (translation ? '\n' + translation : ''));
  }
  return parts.join('\n\n');
}
function paraFor(data) {
  const p = paras[paras.length - 1];
  if (!p) return newPara(data.start);
  const prev = rows.get(p.ids[p.ids.length - 1]);
  if (!prev) return p;
  const chars = p.ids.reduce((n, id) => n + (rows.get(id)?.data.source.length || 0), 0);
  const ended = prev.data.final && /[.!?。！？]["')\]”’]*$/.test(prev.data.source || '');
  if (num(data.start) - num(prev.data.end) > PARA_GAP && prev.data.final) return newPara(data.start);
  if (ended && (p.ids.length >= PARA_SENTENCES || chars >= PARA_CHARS || num(data.start) - p.start >= PARA_SECONDS)) return newPara(data.start);
  if (chars >= PARA_CHARS * 1.6) return newPara(data.start);
  return p;
}
function upsertLine(data) {
  if (!data.id) return;
  if (data.asr_status === 'unresolved') {
    recognitionGaps.set(data.id, {start: data.start, end: data.end});
    while (recognitionGaps.size > MAX_LINES) recognitionGaps.delete(recognitionGaps.keys().next().value);
    removeLine(data.id); // Withdraw an older preview, without inserting a system message.
    renderRecognitionDetails();
    updateCaret();
    $('live-empty').hidden = rows.size > 0;
    return;
  }
  if (recognitionGaps.delete(data.id)) renderRecognitionDetails();
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
function removeLine(key) {
  const old = rows.get(key);
  if (!old) return;
  old.en.finish(); old.zh.finish();
  old.enEl.remove(); old.zhEl.remove();
  rows.delete(key);
  old.para.ids = old.para.ids.filter(id => id !== key);
  if (!old.para.ids.length) { old.para.el.remove(); paras.splice(paras.indexOf(old.para), 1); }
  else {
    const first = rows.get(old.para.ids[0]);
    old.para.el.querySelector('.t').textContent = clock(first.data.start);
  }
}
function dropOldest() { removeLine(rows.keys().next().value); }
// 新识别出的文字没有译文时，保留上一次的中文，绝不退回“翻译中”
function paint(entry) {
  const {data} = entry;
  if (data.asr_status === 'unresolved') {
    if (entry.stateLabel) entry.stateLabel.textContent = '';
    entry.shownZh = '';
    // Defensive withdrawal; unresolved records normally go straight to Details.
    entry.en.target = ''; entry.en.finish();
    entry.zh.target = ''; entry.zh.finish();
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
  recognitionGaps.clear();
  renderRecognitionDetails();
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
  if (following) toEnd(false); else $('jump').hidden = !rows.size || nearEnd();
}).observe($('lines'));
scroller.addEventListener('scroll', () => {
  if (Date.now() < autoScrollUntil) return;
  following = nearEnd();
  $('jump').hidden = following;
}, {passive: true});
$('jump').onclick = () => toEnd(true);

new ResizeObserver(() => {
  const height = $('done').hidden ? 0 : $('done').getBoundingClientRect().height;
  $('live-view').style.setProperty('--footer-height', height + 'px');
}).observe($('done'));

/* ---------- 状态与数据 ---------- */
function applyStatus(s) {
  const prev = current;
  if (prev && prev.id !== s.id) resetLines();
  current = s;
  renderLanguageMode();
  const nowActive = ACTIVE.has(s.state);
  if (nowActive && (!prev || prev.id !== s.id || !ACTIVE.has(prev.state)) && view !== 'live') show('live');
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
  recordMatches = new Set();
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
  if (pending || !connected || !ready || !serverLanguageModes.includes(pref.languageMode)) return;
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
    await post('/api/sessions', {source: pref.source, model: pref.model, language_mode:pref.languageMode, title: $('title').value.trim(), upload_id: uploadId, realtime: pref.source !== 'file'});
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
// ⌘↵（或 Ctrl+↵）在开始页直接开始，输入记录名称时也能用。
document.addEventListener('keydown', e => {
  if (view !== 'start' || e.key !== 'Enter' || !(e.metaKey || e.ctrlKey) || e.isComposing) return;
  e.preventDefault();
  if (!$('start').disabled) $('start').click();
});
$('new-class').onclick = async () => { startNote = {text: '', error: false}; if (viewingHistory) await leaveHistory(); else show('start'); $('title').focus(); };
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

// 识别模型：开始页下拉和设置页下拉是同一个偏好。
function setModel(value) {
  pref.model = value === 'turbo' ? 'turbo' : 'small';
  localStorage.setItem('tk.model', pref.model);
  showModel();
}
function showModel() {
  $('start-model').value = pref.model;
  if ($('settings-model')) $('settings-model').value = pref.model;
}
$('start-model').onchange = () => setModel($('start-model').value);
if ($('settings-model')) {
  $('settings-model').onchange = () => {
    setModel($('settings-model').value);
    if ($('settings-message')) $('settings-message').textContent = '已保存。下次开始翻译时使用所选模型。';
  };
}
showModel();

// 设置页的「默认只看译文」：改这里，字幕页的默认显示也跟着变。
if ($('settings-zh-only')) {
  $('settings-zh-only').checked = pref.zhOnly;
  $('settings-zh-only').onchange = () => {
    pref.zhOnly = $('settings-zh-only').checked;
    localStorage.setItem('tk.zhOnly', pref.zhOnly ? '1' : '0');
    applyPrefs();
    if ($('settings-message')) $('settings-message').textContent = '已保存。下次打开字幕时按此显示。';
  };
}

/* ---------- 阅读设置 ---------- */
function applyPrefs() {
  const root = document.documentElement.style;
  root.setProperty('--zh', SIZES[pref.zh] + 'px');
  root.setProperty('--en', SIZES[pref.en] + 'px');
  $('lines').classList.toggle('zh-only', pref.zhOnly);
  $('zh-only').setAttribute('aria-pressed', String(pref.zhOnly));
  $('zh-only').textContent = pref.zhOnly ? '开' : '关';
  // 设置页的开关跟着字幕页的切换走，两处不会各说各话。
  if ($('settings-zh-only')) $('settings-zh-only').checked = pref.zhOnly;
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
// 顶栏的弹层共用一套开关：一次只开一个，点外面收起，Esc 收起全部。
const POPS = [
  {button: 'display-btn', panel: 'display'},
  {button: 'info-btn', panel: 'info'},
  {button: 'export-btn', panel: 'export'},
];
function togglePop(panel, open) {
  for (const pop of POPS) {
    const want = pop.panel === panel ? (open ?? $(pop.panel).hidden) : false;
    $(pop.panel).hidden = !want;
    $(pop.button).setAttribute('aria-expanded', String(want));
  }
}
for (const pop of POPS) {
  $(pop.button).onclick = e => { e.stopPropagation(); togglePop(pop.panel); };
}
document.addEventListener('click', e => {
  if (e.target.closest('.pop-wrap')) return;
  for (const pop of POPS) if (!$(pop.panel).hidden) togglePop(pop.panel, false);
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    for (const pop of POPS) if (!$(pop.panel).hidden) togglePop(pop.panel, false);
    if (stopArmed) { disarmStop(); renderLive(); }
  }
  if (view !== 'live' || e.target.closest('input, textarea, select') || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === '+' || e.key === '=') setSize('zh', 1);
  else if (e.key === '-') setSize('zh', -1);
  else if (e.key === 'End') toEnd(true);
});

/* ---------- 记录内查找：只在历史记录里可用，实时字幕不做筛选 ---------- */
let matchOrder = [];
let matchIndex = -1;
function runRecordSearch() {
  recordQuery = $('live-search').value.trim().toLowerCase();
  recordMatches = new Set();
  matchOrder = [];
  matchIndex = -1;
  if (!recordQuery) {
    for (const entry of rows.values()) { entry.enEl.classList.remove('match'); entry.zhEl.classList.remove('match'); }
    $('live-search-box').classList.remove('has-query');
    $('live-search-count').textContent = '';
    $('live-search-prev').disabled = true;
    $('live-search-next').disabled = true;
    return;
  }
  $('live-search-box').classList.add('has-query');
  let hits = 0;
  for (const entry of rows.values()) {
    const hay = (entry.data.source || '').toLowerCase() + '\n' + (entry.data.translation || '').toLowerCase();
    const hit = hay.includes(recordQuery);
    entry.enEl.classList.toggle('match', hit);
    entry.zhEl.classList.toggle('match', hit);
    if (hit) { recordMatches.add(entry.data.id); matchOrder.push(entry.data.id); hits++; }
  }
  $('live-search-prev').disabled = !hits;
  $('live-search-next').disabled = !hits;
  if (hits) {
    // 有匹配就落在第一处，用户马上能看到找到了什么。
    matchIndex = 0;
    showMatch();
  } else {
    $('live-search-count').textContent = '';
  }
  flash(hits ? '找到 ' + hits + ' 处' : '没有找到「' + recordQuery + '」', hits ? 'info' : 'error', 2500);
}
function showMatch() {
  if (matchIndex < 0 || matchIndex >= matchOrder.length) return;
  const id = matchOrder[matchIndex];
  const el = document.querySelector('.s[data-id="' + id + '"]');
  if (el) el.scrollIntoView({block: 'center', behavior: 'smooth'});
  $('live-search-count').textContent = (matchIndex + 1) + '/' + matchOrder.length;
}
function stepMatch(step) {
  if (!matchOrder.length) return;
  matchIndex = (matchIndex + step + matchOrder.length) % matchOrder.length;
  showMatch();
}
$('live-search-prev').onclick = () => stepMatch(-1);
$('live-search-next').onclick = () => stepMatch(1);
$('live-search').oninput = runRecordSearch;
$('live-search').onkeydown = e => {
  if (e.key === 'Enter') { e.preventDefault(); e.shiftKey ? stepMatch(-1) : stepMatch(1); }
  else if (e.key === 'Escape') { e.preventDefault(); $('live-search').value = ''; runRecordSearch(); }
};

/* ---------- 复制字幕 ---------- */
async function copyVisibleText() {
  const lines = [];
  for (const entry of rows.values()) {
    const source = (entry.data.source || '').trim();
    const translation = (entry.data.translation || '').trim();
    if (!source && !translation) continue;
    lines.push(source + (translation ? '\n' + translation : ''));
  }
  const text = lines.join('\n\n');
  if (!text) { flash('这里还没有可复制的字幕。', 'error', 2500); return; }
  try {
    await navigator.clipboard.writeText(text);
    flash('已复制 ' + lines.length + ' 句字幕。', 'info', 2500);
  } catch {
    flash('复制没有成功，可以改用「下载双语文本」。', 'error', 4000);
  }
}
$('copy-text').onclick = () => { togglePop('export', false); copyVisibleText(); };
// 菜单里点了下载，菜单也随之收起。
for (const id of ['dl-txt', 'dl-srt', 'dl-md']) $(id).addEventListener('click', () => togglePop('export', false));

/* ---------- 记录工具条：回听、备注、收藏 ---------- */
function renderRecordTools() {
  const s = current;
  const box = $('record-tools');
  if (!s || !s.historical) { box.hidden = true; releasePlayer(); return; }
  // 换了记录、或录音已删除：丢掉旧播放器，免得播出上一条的声音。
  if (audioPlayer && (audioPlayerId !== s.id || !s.has_recording)) releasePlayer();
  box.hidden = false;
  // 正在输入的框不要被状态刷新覆盖，否则每来一次状态就丢掉用户刚打的字。
  const noteEl = $('record-note');
  if (document.activeElement !== noteEl) noteEl.value = s.note || '';
  const titleEl = $('record-title');
  if (document.activeElement !== titleEl) titleEl.value = s.title || '';
  $('record-fav').textContent = s.favorite ? '★ 已收藏' : '☆ 收藏';
  $('record-fav').setAttribute('aria-pressed', String(!!s.favorite));
  const playable = !!s.has_recording;
  $('record-play').disabled = !playable;
  $('record-seek').disabled = !playable;
  $('record-play').textContent = playable ? '▶ 播放录音' : '本次未保留录音';
}
async function saveMeta(changes) {
  // 出错就往外抛：调用方各自决定怎么提示，免得失败了还显示「已保存」。
  const id = current?.id;
  if (!id) throw new Error('没有打开的记录');
  await post('/api/sessions/' + encodeURIComponent(id) + '/meta', changes);
  // 保存期间可能已换到另一条记录：改动只合并回原来那一条。
  if (current?.id === id) { current = {...current, ...changes}; renderLive(); renderRecordTools(); }
  if (historyItems.some(item => item.id === id)) loadHistory();
}
/* 名称与备注：离开输入框或按回车就自动保存，右边直接告诉你已保存，不用再点一次按钮。 */
function fieldFeedback(id, text, isError) {
  const box = $(id);
  if (!box) return;
  box.textContent = text;
  box.classList.toggle('error', !!isError);
  clearTimeout(box._timer);
  box._timer = setTimeout(() => { box.textContent = ''; box.classList.remove('error'); }, 2600);
}
async function saveField(key, inputId, stateId) {
  const value = $(inputId).value.trim();
  if ((current?.[key] || '') === value) { fieldFeedback(stateId, '没有改动'); return; }
  try {
    await saveMeta({[key]: value});
    fieldFeedback(stateId, '已保存');
  } catch (e) {
    fieldFeedback(stateId, '没保存成功', true);
  }
}
function bindField(key, inputId, stateId) {
  const input = $(inputId);
  if (!input) return;
  input.onkeydown = e => {
    if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
    else if (e.key === 'Escape') { input.value = current?.[key] || ''; input.blur(); }
  };
  input.onblur = () => saveField(key, inputId, stateId);
}
bindField('note', 'record-note', 'record-note-state');
bindField('title', 'record-title', 'record-title-state');
// 旧按钮名保留兼容：外部脚本或旧测试仍可能引用。
if ($('record-note-save')) $('record-note-save').onclick = () => saveField('note', 'record-note', 'record-note-state');
if ($('record-title-save')) $('record-title-save').onclick = () => saveField('title', 'record-title', 'record-title-state');
$('record-fav').onclick = async () => { try { await saveMeta({favorite: !current?.favorite}); } catch (e) { flash(e.message); } };
function stopPlayback() {
  if (audioPlayer) { audioPlayer.pause(); audioPlayer.currentTime = 0; }
  $('record-play').textContent = '▶ 播放录音';
}
// 播放器只属于一条记录；离开或换记录时整个释放，进度条归零。
function releasePlayer() {
  if (audioPlayer) {
    audioPlayer.pause();
    if (typeof audioPlayer.removeAttribute === 'function') audioPlayer.removeAttribute('src');
    if (typeof audioPlayer.load === 'function') audioPlayer.load();
  }
  audioPlayer = null;
  audioPlayerId = null;
  $('record-play').textContent = '▶ 播放录音';
  $('record-seek').value = '0';
  $('record-time').textContent = '00:00';
}
$('record-play').onclick = () => {
  if (!current?.id || !current.has_recording) return;
  if (audioPlayer && audioPlayerId !== current.id) releasePlayer();
  if (audioPlayer && !audioPlayer.paused) { audioPlayer.pause(); $('record-play').textContent = '▶ 播放录音'; return; }
  if (!audioPlayer) {
    audioPlayerId = current.id;
    const created = new Audio('/api/sessions/' + encodeURIComponent(current.id) + '/recording');
    audioPlayer = created;
    // 旧播放器释放后仍可能派发事件；只处理当前这一个。
    created.addEventListener('timeupdate', () => {
      if (created !== audioPlayer || !created.duration) return;
      $('record-seek').value = String(Math.round(created.currentTime / created.duration * 1000));
      $('record-time').textContent = clock(created.currentTime);
    });
    created.addEventListener('ended', () => { if (created !== audioPlayer) return; $('record-play').textContent = '▶ 播放录音'; $('record-seek').value = '0'; });
    created.addEventListener('error', () => { if (created === audioPlayer) flash('这段录音暂时无法播放，可以到访达中打开。', 'error'); });
  }
  const player = audioPlayer;
  player.play().then(() => { if (player === audioPlayer) $('record-play').textContent = '⏸ 暂停'; }).catch(() => { if (player === audioPlayer) flash('播放没有成功。', 'error'); });
};
$('record-seek').oninput = () => {
  if (audioPlayer && audioPlayer.duration) audioPlayer.currentTime = audioPlayer.duration * (Number($('record-seek').value) / 1000);
};


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
    serverLanguageModes=Array.isArray(data.language_modes) ? data.language_modes : ['en'];
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
