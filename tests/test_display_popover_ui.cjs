const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

function makeElement(id, tag) {
  return {
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
    onclick: null, oninput: null, onkeydown: null,
  };
}

const source = fs.readFileSync('classroom/static/app.js', 'utf8');
const elements = new Map();
const listeners = {};
// The real markup decides which panels start hidden.
const html = fs.readFileSync('classroom/static/index.html', 'utf8');
const initiallyHidden = new Set([...html.matchAll(/id="([^"]+)"[^>]*\shidden/g)].map(m => m[1]));
const initialAttrs = {};
for (const m of html.matchAll(/<[^>]*id="([^"]+)"[^>]*>/g)) {
  const attrs = {};
  for (const a of m[0].matchAll(/([a-z-]+)="([^"]*)"/g)) attrs[a[1]] = a[2];
  initialAttrs[m[1]] = attrs;
}

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
      if (!elements.has(id)) {
        const node = makeElement(id);
        node.hidden = initiallyHidden.has(id);
        Object.assign(node.attributes, initialAttrs[id] || {});
        elements.set(id, node);
      }
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
  // The display popover groups the size controls and the translation-only switch.
  assert.equal(el('display').hidden, true, 'the display panel starts closed');
  assert.equal(el('display-btn').getAttribute('aria-expanded'), 'false');

  el('display-btn').onclick({stopPropagation() {}});
  assert.equal(el('display').hidden, false, 'clicking 显示 opens the panel');
  assert.equal(el('display-btn').getAttribute('aria-expanded'), 'true');

  // Opening the detail panel closes the display panel: only one panel is open at a time.
  el('info-btn').onclick({stopPropagation() {}});
  assert.equal(el('info').hidden, false, 'the detail panel opens');
  assert.equal(el('display').hidden, true, 'the display panel closes in favour of the detail panel');

  el('display-btn').onclick({stopPropagation() {}});
  assert.equal(el('display').hidden, false, 'the display panel opens again');

  // The 只看译文 switch reads as on/off instead of a bare toggle.
  assert.equal(el('zh-only').textContent, '关', 'the switch starts off');
  el('zh-only').onclick();
  assert.equal(el('zh-only').getAttribute('aria-pressed'), 'true');
  assert.equal(el('zh-only').textContent, '开', 'the switch shows the new state');
  el('zh-only').onclick();
  assert.equal(el('zh-only').textContent, '关', 'the switch toggles back');

  // The font size controls still work from inside the panel.
  const before = el('zh-val').textContent;
  el('zh-up').onclick();
  assert.notEqual(el('zh-val').textContent, before, 'the translation size changes');
  el('zh-down').onclick();
  assert.equal(el('zh-val').textContent, before, 'and comes back');

  console.log('display popover, mutual exclusion and the 只看译文 switch checks passed');
})().catch(e => { console.error(e); process.exitCode = 1; });
