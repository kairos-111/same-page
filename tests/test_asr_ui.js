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
// Current rule: an unresolved range withdraws the old preview and puts no system
// text, card or blank line in the body; the gap is listed under Details instead.
const entry = {
  data: {asr_status: 'unresolved', source: '[识别异常：请定位录音补处理]'},
  shownZh: '旧译文', en: typer(), zh: typer(),
  stateLabel: {textContent: '（等待中文）'},
  zhEl: {classList: {add() {}}, title: ''}, btn: {hidden: false}
};
context.paint(entry);
assert.equal(entry.en.shown, '', 'the old source preview is withdrawn');
assert.equal(entry.zh.shown, '', 'the old translation preview is withdrawn');
assert.equal(entry.shownZh, '', 'no translation is kept for the withdrawn range');
assert.equal(entry.stateLabel.textContent, '', 'no inline status text is shown');
assert.equal(entry.btn.hidden, true, 'no retry button for an unresolved range');
console.log('ASR unresolved UI withdrawal: passed');
