// Start-page language buttons: choice is remembered, locked during a session, keyboard-operable.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync('classroom/static/app.js', 'utf8');
const nodes = {}, saved = [];
function button(mode) {
  return {dataset: {mode}, attrs: {}, disabled: false, tabIndex: 0, focused: false,
    setAttribute(k, v) { this.attrs[k] = v; }, focus() { this.focused = true; }};
}
const buttons = ['en', 'ru', 'mixed'].map(button);
const checked = () => buttons.find(b => b.attrs['aria-checked'] === 'true').dataset.mode;
const c = {
  $: id => nodes[id] || (nodes[id] = {}),
  document: {
    querySelectorAll: sel => sel === '.lang' ? buttons : [],
    querySelector: sel => buttons.find(b => sel.includes('"' + b.dataset.mode + '"')),
  },
  pref: {languageMode: 'en'}, serverLanguageModes: ['en', 'ru', 'mixed'], current: null, pending: '',
  ACTIVE: new Set(['loading', 'running', 'draining']),
  LANGUAGE_MODES: {en: '英语 → 中文', ru: '俄语 → 中文', mixed: '英俄中混合 → 中文'},
  localStorage: {setItem: (...args) => saved.push(args)},
};
vm.createContext(c);
vm.runInContext(source.slice(source.indexOf('const LANGUAGE_NOTES'), source.indexOf('function chosenFile')), c);

c.renderLanguageMode();
assert.equal(checked(), 'en');
assert.equal(buttons[0].tabIndex, 0); assert.equal(buttons[1].tabIndex, -1, 'only the chosen option is in the tab order');
assert.equal(buttons[0].disabled, false);

buttons[1].onclick();
assert.equal(c.pref.languageMode, 'ru'); assert.deepEqual(saved.at(-1), ['tk.languageMode', 'ru']);
assert.equal(checked(), 'ru');
assert.match(nodes['language-mode-note'].textContent, /俄语原文/);

buttons[2].onclick();
assert.match(nodes['language-mode-note'].textContent, /中文直接保留/);

// Arrow keys move the choice and focus.
let prevented = false;
nodes['language-mode'].onkeydown({key: 'ArrowRight', preventDefault() { prevented = true; }});
assert.equal(prevented, true); assert.equal(c.pref.languageMode, 'en', 'wraps around'); assert.equal(buttons[0].focused, true);
nodes['language-mode'].onkeydown({key: 'ArrowLeft', preventDefault() {}});
assert.equal(c.pref.languageMode, 'mixed');

// Locked while a session runs: shows the session's language, ignores clicks.
c.current = {state: 'running', language_mode: 'ru'}; c.renderLanguageMode();
assert.equal(checked(), 'ru'); assert(buttons.every(b => b.disabled));
assert.match(nodes['language-mode-note'].textContent, /已锁定/);
buttons[0].onclick(); assert.equal(c.pref.languageMode, 'mixed');

// After the session ends the next-record preference comes back.
c.current = {state: 'completed', language_mode: 'ru'}; c.renderLanguageMode();
assert.equal(checked(), 'mixed');
c.pending = 'upload'; c.renderLanguageMode(); assert(buttons.every(b => b.disabled));
c.pending = ''; c.serverLanguageModes = ['en']; c.renderLanguageMode();
assert.match(nodes['language-mode-note'].textContent, /引擎需重启/);
assert.equal(buttons[1].disabled, true, 'modes the engine lacks cannot be chosen');
console.log('language buttons: remembered, locked during a session, keyboard-operable');
