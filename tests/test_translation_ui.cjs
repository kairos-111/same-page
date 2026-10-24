// Exercise visible version/failure states without a server, browser or real session.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('classroom/static/app.js', 'utf8');
const context = { current: { state: 'running' } };
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function paint(entry)'), source.indexOf('function repaintAll()')), context);
const entry = {
  data: { source: 'Revised English.', revision: 2, translated_revision: 1, translation: '旧中文' },
  shownZh: '', stateLabel: {}, btn: {}, en: { set() {} },
  zh: { set(text) { this.text = text; } }, zhEl: { classList: { toggle() {} } }
};
context.paint(entry);
assert.equal(entry.zh.text, '旧中文');
assert.match(entry.stateLabel.textContent, /旧译文.*待更新/);
entry.data.translation_error = 'truncated';
context.paint(entry);
assert.match(entry.stateLabel.textContent, /未完成/);
assert.equal(entry.btn.hidden, false);
entry.data = { ...entry.data, translation_error: '', translation: '最新中文', translated_revision: 2 };
context.paint(entry);
assert.equal(entry.zh.text, '最新中文');
assert.equal(entry.stateLabel.textContent, '');
assert.equal(entry.btn.hidden, true);
console.log('translation UI version/failure checks passed');
