const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('auto_trader/static/app.js', 'utf8');
const output = {innerHTML:''};
const context = {document:{querySelector:()=>output}, Intl};
vm.createContext(context);
vm.runInContext(source.slice(0,source.indexOf('async function api(')) +
  source.slice(source.indexOf('const escapeHtml='),source.indexOf('const stockCell=')) +
  source.slice(source.indexOf('function renderRisk(risk){'),source.indexOf('async function refresh()')), context);
const settings = {preset:'DEFAULT',max_order_amount:'1000000',max_symbol_amount:'2000000',
  max_total_investment:'7000000',min_cash_ratio:'30',daily_loss_limit:'500000',daily_order_limit:30,profit_target:'1000000'};
const status = {settings,invested_amount:'2400000',cash_ratio:'76',daily_profit:'-1406',daily_orders:2,
  new_buys_allowed:true,block_reason:null,effective_min_cash_ratio:'30',daily_order_limit_disabled:false};
function render(changes={}) {context.risk={...status,...changes};vm.runInContext('renderRisk(risk)',context);return output.innerHTML;}
let html=render();
assert.ok(html.includes('공통 한도 미도달'));
assert.ok(html.includes('금액·현금·전략 조건은 주문별로 추가 확인'));
assert.ok(html.includes('76.0%'));
assert.ok(html.includes('하루 30회 한도'));
html=render({effective_min_cash_ratio:'0',daily_order_limit_disabled:true});
assert.ok(html.includes('현재 최소 비율 제한 미적용 · 저장값 30%'));
assert.ok(html.includes('현재 횟수 제한 미적용 · 저장값 30회'));
assert.ok(!html.includes('하루 30회 한도'));
html=render({settings:{...settings,daily_order_limit:0},daily_order_limit_disabled:false});
assert.ok(html.includes('주문 횟수 제한 없음'));
html=render({daily_profit:'-700000',new_buys_allowed:false,block_reason:'<b>일일 손실 한도</b>'});
assert.ok(html.includes('신규 매수 제한'));
assert.ok(html.includes('&lt;b&gt;일일 손실 한도&lt;/b&gt;'));
assert.ok(!html.includes('<b>일일 손실 한도</b>'));
assert.ok(![...html.matchAll(/<progress[^>]+value="([^"]+)"/g)].some(match=>Number(match[1])>100));
html=render({daily_profit:'5000'});
assert.ok(html.includes('오늘 손실 사용량</span><strong>₩0'));
console.log('PAPER limits passed: effective overrides, unlimited orders, blocking reasons, escaped output, bounded usage and positive P&L.');
