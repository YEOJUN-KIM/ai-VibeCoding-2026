// PR-FIX-01 UI contract: a non-AUTO management scope is visible and recoverable only by an explicit, confirmed click.
// Synthetic server states; no network, DB or app.
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync('auto_trader/static/paper-mvp.js', 'utf8');
const nodes = new Map();
const $ = sel => { if (!nodes.has(sel)) nodes.set(sel, { textContent: '', innerHTML: '', className: '', disabled: false, hidden: false }); return nodes.get(sel); };
const calls = [];
let responses = [], confirmAnswer = true, confirmText = '';
const ctx = { $, esc: String, calls, toast: t => { ctx.lastToast = t; }, confirm: t => { confirmText = t; return confirmAnswer; },
  api: async (path, opts) => { calls.push(path + ' ' + ((opts && opts.method) || 'GET') + (opts && opts.body ? ' ' + opts.body : '')); const r = responses.shift(); if (r instanceof Error) throw r; return r; } };
vm.createContext(ctx);
const a = source.indexOf("$('#scope-recover').onclick");
const handler = source.slice(a, source.indexOf("$('#prepare').onclick", a));
vm.runInContext(source.slice(source.indexOf('const SCOPE_LABEL'), source.indexOf('async function control(')) + `
let st; const refreshCalls = []; async function refresh() { refreshCalls.push(1); st = await api('/paper/mvp/status'); render(); }
function render() { renderRun(); }
function setSt(v) { st = v; }
` + handler, ctx);
const run = c => vm.runInContext(c, ctx);
const status = (scope, state = 'stopped', extra = {}) => Object.assign({ ready_reason: null, data_source: 'SYNTHETIC', emergency: null, management_scope: scope,
  run: { state, label: state, running: state === 'running', message: '', held_symbols: [], blocked_reason: null, stop_note: '' } }, extra);
['#scope-recover', '#start', '#pause', '#emergency', '#emergency-reset', '#prepare', '#source-badge', '#run-label', '#run-next', '#run-reason'].forEach($);
const show = s => run('setSt(' + JSON.stringify(s) + '); renderRun()');
let n = 0; const ok = (x, y) => { assert.deepEqual(x, y); n++; };

(async () => {
  // 1) AUTO: no recovery button, start available.
  show(status('AUTO')); ok([$('#scope-recover').hidden, $('#start').disabled], [true, false]);
  // 2) legacy scope: visible, start disabled, reason names the scope; nothing was called by rendering.
  for (const scope of ['CURRENT', 'ALL']) {
    show(status(scope)); ok([$('#scope-recover').hidden, $('#start').disabled], [false, true]);
    assert.match($('#run-reason').textContent, /관리 범위/); n++;
  }
  ok(calls, []);
  // 3) missing field (older server) is treated as AUTO, never as a legacy scope.
  const legacy = status('AUTO'); delete legacy.management_scope; show(legacy); ok([$('#scope-recover').hidden, $('#start').disabled], [true, false]);
  // 4) a running run never offers the change (the server would refuse it anyway).
  show(status('ALL', 'running')); ok($('#scope-recover').hidden, true);
  // 5) declining the confirmation changes nothing and sends nothing.
  show(status('ALL')); confirmAnswer = false; await $('#scope-recover').onclick(); ok(calls, []);
  assert.match(confirmText, /수동 보유분/); assert.match(confirmText, /시작되지 않습니다/); n += 2;
  // 6) confirming sends exactly PUT /paper/management {scope: AUTO} then re-reads status; no start/reset/select/snapshot call.
  confirmAnswer = true; responses = [{}, status('AUTO')];
  await $('#scope-recover').onclick();
  ok(calls, ['/paper/management PUT {"scope":"AUTO"}', '/paper/mvp/status GET']);
  assert.ok(!calls.some(c => /start|reset|select|snapshot/.test(c))); n++;
  ok([$('#scope-recover').hidden, $('#start').disabled], [true, false]);
  assert.match(ctx.lastToast, /시작되지 않았습니다/); n++;
  // 7) a server refusal is reported and the scope stays recoverable.
  calls.length = 0; show(status('CURRENT')); responses = [new Error('자동매매를 일시정지한 뒤 관리 범위를 변경하세요.')];
  await $('#scope-recover').onclick(); ok(calls, ['/paper/management PUT {"scope":"AUTO"}']); assert.match(ctx.lastToast, /일시정지/); n++;
  // 8) emergency latch text keeps priority over the scope hint.
  show(status('ALL', 'emergency', { emergency: { at: 'x', unsaved: false } })); assert.match($('#run-reason').textContent, /긴급정지 상태/); n++;
  console.log('paper_pr_fix01_ui: ' + n + ' assertions passed');
})().catch(e => { console.error(e); process.exit(1); });
