const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('auto_trader/static/app.js','utf8');
const nodes=new Map();
const node=selector=>{if(!nodes.has(selector))nodes.set(selector,{value:'',textContent:'',innerHTML:'',disabled:false});return nodes.get(selector);};
const context={$:node,document:{querySelectorAll:()=>[]},number:new Intl.NumberFormat('ko-KR'),won:new Intl.NumberFormat('ko-KR'),
  orderStrategyNames:()=>new Map(),orderTime:value=>value,orderStockCell:item=>item.symbol,
  orderStrategyCell:()=>'',orderStrategyDetails:()=>({reason:'sample'}),escapeHtml:String,
  orderDay:value=>value.slice(0,10)};
vm.createContext(context);
vm.runInContext('let orderPage=1,orderTotalPages=1,orderPageSize=20,latestOrders=[];const orderStockNames=new Map();',context);
vm.runInContext(source.slice(source.indexOf('function renderOrders('),source.indexOf('function decisionDisplay(')),context);
context.orders=Array.from({length:101},(_,i)=>({symbol:String(i),stock_name:`종목 ${i}`,side:'BUY',status:'FILLED',created_at:'2026-10-03',quantity:1,price:100}));
const rowCount=()=>nodes.get('#orders-body').innerHTML.split('<tr>').length-1;
for(const [size,pages] of [[20,6],[50,3],[100,2]]){
  vm.runInContext(`orderPage=1;orderPageSize=${size};renderOrders(orders);`,context);
  assert.equal(rowCount(),size);
  assert.equal(nodes.get('#order-page').textContent,`1 / ${pages}`);
  vm.runInContext(`orderPage=${pages};renderOrders(orders);`,context);
  assert.equal(rowCount(),1);
  assert.equal(nodes.get('#order-next').disabled,true);
}
node('#order-symbol-filter').value='종목 100';
vm.runInContext('orderPage=6;renderOrders(orders);',context);
assert.equal(nodes.get('#order-page').textContent,'1 / 1');
assert.equal(rowCount(),1);
console.log('Order pagination: 20/50/100 rows, final-page remainder and filtering page clamp passed.');

node('#order-symbol-filter').value='';
context.orders=[{id:1,symbol:'005930',side:'SELL',status:'FILLED',created_at:'2026-10-03',quantity:2,price:1000,realized_profit:120,fee:3,tax:4,slippage:5}];
vm.runInContext('renderOrders(orders)',context);
assert.match(node('#orders-body').innerHTML,/2,000/);
assert.match(node('#orders-body').innerHTML,/class="buy">\+120/);
assert.match(node('#orders-body').innerHTML,/수수료.*3/);
assert.match(node('#orders-body').innerHTML,/세금.*4/);
assert.match(node('#orders-body').innerHTML,/체결가에 이미 반영/);
context.orders[0].realized_profit=-120;
vm.runInContext('renderOrders(orders)',context);
assert.match(node('#orders-body').innerHTML,/class="sell">-120/);
context.document.querySelectorAll=()=>[{dataset:{orderId:'1'}}];
context.orders[0].realized_profit=0;
vm.runInContext('renderOrders(orders)',context);
assert.match(node('#orders-body').innerHTML,/data-order-id="1" open/);
assert.match(node('#orders-body').innerHTML,/class="">0<\/td>/);
context.orders[0].status='REJECTED';
vm.runInContext('renderOrders(orders)',context);
assert.doesNotMatch(node('#orders-body').innerHTML,/수수료|2,000/);
assert.match(node('#orders-body').innerHTML,/<td>—<\/td><td>—<\/td>/);
assert.match(node('#orders-body').innerHTML,/거절 이유/);
console.log('Execution amounts, signed net profits, costs, rejected orders and expanded details passed.');
