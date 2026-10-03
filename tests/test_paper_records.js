const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../auto_trader/static/app.js'), 'utf8');
const nodes = new Map();
const $ = selector => {
  if (!nodes.has(selector)) nodes.set(selector, {innerHTML:'', textContent:'', value:'', disabled:false, dataset:{},
    before(){}, getBoundingClientRect(){return {height:48,top:200};}, closest(){return {getBoundingClientRect(){return {top:200};}};}, addEventListener(event, handler) {this[event] = handler;}});
  return nodes.get(selector);
};
const scope = {document:{querySelector:$,querySelectorAll:()=>[],createElement:()=>({})},window:{scrollY:0,scrollTo(){}}, Intl, Date};
vm.createContext(scope);
const handlersStart = source.indexOf('function changeOrderPage(');
vm.runInContext(source.slice(0, source.indexOf('async function api(')) +
  source.slice(source.indexOf('const escapeHtml='), source.indexOf('function renderStrategyReturn(')) +
  source.slice(handlersStart, source.indexOf("document.querySelectorAll('.paper-engine-action", handlersStart)), scope);
const orders = Array.from({length:45}, (_, i) => ({symbol:`STOCK${i}`, side:i%2?'SELL':'BUY',
  status:i%3?'FILLED':'REJECTED', created_at:'2026-10-01T09:00:00+09:00', quantity:1, price:1000,
  reason:'<script>alert(1)</script>', message:'result', fee:1, tax:2, slippage:3,
  realized_profit:i%2?0:null}));
scope.orders = orders;
const render = () => vm.runInContext('renderOrders(orders)', scope);
const rowCount = () => ($ ('#orders-body').innerHTML.match(/<tr>/g) || []).length;
render();
assert.equal(rowCount(), 20);
assert.equal($('#order-page').textContent, '1 / 3');
assert.equal($('#order-first').disabled, true);
assert.equal($('#order-next').disabled, false);
assert.ok($('#orders-body').innerHTML.includes('&lt;script&gt;'));
$('#order-next').click();
assert.equal($('#order-page').textContent, '2 / 3');
assert.equal(rowCount(), 20);
assert.ok($('#orders-body').innerHTML.includes('STOCK20</small>'));
assert.ok(!$('#orders-body').innerHTML.includes('STOCK19</small>'));
render();
assert.equal($('#order-page').textContent, '2 / 3', 'polling preserves selected page');
$('#order-last').click();
assert.equal(rowCount(), 5);
assert.equal($('#order-next').disabled, true);
$('#order-prev').click();
assert.equal($('#order-page').textContent, '2 / 3');
$('#order-first').click();
assert.equal($('#order-page').textContent, '1 / 3');
$('#order-last').click();
scope.orders = orders.slice(0, 20);render();
assert.equal($('#order-page').textContent, '1 / 1', 'smaller response clamps selected page');
scope.orders = [];render();
assert.equal($('#order-page').textContent, '1 / 1');
for (const id of ['first','prev','next','last']) assert.equal($(`#order-${id}`).disabled, true);
assert.ok($('#orders-body').innerHTML.includes('전체 거래 기록 다운로드'));
scope.orders = orders;render();
$('#order-last').click();
$('#order-side-filter').value = 'SELL';$('#order-side-filter').change();
assert.equal($('#order-page').textContent, '1 / 2');
assert.equal($('#order-count').textContent, '전체 45건 · 조건 일치 22건');
$('#order-last').click();assert.equal(rowCount(), 2);
render();assert.equal($('#order-page').textContent, '2 / 2');
assert.equal($('#order-side-filter').value, 'SELL');
$('#order-status-filter').value = 'REJECTED';$('#order-status-filter').change();
assert.equal($('#order-count').textContent, '전체 45건 · 조건 일치 7건');
$('#order-symbol-filter').value = ' stock3 ';$('#order-symbol-filter').input();
assert.equal($('#order-count').textContent, '전체 45건 · 조건 일치 3건');
assert.ok($('#orders-body').innerHTML.includes('STOCK3</small>'));
assert.ok($('#orders-body').innerHTML.includes('STOCK33</small>'));
$('#order-symbol-filter').value = 'unknown';$('#order-symbol-filter').input();
assert.ok($('#orders-body').innerHTML.includes('조건에 맞는 주문이 없습니다'));
for (const id of ['first','prev','next','last']) assert.equal($(`#order-${id}`).disabled, true);
$('#order-filter-reset').click();
assert.equal($('#order-count').textContent, '전체 45건 · 조건 일치 45건');
assert.equal($('#order-page').textContent, '1 / 3');
vm.runInContext('renderDecisions([], {snapshots:[]})', scope);
assert.ok($('#watchlist-body').innerHTML.includes('전략을 시작하면'));
scope.stocks = [{symbol:'005930',name:'삼성전자'}];
scope.strategy = {snapshots:[{symbol:'005930',price:70000,short_average:0,long_average:0,
  trend:'WAITING',decision:'<pending>'}]};
vm.runInContext('renderDecisions(stocks, strategy)', scope);
assert.ok($('#watchlist-body').innerHTML.includes('삼성전자'));
assert.ok($('#watchlist-body').innerHTML.includes('가격 수집 중'));
assert.ok($('#watchlist-body').innerHTML.includes('&lt;pending&gt;'));
console.log('PAPER records passed: 45 orders, all page controls, polling, shrinking/empty records, escaped reasons, latest decision rendering.');

const picker=$('#paper-strategy-select');
scope.workspace={account_mode:'EXPERIMENT',selected_strategy_id:1};scope.strategy={running:true};
const sync=()=>vm.runInContext('syncStrategyPicker(workspace,strategy)',scope);
sync();assert.equal(picker.value,'1');assert.equal(picker.disabled,true);assert.equal($('#paper-strategy-selection-hint').hidden,false);assert.match($('#paper-strategy-selection-hint').textContent,/일시정지/);
scope.strategy.running=false;sync();assert.equal(picker.disabled,false);assert.equal($('#paper-strategy-selection-hint').hidden,true);
picker.value='2';sync();assert.equal(picker.value,'2','polling preserves a pending choice while stopped');
scope.workspace.account_mode='LIVE_COPY';sync();assert.equal(picker.value,'1','switching accounts restores its applied strategy even when IDs match');
scope.workspace.selected_strategy_id=3;sync();assert.equal(picker.value,'3');
scope.workspace.selected_strategy_id=null;sync();assert.equal(picker.value,'');
const timestamp=vm.runInContext("orderTime('2026-10-01T20:05:03Z')",scope);
assert.ok(timestamp.includes('2026. 10. 02.'),'order date uses Korean time across midnight');
assert.ok(timestamp.includes('05:05:03'));
console.log('Strategy selection, pending choices, account changes and KST order dates passed.');

scope.orders=[{...orders[0],created_at:'2026-10-01T14:59:59Z'},{...orders[1],created_at:'2026-10-01T15:00:00Z'},{...orders[2],created_at:'2026-10-02T14:59:59Z'},{...orders[3],created_at:'2026-10-02T15:00:00Z'}];
render();
$('#order-date-from').value='2026-10-02';$('#order-date-from').change();assert.equal(rowCount(),3);
$('#order-date-to').value='2026-10-02';$('#order-date-to').change();assert.equal(rowCount(),2,'inclusive date boundaries use KST');
render();assert.equal(rowCount(),2,'polling retains date range');
$('#order-side-filter').value='SELL';$('#order-side-filter').change();assert.equal(rowCount(),1,'date and side filters combine');
$('#order-date-from').value='2026-10-03';$('#order-date-from').change();assert.equal(rowCount(),1);assert.match($('#order-count').textContent,/시작일/);
$('#order-filter-reset').click();assert.equal($('#order-date-from').value,'');assert.equal($('#order-date-to').value,'');assert.equal(rowCount(),4);
$('#order-date-to').value='2026-10-01';$('#order-date-to').change();assert.equal(rowCount(),1,'end-only date filter');
console.log('KST date boundaries, combined filters, invalid ranges and reset passed.');

vm.runInContext("orderStockNames=new Map([['034020','두산에너빌리티']]);currentOrderStrategy={runId:'new-run',name:'현재 전략',symbols:['034020']}",scope);
scope.orders=[{...orders[0],symbol:'034020',run_id:'old-run',source:'STRATEGY',side:'BUY',reason:'상향 교차 후 2봉 확인 · 가격 추세 확인'},{...orders[1],symbol:'034020',run_id:'old-run',source:'STRATEGY',reason:'이전 전략 · 이름에 구분자 · 이동평균 하향 교차'}];
$('#order-filter-reset').click();render();
assert.match($('#orders-body').innerHTML,/두산에너빌리티/);
assert.equal(($('#orders-body').innerHTML.match(/title="이전 전략 · 이름에 구분자"/g)||[]).length,2,'historical buy and sell share their recorded strategy, not the current strategy');
assert.ok(!$('#orders-body').innerHTML.includes('이름에 구분자 · 이동평균'),'strategy name is removed from the reason cell');
$('#order-symbol-filter').value='두산';$('#order-symbol-filter').input();assert.equal(rowCount(),2);
console.log('Stock-name search and historical strategy columns passed.');

vm.runInContext("strategies=[{id:1,cooldown_minutes:30}]",scope);
scope.decisionArgs={item:{symbol:'034020',trend:'BELOW',decision:'다음 완료 1분봉 대기'},strategy:{run_id:'r',running:true},workspace:{selected_strategy_id:1,managed_holdings:[]},risk:{new_buys_allowed:false,block_reason:'현금 비율 한도'},orders:[{symbol:'034020',run_id:'r',source:'STRATEGY',status:'FILLED',created_at:'2026-10-02T09:54:00+09:00'}]};
const context=()=>vm.runInContext("decisionContext(decisionArgs.item,decisionArgs.strategy,decisionArgs.workspace,decisionArgs.risk,decisionArgs.orders,Date.parse('2026-10-02T10:10:00+09:00'))",scope);
let details=context();assert.ok(details.entry_constraints[0].includes('14분'));assert.ok(details.entry_constraints[1].includes('현금 비율'));assert.ok(details.next_check.includes('완료 1분봉'));
scope.decisionArgs.workspace.managed_holdings=[{symbol:'034020',quantity:3}];details=context();assert.equal(details.entry_constraints.length,0);assert.ok(details.next_check.includes('청산'));
scope.decisionArgs.strategy.running=false;details=context();assert.ok(details.next_check.includes('시작'));
console.log('Entry cooldown, risk restrictions, held positions and stopped-state guidance passed.');

scope.comparisonWorkspace={account_mode:'EXPERIMENT',account_summaries:[{account_mode:'LIVE_COPY',loaded:true,selected:false,running:true,strategy_name:'<old strategy>',position_count:3,total_profit:1200,strategy_profit:600,tick_count:10},{account_mode:'EXPERIMENT',loaded:true,selected:true,running:false,position_count:0,total_profit:-50,strategy_profit:-20,tick_count:4}]};
vm.runInContext('renderAccountComparison(comparisonWorkspace,{})',scope);
assert.match($('#paper-account-comparison').innerHTML,/&lt;old strategy&gt;/);
assert.match($('#paper-account-comparison').innerHTML,/3개/);
assert.match($('#paper-account-control-notice').textContent,/실험계좌에 적용/);
scope.comparisonWorkspace.account_summaries[0]={account_mode:'LIVE_COPY',loaded:false};
vm.runInContext('renderAccountComparison(comparisonWorkspace,{})',scope);
assert.match($('#paper-account-comparison').innerHTML,/조회 정보 없음/,'unknown account metrics must not appear as zero');
console.log('Account comparison, control scope and unknown metrics passed.');
scope.document.querySelectorAll=()=>[{closest:()=>({dataset:{accountMode:'EXPERIMENT'}})}];
vm.runInContext('renderAccountComparison(comparisonWorkspace,{})',scope);
assert.match($('#paper-account-comparison').innerHTML,/data-account-mode="EXPERIMENT"[^]*?<details class="paper-account-metrics" open>/,'polling preserves expanded account details');
scope.document.querySelectorAll=()=>[];

// The setup guide follows actual account/engine state, including an unapplied choice.
scope.setupWorkspace={account_mode:'EXPERIMENT',snapshot_ready:false,selected_strategy_id:null};
scope.setupStrategy={running:false};
vm.runInContext('strategies=[]',scope);
const guide=()=>vm.runInContext('renderControlGuide(setupWorkspace,setupStrategy)',scope);
guide();assert.match($('#paper-next-step').textContent,/1단계/);assert.equal($('#start-button').disabled,true);
scope.setupWorkspace.snapshot_ready=true;guide();assert.match($('#paper-next-step').textContent,/전략을 만들어/);
vm.runInContext('strategies=[{id:1},{id:2}]',scope);
picker.value='';guide();assert.match($('#paper-next-step').textContent,/전략을 고르/);
picker.value='1';guide();assert.match($('#paper-next-step').textContent,/적용하고/);assert.equal($('#start-button').disabled,true);
scope.setupWorkspace.selected_strategy_id=1;guide();assert.match($('#paper-next-step').textContent,/준비가 끝/);assert.equal($('#start-button').disabled,false);assert.equal($('#stop-button').disabled,true);
picker.value='2';guide();assert.match($('#paper-execution-guide').textContent,/기다려/);assert.equal(picker.value,'2');assert.equal($('#start-button').disabled,true,'an unapplied draft cannot start the previous strategy');
scope.setupStrategy.running=true;guide();assert.match($('#paper-next-step').textContent,/주문 기록/);assert.equal($('#start-button').disabled,true);assert.equal($('#stop-button').disabled,false);
scope.setupStrategy={running:false,emergency_stopped:true};picker.value='1';guide();assert.match($('#paper-execution-guide').textContent,/긴급 중지/);assert.match($('#paper-snapshot-help').textContent,/기존 모의계좌/);
console.log('Setup guide passed: unprepared account, no strategies, unapplied choice, ready, running and emergency stop.');

// Choosing a strategy saves immediately, blocks starting during save, and restores selection on failure.
vm.runInContext(source.slice(source.indexOf("$('#paper-account-comparison').addEventListener"),source.indexOf("$('#start-button').addEventListener")),scope);
(async()=>{
 scope.setupWorkspace.selected_strategy_id=1;scope.setupStrategy={running:false};picker.value='2';guide();
 let finish;const saved=new Promise(resolve=>finish=resolve);let calls=0;
 scope.api=async(path,options)=>{calls++;assert.equal(path,'/paper/strategies/2/select');assert.equal(options.method,'POST');await saved;};
 scope.refresh=async()=>{};
 const changing=picker.change();assert.equal(picker.disabled,true);assert.equal($('#start-button').disabled,true);
 await picker.change();assert.equal(calls,1,'duplicate changes do not send another selection');
 finish();await changing;assert.match($('#paper-action-message').textContent,/선택한 전략을 적용/);
 picker.value='2';scope.api=async()=>{throw new Error('저장 실패');};await picker.change();
 assert.equal(picker.value,'1');assert.match($('#paper-action-message').textContent,/변경하지 못/);
 const details=[{open:false},{open:false}];scope.document.querySelectorAll=()=>details;
 const summary={closest:()=>details[0]};let prevented=false;
 $('#paper-account-comparison').click({target:{closest:selector=>selector.includes('summary')?summary:null},preventDefault(){prevented=true;}});
 assert.equal(prevented,true);assert.equal(details[0].open,true);assert.equal(details[1].open,false);
 $('#paper-account-comparison').click({target:{closest:selector=>selector.includes('summary')?summary:null},preventDefault(){}});
 assert.ok(details.every(item=>!item.open));
 console.log('Immediate selection passed: save, duplicate guard, busy start, failure restore; selected account details expand/collapse.');
})().catch(error=>{console.error(error);process.exitCode=1;});
