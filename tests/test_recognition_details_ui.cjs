const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('classroom/static/app.js','utf8');
const nodes={};
const c={rows:new Map(),recognitionGaps:new Map(),paras:[],MAX_LINES:500,
  current:{asr_unresolved:1,integrity:{warning:'保存核对信息',recognition_gaps:[{start:10,end:14}]}},
  $:id=>nodes[id]||(nodes[id]={}),num:v=>Number(v)||0,clock:v=>String(v),
  updateCaret(){},document:{createElement(){throw Error('gap must not create subtitle nodes');}}};
vm.createContext(c);
vm.runInContext(source.slice(source.indexOf('function renderRecognitionDetails'),source.indexOf('function renderLive')),c);
vm.runInContext(source.slice(source.indexOf('function upsertLine'),source.indexOf('// 新识别出的文字')),c);
let removed=0;
const para={ids:['s1'],el:{remove(){removed++;}}};c.paras.push(para);
c.rows.set('s1',{data:{source:'Old source'},para,en:{finish(){}},zh:{finish(){}},
  enEl:{remove(){removed++;}},zhEl:{remove(){removed++;}}});
c.upsertLine({id:'s1',asr_status:'unresolved',start:10,end:14,source:'系统提示'});
assert.equal(c.rows.size,0);assert.equal(c.paras.length,0);assert.equal(removed,3);
assert.equal(c.recognitionGaps.size,1);
assert.equal(nodes['recognition-ranges'].textContent,'10–14'); // metadata duplicate collapsed
assert.equal(nodes['i-asr-gaps'].textContent,'1 段');
assert.equal(nodes['recognition-details'].hidden,false);
assert.equal(nodes['integrity-note'].textContent,'保存核对信息');
assert.equal(nodes['live-empty'].hidden,false);
for(let i=0;i<510;i++) c.upsertLine({id:'g'+i,asr_status:'unresolved',start:i,end:i+1});
assert.equal(c.recognitionGaps.size,500);
assert.equal(nodes['recognition-ranges'].textContent.split('\n').length,20);
assert.match(nodes['recognition-summary'].textContent,/最近20段/);
c.current=null;c.caret={remove(){}};nodes.lines={replaceChildren(){}};
vm.runInContext(source.slice(source.indexOf('function resetLines()'),source.indexOf('/* ---------- 滚动')),c);
c.resetLines();assert.equal(c.recognitionGaps.size,0);
assert.equal(nodes['recognition-details'].hidden,true);assert.equal(nodes['recognition-ranges'].textContent,'');
// Informational integrity warnings stay in Details, not a reading-area banner.
const banner={};const b={$:()=>banner,current:{state:'completed',integrity:{warning:'有区间待核查'}},connected:true,transient:null,num:Number};
vm.createContext(b);vm.runInContext(source.slice(source.indexOf('function renderBanner()'),source.indexOf('function flash(')),b);
b.renderBanner();assert.equal(banner.hidden,true);
b.current={state:'error',error:'采集停止了'};b.renderBanner();assert.equal(banner.hidden,false);assert.match(banner.textContent,/采集停止/);
console.log('recognition details: no subtitle blocks, stale preview withdrawal, deduplication, bounds/reset, fatal errors retained');
