const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

// A minimal DOM good enough for the library and record-tool code paths.
function makeElement(id, tag) {
  const el = {
    id, tagName: (tag || 'div').toUpperCase(), hidden: false, disabled: false, textContent: '', value: '', href: '', title: '',
    className: '', children: [], dataset: {}, attributes: {}, style: {}, files: [],
    classList: {
      _set: new Set(),
      add(...c) { c.forEach(x => this._set.add(x)); },
      remove(...c) { c.forEach(x => this._set.delete(x)); },
      toggle(c, on) { if (on === undefined) { this._set.has(c) ? this._set.delete(c) : this._set.add(c); } else if (on) this._set.add(c); else this._set.delete(c); },
      contains(c) { return this._set.has(c); },
    },
    append(...c) { this.children.push(...c); },
    replaceChildren(...c) { this.children = c; },
    setAttribute(k, v) { this.attributes[k] = v; },
    getAttribute(k) { return this.attributes[k]; },
    removeAttribute(k) { delete this.attributes[k]; },
    addEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
    scrollIntoView() {},
    focus() {},
    close() {}, showModal() {},
    open: false,
    onclick: null, oninput: null, onkeydown: null,
  };
  return el;
}

const source = fs.readFileSync('classroom/static/app.js', 'utf8');
const elements = new Map();
const posts = [];
const flashes = [];
const chips = ['all', 'favorite', 'review', 'trash'].map(filter => {
  const chip = makeElement('chip');
  chip.dataset.filter = filter;
  chip.setAttribute('aria-pressed', String(filter === 'all'));
  return chip;
});

const context = {
  console,
  // Timers are queued, not run: the app clears its own notices on a timer,
  // and the assertions below need to read the notice before it disappears.
  __timers: [],
  setTimeout: (fn) => { context.__timers.push(fn); return context.__timers.length; },
  clearTimeout: () => {},
  setInterval: () => 1,
  navigator: { clipboard: { writeText: async () => {} } },
  location: { protocol: 'http:', host: 'test' },
  WebSocket: class { constructor() { this.close = () => {}; } },
  Audio: class {
    constructor(src) { this.src = src; this.paused = true; this.duration = 0; this.currentTime = 0; }
    play() { this.paused = false; return Promise.resolve(); }
    pause() { this.paused = true; }
    addEventListener() {}
  },
  AbortSignal: { timeout: () => ({}) },
  localStorage: { getItem: () => null, setItem: () => {} },
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  ResizeObserver: class { observe() {} unobserve() {} disconnect() {} },
  URLSearchParams,
  FormData,
  Date,
  JSON,
  document: {
    querySelector: (selector) => (selector === '.chip[aria-pressed="true"]'
      ? chips.find(c => c.getAttribute('aria-pressed') === 'true') || null : null),
    querySelectorAll: (selector) => (selector === '.chip' ? chips : []),
    createElement: (tag) => makeElement(tag, tag),
    getElementById: (id) => {
      if (!elements.has(id)) elements.set(id, makeElement(id));
      return elements.get(id);
    },
    addEventListener() {},
    documentElement: { style: { setProperty() {} }, classList: { add() {}, remove() {}, toggle() {} } },
    body: { classList: { add() {}, remove() {}, toggle() {} } },
  },
  fetch: async (url) => ({ ok: true, status: 200, json: async () => (String(url).startsWith('/api/library')
    ? { items: [], total: 0 } : { items: [] }) }),
  post: async (url, body) => { posts.push([url, body]); return { ok: true }; },
  flash: (text, kind) => { flashes.push([text, kind]); },
  $: (id) => { if (!elements.has(id)) elements.set(id, makeElement(id)); return elements.get(id); },
};
context.window = context;
context.addEventListener = () => {};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context);
const evalIn = code => vm.runInContext(code, context);

(async () => {
  // --- library listing and filters ---
  const items = [
    { id: 'a', title: '数学课', favorite: true, started_at: '2026-10-25T10:00:00', audio_seconds: 60, has_recording: true },
    { id: 'b', title: '英语课', favorite: false, started_at: '2026-10-24T10:00:00', audio_seconds: 30 },
    { id: 'c', title: '待查课', favorite: false, started_at: '2026-10-23T10:00:00', audio_seconds: 10,
      integrity: { warning: '有未识别区间' } },
  ];
  context.fetch = async url => ({ ok: true, status: 200, json: async () => (String(url).startsWith('/api/library')
    ? { items, total: items.length }
    : { items: [{ id: 'z', title: '已删记录', started_at: '2026-10-22T10:00:00' }] }) });
  await evalIn('loadHistory()');
  assert.equal(evalIn('historyTotal'), 3, 'library total');
  assert.equal(elements.get('history').children.length, 3, 'all records listed');
  assert.equal(elements.get('library-count').textContent, 3, 'the sidebar badge counts every record');
  assert.match(elements.get('history-summary').textContent, /共 3 条/, 'the summary states the total');

  // The favorite chip asks the server for starred records only, so a favourite
  // beyond the first page is neither lost nor miscounted.
  const requested = [];
  context.fetch = async url => { requested.push(String(url)); return { ok: true, status: 200, json: async () => (String(url).startsWith('/api/library')
    ? { items, total: items.length }
    : { items: [{ id: 'z', title: '已删记录', started_at: '2026-10-22T10:00:00' }] }) }; };
  chips.forEach(c => c.setAttribute('aria-pressed', 'false'));
  chips[1].setAttribute('aria-pressed', 'true');
  await evalIn('loadHistory()');
  assert.ok(requested.some(url => url.includes('favorite=true')), 'the favorite filter is sent to the server');

  // A record with an integrity warning is filed under 待核查, again on the server.
  requested.length = 0;
  chips[1].setAttribute('aria-pressed', 'false');
  chips[2].setAttribute('aria-pressed', 'true');
  await evalIn('loadHistory()');
  assert.ok(requested.some(url => url.includes('review=true')), 'the review filter is sent to the server');

  // Going back to 全部 clears the filter again.
  requested.length = 0;
  chips[2].setAttribute('aria-pressed', 'false');
  chips[0].setAttribute('aria-pressed', 'true');
  await evalIn('loadHistory()');
  assert.ok(!requested.some(url => url.includes('favorite=true') || url.includes('review=true')),
    'the all view asks for no filter');

  // The trash view replaces the list and explains itself.
  chips.forEach(c => c.setAttribute('aria-pressed', 'false'));
  chips[3].setAttribute('aria-pressed', 'true');
  await evalIn('loadTrash()');
  assert.equal(elements.get('history').children.length, 1, 'trash shows deleted records');
  assert.match(elements.get('history-summary').textContent, /废纸篓/);

  // Each row shows two actions and hides the rest behind one overflow menu.
  chips.forEach(c => c.setAttribute('aria-pressed', 'false'));
  chips[0].setAttribute('aria-pressed', 'true');
  evalIn('renderHistory()');
  const row = elements.get('history').children[0];
  const rowActions = row.children[1];
  const visible = rowActions.children.filter(child => child.tagName !== 'DETAILS');
  assert.equal(visible.length, 2, 'a row shows only two visible actions');
  assert.equal(rowActions.children[rowActions.children.length - 1].tagName, 'DETAILS',
    'the remaining actions live in an overflow menu');
  const menu = rowActions.children[rowActions.children.length - 1].children[1];
  const menuLabels = menu.children.map(child => child.textContent);
  assert.ok(menuLabels.includes('下载双语文本'), 'exports are still reachable');
  assert.ok(menuLabels.includes('删除记录'), 'deletion is still reachable');
  assert.ok(menuLabels.includes('回听录音'), 'playback is still reachable');

  // --- in-record search ---
  const entries = new Map([
    ['1', { data: { id: '1', source: 'Good morning', translation: '早上好' }, enEl: makeElement('en'), zhEl: makeElement('zh') }],
    ['2', { data: { id: '2', source: 'Good night', translation: '晚安' }, enEl: makeElement('en'), zhEl: makeElement('zh') }],
  ]);
  evalIn('rows.clear()');
  for (const [id, entry] of entries) { context.__entry = entry; evalIn(`rows.set(${JSON.stringify(id)}, __entry)`); }
  elements.get('live-search').value = 'morning';
  evalIn('runRecordSearch()');
  assert.equal(evalIn('recordMatches.size'), 1, 'one line matched');
  assert.ok(entries.get('1').enEl.classList.contains('match'), 'matched line is highlighted');
  assert.ok(!entries.get('2').enEl.classList.contains('match'), 'unmatched line is not highlighted');

  elements.get('live-search').value = '';
  evalIn('runRecordSearch()');
  assert.equal(evalIn('recordMatches.size'), 0, 'clearing the query clears matches');
  assert.ok(!entries.get('1').enEl.classList.contains('match'), 'highlight removed again');

  // --- copy ---
  await evalIn('copyVisibleText()');
  // The app owns `flash`, so read the notice it writes instead.
  const note = evalIn('JSON.parse(JSON.stringify(startNote))');
  assert.match(note.text, /复制/, 'copy reports back');
  assert.equal(note.error, false, 'a successful copy is not styled as an error');
  context.__timers.length = 0;

  console.log('library filters, trash view, in-record search and copy checks passed');
})().catch(e => { console.error(e); process.exitCode = 1; });
