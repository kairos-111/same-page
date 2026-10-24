const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('classroom/static/app.js', 'utf8');
const seen = [];
const context = { openingHistory: 0, viewingHistory: false,
  getJSON: async url => url === '/api/state' ? {session: {id:'live',state:'running'},segments:[]} : {session:{id:'old',state:'completed',historical:true},segments:[{id:'first'}]},
  snapshot: (s, lines) => seen.push(['snapshot',s.id,lines.length]), show: v => seen.push(['show',v]),
  clearSession() {}, flash: e => {throw Error(e);} };
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('async function openHistory'),source.indexOf('/* ---------- 课后录音选择')),context);
(async () => {
  await context.openHistory('old');
  assert.equal(context.viewingHistory,true);
  assert.deepEqual(seen[0],['snapshot','old',1]);
  await context.leaveHistory();
  assert.equal(context.viewingHistory,false);
  assert.deepEqual(seen[2],['snapshot','live',0]);
  assert.deepEqual(seen[3],['show','start']);
  await context.openHistory('live',true);
  assert.equal(context.viewingHistory,false);
  console.log('history read and return to current session checks passed');
})().catch(e => {console.error(e);process.exitCode=1;});
