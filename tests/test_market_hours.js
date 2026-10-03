const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor() { this.dataset = {}; this.children = []; this.textContent = ''; }
  setAttribute() {}
  append(child) { this.children.push(child); }
  replaceChildren(...children) { this.children = children; this.textContent = ''; }
  querySelectorAll() { return this.children; }
  text() { return this.textContent + this.children.map(child => child.text()).join(' '); }
}

(async () => {
  const heading = new Element();
  let refresh, fail = false;
  let payload = {
    KR: {state: 'closed', holiday: true, next_open_at: '2026-10-06T09:00:00+09:00'},
    US: {state: 'open', holiday: true, closes_at: '2026-10-03T05:00:00+09:00'},
  };
  const context = {
    document: {hidden: false, querySelector: () => heading,
      createElement: () => new Element(), addEventListener() {}},
    fetch: async () => ({ok: !fail, json: async () => payload}),
    setInterval: callback => { refresh = callback; }, Date, Number,
  };
  vm.runInNewContext(fs.readFileSync('auto_trader/static/market-hours.js', 'utf8'), context);
  await new Promise(resolve => setImmediate(resolve));
  const [kr, us] = heading.children[0].children;
  assert.match(kr.text(), /국내 장 휴장.*다음 개장/);
  assert.match(kr.text(), /09:00/);
  assert.match(us.text(), /해외 장 열림.*종료/);
  assert.match(us.title, /한국시간/);
  fail = true;
  await refresh();
  assert.equal(kr.dataset.state, 'unknown');
  assert.match(kr.text(), /확인 불가/);
  assert.doesNotMatch(kr.text(), /다음 개장/);
  fail = false;
  payload = {KR: {state: 'closed', holiday: false}, US: {state: 'unknown'}};
  await refresh();
  assert.match(kr.text(), /국내 장 닫힘/);
  assert.match(us.text(), /해외 장 확인 불가/);
  console.log('market hours display: passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
