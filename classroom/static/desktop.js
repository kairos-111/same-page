'use strict';
let nativeInfo = null;
let controllingModels = false;
const bytesLabel = bytes => bytes >= 1024 ** 3 ? (bytes / 1024 ** 3).toFixed(1) + ' GB' : Math.round(bytes / 1024 ** 2) + ' MB';
async function navigatePage(page) {
  ++openingHistory; // Cancel a history load when the user navigates elsewhere.
  if (viewingHistory) await leaveHistory();
  show(page);
}
document.querySelectorAll('[data-page]').forEach(button => { button.onclick = () => navigatePage(button.dataset.page); });
$('home-library').onclick = () => navigatePage('library');
$('engine-status').onclick = () => navigatePage('settings');
// 搜索与筛选逻辑统一在 app.js 中维护。
$('resume-class').onclick = async () => {
  try {
    const data = await getJSON('/api/state');
    viewingHistory = false;
    if (data.session) { snapshot(data.session, data.segments); show('live'); }
  } catch (e) { flash(e.message); }
};
async function loadSettings() {
  $('settings-model').value = pref.model;
  $('settings-zh-only').checked = pref.zhOnly;
  try {
    const [data,state] = await Promise.all([getJSON('/api/system'),getJSON('/api/state')]);
    const locked = controllingModels || !!data.model_operation || !!(state.session && ACTIVE.has(state.session.state));
    $('models-start').disabled = locked; $('models-stop').disabled = locked;
    if (!controllingModels) $('model-control-message').textContent = data.model_error || (data.model_operation ? '正在操作模型…' : (locked ? '翻译进行中，暂不能启停' : ''));
    $('about-version').textContent = data.version;
    if (!data.development) $('model-settings-note').textContent='Small、Turbo 和 Hy-MT 已包含在本机应用中。两个识别模型可以同时加载，翻译时只使用所选的一个；翻译和收尾期间不能启停。';
    $('storage-summary').textContent = data.course_count + ' 条记录 · 磁盘可用 ' + bytesLabel(data.free_bytes);
    const loaded=data.models.filter(model=>model.loaded).length;
    $('engine-summary').textContent=data.model_operation ? '正在处理…' : loaded ? loaded+'/'+data.models.length+' 运行中' : '未加载';
    $('engine-status').classList.toggle('running',loaded>0 && !data.model_operation);
    $('engine-status').title=loaded ? '已加载 '+loaded+' 个模型，点击管理' : '模型未加载；开始翻译时会自动加载。点击管理';
    $('models-start').disabled=locked || data.models.length===3 && data.models.every(m=>m.loaded);
    $('models-stop').disabled=locked || !loaded;
    $('model-list').replaceChildren(...data.models.map(model => {
      const row=document.createElement('div');row.className='engine-model'+(model.loaded ? ' loaded' : '');
      const dot=document.createElement('span');dot.className='engine-dot';dot.setAttribute('aria-hidden','true');
      const name=document.createElement('span');name.className='engine-model-name';
      name.textContent=model.id==='hy-mt' ? 'Hy-MT 翻译' : model.id==='small' ? 'Small 识别' : 'Turbo 识别';
      const control=document.createElement('button');control.type='button';control.className='engine-model-toggle'+(model.loaded ? ' running' : ' stopped');
      control.textContent=model.loaded ? '运行中' : model.available ? '已停止' : '缺少文件';
      control.setAttribute('aria-label',model.name+'，'+(model.loaded ? '运行中，点击停止' : model.available ? '已停止，点击启动' : '缺少文件'));
      control.title=model.name+' · '+bytesLabel(model.bytes)+(model.loaded ? ' · 运行中，点击停止' : model.available ? ' · 已停止，点击启动' : ' · 缺少文件');
      control.disabled=locked || (!model.available && !model.loaded);
      control.onclick=()=>controlModels(model.id,model.loaded ? 'stop' : 'start');
      row.append(dot,name,control);return row;
    }));
    $('engine-ownership').textContent = nativeInfo ? (nativeInfo.owns_engine ? '由桌面应用管理本地引擎' : '连接已运行的本地引擎，退出窗口不会停止共享服务') : '浏览器预览 · 与桌面应用共享同一本地引擎';
  } catch (e) { $('engine-summary').textContent = '未连接'; $('engine-status').classList.remove('running'); $('model-control-message').textContent = '暂时无法连接本地引擎'; $('settings-message').textContent = '无法读取设置：' + e.message; }
}
async function controlModels(target,action) {
  if (controllingModels) return;
  controllingModels=true;
  $('model-control-message').textContent=action==='start' ? '正在加载模型，请稍等…' : '正在停止模型…';
  await loadSettings();
  try {
    await post('/api/models/'+target+'/'+action,{recognition:pref.model});
    $('model-control-message').textContent=action==='start' ? '模型已启动，不会自动录音。' : '模型已停止，文件仍保留。';
  } catch(e) { $('model-control-message').textContent=e.message; }
  finally {controllingModels=false;await loadSettings();}
}
$('models-start').onclick=()=>controlModels('all','start');
$('models-stop').onclick=()=>controlModels('all','stop');
loadSettings();
setInterval(loadSettings,3000);
// 设置页的模型与「只看译文」由 app.js 统一维护，这里只负责把状态显示出来。
document.querySelectorAll('[data-folder]').forEach(button => {
  button.onclick = async () => { try { await post('/api/folders/' + button.dataset.folder); } catch (e) { $('settings-message').textContent = e.message; } };
});
async function requestDesktopExit() {
  try {
    const data = await getJSON('/api/state');
    if (data.session?.saved && data.session.recording_decision==='pending' && num(data.session.audio_seconds)) {
      if (!$('recording-dialog').open) openRecordingChoice(data.session.id);
      flash('请先选择是否保留本次录音，再退出。');return;
    }
    const busy = data.session && ACTIVE.has(data.session.state);
    $('desktop-exit-message').textContent = busy ? '当前仍在翻译。确认后将停止收音、处理剩余内容并保存，再退出应用。' : (nativeInfo?.owns_engine ? '当前翻译已结束。退出将关闭本应用启动的本地引擎。' : '退出桌面窗口。已运行的共享本地服务将继续可用。');
    $('exit-confirm').textContent = busy ? '结束、保存并退出' : '退出应用';
    $('desktop-exit-error').textContent = '';
    if (!$('desktop-exit').open) $('desktop-exit').showModal();
  } catch (e) { flash('无法确认处理状态，请恢复连接后再退出：' + e.message); }
}
$('exit-cancel').onclick = () => $('desktop-exit').close();
$('desktop-quit').onclick = requestDesktopExit;
$('exit-confirm').onclick = async () => {
  $('exit-confirm').disabled = true; $('exit-cancel').disabled = true;
  try {
    const data = await getJSON('/api/state');
    if (data.session && ACTIVE.has(data.session.state)) {
      $('desktop-exit-message').textContent = '正在收尾保存，请稍等…';
      await post('/api/stop');
    }
    const saved = await getJSON('/api/state');
    if (saved.session?.saved && saved.session.recording_decision==='pending' && num(saved.session.audio_seconds)) {
      $('desktop-exit').close();
      if (!$('recording-dialog').open) openRecordingChoice(saved.session.id);
      flash('记录已保存，请选择是否保留录音，再退出。');return;
    }
    await window.__TAURI__.core.invoke('finish_exit');
  } catch (e) { $('desktop-exit-error').textContent = String(e); }
  finally { $('exit-confirm').disabled = false; $('exit-cancel').disabled = false; }
};
async function connectDesktop() {
  if (!window.__TAURI__) return;
  try {
    nativeInfo = await window.__TAURI__.core.invoke('desktop_info');
    await window.__TAURI__.event.listen('desktop-close-requested', requestDesktopExit);
    $('desktop-quit').hidden = false;
    $('desktop-version').textContent = (nativeInfo.development ? '桌面开发版 · ' : '本机独立版 · ') + nativeInfo.version;
  } catch (e) { $('desktop-version').textContent = '桌面连接未完成'; $('settings-message').textContent = String(e); }
}
// Update the return-to-live shortcut without changing the page being read.
async function refreshLiveShortcut() {
  try { const data = await getJSON('/api/state'); $('resume-class').hidden = !(data.session && ACTIVE.has(data.session.state)); } catch {}
}
connectDesktop();
refreshLiveShortcut();
setInterval(refreshLiveShortcut, 5000);
