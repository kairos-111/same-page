const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('classroom/static/app.js', 'utf8');
const nodes = {};
for (const id of ['recording-dialog', 'recording-error', 'recording-keep', 'recording-discard', 'recording-choice']) nodes[id] = {};
let cancelHandler;
nodes['recording-dialog'] = {addEventListener(type,fn) {if(type==='cancel') cancelHandler=fn;}, open: false, showModal() {this.open = true;}, close() {this.open = false;}};
const calls = [];
const context = { $: id => nodes[id], ACTIVE: new Set(['loading', 'running', 'draining']), num: Number,
  recordingPrompted: new Set(), recordingDialogId: null, current: null, viewingHistory: false,
  post: async (url, body) => { calls.push([url, body]); },
  getJSON: async () => ({session: null}), loadHistory: async () => {}, applyStatus() {} };
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function maybePromptRecording'), source.indexOf('/* ---------- 字幕页')), context);
const s = {id: 'fake-class', state: 'completed', saved: false, recording_decision: 'pending', audio_seconds: 10};
context.maybePromptRecording(s);
assert.equal(nodes['recording-dialog'].open, false);
s.saved = true;
context.maybePromptRecording(s);
assert.equal(nodes['recording-dialog'].open, true);
let prevented=false;
cancelHandler({preventDefault(){prevented=true;}});
assert.equal(prevented,true);
assert.equal(nodes['recording-dialog'].open,true);
assert.equal(calls.length,0);
(async () => {
  await context.chooseRecording(false);
  assert.equal(calls[0][0], '/api/sessions/fake-class/recording');
  assert.equal(calls[0][1].keep, false);
  assert.equal(nodes['recording-dialog'].open, false);
  assert.equal(nodes['recording-error'].textContent, '');
  console.log('recording prompt timing, mandatory choice and discard passed');
})().catch(e => {console.error(e); process.exitCode = 1;});
