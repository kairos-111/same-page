const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('classroom/static/app.js', 'utf8');
const nodes = {};
for (const id of ['recording-dialog', 'recording-error', 'recording-keep', 'recording-discard', 'recording-later', 'recording-choice']) nodes[id] = {};
nodes['recording-dialog'] = {open: false, showModal() {this.open = true;}, close() {this.open = false;}};
const calls = [];
const context = { $: id => nodes[id], ACTIVE: new Set(['loading', 'running', 'draining']), num: Number,
  recordingPrompted: new Set(), recordingDialogId: null, current: null,
  post: async (url, body) => { calls.push([url, body]); },
  getJSON: async () => ({session: null}), loadHistory: async () => {}, applyStatus() {} };
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function maybePromptRecording'), source.indexOf('/* ---------- 上课页')), context);
const s = {id: 'fake-class', state: 'completed', saved: false, recording_decision: 'pending', audio_seconds: 10};
context.maybePromptRecording(s);
assert.equal(nodes['recording-dialog'].open, false);
s.saved = true;
context.maybePromptRecording(s);
assert.equal(nodes['recording-dialog'].open, true);
nodes['recording-later'].onclick();
assert.equal(calls.length, 0);
context.maybePromptRecording(s);
assert.equal(nodes['recording-dialog'].open, false); // no nagging until explicit/reload
context.openRecordingChoice(s.id);
(async () => {
  await context.chooseRecording(false);
  assert.equal(calls[0][0], '/api/sessions/fake-class/recording');
  assert.equal(calls[0][1].keep, false);
  assert.equal(nodes['recording-dialog'].open, false);
  console.log('recording prompt timing, defer and discard choice passed');
})().catch(e => {console.error(e); process.exitCode = 1;});
