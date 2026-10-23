'use strict';
const $ = id => document.getElementById(id);
const activeStates = new Set(['loading', 'running', 'draining']);
const stateNames = {loading: '正在加载本地模型…', running: '正在听译', draining: '正在完成尾段并保存…', completed: '已保存', error: '会话出错', interrupted: '已中断'};
const sourceNames = {microphone: '麦克风', system: '电脑声音', file: '导入录音'};
const sourceHints = {
  microphone: '采集本机麦克风。首次使用时，请允许 macOS 麦克风权限。',
  system: '采集其他应用正在播放的声音。macOS 可能要求屏幕与系统音频录制权限；不保存屏幕图像。',
  file: '选择本机已有的录音或视频，按原速读取并生成双语字幕。'
};
let source = 'microphone';
let token = '';
let current = null;
let websocket = null;
let rows = new Map();
let connected = false;
let ready = false;
let pendingAction = '';
let reconnectTimer = null;
let reconnectDelay = 1500;
let historyLoaded = false;
let historyTimer = null;
const clock = seconds => {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(s / 3600);
  return (hours ? String(hours).padStart(2, '0') + ':' : '') + String(Math.floor(s / 60) % 60).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
};
const number = value => Number.isFinite(Number(value)) ? Number(value) : 0;
function message(text, error = false) {
  $('message').textContent = text;
  $('message').className = 'message' + (error ? ' error' : '');
}
function updateControls() {
  const busy = current && activeStates.has(current.state);
  const locked = busy || !!pendingAction;
  $('start').hidden = !!busy;
  $('stop').hidden = !busy;
  $('start').disabled = !connected || !ready || !!pendingAction;
  $('stop').disabled = !connected || !!pendingAction || current?.state === 'draining';
  $('start').innerHTML = pendingAction === 'start' ? '正在准备…' : '开始听译 <span aria-hidden="true">→</span>';
  $('stop').textContent = pendingAction === 'stop' || current?.state === 'draining' ? '正在完成并保存…' : '停止并保存';
  document.querySelectorAll('.source').forEach(el => { el.disabled = !!locked; });
  for (const id of ['model', 'title', 'file']) $(id).disabled = !!locked;
  for (const {row, data} of rows.values()) row.querySelector('.retry').hidden = !data.translation_error || current?.state !== 'running';
}
function connectionState(value, text) {
  connected = value;
  $('connection-dot').className = 'dot ' + (value ? 'connected' : 'disconnected');
  $('connection-text').textContent = text;
  updateControls();
}
async function readJSON(path, options = {}) {
  const response = await fetch(path, {...options, cache: 'no-store'});
  let data;
  try { data = await response.json(); } catch { data = {}; }
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '操作失败（' + response.status + '）');
  return data;
}
function api(path, data) {
  return readJSON(path, {
    method: 'POST',
    headers: {'x-session-token': token, ...(data instanceof FormData ? {} : {'Content-Type': 'application/json'})},
    body: data instanceof FormData ? data : JSON.stringify(data || {})
  });
}
function selectSource(value) {
  source = value;
  document.querySelectorAll('.source').forEach(el => {
    const selected = el.dataset.source === value;
    el.classList.toggle('selected', selected);
    el.setAttribute('aria-pressed', String(selected));
  });
  $('file-row').hidden = value !== 'file';
  $('source-hint').textContent = sourceHints[value];
}
function clearSegments() {
  rows.clear();
  $('subtitles').replaceChildren();
  $('empty').hidden = false;
  $('segment-count').textContent = '0 个片段';
}
function followCaptions() {
  if ($('follow').checked) $('subtitle-scroll').scrollTop = $('subtitle-scroll').scrollHeight;
}
function segment(data, follow = true) {
  if (!data.id) return;
  $('empty').hidden = true;
  let entry = rows.get(data.id);
  if (!entry) {
    const row = document.createElement('article');
    row.className = 'subtitle';
    row.innerHTML = '<div class="sub-meta"><span class="time"></span><span class="sub-label"></span></div><div class="english" lang="en"></div><div class="chinese" lang="zh-CN"></div><div class="translation-note" hidden></div><div class="translation-error" hidden></div><button type="button" class="retry" hidden>重试翻译</button>';
    $('subtitles').append(row);
    entry = {row, data};
    rows.set(data.id, entry);
    row.querySelector('.retry').onclick = async () => {
      const button = row.querySelector('.retry');
      button.disabled = true;
      button.textContent = '正在提交…';
      try {
        await api('/api/retry/' + encodeURIComponent(data.id));
        button.textContent = '已加入翻译队列';
        message('已重新提交该片段；译文完成后会自动更新。');
      } catch (error) {
        message(error.message, true);
        button.textContent = '重试翻译';
        button.disabled = false;
      }
    };
  }
  entry.data = data;
  const row = entry.row;
  const hasTranslation = typeof data.translation === 'string' && !!data.translation.trim();
  const matched = hasTranslation && data.translated_revision != null && data.translated_revision === data.revision;
  const stale = hasTranslation && !matched;
  const failed = !!data.translation_error;
  row.className = 'subtitle' + (!data.final ? ' draft' : '') + (stale ? ' stale' : '') + (!hasTranslation ? ' pending' : '') + (failed ? ' failed' : '');
  row.querySelector('.time').textContent = clock(data.start) + ' — ' + clock(data.end);
  row.querySelector('.sub-label').textContent = failed ? '翻译失败' : stale ? '译文更新中' : !hasTranslation ? (data.final ? '等待翻译' : '识别中 · 等待翻译') : data.final ? '已确认' : '临时译文 · 可能修订';
  row.querySelector('.english').textContent = data.source || '';
  row.querySelector('.chinese').textContent = hasTranslation ? data.translation : failed ? '暂未获得中文译文' : '正在等待中文译文…';
  const note = row.querySelector('.translation-note');
  note.hidden = !stale;
  note.textContent = failed ? '显示的是旧版译文，尚未匹配当前原文。' : '原文已有变化，先显示旧版译文，等待更新。';
  const error = row.querySelector('.translation-error');
  error.hidden = !failed;
  error.textContent = failed ? '原文已保留。' + data.translation_error : '';
  const retry = row.querySelector('.retry');
  retry.hidden = !failed || current?.state !== 'running';
  retry.disabled = false;
  retry.textContent = '重试翻译';
  // The server retains a rolling window; full records remain available through exports.
  while (rows.size > 500) {
    const [key, previous] = rows.entries().next().value;
    previous.row.remove();
    rows.delete(key);
  }
  $('segment-count').textContent = rows.size + ' 个片段' + (rows.size === 500 ? ' · 显示最近内容' : '');
  if (follow) followCaptions();
}
function renderStatusMessage(s) {
  if (!connected) return;
  if (s.error) message(s.error, true);
  else if (s.state === 'loading') message('正在加载本地识别与翻译模型，首次加载可能需要一点时间。');
  else if (s.state === 'draining') message('已停止收音，正在处理剩余音频和译文；保存完成前请保持服务运行。');
  else if (number(s.asr_lag) > 30 || number(s.translation_wait) > 30) message('处理明显落后。音频继续保存在本机，未处理内容会继续排队。', true);
  else if (number(s.asr_lag) > 15 || number(s.translation_wait) > 15) message('正在追赶课堂进度，已减少临时重译；音频与未完成片段持续保存。');
  else if (s.state === 'running' && number(s.input_idle) > 5) message('暂时没有新的音频数据，请检查声音播放、输入设备或系统权限。', true);
  else if (s.state === 'running') message('中文尽早显示；临时译文可能修订，原文与译文匹配后才会标记「已确认」。');
  else if (s.state === 'completed') message('会话已保存。双语 TXT 保留片段状态，SRT 仅导出已确认的双语字幕。');
  else if (s.state === 'interrupted') message('会话已中断，已接收的音频保存在本机；可导入录音重新处理未完成内容。', true);
}
function status(data) {
  if (!data) return;
  const previous = current;
  if (previous?.id !== data.id) clearSegments();
  current = data;
  if (activeStates.has(data.state)) {
    selectSource(data.source || 'microphone');
    $('model').value = data.model || 'turbo';
    $('title').value = data.title || '';
  }
  $('state').textContent = stateNames[data.state] || '等待开始';
  $('state-dot').className = 'dot ' + (data.state === 'running' ? 'running' : ['error', 'interrupted'].includes(data.state) ? 'error' : '');
  $('duration').textContent = clock(data.audio_seconds);
  $('lag').textContent = number(data.asr_lag).toFixed(1) + ' 秒';
  $('lag').parentElement.classList.toggle('alert', number(data.asr_lag) > 15);
  $('queue').textContent = number(data.translation_queue) + (data.translation_busy ? ' + 1 处理中' : '');
  $('queue').style.fontSize = data.translation_busy ? '16px' : '';
  $('queue').parentElement.classList.toggle('alert', number(data.translation_wait) > 15);
  $('memory').textContent = data.memory_bytes ? (number(data.memory_bytes) / 1024 ** 3).toFixed(1) + ' GB' : '—';
  const level = activeStates.has(data.state) ? Math.min(100, number(data.level) * 600) : 0;
  $('level').style.width = level + '%';
  $('meter').setAttribute('aria-valuenow', String(Math.round(level)));
  $('caption-title').textContent = data.title || '课堂字幕';
  $('session-meta').textContent = (sourceNames[data.source] || '本机音频') + ' · Whisper ' + (data.model === 'small' ? 'Small' : 'Turbo') + ' · English → 简体中文';
  $('exports').hidden = false;
  $('save-note').textContent = activeStates.has(data.state) ? '音频与字幕持续保存在本机' : '记录已保存在本机';
  for (const kind of ['txt', 'srt']) {
    $('export-' + kind).href = '/api/export/' + encodeURIComponent(data.id) + '/' + kind;
    $('export-' + kind).setAttribute('aria-label', '下载当前课堂双语 ' + kind.toUpperCase());
  }
  updateControls();
  renderStatusMessage(data);
  if (!activeStates.has(data.state) && (previous?.id !== data.id || previous?.state !== data.state)) {
    clearTimeout(historyTimer);
    historyTimer = setTimeout(() => { if ($('history-details').open || historyLoaded) loadHistory(); }, 1200);
  }
}
function snapshot(data) {
  clearSegments();
  if (data.status) status(data.status);
  for (const item of data.segments || []) segment(item, false);
  followCaptions();
}
function scheduleReconnect() {
  if (reconnectTimer) return;
  connectionState(false, '连接中断 · 正在重连');
  message('本机字幕连接中断，正在自动重连；页面断线不会停止服务端采集。', true);
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    initialize();
  }, reconnectDelay);
  reconnectDelay = Math.min(10000, reconnectDelay * 1.5);
}
function connect() {
  const socket = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws?token=' + encodeURIComponent(token));
  websocket = socket;
  socket.onopen = () => {
    if (websocket !== socket) return;
    reconnectDelay = 1500;
    connectionState(true, ready ? '本机服务已连接' : '模型尚未准备完成');
    if (!ready) message('模型与运行环境仍在准备，请从页底查看下载进度。');
    else if (current) renderStatusMessage(current);
    else message('选择声音来源，开始听译。中文会尽早出现，临时译文可能随讲话修订。');
  };
  socket.onmessage = event => {
    if (websocket !== socket) return;
    try {
      const data = JSON.parse(event.data);
      if (data.type === 'snapshot') snapshot(data);
      else if (data.type === 'segment') segment(data);
      else if (data.type === 'status') status(data);
      else if (data.type === 'error') message(data.message || '本机处理发生错误', true);
    } catch { message('收到的字幕数据无法解析，正在重新同步。', true); socket.close(); }
  };
  socket.onclose = () => { if (websocket === socket) scheduleReconnect(); };
  socket.onerror = () => { socket.close(); };
}
async function initialize() {
  try {
    const data = await readJSON('/api/state', {signal: AbortSignal.timeout(10000)});
    token = data.token;
    ready = !!data.ready;
    if (data.session) snapshot({status: data.session, segments: data.segments});
    else {
      current = null;
      clearSegments();
      $('exports').hidden = true;
      $('caption-title').textContent = '课堂字幕';
      $('session-meta').textContent = '英文原文 · 中文译文';
      $('state').textContent = '准备开始';
      $('state-dot').className = 'dot';
      $('duration').textContent = '00:00';
      for (const id of ['lag', 'queue', 'memory']) $(id).textContent = '—';
      $('level').style.width = '0%';
      $('meter').setAttribute('aria-valuenow', '0');
    }
    if (!websocket || websocket.readyState > WebSocket.OPEN) connect();
  } catch { scheduleReconnect(); }
}
document.querySelectorAll('.source').forEach(el => {
  el.onclick = () => selectSource(el.dataset.source);
});
$('file').onchange = () => {
  const file = $('file').files[0];
  $('file-info').textContent = file ? file.name + ' · ' + (file.size / 1024 ** 2).toFixed(1) + ' MB · 按原速读取' : '按原速读取录音，结果自动保存在本机。';
};
$('follow').onchange = followCaptions;
$('start').onclick = async () => {
  if (pendingAction || !connected || !ready) return;
  pendingAction = 'start';
  updateControls();
  try {
    let uploadId;
    if (source === 'file') {
      const file = $('file').files[0];
      if (!file) throw new Error('请先选择录音或视频文件。');
      if (!file.size) throw new Error('录音文件为空，请重新选择。');
      if (file.size > 1024 ** 3) throw new Error('单个录音最多 1 GB，请选择较小的文件。');
      message('正在导入「' + file.name + '」，请稍候…');
      const form = new FormData();
      form.append('file', file);
      uploadId = (await api('/api/uploads', form)).id;
    }
    await api('/api/sessions', {source, model: $('model').value, title: $('title').value.trim(), upload_id: uploadId, realtime: true});
    message('正在加载本地模型，字幕将在识别到英语后出现。');
    const data = await readJSON('/api/state');
    if (data.session) snapshot({status: data.session, segments: data.segments});
  } catch (error) { message(error.message || '开始失败，请重试。', true); }
  finally { pendingAction = ''; updateControls(); }
};
$('stop').onclick = async () => {
  if (pendingAction) return;
  pendingAction = 'stop';
  updateControls();
  message('正在停止收音，并完成剩余内容与保存…');
  try {
    await api('/api/stop');
    const data = await readJSON('/api/state');
    if (data.session) snapshot({status: data.session, segments: data.segments});
  } catch (error) { message(error.message || '停止请求失败，请检查服务。', true); }
  finally { pendingAction = ''; updateControls(); }
};
async function loadHistory() {
  $('history-button').disabled = true;
  $('history-status').textContent = '正在读取…';
  try {
    const data = await readJSON('/api/sessions');
    const sessions = [...data].sort((a, b) => String(b.started_at || b.id).localeCompare(String(a.started_at || a.id))).slice(0, 20);
    $('history').replaceChildren(...sessions.map(item => {
      const row = document.createElement('div');
      row.className = 'history-row';
      const info = document.createElement('div');
      info.className = 'history-info';
      const title = document.createElement('span');
      title.className = 'history-title';
      title.textContent = item.title || '课堂记录';
      const meta = document.createElement('span');
      meta.className = 'history-meta';
      let date = (item.started_at || '').slice(0, 16).replace('T', ' ');
      const parsed = new Date(item.started_at);
      if (!Number.isNaN(parsed.getTime())) date = parsed.toLocaleString('zh-CN', {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false});
      meta.textContent = date + ' · ' + clock(item.audio_seconds) + ' · ' + (sourceNames[item.source] || '音频') + ' · ' + (stateNames[item.state] || item.state || '记录');
      info.append(title, meta);
      row.append(info);
      for (const [kind, text] of [['txt', '双语 TXT'], ['srt', 'SRT']]) {
        const link = document.createElement('a');
        link.href = '/api/export/' + encodeURIComponent(item.id) + '/' + kind;
        link.textContent = text;
        link.setAttribute('aria-label', '下载「' + (item.title || '课堂记录') + '」' + text);
        row.append(link);
      }
      return row;
    }));
    historyLoaded = true;
    $('history-status').textContent = sessions.length ? '最近 ' + sessions.length + ' 条记录 · 下载双语文本或已确认的字幕' : '还没有课堂记录。开始一堂课后，记录会自动保存在这里。';
  } catch (error) { $('history-status').textContent = '历史记录读取失败：' + error.message; }
  finally { $('history-button').disabled = false; }
}
$('history-button').onclick = loadHistory;
$('history-details').addEventListener('toggle', () => { if ($('history-details').open && !historyLoaded) loadHistory(); });
document.addEventListener('visibilitychange', () => { if (!document.hidden && websocket?.readyState === WebSocket.CLOSED && !reconnectTimer) initialize(); });
initialize();
