// Check withdrawal of a previously displayed translation without a browser/model.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('classroom/static/app.js', 'utf8');
const paint = source.slice(source.indexOf('function paint(entry)'), source.indexOf('function repaintAll()'));
const context = {};
vm.createContext(context);
vm.runInContext(paint, context);
function typer() {
  return {target: '旧译文', shown: '旧译文', finish() { this.shown = this.target; }};
}
const entry = {
  data: {asr_status: 'unresolved', source: '[识别异常：请定位录音补处理]'},
  shownZh: '旧译文', en: typer(), zh: typer(),
  zhEl: {classList: {add() {}}, title: ''}, btn: {hidden: false}
};
context.paint(entry);
assert.equal(entry.zh.shown, entry.data.source);
assert.equal(entry.en.shown, entry.data.source);
assert.equal(entry.shownZh, entry.data.source);
assert.equal(entry.btn.hidden, true);
assert.match(entry.zhEl.title, /未确认/);
console.log('ASR unresolved UI withdrawal: passed');
