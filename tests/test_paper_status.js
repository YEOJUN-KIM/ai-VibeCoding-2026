const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('auto_trader/static/app.js', 'utf8');
const context = {};
vm.createContext(context);
const guideStart = source.indexOf('function paperStatusGuide(');
const summaryStart = source.indexOf('function renderDataSummary(');
const returnStart = source.indexOf('function renderStrategyReturn(');
// slice(-1, ...) would silently run the wrong code, so fail loudly when a boundary function moves or is renamed.
assert.notEqual(guideStart, -1, 'app.js: function paperStatusGuide( not found');
assert.notEqual(summaryStart, -1, 'app.js: function renderDataSummary( not found');
assert.notEqual(returnStart, -1, 'app.js: function renderStrategyReturn( not found');
assert.ok(guideStart < summaryStart && summaryStart < returnStart, 'app.js: paperStatusGuide < renderDataSummary < renderStrategyReturn order changed');
vm.runInContext(source.slice(guideStart, summaryStart), context);
const guide = (strategy, workspace={}, risk={}) => context.paperStatusGuide(strategy, workspace, risk);
assert.equal(guide({running:false,data_message:'운영 시간 외 대기'}).label,'전략 정지');
assert.ok(guide({running:false},{selected_strategy_id:17,snapshot_ready:true}).next.includes('전략 시작'));
assert.equal(guide({running:false,emergency_stopped:true}).label,'긴급 정지');
assert.equal(guide({running:true,data_message:'운영 시간 외 대기'}).label,'운영 시간 대기');
assert.equal(guide({running:true,data_message:'시세 오류·지연 또는 휴장'}).label,'시세 대기');
assert.equal(guide({running:true,data_message:'0/10종목 판단 가능 · 대기'}).label,'시세 대기');
assert.equal(guide({running:true,data_message:'7/10종목 판단 가능 · 대기'}).label,'일부 종목 시세 대기');
const blocked=guide({running:true,data_message:'실제 현재가 · 완료된 1분봉 · 모의 체결'}, {}, {new_buys_allowed:false,block_reason:'일일 손실 한도'});
assert.equal(blocked.reason,'일일 손실 한도');
assert.ok(blocked.next.includes('청산 감시'));
assert.equal(guide({running:true,data_message:'실제 현재가 · 완료된 1분봉 · 모의 체결'}).label,'정상 감시 중');
assert.equal(guide({running:true,operating_state:'WEEKEND'}).label,'주말 대기');
assert.equal(guide({running:false,operating_state:'WEEKEND'}).label,'전략 정지');
assert.equal(guide({running:true,operating_state:'OUTSIDE_HOURS'}).label,'운영 시간 대기');
assert.equal(guide({running:true,operating_state:'HOLIDAY'}).label,'휴장일 대기');
assert.equal(guide({running:true,operating_state:'MARKET_CLOSED'}).label,'거래 시간 대기');
assert.equal(guide({running:true,operating_state:'MARKET_UNKNOWN'}).label,'거래 시간 확인 대기');
assert.ok(guide({running:true,operating_state:'HOLIDAY',market_next_open_at:'2026-10-05T08:00:00+09:00'}).next.includes('설정 운영 시간'));
assert.equal(guide({running:false,operating_state:'MARKET_UNKNOWN'}).label,'전략 정지');
assert.equal(guide({running:true,data_message:'API 요청 한도 대기: 다음 조회'}).label,'API 요청 한도 대기');
assert.equal(guide({running:true,last_data_at:'2026-10-03T10:00:00+09:00',checked_at:'2026-10-03T10:02:00+09:00'}).label,'시세 지연');
assert.equal(guide({running:true,operating_state:'WEEKEND',last_data_at:'2026-10-03T10:00:00+09:00',checked_at:'2026-10-03T10:02:00+09:00'}).label,'주말 대기');
assert.ok(guide({running:true}).next.includes('추가 확인'));
console.log('PAPER status: stop precedence, preparation, hours, API wait, partial feed and buy-only limits passed.');
const nodes=new Map();
context.$=selector=>{if(!nodes.has(selector))nodes.set(selector,{textContent:'',innerHTML:'',dataset:{},querySelector(){return null;}});return nodes.get(selector);};
context.escapeHtml=value=>String(value);
context.recentSnapshots=()=>[];
vm.runInContext(source.slice(summaryStart,returnStart),context);
context.renderDataSummary({running:false,last_data_at:'2026-10-03T01:02:03Z',last_decision_at:'2026-10-03T01:01:00Z'});
assert.ok(nodes.get('#paper-last-data').textContent.includes('10:02:03'));
assert.ok(nodes.get('#paper-last-decision').textContent.includes('10:01:00'));
assert.equal(nodes.get('#strategy-pill').textContent,'전략 정지');
context.renderDataSummary({running:true,operating_state:'WEEKEND'});
assert.equal(nodes.get('#paper-last-data').textContent,'수신 전');
assert.equal(nodes.get('#paper-last-decision').textContent,'확인 전');
assert.equal(nodes.get('#paper-operation-label').textContent,'주말 대기');
console.log('PAPER status rendering: Korean time, stop label, weekend and account/strategy timestamp reset passed.');

// ---- PAPER-01 regression scenarios (synthetic data only; pins current labels and precedence) ----
const T0 = '2026-10-03T10:00:00+09:00';
const STALE = {last_data_at: T0, checked_at: '2026-10-03T10:02:00+09:00'};
const OK_MSG = '실제 현재가 · 완료된 1분봉 · 모의 체결';
const API_MSG = 'API 요청 한도 대기: 다음 조회';
const NONE_READY = '0/22종목 판단 가능 · 대기';
const SOME_READY = '7/22종목 판단 가능 · 대기';
const ALL_READY = '22/22종목 판단 가능';
const BLOCK = {new_buys_allowed: false, block_reason: '일일 손실 한도'};
const FIELDS = ['label', 'reason', 'next'];
const WAITING_LABELS = ['휴장일 대기', '거래 시간 대기', '주말 대기', '운영 시간 대기', '거래 시간 확인 대기', '일부 종목 시세 대기', '시세 대기', 'API 요청 한도 대기'];
const assertComplete = (result, name) => {
  for (const field of FIELDS) {
    assert.equal(typeof result[field], 'string', `${name}: ${field} must be a string`);
    assert.ok(result[field].trim().length > 0, `${name}: ${field} must not be empty`);
    assert.doesNotMatch(result[field], /undefined|NaN|\[object|null/, `${name}: ${field} leaked a raw value`);
    assert.doesNotMatch(result[field], /수익\s*보장|반드시 (매수|체결)/, `${name}: ${field} must not imply guaranteed profit or orders`);
  }
};
const scenarios = [
  // [name, strategy, workspace, risk, expected label]
  ['stopped beats no-feed message', {running: false, data_message: NONE_READY}, {}, {}, '전략 정지'],
  ['stopped beats HOLIDAY', {running: false, operating_state: 'HOLIDAY'}, {}, {}, '전략 정지'],
  ['stopped beats MARKET_CLOSED', {running: false, operating_state: 'MARKET_CLOSED'}, {}, {}, '전략 정지'],
  ['stopped beats OUTSIDE_HOURS', {running: false, operating_state: 'OUTSIDE_HOURS'}, {}, {}, '전략 정지'],
  ['stopped beats API limit', {running: false, data_message: API_MSG}, {}, {}, '전략 정지'],
  ['stopped beats stale feed', {running: false, ...STALE}, {}, {}, '전략 정지'],
  ['stopped beats buy limit', {running: false, data_message: OK_MSG}, {}, BLOCK, '전략 정지'],
  ['emergency stop beats HOLIDAY', {running: false, emergency_stopped: true, operating_state: 'HOLIDAY'}, {}, {}, '긴급 정지'],
  ['emergency stop beats buy limit', {running: false, emergency_stopped: true}, {}, BLOCK, '긴급 정지'],
  ['HOLIDAY beats no-feed, stale feed and buy limit', {running: true, operating_state: 'HOLIDAY', data_message: NONE_READY, ...STALE}, {}, BLOCK, '휴장일 대기'],
  ['MARKET_CLOSED beats API limit', {running: true, operating_state: 'MARKET_CLOSED', data_message: API_MSG}, {}, {}, '거래 시간 대기'],
  ['MARKET_UNKNOWN beats partial feed and buy limit', {running: true, operating_state: 'MARKET_UNKNOWN', data_message: SOME_READY}, {}, BLOCK, '거래 시간 확인 대기'],
  ['WEEKEND beats API limit', {running: true, operating_state: 'WEEKEND', data_message: API_MSG}, {}, {}, '주말 대기'],
  ['OUTSIDE_HOURS beats no-feed and stale feed', {running: true, operating_state: 'OUTSIDE_HOURS', data_message: NONE_READY, ...STALE}, {}, {}, '운영 시간 대기'],
  ['OPEN falls through to partial feed', {running: true, operating_state: 'OPEN', data_message: SOME_READY}, {}, {}, '일부 종목 시세 대기'],
  ['OPEN falls through to normal', {running: true, operating_state: 'OPEN', data_message: OK_MSG}, {}, {}, '정상 감시 중'],
  ['outside-hours message beats stale feed and buy limit', {running: true, data_message: '운영 시간 외 대기', ...STALE}, {}, BLOCK, '운영 시간 대기'],
  ['engine outside-hours message', {running: true, data_message: '운영 시간 외 대기 · 실제 시세 기반 모의매매'}, {}, {}, '운영 시간 대기'],
  ['API limit beats stale feed and buy limit', {running: true, data_message: API_MSG, ...STALE}, {}, BLOCK, 'API 요청 한도 대기'],
  ['stale feed beats 0/22', {running: true, data_message: NONE_READY, ...STALE}, {}, {}, '시세 지연'],
  ['stale feed beats 7/22', {running: true, data_message: SOME_READY, ...STALE}, {}, {}, '시세 지연'],
  ['stale feed beats buy limit', {running: true, data_message: OK_MSG, ...STALE}, {}, BLOCK, '시세 지연'],
  ['0/22 beats buy limit', {running: true, data_message: NONE_READY}, {}, BLOCK, '시세 대기'],
  ['feed error message beats buy limit', {running: true, data_message: '시세 오류·지연 또는 휴장'}, {}, BLOCK, '시세 대기'],
  ['buy limit beats 7/22', {running: true, data_message: SOME_READY}, {}, BLOCK, '신규 매수 제한'],
  ['buy limit with normal feed', {running: true, data_message: OK_MSG}, {}, BLOCK, '신규 매수 제한'],
  ['buy limit with 22/22', {running: true, data_message: ALL_READY}, {}, BLOCK, '신규 매수 제한'],
  ['buy limit without reason', {running: true, data_message: OK_MSG}, {}, {new_buys_allowed: false}, '신규 매수 제한'],
  ['7/22 with buys allowed', {running: true, data_message: SOME_READY}, {}, {new_buys_allowed: true}, '일부 종목 시세 대기'],
  ['7/22 with empty risk', {running: true, data_message: SOME_READY}, {}, {}, '일부 종목 시세 대기'],
  ['21/22 partial', {running: true, data_message: '21/22종목 판단 가능 · 대기'}, {}, {}, '일부 종목 시세 대기'],
  ['22/22 is normal', {running: true, data_message: ALL_READY}, {}, {}, '정상 감시 중'],
  ['normal with buys allowed', {running: true, data_message: OK_MSG}, {}, {new_buys_allowed: true}, '정상 감시 중'],
  ['stale gap equal to 90s is not stale', {running: true, last_data_at: T0, checked_at: '2026-10-03T10:01:30+09:00'}, {}, {}, '정상 감시 중'],
  ['stale gap of 91s is stale', {running: true, last_data_at: T0, checked_at: '2026-10-03T10:01:31+09:00'}, {}, {}, '시세 지연'],
  ['60s interval widens stale threshold to 180s', {running: true, interval_seconds: 60, ...STALE}, {}, {}, '정상 감시 중'],
  ['40s interval keeps 120s gap not stale', {running: true, interval_seconds: 40, ...STALE}, {}, {}, '정상 감시 중'],
  ['40s interval 121s gap is stale', {running: true, interval_seconds: 40, last_data_at: T0, checked_at: '2026-10-03T10:02:01+09:00'}, {}, {}, '시세 지연'],
  ['stale check needs checked_at', {running: true, last_data_at: T0}, {}, {}, '정상 감시 중'],
  ['stale check needs last_data_at', {running: true, checked_at: STALE.checked_at}, {}, {}, '정상 감시 중'],
];
for (const [name, strategy, workspace, risk, expected] of scenarios) {
  const result = guide(strategy, workspace, risk);
  assert.equal(result.label, expected, `${name}: expected label ${expected}, got ${result.label}`);
  assertComplete(result, name);
  if (WAITING_LABELS.includes(result.label)) assert.doesNotMatch(result.label, /오류|실패|장애/, `${name}: waiting label must not read as an error`);
}
console.log('PAPER status regression: label precedence matrix, stale-feed thresholds and non-empty reason/next passed.');

// Reasons must carry the meaning, not an exact sentence (wording is owned by the UI change).
// 0/N: every one of the N symbols is waiting for a quote. Must not echo "all ready" and must not read as an error.
for (const [message, total] of [[NONE_READY, '22'], ['0/10종목 판단 가능 · 대기', '10']]) {
  const noneReason = guide({running: true, data_message: message}).reason;
  assert.ok(noneReason.includes(total), `0/${total}: reason must mention the total symbol count`);
  assert.match(noneReason, /대기|기다/, `0/${total}: reason must say the symbols are waiting`);
  assert.doesNotMatch(noneReason, /오류|실패|장애/, `0/${total}: waiting must not read as an error`);
}
// 7/22: both the total and the ready count are visible.
const someReason = guide({running: true, data_message: SOME_READY}).reason;
assert.ok(someReason.includes('22') && someReason.includes('7'), '7/22: reason must mention total and ready counts');
assert.equal(guide({running: true, data_message: SOME_READY}, {}, BLOCK).reason, '일일 손실 한도');
// Three stopped states must tell the user three different next steps.
const nextNoStrategy = guide({running: false}, {}).next;
const nextNoSnapshot = guide({running: false}, {selected_strategy_id: 17}).next;
const nextReady = guide({running: false}, {selected_strategy_id: 17, snapshot_ready: true}).next;
assert.equal(new Set([nextNoStrategy, nextNoSnapshot, nextReady]).size, 3);
assert.equal(guide({running: false, emergency_stopped: true}, {selected_strategy_id: 17, snapshot_ready: true}).label, '긴급 정지');
// RISK (not an endorsed behavior): running:true with emergency_stopped:true. The engine clears emergency_stopped on start()
// and sets running=false on stop(), so the combination should not be produced, but paperStatusGuide() has no branch for it.
// Only the shape is pinned here; the label is deliberately NOT asserted. See the task record.
assertComplete(guide({running: true, emergency_stopped: true, data_message: OK_MSG}), 'running+emergency_stopped');
console.log('PAPER status reasons: data-carrying reasons, distinct stopped guidance and emergency conflict shape passed.');

// ---- renderDataSummary with waiting rows (stubs added only as far as needed) ----
context.recentSnapshots = strategy => strategy.snapshots || [];
context.decisionDisplay = item => `[${item.symbol}|${item.decision}]`;
const waitingSnapshots = count => Array.from({length: count}, (_, index) => ({symbol: `S${String(index).padStart(3, '0')}`, name: `종목${index}`, decision: '시세 대기 · 최근 거래량 없음', data_at: `2026-10-03T01:${String(index % 60).padStart(2, '0')}:00Z`}));
const rowCount = html => (html.match(/class="paper-data-row"/g) || []).length;
const renderWith = strategy => {
  nodes.clear();
  context.renderDataSummary(strategy, {}, {});
  return nodes.get('#strategy-data-status');
};
let status = renderWith({running: true, data_message: NONE_READY, snapshots: waitingSnapshots(22)});
assert.equal(status.hidden, false);
assert.ok(status.innerHTML.includes('<strong>0개</strong>') && status.innerHTML.includes('<strong>22개</strong>'));
assert.equal(rowCount(status.innerHTML), 22);
assert.ok(status.innerHTML.includes('paper-data-more') && status.innerHTML.includes('나머지 19개 종목'));
assert.equal(nodes.get('#paper-operation-label').textContent, '시세 대기');
assert.equal(nodes.get('#strategy-pill').textContent, '시세 대기');
status = renderWith({running: true, data_message: SOME_READY, snapshots: waitingSnapshots(3)});
assert.equal(rowCount(status.innerHTML), 3);
assert.ok(!status.innerHTML.includes('paper-data-more'));
assert.ok(status.innerHTML.includes('paper-data-hint'));
status = renderWith({running: true, data_message: SOME_READY, snapshots: waitingSnapshots(4)});
assert.equal(rowCount(status.innerHTML), 4);
assert.ok(status.innerHTML.includes('나머지 1개 종목'));
status = renderWith({running: true, data_message: OK_MSG, snapshots: []});
assert.equal(rowCount(status.innerHTML), 0);
assert.ok(!status.innerHTML.includes('paper-data-hint') && !status.innerHTML.includes('paper-data-list'));
status = renderWith({running: false, data_message: NONE_READY, snapshots: waitingSnapshots(5)});
assert.equal(status.hidden, false);
assert.ok(status.innerHTML.includes('paper-status-guide'));
assert.ok(!status.innerHTML.includes('paper-data-summary') && rowCount(status.innerHTML) === 0);
assert.equal(nodes.get('#paper-operation-label').textContent, '전략 정지');
// escapeHtml must wrap every user-facing string that reaches innerHTML.
context.escapeHtml = value => `E(${value})`;
status = renderWith({running: true, data_message: '시세 오류·지연 또는 휴장', snapshots: waitingSnapshots(1)});
const shown = guide({running: true, data_message: '시세 오류·지연 또는 휴장'});
for (const field of FIELDS) assert.ok(status.innerHTML.includes(`E(${shown[field]})`), `escapeHtml missing for ${field}`);
assert.ok(status.innerHTML.includes('<span>E(시세 오류·지연 또는 휴장)</span>'));
assert.ok(status.innerHTML.includes('E(종목0)') && status.innerHTML.includes('E(S000)'));
context.escapeHtml = value => String(value);
nodes.clear();
context.renderDataSummary({running: true, operating_state: 'HOLIDAY', data_message: NONE_READY, snapshots: waitingSnapshots(2)}, {}, {});
assert.equal(nodes.get('#paper-operation-label').textContent, '휴장일 대기');
assert.equal(nodes.get('#paper-last-data').textContent, '수신 전');
assert.equal(nodes.get('#strategy-data-updated').textContent, '조건 확인 전');
console.log('PAPER status rendering (waiting rows): counts, 3-row fold, stopped hiding and escapeHtml wrapping passed.');
