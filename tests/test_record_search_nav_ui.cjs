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
    querySelector(sel) {
      const cls = sel.replace(/^\./, '');
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
    scrollIntoView() { this.scrolled = (this.scrolled || 0) + 1; },
    focus() {}, close() {}, showModal() {},
    onclick: null, oninput: null, onkeydown: null,
  };
  el.remove = () => {};
  return el;
}

const source = fs.readFileSync('classroom/static/app.js', 'utf8');
const elements = new Map();

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
  fetch: async () => ({ ok: true, status: 200, json: async () => ({ items: [], total: 0 }) }),
  setTimeout: (fn) => { try { fn(); } catch (e) {} return 1; },
};
context.window = context;
context.addEventListener = () => {};
vm.createContext(context);
vm.runInContext(source, context);

const el = id => context.document.getElementById(id);
const evalIn = expr => vm.runInContext(expr, context);

(async () => {
  evalIn(`(() => {
    newPara(0);
    upsertLine({id: 'a', start: 0, end: 1, source: 'apple pie', translation: '苹果派', final: true});
    upsertLine({id: 'b', start: 1, end: 2, source: 'banana', translation: '香蕉', final: true});
    upsertLine({id: 'c', start: 2, end: 3, source: 'apple juice', translation: '苹果汁', final: true});
  })()`);

  // Searching marks every match and counts them.
  el('live-search').value = 'apple';
  el('live-search').oninput();
  assert.equal(evalIn('matchOrder.length'), 2, 'both lines with the word are matches');
  assert.equal(el('live-search-count').textContent, '1/2', 'the counter shows where we are');
  assert.equal(el('live-search-prev').disabled, false, 'the previous button is usable');

  // Next moves to the second match and wraps around.
  el('live-search-next').onclick();
  assert.equal(el('live-search-count').textContent, '2/2', 'next moves to the second match');
  el('live-search-next').onclick();
  assert.equal(el('live-search-count').textContent, '1/2', 'next wraps around to the first match');
  el('live-search-prev').onclick();
  assert.equal(el('live-search-count').textContent, '2/2', 'previous wraps back to the last match');

  // Enter jumps to the next match too.
  el('live-search').onkeydown({key: 'Enter', preventDefault() {}});
  assert.equal(el('live-search-count').textContent, '1/2', 'Enter moves to the next match');

  // Escape clears the search and the highlighting.
  el('live-search').onkeydown({key: 'Escape', preventDefault() {}});
  assert.equal(el('live-search').value, '', 'Escape empties the search box');
  assert.equal(evalIn('matchOrder.length'), 0, 'Escape clears the matches');
  assert.equal(el('live-search-count').textContent, '', 'the counter is cleared');
  assert.equal(el('live-search-next').disabled, true, 'the buttons are disabled again');

  // A search with no hits says so and offers nothing to step through.
  el('live-search').value = 'zzz';
  el('live-search').oninput();
  assert.equal(el('live-search-count').textContent, '', 'no counter without a match');
  assert.match(evalIn('transient && transient.text || ""'), /没有找到/, 'the user is told nothing was found');

  console.log('in-record search steps through matches and clears cleanly');
})().catch(e => { console.error(e); process.exitCode = 1; });
