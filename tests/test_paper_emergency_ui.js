// Pure UI contract for the emergency latch (PAPER-SAFETY-REVIEW-01 P2). Synthetic server states; no network, DB or app.
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync('auto_trader/static/paper-mvp.js', 'utf8');
const nodes = new Map();
const $ = sel => { if (!nodes.has(sel)) nodes.set(sel, { textContent: '', innerHTML: '', className: '', disabled: false, hidden: false }); return nodes.get(sel); };
const calls = [];
let responses = [];
const ctx = { $, esc: String, calls, toast: t => { ctx.lastToast = t; }, api: async (path, opts) => { calls.push(path + ' ' + ((opts && opts.method) || 'GET')); const r = responses.shift(); if (r instanceof Error) throw r; return r; } };
vm.createContext(ctx);
const handler = source.slice(source.indexOf("$('#emergency').onclick"), source.indexOf('\n// ----', source.indexOf("$('#emergency').onclick")));
vm.runInContext(source.slice(source.indexOf('function renderRun()'), source.indexOf('async function control(')) + `
let st; const refreshCalls = []; async function refresh() { refreshCalls.push(1); st = await api('/paper/mvp/status'); render(); }
function render() { renderRun(); }
function setSt(v) { st = v; }
` + handler, ctx);
const run = c => vm.runInContext(c, ctx);
const status = (emergency, state = 'emergency') => ({ ready_reason: null, data_source: 'SYNTHETIC', emergency,
  run: { state, label: state, running: false, message: '', held_symbols: ['005930'], blocked_reason: null, stop_note: '' } });
const view = () => ({ start: $('#start').disabled, emergency: $('#emergency').disabled, reset: $('#emergency-reset').hidden, reason: $('#run-reason').textContent, label: $('#emergency').textContent });
$('#emergency-reset'); $('#emergency'); $('#start'); $('#pause'); $('#prepare'); $('#source-badge'); $('#run-label'); $('#run-next');
let n = 0; const ok = (a, e) => { assert.deepEqual(a, e); n++; };
const click = async () => { await $('#emergency').onclick(); };

// 1) unsaved latch (e.g. after a failed save, then a status refresh): warning, retry enabled, start blocked, no reset shortcut, no persistence promise.
run('setSt(' + JSON.stringify(status({ at: null, unsaved: true })) + '); renderRun()');
let v = view();
ok([v.start, v.emergency, v.reset, v.label], [true, false, true, '긴급정지 다시 시도']);
assert.match(v.reason, /저장되지 않았습니다/); assert.doesNotMatch(v.reason, /서버 재시작 후에도 유지됩니다/); n += 2;

// 2) retry still fails: warning stays, retry stays enabled, status is re-read (fail closed), only the emergency-stop endpoint is used.
responses = [new Error('긴급정지를 저장하지 못했습니다.'), status({ at: null, unsaved: true })];
calls.length = 0;
(async () => {
  await click();
  ok(calls, ['/paper/mvp/emergency-stop POST', '/paper/mvp/status GET']);
  v = view(); ok([v.start, v.emergency, v.reset], [true, false, true]); assert.match(v.reason, /저장되지 않았습니다/); n++;
  assert.match(ctx.lastToast, /저장하지 못했습니다/); n++;
  // 3) retry succeeds: persisted display only because the server says unsaved=false.
  responses = [status({ at: '2026-11-27T00:00:00', unsaved: false })]; calls.length = 0;
  await click();
  ok(calls, ['/paper/mvp/emergency-stop POST']);
  v = view(); ok([v.start, v.emergency, v.reset, v.label], [true, true, false, '긴급정지']);
  assert.match(v.reason, /서버 재시작 후에도 유지됩니다/); assert.doesNotMatch(v.reason, /저장되지 않았습니다/); n += 2;
  // 4) a success response that still says unsaved is NOT shown as persisted.
  responses = [status({ at: null, unsaved: true })];
  await click();
  v = view(); ok([v.emergency, v.reset], [false, true]); assert.match(v.reason, /저장되지 않았습니다/); n++;
  // 5) never a start/reset call from the emergency control; calls so far contained only emergency-stop/status.
  assert.ok(![...calls].some(c => /start|reset/.test(c))); n++;
  // 6) non-latched states unchanged.
  run('setSt(' + JSON.stringify(status(null, 'idle')) + '); renderRun()'); v = view();
  ok([v.emergency, v.reset, v.label], [false, true, '긴급정지']);
  console.log(`Emergency UI contract: ${n}/${n} passed (unsaved warning, retry, repeated failure, confirmed persisted display, no reset/start shortcut).`);
})().catch(e => { console.error(e); process.exit(1); });
