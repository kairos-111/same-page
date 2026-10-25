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
  // Let the page's own start-up request settle first; it clears the session when
  // the server reports none, and must not race the record set up below.
  await new Promise(resolve => setImmediate(resolve));
  // The full live view is out of scope here; the bar under test is rendered on its own.
  evalIn('renderLive = () => {}; loadHistory = async () => {};');
  // Put the app in a finished-record state so the metadata bar is available.
  evalIn(`current = {id: 's1', title: '原名', note: '原备注', historical: true, has_recording: false, favorite: false}`);
  evalIn('renderRecordTools()');

  // Leaving the note field saves it; no extra button to press.
  el('record-note').value = '新的备注';
  await el('record-note').onblur();
  assert.deepEqual(posted.pop().body, {note: '新的备注'}, 'leaving the note field saves it');

  // The user is told it worked, without hunting for a button.
  assert.equal(el('record-note-state').textContent, '已保存', 'the saved state is shown');

  // The title saves the same way.
  el('record-title').value = '新名字';
  await el('record-title').onblur();
  assert.deepEqual(posted.pop().body, {title: '新名字'}, 'leaving the title field saves it');
  assert.equal(el('record-title-state').textContent, '已保存', 'the title shows the saved state');

  // Nothing changed means nothing is posted.
  el('record-title').value = '新名字';
  await el('record-title').onblur();
  assert.equal(posted.length, 0, 'an unchanged field is not saved again');
  assert.equal(el('record-title-state').textContent, '没有改动', 'the user is told nothing changed');

  // A failing save says so instead of quietly pretending it worked.
  context.fetch = async () => { throw new Error('网络不通'); };
  el('record-note').value = '写不进去';
  await el('record-note').onblur();
  assert.equal(el('record-note-state').textContent, '没保存成功', 'a failed save is reported');
  assert.equal(el('record-note-state').classList.contains('error'), true, 'the failed state is marked');

  // Typing is not wiped by a status refresh while the field has focus.
  context.fetch = async (url, opts) => {
    if (opts && opts.method === 'POST') { posted.push({url, body: JSON.parse(opts.body)}); return { ok: true, status: 200, json: async () => ({}) }; }
    return { ok: true, status: 200, json: async () => ({items: [], total: 0}) };
  };
  el('record-note').value = '正在输入的半句话';
  context.document.activeElement = el('record-note');
  evalIn(`current = {...current, note: '别覆盖我'}; renderRecordTools();`);
  assert.equal(el('record-note').value, '正在输入的半句话', 'a refresh keeps what the user is typing');
  context.document.activeElement = null;

  console.log('record name and note save on their own and say what happened');
})().catch(e => { console.error(e); process.exitCode = 1; });
