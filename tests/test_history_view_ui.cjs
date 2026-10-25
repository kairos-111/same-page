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
  assert.deepEqual(seen[3],['show','library']);
  await context.openHistory('live',true);
  assert.equal(context.viewingHistory,false);
  let resolve;
  context.getJSON=()=>new Promise(r=>{resolve=r;});
  const opening=context.openHistory('delayed');
  const desktop=fs.readFileSync('classroom/static/desktop.js','utf8');
  vm.runInContext(desktop.slice(desktop.indexOf('async function navigatePage'),desktop.indexOf("document.querySelectorAll")),context);
  await context.navigatePage('settings');
  resolve({session:{id:'delayed',state:'completed',historical:true},segments:[]});
  await opening;
  assert.equal(seen.at(-1)[1],'settings');
  assert.equal(context.viewingHistory,false);
  console.log('history read, return and navigation race checks passed');
})().catch(e => {console.error(e);process.exitCode=1;});
