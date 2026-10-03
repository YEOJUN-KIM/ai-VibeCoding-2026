const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const nodes=new Map();
const node=id=>{if(!nodes.has(id))nodes.set(id,{value:'',innerHTML:'',textContent:'',disabled:false,addEventListener(e,f){this[e]=f;},setAttribute(){},removeAttribute(){}});return nodes.get(id);};
node('#etf-sort').value='POPULAR';
const pending=[],calls=[];
const context={$:node,window:{},location:{hash:''},AbortController,URLSearchParams,escapeHtml:s=>String(s),metric:s=>s??'-',won:{format:String},
  document:{querySelectorAll:()=>[],body:{append(){}},createElement:()=>({setAttribute(){},addEventListener(){},querySelector:node})},
  api:url=>{calls.push(url);return new Promise(resolve=>pending.push(resolve));}};
vm.createContext(context);vm.runInContext(fs.readFileSync('auto_trader/static/etf.js','utf8'),context);
const result=name=>({page:1,total_pages:2,total:11,results:[{name,symbol:'123456',security_type:'ETF',market:'KOSPI',price:10000}]});
(async()=>{
  const first=context.window.loadEtfs();context.window.loadEtfs();assert.equal(calls.length,1,'initial requests merge');
  assert.ok(calls[0].includes('security_type=ETF'));assert.ok(calls[0].includes('page_size=10'));
  pending.shift()(result('첫 ETF'));await first;
  assert.ok(node('#etf-body').innerHTML.includes('첫 ETF'));
  const old=context.window.loadEtfs(true);
  node('#etf-query').value='배당';const latest=context.window.loadEtfs(true);
  const oldResolve=pending.shift(),newResolve=pending.shift();newResolve(result('배당 ETF'));await latest;
  oldResolve(result('이전 ETF'));await old;
  assert.ok(node('#etf-body').innerHTML.includes('배당 ETF'),'late response must not replace latest search');
  assert.equal(node('#etf-prev').disabled,true);assert.equal(node('#etf-next').disabled,false);
  const editing=context.window.loadEtfs(true);
  node('#etf-query').value='새 검색';node('#etf-query').input();
  pending.shift()(result('입력 전 ETF'));await editing;
  assert.ok(!node('#etf-body').innerHTML.includes('입력 전 ETF'));
  console.log('ETF checks passed: ETF-only request, 10-item paging, initial merge and stale search protection.');
})().catch(e=>{console.error(e);process.exitCode=1;});
