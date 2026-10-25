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
    querySelector() { return null; },
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
const players = [];

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
  Audio: class { constructor(src) { this.src = src; this.paused = true; this.duration = 0; this.currentTime = 0; players.push(this); } play() { this.paused = false; return Promise.resolve(); } pause() { this.paused = true; } addEventListener() {} removeAttribute(k) { if (k === 'src') this.src = ''; } load() {} },
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
    addEventListener() {},
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
  evalIn('renderLive = () => {}; loadHistory = async () => {};');

  // Play record A.
  evalIn(`current = {id: 'a', title: 'A', historical: true, has_recording: true}; renderRecordTools();`);
  el('record-play').onclick();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(players.length, 1);
  assert.match(players[0].src, /\/api\/sessions\/a\/recording$/);
  assert.equal(players[0].paused, false);

  // Open record B: the old player stops and B gets its own.
  evalIn(`current = {id: 'b', title: 'B', historical: true, has_recording: true}; renderRecordTools();`);
  assert.equal(players[0].paused, true, 'switching records stops the old recording');
  assert.equal(el('record-seek').value, '0', 'the progress bar starts over');
  el('record-play').onclick();
  assert.equal(players.length, 2, 'a new player is made for the new record');
  assert.match(players[1].src, /\/api\/sessions\/b\/recording$/, 'B plays its own recording');

  // Deleting B's recording releases its player too.
  evalIn(`current = {...current, has_recording: false}; renderRecordTools();`);
  assert.equal(players[1].paused, true);
  el('record-play').onclick();
  assert.equal(players.length, 2, 'no player for a record without a recording');

  // A fast import shows progress, never the "no sound" warning.
  evalIn(`connected = true; viewingHistory = false; transient = null;
    current = {id: 'c', state: 'running', fast_import: true, input_done: true, input_idle: 60,
               audio_seconds: 600, processed_seconds: 150, asr_lag: 450}; renderBanner();`);
  assert.match(el('banner').textContent, /已识别 02:30 \/ 10:00（25%）/);
  assert.doesNotMatch(el('banner').textContent, /没收到声音/);

  // Live capture still warns after silence.
  evalIn(`current = {id: 'd', state: 'running', input_idle: 8}; renderBanner();`);
  assert.match(el('banner').textContent, /没收到声音/);

  console.log('recording player follows the open record; imports show progress');
})().catch(e => { console.error(e); process.exitCode = 1; });
