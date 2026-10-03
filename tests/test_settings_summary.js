const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
let payload={symbols:['005930','000660'],symbol:'005930',sizing_mode:'AMOUNT',order_amount:1000000,
  order_quantity:1,execution_mode:'DRY_RUN',short_period:10,long_period:40,take_profit_rate:4,
  stop_loss_rate:2,max_holding_days:3,trading_start:'08:00',trading_end:'20:00',cooldown_minutes:10,daily_order_limit:0};
const nodes=new Map();
const node=selector=>{if(!nodes.has(selector))nodes.set(selector,{textContent:'',hidden:false,
  addEventListener(event,handler){this[event]=handler;},setCustomValidity(value){this.validityMessage=value;},setAttribute(){}});return nodes.get(selector);};
let targetsChanged;
const context={document:{querySelector:node},strategyPayload:()=>payload,
  MutationObserver:class {constructor(handler){targetsChanged=handler;}observe(){}},Number};
vm.createContext(context);
vm.runInContext(fs.readFileSync('auto_trader/static/settings-summary.js','utf8'),context);
assert.ok(node('#strategy-input-summary').textContent.includes('2종목'));
assert.ok(node('#strategy-input-summary').textContent.includes('1,000,000원'));
assert.ok(node('#strategy-input-summary').textContent.includes('무제한'));
payload={...payload,long_period:5};node('#strategy-form').input();
assert.ok(node('#strategy-long').validityMessage.includes('커야'));
assert.equal(node('#strategy-summary-error').hidden,false);
payload={...payload,long_period:40,trading_end:'07:00'};node('#strategy-form').change();
assert.equal(node('#strategy-long').validityMessage,'');
assert.ok(node('[data-time-picker="strategy-end"] [data-time-hour]').validityMessage.includes('늦어야'));
payload={...payload,trading_end:'20:00',sizing_mode:'QUANTITY',order_quantity:3,symbols:[],symbol:'',daily_order_limit:6};targetsChanged();
assert.equal(node('#strategy-summary-error').hidden,true);
assert.ok(node('#strategy-input-summary').textContent.includes('0종목'));
assert.ok(node('#strategy-input-summary').textContent.includes('1회 3주'));
assert.ok(node('#strategy-input-summary').textContent.includes('6회'));
console.log('Strategy summary: budget/quantity, unlimited cap, cross-field validity and programmatic reset passed.');
