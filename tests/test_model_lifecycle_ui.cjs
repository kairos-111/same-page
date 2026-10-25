// The desktop app loads all models at launch and releases them at quit.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('classroom/static/desktop.js','utf8');
const code=source.slice(source.indexOf('async function autoStartModels'),source.indexOf('async function connectDesktop'));
function context(system,state={ready:true,session:null}) {
  const c={calls:[],pref:{model:'small'},ACTIVE:new Set(['loading','running','draining']),system,state,
    getJSON:async path=>path==='/api/system'?c.system:c.state,
    post:async url=>{c.calls.push(url);},
    controlModels:async (target,action)=>{c.calls.push(target+':'+action);},setTimeout:(f)=>f()};
  vm.createContext(c);vm.runInContext(code,c);return c;
}
const models=loaded=>loaded.map((on,i)=>({id:['small','turbo','hy-mt'][i],available:true,loaded:on}));
(async()=>{
  let c=context({models:models([false,false,false])});
  await c.autoStartModels();assert.deepEqual(c.calls,['all:start'],'launch loads every model');
  c=context({models:models([true,true,true])});
  await c.autoStartModels();assert.deepEqual(c.calls,[],'already running: nothing to do');
  c=context({models:models([false,false,false])},{ready:true,session:{state:'running'}});
  await c.autoStartModels();assert.deepEqual(c.calls,[],'never touches models during a translation');
  c=context({models:models([false,false,false])},{ready:false,session:null});
  await c.autoStartModels();assert.deepEqual(c.calls,[],'not deployed yet');
  c=context({models:models([true,false,true])});
  await c.stopAllModels();assert.deepEqual(c.calls,['/api/models/all/stop'],'quit releases every model');
  c=context({models:models([false,false,false])});
  await c.stopAllModels();assert.deepEqual(c.calls,[],'nothing loaded: no request');
  let polls=0;c=context(null);
  c.getJSON=async()=>({model_operation:++polls<3?{action:'start'}:null,models:models([true,true,true])});
  await c.stopAllModels();assert.equal(polls,3);assert.deepEqual(c.calls,['/api/models/all/stop'],'waits for a launch load to finish, then stops');
  console.log('model lifecycle: launch auto-start and quit auto-stop passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
