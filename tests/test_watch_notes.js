const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const nodes=new Map();const node=s=>{if(!nodes.has(s))nodes.set(s,{value:'',textContent:'',disabled:false,addEventListener(e,f){this[e]=f;},focus(){}});return nodes.get(s);};
const dialog={open:false,setAttribute(){},querySelector:node,addEventListener(e,f){this[e]=f;},showModal(){this.open=true;},close(){this.open=false;this.closeEvent();}};
dialog.addEventListener=(e,f)=>{if(e==='close')dialog.closeEvent=f;else dialog[e]=f;};
const pending=[],calls=[];let click,rendered;
const button={dataset:{watchNote:'005930'},isConnected:true,focus(){}};
const scope={document:{createElement:()=>dialog,body:{append(){}},addEventListener(e,f){click=f;},querySelectorAll:()=>[button],querySelector:()=>button},
 myCandidates:[{symbol:'005930',name:'삼성전자',note:'기존 메모'}],renderMyCandidates:items=>{rendered=items;},api:(path,options)=>{calls.push({path,options});return new Promise((resolve,reject)=>pending.push({resolve,reject}));}};
vm.createContext(scope);vm.runInContext(fs.readFileSync('auto_trader/static/watch-notes.js','utf8'),scope);
(async()=>{
 click({target:{closest:()=>button}});assert.equal(node('textarea').value,'기존 메모');
 node('textarea').value='  새 메모  ';
 const save=node('form').submit({preventDefault(){}});
 await node('form').submit({preventDefault(){}});assert.equal(calls.length,1,'duplicate saves blocked');
 assert.equal(node('[data-note-cancel]').disabled,true);
 pending.shift().reject(new Error('연결 실패'));await save;
 assert.equal(dialog.open,true);assert.equal(node('textarea').value,'  새 메모  ');assert.match(node('[data-note-status]').textContent,/연결 실패/);
 const retry=node('form').submit({preventDefault(){}});
 assert.equal(calls.at(-1).options.method,'PUT');assert.deepEqual(JSON.parse(calls.at(-1).options.body),{note:'새 메모'});
 pending.shift().resolve({saved:true});await retry;
 assert.equal(rendered[0].note,'새 메모');assert.equal(dialog.open,false);assert.equal(node('textarea').disabled,false);
 console.log('Watch notes: existing draft, duplicate guard, failure recovery, trimmed save and list update passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
