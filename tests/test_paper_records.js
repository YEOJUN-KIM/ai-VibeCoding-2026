const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../auto_trader/static/app.js'), 'utf8');
const nodes = new Map();
const $ = selector => {
  if (!nodes.has(selector)) nodes.set(selector, {innerHTML:'', textContent:'', value:'', disabled:false,
    addEventListener(event, handler) {this[event] = handler;}});
  return nodes.get(selector);
};
const scope = {document:{querySelector:$}, Intl, Date};
vm.createContext(scope);
const handlersStart = source.indexOf("$('#order-first').addEventListener");
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
assert.ok($('#orders-body').innerHTML.includes('STOCK20</td>'));
assert.ok(!$('#orders-body').innerHTML.includes('STOCK19</td>'));
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
assert.ok($('#orders-body').innerHTML.includes('STOCK3</td>'));
assert.ok($('#orders-body').innerHTML.includes('STOCK33</td>'));
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
