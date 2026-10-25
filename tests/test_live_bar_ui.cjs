const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

function makeElement(id, tag) {
  const el = {
    id, tagName: (tag || 'div').toUpperCase(), hidden: false, disabled: false, textContent: '',
    value: '', className: '', children: [], dataset: {}, attributes: {}, style: {},
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
    querySelector() { return (this._q ||= makeElement('q')); },
    querySelectorAll() { return []; },
    scrollIntoView() {}, focus() {}, close() {}, showModal() {},
    onclick: null, oninput: null, onkeydown: null, onblur: null,
  };
  el.remove = () => {};
  return el;
}

const source = fs.readFileSync('classroom/static/app.js', 'utf8');
const elements = new Map();
const posted = [];
const listeners = {};

const context = {
  console,
  setTimeout: () => 1,
  clearTimeout: () => {},
  setInterval: () => 1,
  localStorage: { getItem: () => null, setItem: () => {} },
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  ResizeObserver: class { observe() {} unobserve() {} disconnect() {} },
  navigator: { clipboard: { writeText: async () => {} } },
  location: { protocol: 'http:', host: 'test' },
  WebSocket: class { constructor() { this.close = () => {}; } },
  Audio: class { constructor() { this.paused = true; this.duration = 0; this.currentTime = 0; } play() { return Promise.resolve(); } pause() {} addEventListener() {} },
  AbortSignal: { timeout: () => ({}) },
  URLSearchParams, FormData, Date, JSON,
  document: {
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: (tag) => makeElement(tag, tag),
    getElementById: (id) => {
      if (!elements.has(id)) elements.set(id, makeElement(id));
      return elements.get(id);
    },
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    documentElement: { style: { setProperty() {} }, classList: { add() {}, remove() {}, toggle() {} } },
    body: { classList: { add() {}, remove() {}, toggle() {} } },
  },
  fetch: async (url, opts) => {
    if (opts && opts.method === 'POST') {
      posted.push({url, body: JSON.parse(opts.body)});
      return { ok: true, status: 200, json: async () => ({ok: true}) };
    }
    return { ok: true, status: 200, json: async () => ({items: [], total: 0}) };
  },
  flash: () => {},
};
context.window = context;
context.addEventListener = () => {};
vm.createContext(context);
vm.runInContext(source, context);

const el = id => context.document.getElementById(id);
const evalIn = expr => vm.runInContext(expr, context);

(async () => {
  await new Promise(resolve => setImmediate(resolve));
  const pill = () => el('info-btn');
  const run = (status) => { evalIn(`connected = true; viewingHistory = false; transient = null; current = ${JSON.stringify(status)}; renderLive();`); };

  run({id: 'a', state: 'running', asr_lag: 2, translation_wait: 1, chinese_lag: 0});
  assert.equal(pill().textContent, '已跟上');
  assert.equal(pill().classList.contains('ok'), true);
  run({id: 'a', state: 'running', asr_lag: 8.4, translation_wait: 1, chinese_lag: 0});
  assert.equal(pill().textContent, '延迟约 8 秒');
  assert.equal(pill().classList.contains('ok'), false);
  run({id: 'a', state: 'running', asr_lag: 3, translation_wait: 21, chinese_lag: 0});
  assert.equal(pill().textContent, '落后 21 秒');
  assert.equal(pill().classList.contains('warn'), true);
  assert.equal(el('banner').hidden, true, 'a 15–30 s delay is shown by the label only, not a banner too');
  run({id: 'a', state: 'running', fast_import: true, input_done: true, audio_seconds: 400, processed_seconds: 100});
  assert.equal(pill().textContent, '已处理 25%');
  run({id: 'a', state: 'completed', asr_unresolved: 2});
  assert.equal(pill().textContent, '待核查 2 处');
  run({id: 'a', state: 'completed'});
  assert.equal(pill().textContent, '详情');

  // Export menu opens from the bar and closes after copying.
  for (const id of ['export', 'info', 'display']) el(id).hidden = true; // as in the page
  el('export-btn').onclick({stopPropagation() {}});
  assert.equal(el('export').hidden, false);
  assert.equal(el('info').hidden, true, 'one panel at a time');
  el('copy-text').onclick();
  assert.equal(el('export').hidden, true, 'copying closes the menu');
  assert.match(el('dl-md').href, /\/api\/export\/a\/md$/);

  // ⌘↵ on the start page starts; it does nothing elsewhere or when start is unavailable.
  let started = 0;
  el('start').click = () => { started++; };
  const press = (extra) => listeners.keydown.forEach(fn => fn({key: 'Enter', metaKey: true, preventDefault() {}, target: {closest: () => null}, ...extra}));
  evalIn(`view = 'start'`);
  el('start').disabled = false; press(); assert.equal(started, 1);
  el('start').disabled = true; press(); assert.equal(started, 1, 'disabled start is not triggered');
  el('start').disabled = false; press({isComposing: true}); assert.equal(started, 1, 'IME composition is left alone');
  evalIn(`view = 'live'`); press(); assert.equal(started, 1, 'only on the start page');

  console.log('live bar: latency label, export menu and start shortcut');
})().catch(e => { console.error(e); process.exitCode = 1; });
