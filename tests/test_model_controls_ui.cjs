const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('classroom/static/desktop.js','utf8');
class Node {constructor(){this.children=[];this.classList={toggle(){}};}setAttribute(k,v){this[k]=v;}append(...n){this.children.push(...n);}replaceChildren(...n){this.children=n;}}
const nodes={};const calls=[];
let data={version:'0.9.1',models:[{id:'small',name:'Small',available:true,loaded:false},{id:'turbo',name:'Turbo',available:true,loaded:false},{id:'hy-mt',name:'Hy-MT',available:true,loaded:false}],course_count:0,free_bytes:0,development:false};
const c={$:id=>nodes[id]||(nodes[id]=new Node()),document:{createElement:()=>new Node()},pref:{model:'small',zhOnly:false},ACTIVE:new Set(['running']),controllingModels:false,nativeInfo:null,bytesLabel:()=>'',getJSON:async path=>path==='/api/system'?data:{session:null},post:async(url)=>calls.push(url)};
vm.createContext(c);vm.runInContext(source.slice(source.indexOf('async function loadSettings'),source.indexOf('loadSettings();\nsetInterval')),c);
(async()=>{
 await c.loadSettings();assert.equal(nodes['models-start'].disabled,false);
 assert.equal(nodes['engine-summary'].textContent,'未加载','the sidebar shows one short status');
 let buttons=nodes['model-list'].children.map(row=>row.children[2]);
 assert.deepEqual(buttons.map(b=>b.textContent),['已停止','已停止','已停止']);assert(buttons.every(b=>b.className.endsWith(' stopped')));
 assert.match(buttons[0]['aria-label'],/已停止，点击启动/);
 await buttons[0].onclick();assert.equal(calls.at(-1),'/api/models/small/start');
 data.models[0].loaded=true;data.models[2].loaded=true;
 await c.loadSettings();assert.equal(nodes['models-start'].disabled,false,'Turbo is still stopped');
 assert.equal(nodes['engine-summary'].textContent,'2/3 运行中');
 buttons=nodes['model-list'].children.map(row=>row.children[2]);assert.equal(buttons[0].textContent,'运行中');assert(buttons[0].className.endsWith(' running'));
 assert.match(buttons[0]['aria-label'],/点击停止/);
 await buttons[0].onclick();assert.equal(calls.at(-1),'/api/models/small/stop');
 data.models[1].loaded=true;await c.loadSettings();assert.equal(nodes['models-start'].disabled,true);
 await nodes['models-stop'].onclick();assert.equal(calls.at(-1),'/api/models/all/stop');
 data.model_operation={target:'all',action:'start'};await c.loadSettings();assert(nodes['model-list'].children.every(row=>row.children[2].disabled));
 console.log('model controls: state labels and colors, partial readiness, all-start/stop and busy protection passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
