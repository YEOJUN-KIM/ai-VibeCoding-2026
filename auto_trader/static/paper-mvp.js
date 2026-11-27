'use strict';
// PAPER-MVP-01 page script. Every number comes from GET /paper/mvp/status; the page never invents data.
// Writes: PUT /paper/mvp/settings ({budget} only), POST /paper/mvp/{start,stop,emergency-stop},
// POST /paper/accounts/EXPERIMENT/select (prepare the virtual 10,000,000 KRW account). CSRF token from /auth/me.
const $ = s => document.querySelector(s), $$ = s => Array.from(document.querySelectorAll(s));
const num = v => Number(v), money = n => Math.round(n).toLocaleString('ko-KR'), won = n => money(n) + '원';
const signedWon = n => (n > 0 ? '+' : n < 0 ? '−' : '') + won(Math.abs(n));
const signedPct = n => (n > 0 ? '+' : n < 0 ? '−' : '') + Math.abs(n).toFixed(2) + '%';
const esc = t => String(t ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const KST = { timeZone: 'Asia/Seoul', hour12: false };
const fmtTime = iso => { const d = new Date(iso); return isNaN(d) ? '' : d.toLocaleString('ko-KR', { ...KST, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }); };
const fmtFull = iso => { const d = new Date(iso); return isNaN(d) ? '' : d.toLocaleString('ko-KR', { ...KST, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) + ' KST'; };
// PAPER-UI-POLISH-01: list paging. Each list keeps its own page, clamped to the last valid page on every render.
const PAGE_SIZE = { home: 5, all: 10, records: 10 }, pageOf = { home: 1, all: 1, records: 1 };
const PAGE_LABEL = { home: '현재 보유 종목 페이지', all: '전체 보유 종목 페이지', records: '매매 및 판단 기록 페이지' };
const knownNames = new Map(), openRecords = new Set();   // names already present in earlier status data (no lookups); expanded records
function slicePage(key, items) {
  const size = PAGE_SIZE[key], pages = Math.max(1, Math.ceil(items.length / size));
  pageOf[key] = Math.min(Math.max(1, pageOf[key]), pages);
  return { rows: items.slice((pageOf[key] - 1) * size, pageOf[key] * size), page: pageOf[key], pages, total: items.length };
}
function pagerHtml(key, p) {
  return FolioPagination.paper(key, p, PAGE_LABEL[key]);
}
function bindPagers(root, key, rerender) {
  $$(root + ' [data-page-key]').forEach(b => b.onclick = () => {
    pageOf[key] = Number(b.dataset.pageTo); rerender();
    const f = $(root + ' [aria-current="page"]'); if (f) f.focus({ preventScroll: true });
  });
}
const digitsOnly = t => String(t).replace(/\D/g, '').replace(/^0+(?=\d)/, '').slice(0, 13);
const commas = n => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
const budgetText = v => Number.isFinite(v) ? commas(v) : '';
const codeLabel = (name, symbol) => name && name !== symbol ? name + ' (' + symbol + ')' : symbol;
let csrf = '', st = null, saved = null, draft = null, dirty = false, tab = 'home', period = 'day', historyPeriod = 'all', filter = '전체', toastTimer, chartPts = [];

async function api(path, options = {}) {
  const method = (options.method || 'GET').toUpperCase();
  const headers = method !== 'GET' && csrf ? { 'X-CSRF-Token': csrf } : {};
  const res = await fetch(path, { ...options, headers: { 'Content-Type': 'application/json', ...headers } });
  if (res.status === 401) { location.replace('/login'); throw new Error('로그인이 필요합니다.'); }
  if (!res.ok) {
    const e = await res.json().catch(() => ({ detail: '요청에 실패했습니다.' }));
    throw new Error(typeof e.detail === 'string' ? e.detail : '입력값을 확인하세요.');
  }
  return res.json();
}
function toast(t) { clearTimeout(toastTimer); $('#toast').textContent = t; $('#toast').hidden = false; toastTimer = setTimeout(() => $('#toast').hidden = true, 4000); }
const copy = x => JSON.parse(JSON.stringify(x));
const fromServer = s => ({ budget: s.budget });
const toServer = d => ({ budget: d.budget });
const total = () => num(st.account.total_asset);
const summary = s => '운용 금액 ' + (Number.isFinite(s.budget) ? won(s.budget) : '입력 필요');
const cls = n => n > 0 ? 'up' : n < 0 ? 'down' : '';

// ---------------------------------------------------------------- load / refresh
async function refresh() {
  try {
    st = await api('/paper/mvp/status?period=' + (tab === 'history' ? historyPeriod : period));
    st.holdings.forEach(h => { if (h.name) knownNames.set(h.symbol, h.name); });
    if (!saved) { saved = fromServer(st.settings); draft = copy(saved); renderSettings(); }
    else if (!dirty && tab !== 'settings') { saved = fromServer(st.settings); draft = copy(saved); }
    render();
  } catch (e) {
    $('#run-label').className = 'amber'; $('#run-label').innerHTML = '<i class="dot"></i> 상태 확인 실패';
    $('#run-reason').textContent = e.message + ' · 현재 실행 상태를 확인할 수 없습니다. 화면의 이전 정보는 최신이 아닐 수 있습니다.';
    $('#run-next').textContent = '실행 중일 수 있으니 연결을 확인한 뒤 새로고침하세요.';
    $('#start').disabled = $('#pause').disabled = true;
  }
}
function render() { renderRun(); renderHome(); renderHistory(); if (tab === 'settings') settingSummary(); }

// ---------------------------------------------------------------- control strip
function renderRun() {
  const r = st.run, held = r.held_symbols.length;
  const tone = { idle: 'muted', running: 'green', stopped: 'amber', emergency: 'up', other: 'amber', blocked: 'up' }[r.state];
  $('#run-label').className = tone; $('#run-label').innerHTML = '<i class="dot"></i> ' + esc(r.label);
  let reason = r.message;
  if (r.state === 'idle') reason = '저장된 운용 금액으로 모의 운용을 시작할 수 있어요.';
  if (st.ready_reason) reason = st.ready_reason;
  if (!r.running && r.state !== 'idle') reason = (held ? '보유 ' + held + '종목이 있습니다. ' : '') + '신규 판단·주문이 멈춰 있습니다.';
  if (r.state === 'blocked') reason = r.blocked_reason;
  $('#run-reason').textContent = reason;
  const src = st.data_source === 'SYNTHETIC' ? '합성(가상) 시장 데이터로 검증 중 · 실제 시장 데이터는 아직 검증되지 않았습니다.' : '검증된 시장 데이터 소스가 연결되지 않았습니다 · 신규 진입은 보류되고 보유분 감시만 합니다.';
  $('#run-next').textContent = r.running ? '실행 중 · 보유분 손절·익절 감시가 켜져 있습니다. ' + src : r.stop_note + (r.state === 'emergency' ? ' 긴급정지의 실제 보장 범위는 일시정지와 같으며 별도 검증되지 않았습니다.' : '');
  $('#prepare').hidden = !st.ready_reason;
  const latched = r.state === 'emergency';
  $('#start').disabled = r.running || latched || !!st.ready_reason; $('#start').textContent = r.state === 'stopped' ? '다시 시작' : '시작';
  // Only a server-confirmed save is shown as persisted; an unsaved latch keeps the warning and allows an explicit retry.
  const unsaved = latched && !!(st.emergency && st.emergency.unsaved);
  $('#pause').disabled = r.state !== 'running'; $('#emergency').disabled = latched && !unsaved;
  $('#emergency').textContent = unsaved ? '긴급정지 다시 시도' : '긴급정지';
  const rs = $('#emergency-reset'); rs.hidden = !latched || unsaved;
  if (unsaved) {
    $('#run-reason').textContent = '긴급정지는 이 서버에서만 적용되었고 아직 저장되지 않았습니다. 자동매매는 시작되지 않지만 서버 재시작 후에는 유지되지 않을 수 있습니다. 긴급정지를 다시 시도하세요.';
    $('#run-next').textContent = '저장이 확인될 때까지 이 경고가 유지됩니다. 보유 주식은 매도하지 않았습니다.';
  } else if (latched) {
    $('#run-reason').textContent = '긴급정지 상태입니다. 해제하기 전에는 자동매매를 시작할 수 없습니다. 새로고침·로그아웃·서버 재시작 후에도 유지됩니다.';
    $('#run-next').textContent = '긴급정지로 신규 판단·주문과 청산 감시가 멈췄고 보유 주식은 매도하지 않았습니다. 해제하면 일시정지 상태가 되며, 자동매매는 직접 다시 시작해야 합니다.';
  }
  $('#source-badge').textContent = st.data_source === 'SYNTHETIC' ? 'PAPER · 합성 데이터' : 'PAPER · 데이터 소스 미연결';
  $('#source-badge').className = 'badge ' + (st.data_source === 'SYNTHETIC' ? 'amber' : 'neutral');
}
async function control(path, ok) {
  try { st = await api(path, { method: 'POST' }); render(); toast(ok); } catch (e) { toast(e.message); }
}
$('#start').onclick = () => { if (dirty) { toast('저장하지 않은 설정이 있습니다. 설정 저장·적용 후 시작하세요.'); return; } control('/paper/mvp/start', '자동매매를 시작했습니다. 모든 거래는 가상이며 실제 주문은 없습니다.'); };
$('#prepare').onclick = async () => {
  try { await api('/paper/accounts/EXPERIMENT/select', { method: 'POST' }); saved = null; await refresh(); toast('가상 1,000만원 모의계좌를 준비했습니다. 실제 계좌와 연결되지 않습니다.'); } catch (e) { toast(e.message); }
};
{ const b = document.createElement('button'); b.id = 'emergency-reset'; b.hidden = true; b.textContent = '긴급정지 해제'; $('#emergency').after(b); }
$('#emergency-reset').onclick = async () => {
  if (!confirm('긴급정지를 해제할까요?\n해제해도 자동매매는 시작되지 않고 일시정지 상태가 됩니다. 시작은 직접 해야 합니다.')) return;
  try { st = await api('/paper/mvp/emergency-reset', { method: 'POST', body: JSON.stringify({ confirm: true }) }); render(); toast('긴급정지를 해제했습니다. 자동매매는 시작되지 않았습니다.'); } catch (e) { toast(e.message); }
};
$('#pause').onclick = () => control('/paper/mvp/stop', '일시정지했습니다. 보유 주식은 그대로 남고 청산 감시도 멈춥니다.');
$('#emergency').onclick = async () => {
  try { st = await api('/paper/mvp/emergency-stop', { method: 'POST' }); render(); toast('긴급정지했습니다. 보유 주식은 매도하지 않으며 청산 감시도 멈춥니다.'); }
  catch (e) { toast(e.message); await refresh(); }   // a failed save still latches in memory: re-read so the unsaved warning and retry show
};

// ---------------------------------------------------------------- home
function holdingTable(key) {
  const pg = slicePage(key, st.holdings);
  if (!st.holdings.length) return '<div class="empty"><p>현재 보유 종목이 없습니다.</p><small>조건에 맞는 후보가 생기면 가상 매수합니다. 현금 보유도 정상 판단입니다.</small></div>';
  return '<table><thead><tr><th>종목</th><th>수량</th><th>평가금액</th><th>평가손익 · 수익률</th></tr></thead><tbody>' + pg.rows.map(h => {
    const p = num(h.profit), pct = h.profit_pct == null ? 0 : num(h.profit_pct);
    return '<tr><td><div class="stockcell"><span class="stockicon">' + esc((h.name || h.symbol).slice(0, 1)) + '</span><div><button class="stockname" data-detail="' + esc(h.symbol) + '">' + esc(h.name || h.symbol) + ' ↗</button><small>' + esc(h.symbol) + '</small></div></div></td><td class="num">' + h.quantity + '주</td><td class="num">' + won(num(h.value)) + '</td><td class="num ' + cls(p) + '">' + signedWon(p) + '<span class="subnum">' + signedPct(pct) + '</span></td></tr>';
  }).join('') + '</tbody></table>' + pagerHtml(key, pg);
}
function periodStart(k) {
  const now = new Date(st.checked_at), kst = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Seoul' })), off = now - kst;
  if (k === 'day') { kst.setHours(0, 0, 0, 0); return new Date(kst.getTime() + off); }
  return new Date(now.getTime() - { week: 7, month: 30, year: 365 }[k] * 864e5);
}
const PERIOD_NAME = { day: '오늘 손익', week: '최근 1주 손익', month: '최근 1개월 손익', year: '최근 1년 손익', all: '전체 손익' };
function renderHome() {
  const a = st.account, T = num(a.total_asset);
  $('#wealth-value').innerHTML = money(T) + '<span>원</span>';
  $('#wealth-note').textContent = '초기 모의자금 ' + won(num(a.initial_cash)) + ' · 기준 시각 ' + fmtFull(st.checked_at) + ' · 가상 거래';
  const g = num(st.pnl.gain), gp = st.pnl.percent == null ? 0 : num(st.pnl.percent);
  $('#period-title').textContent = PERIOD_NAME[st.pnl.period] || '기간 손익';
  $('#period-value').className = 'value num ' + cls(g); $('#period-value').innerHTML = signedWon(g) + ' <span>' + signedPct(gp) + '</span>';
  $('#period-basis').textContent = st.pnl.basis;
  const tp = num(a.total_profit), tr = a.return_percent == null ? 0 : num(a.return_percent);
  $('#total-profit').className = 'value num ' + cls(tp); $('#total-profit').innerHTML = signedWon(tp) + ' <span>' + signedPct(tr) + '</span>';
  $('#holdings-value').innerHTML = money(num(a.holdings_value)) + '<span>원</span>';
  $('#cash-value').innerHTML = money(num(a.cash)) + '<span>원</span>';
  $('#holdings-count').innerHTML = st.holdings.length + '<span>종목</span>';
  $('#home-holdings').innerHTML = holdingTable('home');
  $$('#home-holdings [data-detail]').forEach(e => e.onclick = () => detail(e.dataset.detail));
  bindPagers('#home-holdings', 'home', renderHome);
  $$('[data-period]').forEach(e => { const on = e.dataset.period === period; e.classList.toggle('active', on); e.setAttribute('aria-pressed', on); });
  drawChart();
}
function drawChart() {
  const start = periodStart(period), pts = st.series.map(([t, v]) => ({ t: new Date(t), v: num(v) })).filter(p => p.t >= start);
  const plot = $('#plot'); chartPts = [];
  if (pts.length < 2) { plot.innerHTML = ''; $('#chart-count').textContent = '표본 ' + pts.length + '개 · 아직 그래프를 그릴 기록이 부족합니다'; $('#chart-range').textContent = ''; $('#chart-from').textContent = ''; $('#chart-to').textContent = ''; return; }
  const first = pts[0], last = pts.at(-1), vals = pts.map(p => p.v), min = Math.min(...vals), max = Math.max(...vals);
  const span = Math.max(max - min, first.v * .02) * 1.2, mid = (min + max) / 2, lo = mid - span / 2, dt = Math.max(1, last.t - first.t);
  chartPts = pts.map(p => [16 + (p.t - first.t) / dt * 968, 220 - (p.v - lo) / span * 200, p]);
  const d = chartPts.map((pt, i) => (i ? 'L' : 'M') + pt[0] + ',' + pt[1]).join(' ');
  plot.innerHTML = [20, 120, 220].map(y => '<line x1="16" x2="984" y1="' + y + '" y2="' + y + '" stroke="#2a2b35" stroke-dasharray="3 5"/>').join('') + '<path d="' + d + ' L984,228 L16,228 Z" fill="url(#fill)"/><path d="' + d + '" fill="none" stroke="#b5a0ef" stroke-width="2.5" vector-effect="non-scaling-stroke"/>';
  $('#chart-count').textContent = '표본 ' + pts.length + '개'; $('#chart-range').textContent = won(first.v) + ' → ' + won(last.v);
  $('#chart-from').textContent = fmtFull(first.t); $('#chart-to').textContent = fmtFull(last.t);
}
$('#asset-chart').addEventListener('pointermove', e => {
  if (!chartPts.length) return;
  const r = e.currentTarget.getBoundingClientRect(), x = (e.clientX - r.left) / r.width * 1000;
  const pt = chartPts.reduce((b, p) => Math.abs(p[0] - x) < Math.abs(b[0] - x) ? p : b);
  $('#chart-guide').setAttribute('x1', pt[0]); $('#chart-guide').setAttribute('x2', pt[0]); $('#chart-dot').setAttribute('cx', pt[0]); $('#chart-dot').setAttribute('cy', pt[1]);
  $('#chart-cursor').removeAttribute('hidden');
  const tip = $('#chart-tip'), delta = pt[2].v - chartPts[0][2].v;
  tip.innerHTML = '<time>' + fmtFull(pt[2].t) + '</time><strong>총자산 ' + won(pt[2].v) + '</strong><span class="' + (delta < 0 ? 'negative' : '') + '">기간 시작 대비 ' + signedWon(delta) + '</span>';
  tip.hidden = false; const w = $('.chart-frame').clientWidth;
  tip.style.left = Math.max(8, Math.min(w - tip.offsetWidth - 8, pt[0] / 1000 * w - tip.offsetWidth / 2)) + 'px'; tip.style.top = '8px';
});
$('#asset-chart').addEventListener('pointerleave', () => { $('#chart-tip').hidden = true; $('#chart-cursor').setAttribute('hidden', ''); });
$$('[data-period]').forEach(e => e.onclick = () => { period = e.dataset.period; refresh(); });

// ---------------------------------------------------------------- history
function records() {
  const out = [];
  for (const t of st.trades) {
    const filled = t.status === 'FILLED', buy = t.side === 'BUY';
    out.push({ at: t.at, type: filled ? (buy ? '매수' : '매도') : '관망', name: codeLabel(t.name || knownNames.get(t.symbol), t.symbol) + ' · ' + t.quantity + '주',
      tag: filled ? (buy ? '매수 완료' : '매도 완료') : '주문 거절', label: '체결가 ' + won(num(t.price)) + (t.fee !== '0' ? ' · 수수료 ' + won(num(t.fee)) : ''),
      reason: t.reason || t.message, extra: t.realized != null ? '실현손익 ' + signedWon(num(t.realized)) + ' (비용 차감 후) · 가상 체결' : filled ? '가상 체결 · 매수 당시 청산 기준은 이 매수분에 고정됩니다.' : t.message });
  }
  for (const d of st.decisions) {
    const type = d.kind === 'CONTROL' ? '제어' : '관망';
    const tag = { CANDIDATE: '후보 선정', WATCH: '정상 관망', HOLD_DATA: '판단 보류 · 데이터 확인', CONTROL: '제어' }[d.kind] || d.kind;
    out.push({ at: d.at, type, name: d.title, tag, label: d.symbols && d.symbols.length ? d.symbols.join(' · ') : '', reason: d.reason,
      extra: d.kind === 'HOLD_DATA' ? '데이터가 불완전하거나 시각을 신뢰할 수 없으면 신규 매수하지 않습니다(정상 관망과 구분).' : '' });
  }
  return out.sort((a, b) => new Date(b.at) - new Date(a.at));
}
function renderHistory() {
  const a = st.account, un = num(a.unrealized_profit), re = num(a.realized_profit), tp = num(a.total_profit);
  $('#h-unrealized').className = 'value num ' + cls(un); $('#h-unrealized').textContent = signedWon(un);
  $('#h-realized').className = 'value num ' + cls(re); $('#h-realized').textContent = signedWon(re);
  $('#h-realized-note').textContent = '매도 완료 · 누적 수수료 ' + won(num(a.total_fees)) + ' 반영';
  $('#h-total').className = 'value num ' + cls(tp); $('#h-total').textContent = signedWon(tp);
  const g = num(st.pnl.gain), gp = st.pnl.percent == null ? 0 : num(st.pnl.percent);
  $('#history-period-value').className = 'num ' + cls(g); $('#history-period-value').textContent = signedWon(g) + ' (' + signedPct(gp) + ')';
  $('#history-period-basis').textContent = '가상 순자산 변화 · ' + st.pnl.basis;
  $$('[data-history-period]').forEach(e => { const on = e.dataset.historyPeriod === historyPeriod; e.classList.toggle('active', on); e.setAttribute('aria-pressed', on); });
  $('#all-holdings').innerHTML = holdingTable('all');
  $$('#all-holdings [data-detail]').forEach(e => e.onclick = () => detail(e.dataset.detail));
  bindPagers('#all-holdings', 'all', renderHistory);
  $('#holdings-badge').textContent = 'PAPER · ' + st.holdings.length + '종목';
  const r = st.risk, s = fromServer(st.settings);
  $('#account-safety').textContent = '자동매매 운용 금액 ' + won(s.budget) + ' · 현재 자동매매 투자금 ' + won(num(r.invested_amount)) + (r.new_buys_allowed ? ' · 신규 매수 가능' : ' · 신규 매수 제한: ' + r.block_reason) + '. 금액을 낮춰도 기존 보유분을 강제 매도하지 않습니다. 직접 하는 모의 주문에는 적용되지 않습니다.';
  $('#account-safety').classList.toggle('amber', !r.new_buys_allowed);
  const start = historyPeriod === 'all' ? null : periodStart(historyPeriod);
  const list = records().filter(x => (filter === '전체' || x.type === filter) && (!start || new Date(x.at) >= start));
  const pg = slicePage('records', list);
  $('#records').innerHTML = pg.rows.map(x => { const key = x.at + '|' + x.type + '|' + x.name; return '<details class="record" data-key="' + esc(key) + '"' + (openRecords.has(key) ? ' open' : '') + '><summary><span class="badge ' + (x.type === '매도' ? 'neutral' : x.type === '관망' ? 'amber' : x.type === '제어' ? 'neutral' : '') + '">' + esc(x.type) + '</span><div><strong>' + esc(x.name) + '</strong><small>' + esc(x.label ? x.label + ' · ' : '') + esc(x.tag) + '</small></div><span class="time">' + esc(fmtTime(x.at)) + '</span></summary><div class="record-content"><p>' + esc(x.reason) + '</p>' + (x.extra ? '<div class="notice">' + esc(x.extra) + '</div>' : '') + '</div></details>'; }).join('') || '<div class="empty"><p>이 기간의 기록이 없습니다.</p></div>';
  $('#records').insertAdjacentHTML('beforeend', pagerHtml('records', pg));
  bindPagers('#records', 'records', renderHistory);
  $('#record-count').textContent = '선택 기간 ' + filter + ' ' + list.length + '건 · 모두 가상 기록(실제 주문 없음)';
}
$$('[data-history-period]').forEach(e => e.onclick = () => { historyPeriod = e.dataset.historyPeriod; pageOf.records = 1; refresh(); });
$('#records').addEventListener('toggle', e => { const d = e.target; if (d.dataset && d.dataset.key) { if (d.open) openRecords.add(d.dataset.key); else openRecords.delete(d.dataset.key); } }, true);
$$('[data-filter]').forEach(e => e.onclick = () => { filter = e.dataset.filter; pageOf.records = 1; $$('[data-filter]').forEach(b => { const on = b === e; b.classList.toggle('active', on); b.setAttribute('aria-pressed', on); }); renderHistory(); });
function modal(title, body, footer = '') {
  if ($('#modal').open) $('#modal').close();
  $('#modal-title').textContent = title; $('#modal-body').innerHTML = body; $('#modal-footer').innerHTML = footer; $('#modal-error').textContent = ''; $('#modal').showModal();
}
$('#modal-close').onclick = () => $('#modal').close();
function detail(symbol) {
  const h = st.holdings.find(x => x.symbol === symbol); if (!h) return;
  modal(h.name + ' · 매수분별 상세', '<div class="row"><span class="badge">PAPER · ' + esc(h.symbol) + '</span><small>가상 데이터 · 매수분 ' + h.lots.length + '개</small></div><div class="balance-strip"><span>보유 <strong>' + h.quantity + '주</strong></span><span>평가금액 <strong>' + won(num(h.value)) + '</strong></span><span>평가손익 <strong class="' + cls(num(h.profit)) + '">' + signedWon(num(h.profit)) + '</strong></span></div>' +
    h.lots.map((l, i) => '<article class="lot"><div class="row"><span class="badge">매수분 ' + (i + 1) + '</span><small>' + esc(fmtFull(l.opened_at)) + ' · ' + l.quantity + '주</small></div><dl><div><dt>매수 기준단가 (비용 포함)</dt><dd>' + won(num(l.average_price)) + '</dd></div><div><dt>매수 당시 청산 기준</dt><dd>손절 ' + esc(l.stop_pct) + '% · 익절 ' + esc(l.take_pct) + '%</dd></div><div><dt>최대 보유 일수</dt><dd>' + esc(l.max_days) + '일</dd></div></dl><p class="context">이 청산 기준은 설정을 바꿔도 매도까지 유지됩니다.</p></article>').join(''));
}

// ---------------------------------------------------------------- settings
const changed = () => JSON.stringify(draft) !== JSON.stringify(saved);
function settingSummary() {
  const T = total(), percent = draft.budget / T * 100, run = st.run.running;
  $('#budget-percent').textContent = (isFinite(percent) ? percent : 0).toFixed(1) + '%'; $('#budget-slider').value = Math.max(0, Math.min(100, percent || 0));
  $('#budget-max').textContent = '100% · ' + won(T);
  $$('[data-ratio]').forEach(e => e.classList.toggle('active', Math.abs(+e.dataset.ratio - percent) < .01));
  $('#draft-summary').textContent = summary(draft);
  dirty = changed();
  $('#save-status').textContent = run ? '실행 중에는 설정을 바꿀 수 없습니다 · 일시정지 후 변경하세요.' : dirty ? '저장하지 않은 변경 · 저장·적용을 눌러 반영하세요.' : '현재 적용값 · 서버(모의계좌)에 저장됨';
  $('#save-button').disabled = run;
  $('#settings-error').textContent = '';
}
function renderSettings() {
  $('#budget').value = budgetText(draft.budget); settingSummary();
}
function validSettings(s) {
  const T = total();
  if (!Number.isInteger(s.budget) || s.budget < 1 || s.budget > T) return '자동매매 운용 금액은 1원 이상 ' + won(T) + ' 이하의 정수로 입력하세요.';
  return '';
}
$('#budget').oninput = () => {                       // thousands separators while typing/pasting; value stays a whole-won integer
  const el = $('#budget'), raw = el.value, before = raw.slice(0, el.selectionStart ?? raw.length).replace(/\D/g, '').length, d = digitsOnly(raw);
  draft.budget = d === '' ? NaN : Number(d);
  const text = d === '' ? '' : commas(d); el.value = text;
  let pos = 0, seen = 0; while (pos < text.length && seen < before) { if (/\d/.test(text[pos])) seen++; pos++; }
  if (document.activeElement === el) el.setSelectionRange(pos, pos);
  settingSummary();
};
$('#budget-slider').oninput = () => { draft.budget = Math.round(total() * Number($('#budget-slider').value) / 100); $('#budget').value = budgetText(draft.budget); settingSummary(); };
$$('[data-ratio]').forEach(e => e.onclick = () => { draft.budget = Math.round(total() * Number(e.dataset.ratio) / 100); renderSettings(); });
$('#settings-form').onsubmit = async e => {
  e.preventDefault(); const err = validSettings(draft); if (err) { $('#settings-error').textContent = err; return; }
  try { st = await api('/paper/mvp/settings', { method: 'PUT', body: JSON.stringify(toServer(draft)) }); saved = fromServer(st.settings); draft = copy(saved); dirty = false; render(); renderSettings(); toast('운용 금액을 저장·적용했습니다. 신규 매수부터 적용되며 기존 보유분의 청산 기준은 유지됩니다.'); }
  catch (error) { $('#settings-error').textContent = error.message; }
};
$('#revert').onclick = () => { draft = copy(saved); renderSettings(); toast('현재 적용값으로 되돌렸습니다.'); };
$('#defaults').onclick = () => { draft = { budget: Math.round(total() / 2) }; renderSettings(); toast('초기값을 입력했습니다. 적용하려면 저장하세요.'); };
// ---------------------------------------------------------------- tabs / boot
function selectTab(t) {
  if (t === 'settings' && tab !== 'settings') { draft = copy(saved); renderSettings(); }
  tab = t;
  $$('[data-tab]').forEach(e => { const on = e.dataset.tab === t; e.classList.toggle('active', on); e.setAttribute('aria-selected', on); e.tabIndex = on ? 0 : -1; });
  ['home', 'settings', 'history'].forEach(x => $('#paper-' + x).hidden = x !== t);
  refresh(); window.scrollTo(0, 0);
}
$$('[data-tab]').forEach(e => { e.onclick = () => selectTab(e.dataset.tab); });
$$('[data-go-history]').forEach(e => e.onclick = () => selectTab('history'));
(async () => {
  try { const me = await api('/auth/me'); csrf = me.csrf_token; } catch (e) { return; }
  await refresh(); setInterval(() => { if (!document.hidden) refresh(); }, 2500);
})();
