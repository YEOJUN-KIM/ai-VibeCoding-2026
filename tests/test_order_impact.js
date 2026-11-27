// PAPER-SIMPLIFY-02: the manual-order preview shows a neutral before/after view and no investment-policy warning flow.
const fs = require('fs'), vm = require('vm'), assert = require('node:assert/strict');
const source = fs.readFileSync('auto_trader/static/stock-detail.js', 'utf8');
const start = source.indexOf('function impactNumber'), end = source.indexOf('$("#live-order-preview-button")');
assert.ok(start > 0 && end > start, 'impact helpers are present');
const el = () => ({ children: [], className: '', textContent: '', append(...c) { this.children.push(...c); } });
const scope = {
  won: new Intl.NumberFormat('ko-KR', { style: 'currency', currency: 'KRW', maximumFractionDigits: 0 }),
  integer: new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 }),
  document: { createElement: el },
};
vm.createContext(scope);
vm.runInContext(source.slice(start, end), scope);

const items = [
  { label: '원화 주문 가능 금액', before: '1000', after: '500', unit: '원', note: null },
  { label: '보유 수량', before: '0', after: '5', unit: '주', note: null },
  { label: '종목 비중(주문가 기준 추정)', before: '0', after: '50', unit: '%', note: null },
  { label: '예상 매도 손익(평균단가 기준, 수수료·세금 제외)', before: null, after: '80', unit: '원', note: null },
  { label: '수수료·세금', before: null, after: null, unit: '원', note: '주문 전에는 확정할 수 없습니다. 체결 후 토스증권 내역에서 확인하세요.' },
];
const text = items.map(scope.impactText);
assert.match(text[0], /1,000.*→.*500/);
assert.equal(text[1], '0주 → 5주');
assert.equal(text[2], '0.0% → 50.0%');
assert.match(text[3], /80/);
assert.ok(!text[3].includes('→'), 'a value without a known "before" is shown alone');
assert.equal(text[4], items[4].note, 'an unknown value is never turned into a number, only the note is shown');

const list = scope.impactList(items);
assert.equal(list.children.length, items.length * 2);
assert.equal(list.children[0].textContent, '원화 주문 가능 금액');
assert.equal(scope.impactList(undefined).children.length, 0, 'older servers without impact still render');

for (const gone of ['accept_financial_warnings', '한도 경고', 'check.warning', '투자 한도를 초과해도']) {
  assert.ok(!source.includes(gone), gone + ' must not remain in the order screen');
}
console.log('Order impact view passed: neutral before/after, no estimates for unknown values, no policy-warning flow.');
