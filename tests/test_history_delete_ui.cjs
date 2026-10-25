const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const source=fs.readFileSync('classroom/static/app.js','utf8');
class Node {
  constructor(tag='div'){this.tag=tag;this.children=[];this.events={};this.disabled=false;this.textContent='';}
  append(...nodes){this.children.push(...nodes);}
  setAttribute(){}
  addEventListener(type,fn){this.events[type]=fn;}
  showModal(){this.open=true;}
  close(){this.open=false;if(this.events.close)this.events.close();}
  focus(){this.focused=true;}
}
const nodes=Object.fromEntries(['delete-heading','delete-course-name','delete-description','delete-confirm','delete-cancel','delete-dialog'].map(id=>[id,new Node()]));
const calls=[];let refresh=0;
const context={$:id=>nodes[id],document:{createElement:tag=>new Node(tag)},
  current:null,ACTIVE:new Set(['loading','running','draining']),SOURCE_NAME:{file:'导入录音'},duration:()=> '1秒',
  post:async(url,body)=>{calls.push({url,body});},getJSON:async()=>({session:null}),
  clearSession(){},snapshot(){},renderStart(){},renderLive(){},loadHistory:async()=>{refresh++;},
  flash:e=>{throw Error(e);},openHistory(){}};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('let resolveHistoryDeletion'),source.indexOf('async function openHistory')),context);
const item={id:'fake',title:'<b>课程</b>',source:'file',state:'completed',has_recording:true};
function button(row,text){
  // 行内动作和折叠菜单里的动作都要能找到。
  const found=[];
  const walk=nodes=>nodes.forEach(n=>{if(n.textContent===text)found.push(n);if(n.children)walk(n.children);});
  walk(row.children);
  assert.equal(found.length,1,`expected exactly one 「${text}」button`);
  return found[0];
}
(async()=>{
  const row=context.historyRow(item);
  let action=button(row,'删除记录').onclick();
  assert.equal(nodes['delete-dialog'].open,true);
  assert.equal(nodes['delete-course-name'].textContent,item.title);
  assert.equal(nodes['delete-cancel'].focused,true);
  nodes['delete-cancel'].onclick();await action;assert.equal(calls.length,0);
  action=button(row,'删除录音').onclick();
  assert.match(nodes['delete-description'].textContent,/字幕和记录会保留/);
  let prevented=false;nodes['delete-dialog'].events.cancel({preventDefault(){prevented=true;}});
  await action;assert.equal(prevented,true);assert.equal(calls.length,0);
  action=button(row,'删除录音').onclick();nodes['delete-confirm'].onclick();await action;
  assert.equal(calls[0].url,'/api/sessions/fake/recording');assert.equal(calls[0].body.keep,false);
  action=button(row,'删除记录').onclick();nodes['delete-confirm'].onclick();await action;
  assert.equal(calls[1].url,'/api/sessions/fake/delete');assert.equal(calls[1].body.confirm,true);
  assert.equal(refresh,2);
  const live=context.historyRow({...item,busy:true});
  assert.equal(button(live,'删除记录').disabled,true);assert.equal(button(live,'删除录音').disabled,true);
  context.post=async()=>{throw Error('synthetic error');};let message='';context.flash=e=>{message=e;};
  const retry=context.historyRow(item);action=button(retry,'删除记录').onclick();nodes['delete-confirm'].onclick();await action;
  assert.equal(message,'synthetic error');assert.equal(button(retry,'删除记录').disabled,false);
  console.log('history deletion: cancel, Escape, audio-only, whole-class, busy protection and error retry passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
