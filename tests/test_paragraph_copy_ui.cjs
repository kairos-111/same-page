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
    // The mock understands the tiny bit of markup the paragraph builder uses.
    querySelector(sel) {
      const cls = sel.replace(/^\./, '');
      // The paragraph skeleton is set with innerHTML, so parse it on demand.
      if (this._byClass === undefined) {
        this._byClass = new Map();
        for (const m of String(this.innerHTML || '').matchAll(/class="([^"]+)"/g)) {
          const child = makeElement(m[1]);
          child.className = m[1];
          this._byClass.set(m[1], child);
        }
      }
      if (this.className === cls) return this;
      return this._byClass.get(cls) || null;
    },
    querySelectorAll() { return []; },
    scrollIntoView() {}, focus() {}, close() {}, showModal() {},
    onclick: null, oninput: null, onkeydown: null,
  };
  el.remove = () => {};
  return el;
}

const source = fs.readFileSync('classroom/static/app.js', 'utf8');
const elements = new Map();
const copied = [];

const context = {
  console,
  setTimeout: () => 1,
  clearTimeout: () => {},
  setInterval: () => 1,
  localStorage: { getItem: () => null, setItem: () => {} },
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  ResizeObserver: class { observe() {} unobserve() {} disconnect() {} },
  navigator: { clipboard: { writeText: async (t) => { copied.push(t); } } },
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
  fetch: async () => ({ ok: true, status: 200, json: async () => ({ items: [], total: 0 }) }),
  flash: () => {},
};
context.window = context;
context.addEventListener = () => {};
vm.createContext(context);
vm.runInContext(source, context);

const evalIn = expr => vm.runInContext(expr, context);

(async () => {
  // A paragraph is a self-contained block of transcript with its own copy button.
  const para = evalIn(`(() => {
    const p = newPara(0);
    upsertLine({id: 's1', start: 0, end: 1, source: 'Hello world', translation: '你好世界', final: true});
    upsertLine({id: 's2', start: 1, end: 2, source: 'Second line', translation: '第二句', final: true});
    return p;
  })()`);

  assert.ok(para.copy, 'the paragraph has a copy button');
  assert.equal(para.copy.className, 'para-copy');

  // The button copies exactly this paragraph: source line, then translation.
  await para.copy.onclick();
  assert.equal(copied.length, 1, 'the paragraph was copied');
  assert.equal(copied[0], 'Hello world\n你好世界\n\nSecond line\n第二句',
    'the copied text holds this paragraph’s source and translation');

  // A second paragraph copies only its own text, not the whole transcript.
  const second = evalIn(`(() => {
    const p = newPara(10);
    upsertLine({id: 's3', start: 10, end: 11, source: 'Third', translation: '第三', final: true});
    return p;
  })()`);
  await second.copy.onclick();
  assert.equal(copied[1], 'Third\n第三', 'the second paragraph copies only itself');

  // Copying an empty paragraph says so instead of silently doing nothing.
  const empty = evalIn(`newPara(20)`);
  await empty.copy.onclick();
  assert.equal(copied.length, 2, 'an empty paragraph copies nothing');

  console.log('per-paragraph copy takes just that paragraph’s source and translation');
})().catch(e => { console.error(e); process.exitCode = 1; });
