const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

function makeElement(id, tag) {
  return {
    id, tagName: (tag || 'div').toUpperCase(), hidden: false, disabled: false, textContent: '',
    value: '', className: '', children: [], checked: false, dataset: {}, attributes: {}, style: {},
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
    onclick: null, oninput: null, onkeydown: null,
  };
}

const source = fs.readFileSync('classroom/static/app.js', 'utf8');
const elements = new Map();
const radios = [
  { value: 'small', checked: true, setAttribute() {}, getAttribute() { return 'true'; } },
  { value: 'turbo', checked: false, setAttribute() {}, getAttribute() { return 'false'; } },
];
const context = {
  console,
  setTimeout: () => 1, clearTimeout: () => {}, setInterval: () => 1,
  localStorage: { store: {}, getItem(k) { return this.store[k] ?? null; }, setItem(k, v) { this.store[k] = v; } },
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
    querySelectorAll: (selector) => (selector === 'input[name="model"]' ? radios : []),
    createElement: (tag) => makeElement(tag, tag),
    getElementById: (id) => {
      if (!elements.has(id)) elements.set(id, makeElement(id));
      return elements.get(id);
    },
    addEventListener() {},
    documentElement: { style: { setProperty() {} }, classList: { add() {}, remove() {}, toggle() {} } },
    body: { classList: { add() {}, remove() {}, toggle() {} } },
  },
  fetch: async () => ({ ok: true, status: 200, json: async () => ({ items: [], total: 0 }) }),
  flash: () => {},
};
context.window = context;
context.addEventListener = () => {};
vm.createContext(context);
vm.runInContext(source, context);

const el = id => context.document.getElementById(id);

(async () => {
  // The settings page dropdown was never wired up: changing it did nothing.
  assert.equal(el('settings-model').value, 'small', 'the settings dropdown starts on the saved model');
  el('settings-model').value = 'turbo';
  el('settings-model').onchange();
  assert.equal(el('start-model').value, 'turbo', 'the start page dropdown follows the settings dropdown');
  assert.equal(context.localStorage.store['tk.model'], 'turbo', 'the choice is remembered');

  // And the start page dropdown drives the settings dropdown back.
  el('start-model').value = 'small';
  el('start-model').onchange();
  assert.equal(el('settings-model').value, 'small', 'the settings dropdown follows the start page');
  assert.equal(context.localStorage.store['tk.model'], 'small');

  // The settings page 只看译文 switch was dead too.
  const toggle = el('settings-zh-only');
  assert.equal(toggle.checked, false);
  toggle.checked = true;
  toggle.onchange();
  assert.equal(context.localStorage.store['tk.zhOnly'], '1', 'the preference is saved');
  assert.equal(el('zh-only').textContent, '开', 'the subtitle page switch shows the new state');

  // Toggling on the subtitle page moves the settings switch back.
  el('zh-only').onclick();
  assert.equal(toggle.checked, false, 'the settings switch follows the subtitle page');
  assert.equal(context.localStorage.store['tk.zhOnly'], '0');

  console.log('settings model dropdown and 只看译文 switch are wired to the same preferences');
})().catch(e => { console.error(e); process.exitCode = 1; });
