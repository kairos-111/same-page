'use strict';
let nativeInfo = null;
const bytesLabel = bytes => bytes >= 1024 ** 3 ? (bytes / 1024 ** 3).toFixed(1) + ' GB' : Math.round(bytes / 1024 ** 2) + ' MB';
async function navigatePage(page) {
  if (viewingHistory) await leaveHistory();
  show(page);
}
document.querySelectorAll('[data-page]').forEach(button => { button.onclick = () => navigatePage(button.dataset.page); });
$('home-library').onclick = () => navigatePage('library');
$('library-new').onclick = () => navigatePage('start');
$('history-search').oninput = renderHistory;
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
    const data = await getJSON('/api/system');
    $('about-version').textContent = data.version;
    $('storage-summary').textContent = data.course_count + ' 堂课 · 磁盘可用 ' + bytesLabel(data.free_bytes);
    $('model-list').replaceChildren(...data.models.map(model => {
      const row = document.createElement('div'); row.className = 'model-row';
      const icon = document.createElement('span'); icon.className = 'model-icon'; icon.textContent = model.id === 'hy-mt' ? '译' : '听';
      const content = document.createElement('div');
      const title = document.createElement('strong'); title.textContent = model.name;
      const description = document.createElement('small'); description.textContent = model.description + ' · ' + bytesLabel(model.bytes);
      content.append(title, description);
      const badge = document.createElement('span'); badge.className = 'model-status' + (model.available ? ' available' : ''); badge.textContent = model.available ? '文件已找到' : '尚未准备';
      row.append(icon, content, badge); return row;
    }));
    $('engine-ownership').textContent = nativeInfo ? (nativeInfo.owns_engine ? '由桌面应用管理本地引擎' : '连接已运行的本地引擎，退出窗口不会停止共享服务') : '浏览器预览 · 与桌面应用共享同一本地引擎';
  } catch (e) { $('settings-message').textContent = '无法读取设置：' + e.message; }
}
$('settings-model').onchange = e => {
  pref.model = e.target.value; localStorage.setItem('tk.model', pref.model);
  document.querySelectorAll('input[name="model"]').forEach(input => { input.checked = input.value === pref.model; });
  showModel(); $('settings-message').textContent = '已保存。下次开始新课时使用所选模型。';
};
$('settings-zh-only').onchange = e => {
  pref.zhOnly = e.target.checked; localStorage.setItem('tk.zhOnly', pref.zhOnly ? '1' : '0'); applyPrefs();
};
document.querySelectorAll('[data-folder]').forEach(button => {
  button.onclick = async () => { try { await post('/api/folders/' + button.dataset.folder); } catch (e) { $('settings-message').textContent = e.message; } };
});
async function requestDesktopExit() {
  try {
    const data = await getJSON('/api/state');
    const busy = data.session && ACTIVE.has(data.session.state);
    $('desktop-exit-message').textContent = busy ? '当前仍在听课。确认后将停止收音、处理剩余内容并保存，再退出应用。' : (nativeInfo?.owns_engine ? '当前课堂已结束。退出将关闭本应用启动的本地引擎。' : '退出桌面窗口。已运行的共享本地服务将继续可用。');
    $('exit-confirm').textContent = busy ? '结束、保存并退出' : '退出应用';
    $('desktop-exit-error').textContent = '';
    if (!$('desktop-exit').open) $('desktop-exit').showModal();
  } catch (e) { flash('无法确认课堂状态，请恢复连接后再退出：' + e.message); }
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
    $('desktop-version').textContent = '桌面开发版 · ' + nativeInfo.version;
  } catch (e) { $('desktop-version').textContent = '桌面连接未完成'; $('settings-message').textContent = String(e); }
}
// Update the return-to-live shortcut without changing the page being read.
async function refreshLiveShortcut() {
  try { const data = await getJSON('/api/state'); $('resume-class').hidden = !(data.session && ACTIVE.has(data.session.state)); } catch {}
}
connectDesktop();
refreshLiveShortcut();
setInterval(refreshLiveShortcut, 5000);
