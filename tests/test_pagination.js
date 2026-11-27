// Pure UI contracts only: no app imports, requests, DB or local configuration.
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const context = { window: {} };
vm.createContext(context);
vm.runInContext(fs.readFileSync('auto_trader/static/pagination.js', 'utf8'), context);
const pager = context.window.FolioPagination;
let checks = 0;
const check = (actual, expected) => { assert.deepEqual(JSON.parse(JSON.stringify(actual)), expected); checks++; };
for (let total = 0; total <= 120; total++) {
  for (let current = 1; current <= Math.max(1, total); current++) {
    const pages = Array.from(pager.numbers(current, total));
    check(pages.length, total <= 1 ? 0 : Math.min(5, total));
    if (total <= 1) continue;
    check(pages.includes(current), true);
    check(pages.every((n, i) => n >= 1 && n <= total && (!i || n === pages[i - 1] + 1)), true);
    const html = pager.paper('records', {page: current, pages: total, total: total * 10}, 'records');
    check((html.match(/<button /g) || []).length, pages.length + 4);
    check((html.match(/aria-current="page"/g) || []).length, 1);
    check((html.match(/ disabled/g) || []).length, current === 1 || current === total ? 2 : 0);
    check(html.includes('…'), false);
  }
}
for (const [current, expected] of [[1,[1,2,3,4,5]], [7,[5,6,7,8,9]], [108,[104,105,106,107,108]]]) check(pager.numbers(current,108),expected);
check(pager.paper('home',{page:1,pages:1,total:5},'home'),'');

// Use the real PAPER slicing/record normalization with synthetic data.
const source = fs.readFileSync('auto_trader/static/paper-mvp.js', 'utf8');
Object.assign(context, { FolioPagination:pager, num:Number, won:String, signedWon:String, codeLabel:(name,symbol)=>name || symbol });
vm.runInContext(source.slice(source.indexOf('const PAGE_SIZE'), source.indexOf('function bindPagers')), context);
vm.runInContext(source.slice(source.indexOf('function records()'),source.indexOf('function renderHistory()')),context);
const run = code => vm.runInContext(code, context);
context.items = Array.from({length:1074},(_,i)=>i);
check(run("slicePage('records',items).pages"),108);
check(run("pageOf.records=7;slicePage('records',items).rows[0]"),60);
check(run("slicePage('records',items).page"),7);
check(run("slicePage('records',items.slice(0,25)).page"),3);
check(run("slicePage('records',[]).page"),1);
check(run("slicePage('home',items).rows.length"),5);
check(run("slicePage('all',items).rows.length"),10);
context.st = {trades:[{at:'2026-11-25T00:00:00Z',status:'FILLED',side:'BUY',symbol:'000001',quantity:1,price:'10',fee:'0'},
  {at:'2026-11-25T00:00:01Z',status:'FILLED',side:'SELL',symbol:'000001',quantity:1,price:'10',fee:'0'},
  {at:'2026-11-25T00:00:02Z',status:'REJECTED',side:'BUY',symbol:'000001',quantity:1,price:'10',fee:'0'}],
  decisions:['CONTROL','CANDIDATE','WATCH','HOLD_DATA','UNKNOWN'].map((kind,i)=>({kind,at:`2026-11-25T00:01:0${i}Z`,title:kind,reason:kind}))};
check(run('records().length'),8);
check(run("records().filter(x=>x.type==='제어').map(x=>x.name)"),['CONTROL']);
check(run("records().filter(x=>x.type==='관망').length"),5);
check(run("records().filter(x=>x.type==='매수').length"),1);
check(run("records().filter(x=>x.type==='매도').length"),1);
console.log(`Pagination contracts: ${checks}/${checks} passed (exhaustive 0–120 pages, 1074 records, refresh/clamp, categories).`);
