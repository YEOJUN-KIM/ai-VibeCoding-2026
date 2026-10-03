const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function harness(){
  const nodes=new Map(), pending=[], calls=[], timers=new Map();let timerId=0;
  const node=id=>{if(!nodes.has(id))nodes.set(id,{value:'',innerHTML:'',textContent:'',disabled:false,classList:{add(){},remove(){}},addEventListener(type,fn){this[type]=fn;}});return nodes.get(id);};
  const context={$:node,AbortController,URLSearchParams,clearTimeout:id=>timers.delete(id),setTimeout:fn=>{timers.set(++timerId,fn);return timerId;},escapeHtml:String,won:{format:String},integer:{format:String},
    api:(url,options)=>{calls.push({url,signal:options?.signal});return new Promise((resolve,reject)=>pending.push({resolve,reject}));}};
  vm.createContext(context);
  return {node,context,pending,calls,timers,run:code=>vm.runInContext(code,context)};
}
const read=file=>fs.readFileSync('auto_trader/static/'+file,'utf8');
const result=name=>({page:1,total_pages:1,total:1,page_size:20,results:[{symbol:'005930',name,market:'KOSPI',security_type:'STOCK',is_common_share:true}]});
async function testSelection(){
  const h=harness(),source=read('settings.js');
  h.run('let stockSearchSequence=0,stockSearchController=null,stockSearchTimer=null,stockSearchResults=[],activeStockResult=-1;');
  h.run(source.slice(source.indexOf('function hideStockAutocomplete'),source.indexOf('function renderStrategyTargets')));
  h.run(source.slice(source.indexOf('function renderStockAutocomplete'),source.indexOf('function setTimePicker')));
  h.run(source.slice(source.indexOf("$('#strategy-symbol').addEventListener('input'"),source.indexOf("$('#strategy-symbol').addEventListener('keydown'")));
  h.node('#strategy-symbol').value='삼';const old=h.run("searchStocks('삼')");
  h.node('#strategy-symbol').value='현';h.node('#strategy-symbol').input({target:h.node('#strategy-symbol'),isComposing:true});
  assert.equal(h.calls[0].signal.aborted,true);
  assert.equal(h.timers.size,1,'search must be scheduled during Korean composition');
  h.pending.shift().resolve(result('삼성전자'));await old;
  assert.ok(!h.node('#stock-autocomplete').innerHTML.includes('삼성전자'),'input must invalidate before debounce');
  const latest=h.run("searchStocks('현')");h.pending.shift().resolve(result('현대차'));await latest;
  assert.ok(h.node('#stock-autocomplete').innerHTML.includes('현대차'));
  const closing=h.run("searchStocks('현')");h.run('hideStockAutocomplete()');
  h.pending.shift().resolve(result('닫은 뒤 결과'));await closing;
  assert.ok(!h.node('#stock-autocomplete').innerHTML.includes('닫은 뒤 결과'));
  h.node('#strategy-symbol').value='삼';const failing=h.run("searchStocks('삼')");
  h.node('#strategy-symbol').value='현';const good=h.run("searchStocks('현')");
  const failure=h.pending.shift(),success=h.pending.shift();success.resolve(result('현대차'));await good;
  failure.reject(new Error('old network failure'));await failing;
  assert.ok(h.node('#stock-autocomplete').innerHTML.includes('현대차'));
}
async function testMarket(){
  const h=harness(),source=read('stocks.js');
  h.run('let loading=false,stockListSequence=0,stockListController=null,sparklineSequence=0,sparklineController=null,searchTimer=null,activePage=1,totalPages=1;const marketItems=new Map();function closeMarketStream(){}function loadSparklines(){}');
  h.context.renderRows=items=>h.node('#stock-list-body').innerHTML=items.map(i=>i.name).join(',');
  h.run(source.slice(source.indexOf('function invalidateStockList'),source.indexOf('async function toggleFavorite')));
  h.run(source.slice(source.indexOf('$("#stock-query").addEventListener("input"'),source.indexOf('$("#stock-list-body").addEventListener')));
  h.node('#stock-query').value='삼';const old=h.run('loadStocks()');
  h.node('#stock-page-size').value='100';
  h.node('#stock-query').value='현';const latest=h.run('loadStocks()');
  assert.equal(h.calls.length,2,'new query must run while old query is loading');
  assert.equal(h.calls[0].signal.aborted,true);
  const first=h.pending.shift(),second=h.pending.shift();second.resolve(result('현대차'));
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(h.node('#stock-list-body').innerHTML,'현대차','names should render before pricing');
  assert.ok(h.calls[1].url.includes('include_quotes=false'));
  assert.ok(h.calls[1].url.includes('page_size=100'));
  assert.equal(h.pending.length,0,'pricing waits while typing settles');
  for(const [id,fn] of h.timers){h.timers.delete(id);fn();}
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(h.pending.length,1,'pricing follows the fast identity response');
  h.pending.shift().resolve(result('현대차'));await latest;
  first.resolve(result('삼성전자'));await old;assert.equal(h.node('#stock-list-body').innerHTML,'현대차');
  const editing=h.run('loadStocks()');h.node('#stock-query').value='';h.node('#stock-query').input({isComposing:true});
  h.pending.shift().resolve(result('이전 검색'));await editing;
  assert.ok(!h.node('#stock-list-body').innerHTML.includes('이전 검색'));
}
async function testChartCancellation(){
  const h=harness(),source=read('stocks.js');
  h.context.document={querySelector:()=>null};h.context.CSS={escape:String};
  h.run('let sparklineSequence=0,sparklineController=null;const latestPrices=new Map();');
  h.run(source.slice(source.indexOf('async function loadSparklines'),source.indexOf('function renderRows')));
  const old=h.run("loadSparklines(['1','2','3','4','5','6','7'])");
  assert.equal(h.calls.length,1,'charts should leave browser connections available for search');
  const latest=h.run("loadSparklines(['new'])");
  assert.equal(h.calls[0].signal.aborted,true);
  const first=h.pending.shift(),second=h.pending.shift();second.resolve({});await latest;
  first.resolve({});await old;
  assert.equal(h.calls.length,2,'old chart chunks must not continue after search changes');
}
async function testResearch(){
  const h=harness(),source=read('long-term.js');h.run('let researchSearchSequence=0,researchSearchController=null;');
  h.run(source.slice(source.indexOf('function invalidateResearchSearch'),source.indexOf('for (const [id, event]')));
  h.node('#long-term-query').value='삼';const old=h.run("search('삼')");
  h.node('#long-term-query').value='현';const latest=h.run("search('현')");
  const first=h.pending.shift(),second=h.pending.shift();second.resolve(result('현대차'));await latest;
  first.resolve(result('삼성전자'));await old;
  assert.equal(h.calls[0].signal.aborted,true);assert.ok(h.node('#long-term-search-results').innerHTML.includes('현대차'));
  const editing=h.run("search('현')");h.node('#long-term-query').value='';h.run('invalidateResearchSearch()');
  h.pending.shift().resolve(result('오래된 결과'));await editing;
  assert.equal(h.node('#long-term-search-results').innerHTML,'');
}
async function testNews(){
  const h=harness(),source=read('news.js');
  h.context.URL=URL;h.context.window={location:{href:'http://localhost/news'},history:{replaceState(){}}};
  h.context.clearIssueIndex=()=>{};h.context.renderDigest=data=>h.node('#news-list').innerHTML=data.name;
  h.node('#news-query').setCustomValidity=()=>{};
  h.run('let newsSearchSequence=0,newsSearchController=null;');
  h.run(source.slice(source.indexOf('async function loadNews'),source.indexOf('$("#news-search-form").addEventListener')));
  h.run(source.slice(source.indexOf('$("#news-query").addEventListener'),source.indexOf('document.querySelectorAll("[data-news-query]"')));
  const old=h.run("loadNews('삼성')"),latest=h.run("loadNews('현대')");
  const first=h.pending.shift(),second=h.pending.shift();second.resolve({name:'현대 뉴스'});await latest;
  first.resolve({name:'삼성 뉴스'});await old;
  assert.equal(h.node('#news-list').innerHTML,'현대 뉴스');assert.equal(h.calls[0].signal.aborted,true);
  const editing=h.run("loadNews('현대')");h.node('#news-query').input();
  h.pending.shift().resolve({name:'오래된 뉴스'});await editing;
  assert.ok(!h.node('#news-list').innerHTML.includes('오래된 뉴스'));
}
(async()=>{await testSelection();await testMarket();await testChartCancellation();await testResearch();await testNews();console.log('Search synchronization passed: typing, clear, dismiss, overlapping requests, late success and stale failure.');})().catch(e=>{console.error(e);process.exitCode=1;});
