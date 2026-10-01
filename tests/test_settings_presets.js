const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const nodes = new Map();
const node = key => {
  if (!nodes.has(key)) nodes.set(key, {value:'',innerHTML:'',textContent:'',hidden:true,disabled:false,
    reportValidity:()=>true,addEventListener(e,f){this[e]=f;}});
  return nodes.get(key);
};
const buttons = ['popular','affordable'].map(kind=>({dataset:{defaultPreset:kind},addEventListener(e,f){this[e]=f;}}));
const pending = [], calls = [];
let applied = 0;
const context = {document:{querySelector:node,querySelectorAll:()=>buttons},window:{applyDefaultPreset:()=>{applied++;return true;}},
  api:(url)=>{calls.push(url);return new Promise((resolve,reject)=>pending.push({resolve,reject}));},
  won:{format:String},Date,encodeURIComponent};
vm.createContext(context);
vm.runInContext(fs.readFileSync('auto_trader/static/settings-presets.js','utf8'),context);
const result = name=>({name,basis:'기준',week_start:'2026-09-28',next_refresh_on:'2026-10-05',payload:{},targets:[{symbol:'005930',stock_name:'<safe>',price:50000,quantity_estimate:5,rank:2}],planned_budget:300000,generated_at:'2026-10-01T01:00:00Z',notes:['<note>']});
(async()=>{
  node('#preset-budget').value='300000';
  const first = buttons[0].click();
  assert.equal(buttons[1].disabled,true);
  node('#preset-budget').value='200000';node('#preset-budget').input();
  const second = buttons[1].click();
  pending[1].resolve(result('가성비'));await second;
  pending[0].resolve(result('늦은 인기'));await first;
  assert.ok(node('#preset-preview').innerHTML.includes('가성비'));
  assert.ok(!node('#preset-preview').innerHTML.includes('늦은 인기'));
  assert.ok(node('#preset-preview').innerHTML.includes('&lt;safe&gt;'));
  assert.ok(node('#preset-preview').innerHTML.includes('&lt;note&gt;'));
  assert.ok(node('#preset-preview').innerHTML.includes('2026-10-05'));
  assert.ok(node('#preset-preview').innerHTML.includes('이름을 바꿔 따로 저장'));
  assert.ok(calls[1].endsWith('order_amount=200000'));
  node('#preset-preview').click({target:{closest:()=>true}});
  assert.equal(applied,1);
  node('#preset-budget').input();
  node('#preset-preview').click({target:{closest:()=>true}});
  assert.equal(applied,1,'budget edits invalidate the previous draft');
  const failed=buttons[0].click();pending[2].reject(new Error('조회 실패'));await failed;
  assert.equal(node('#preset-status').textContent,'조회 실패');
  assert.equal(buttons[0].disabled,false);
  assert.equal(node('#preset-preview').hidden,true);
  node('#preset-budget').reportValidity=()=>false;
  await buttons[0].click();assert.equal(calls.length,3,'invalid budgets make no requests');
  let resetCount=0, confirmed=false;
  const values = new Map();
  const field = id=>{if(!values.has(id))values.set(id,{value:'',scrollIntoView(){}});return values.get(id);};
  const formContext={window:{},$:field,confirm:()=>confirmed,
    resetStrategy:()=>{resetCount++;field('#strategy-id').value='';},strategies:[{id:1,name:'기본 인기'}],
    setTimePicker:(id,v)=>field('#'+id).value=v,renderStrategyTargets(){},updateSizing(){},setStrategyMessage(){}};
  vm.createContext(formContext);
  const source=fs.readFileSync('auto_trader/static/settings.js','utf8');
  const start=source.indexOf('window.applyDefaultPreset = function');
  const end=source.indexOf('\n};',start)+4;
  vm.runInContext(source.slice(start,end),formContext);
  const adopted={payload:{name:'기본 인기',execution_mode:'DRY_RUN',order_amount:'300000',short_period:10,long_period:40,trading_start:'09:10',trading_end:'15:20'},targets:[{symbol:'005930',stock_name:'삼성전자'}]};
  field('#strategy-id').value='1';field('#strategy-name').value='수정 중';
  assert.equal(formContext.window.applyDefaultPreset(adopted),false);
  assert.equal(resetCount,0);
  confirmed=true;
  assert.equal(formContext.window.applyDefaultPreset(adopted),true);
  assert.equal(field('#strategy-id').value,'');
  assert.equal(field('#strategy-name').value,'기본 인기 (2)');
  assert.equal(field('#strategy-amount').value,'300000');
  assert.equal(formContext.strategies[0].name,'기본 인기');
  console.log('Preset preview passed: stale requests, budget invalidation, escaped results, apply, errors and validation.');
})().catch(e=>{console.error(e);process.exitCode=1;});
