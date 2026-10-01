const $ = (selector) => document.querySelector(selector);
const won = new Intl.NumberFormat("ko-KR", { style: "currency", currency: "KRW", maximumFractionDigits: 0 });
let csrfToken = "";
let scanStatusTimer = null;
let myScanStatusTimer = null;
let myRefreshRunning = false;
let myManualFinishedAt = 0;

const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
})[character]);

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const response = await fetch(path, { ...options, headers: {
    "Content-Type": "application/json", ...(!["GET", "HEAD"].includes(method) && csrfToken ? { "X-CSRF-Token": csrfToken } : {}), ...(options.headers || {}),
  } });
  if (response.status === 401) { location.replace("/login"); throw new Error("로그인이 필요합니다."); }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || "요청에 실패했습니다.");
  return body;
}

function metric(value) {
  if (value == null) return "-";
  return `${Number(value) > 0 ? "+" : ""}${Number(value).toFixed(1)}%`;
}

function renderCandidates(items) {
  const grid = $("#long-term-candidates");
  if (!items.length) {
    grid.innerHTML = '<div class="empty long-term-board-empty"><strong>아직 추천 기업이 없습니다.</strong><span>추천 갱신 후 기준을 통과한 기업을 이곳에서 확인할 수 있습니다.</span></div>';
    $("#long-term-board-date").textContent = "추천 기준을 통과한 종목을 점수순으로 최대 5개 표시합니다.";
    return;
  }
  const newest = new Date(Math.max(...items.map((item) => new Date(item.generated_at).getTime())));
  $("#long-term-board-date").textContent = `${newest.toLocaleDateString("ko-KR")} 분석 기준 · ${items.length}개`;
  grid.innerHTML = items.map((item) => {
    const opportunities = item.opportunities || [];
    const risks = item.risks || [];
    const compactReason = (text) => String(text)
      .replace("매출 변화율이 ", "매출 ")
      .replace("최근 현금배당수익률은 ", "배당 ")
      .replace("최근 영업이익률이 ", "영업이익률 ")
      .replace("부채비율이 ", "부채비율 ")
      .replace("최근 1년 관측 최대 낙폭이 ", "최대 낙폭 ")
      .replace(/입니다\.$/, "");
    const reasonSummary = (texts, fallback) => escapeHtml(texts.length ? texts.map(compactReason).join(" · ") : fallback);
    return `<a class="long-term-candidate-card rank-${String(item.rank || "d").toLowerCase()}" data-report-symbol="${escapeHtml(item.symbol)}" data-report-name="${escapeHtml(item.name)}" href="/stocks/${encodeURIComponent(item.symbol)}#long-term"><div class="candidate-rank"><b>${escapeHtml(item.rank || "-")}</b></div><div class="candidate-main"><small>${escapeHtml(item.market)} · ${escapeHtml(item.symbol)}</small><strong>${escapeHtml(item.name)}</strong></div><div class="candidate-score"><strong>${item.overall_score ?? "-"}</strong><span>점</span></div><div class="candidate-metrics"><span>매출 변화 <b>${metric(item.revenue_growth_percent)}</b></span><span>영업이익률 <b>${metric(item.operating_margin_percent)}</b></span><span>1년 수익률 <b>${metric(item.price_return_1y_percent)}</b></span></div><div class="candidate-rationale"><section><h3>선정 근거</h3><p>${reasonSummary(opportunities, "추가 긍정 요인 없음")}</p></section><section class="caution"><h3>주의</h3><p>${reasonSummary(risks, "추가 정량 위험 신호 없음")}</p></section></div><span class="candidate-detail-link">근거·점수 자세히 보기 →</span><small class="candidate-date">${new Date(item.generated_at).toLocaleDateString("ko-KR")} 분석 · ${escapeHtml(item.fiscal_year || "공시연도 미확인")}년 공시</small></a>`;
  }).join("");
}

async function loadCandidates() {
  try { renderCandidates(await api("/research/long-term/candidates?limit=5")); }
  catch (error) { $("#long-term-candidates").innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`; }
}

let myCandidates = [];
let myPage = 1;
const myPageSize = 10;

function renderMyCandidates(items) {
  myCandidates = items;
  $("#my-long-term-count").textContent = items.length;
  const query = $("#my-watch-query").value.trim().toLocaleLowerCase();
  const rank = $("#my-watch-rank").value;
  const filtered = items.filter(item => (!query || `${item.name} ${item.symbol}`.toLocaleLowerCase().includes(query))
    && (!rank || (item.analysis?.rank || "pending") === rank));
  const pages = Math.max(1, Math.ceil(filtered.length / myPageSize));
  myPage = Math.min(myPage, pages);
  const start = (myPage - 1) * myPageSize;
  const visible = filtered.slice(start, start + myPageSize);
  $("#my-watch-summary").textContent = `전체 ${items.length}개 · ${filtered.length ? `${start + 1}–${start + visible.length} / ${filtered.length}개 표시` : "일치하는 기업 없음"}`;
  $("#my-long-term-candidates").innerHTML = visible.length ? visible.map(item => {
    const analysis = item.analysis;
    const sources = [item.is_favorite ? "관심종목" : "", item.added_manually ? "직접 추가" : ""].filter(Boolean).join(" · ");
    return `<tr><td><a class="watch-company" data-report-symbol="${escapeHtml(item.symbol)}" data-report-name="${escapeHtml(item.name)}" href="/stocks/${encodeURIComponent(item.symbol)}#long-term" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</a><small class="stock-code">${escapeHtml(item.market)} · ${escapeHtml(item.symbol)}</small></td><td><span class="watch-grade rank-${String(analysis?.rank || "d").toLowerCase()}">${escapeHtml(analysis?.rank || "미분석")}</span></td><td class="watch-score">${analysis?.overall_score ?? "-"}</td><td>${metric(analysis?.revenue_growth_percent)}</td><td>${metric(analysis?.operating_margin_percent)}</td><td>${metric(analysis?.price_return_1y_percent)}</td><td><span>${analysis ? new Date(analysis.generated_at).toLocaleDateString("ko-KR") : "분석 대기"}</span><small class="stock-code">${escapeHtml(sources)}</small></td></tr>`;
  }).join("") : `<tr><td colspan="7" class="empty">${items.length ? "검색 조건에 맞는 기업이 없습니다." : "관찰할 기업을 추가해 보세요. 관심종목 또는 종목의 장기분석에서 추가할 수 있습니다."}</td></tr>`;
  $("#my-watch-page").textContent = `${myPage} / ${pages}`;
  for (const id of ["first", "prev"]) $("#my-watch-" + id).disabled = myPage === 1;
  for (const id of ["next", "last"]) $("#my-watch-" + id).disabled = myPage === pages;
}

async function loadMyCandidates() {
  try { renderMyCandidates(await api("/research/long-term/watchlist")); }
  catch (error) { $("#my-long-term-candidates").innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`; }
}

async function refreshMyCandidates() {
  const button = $("#my-long-term-refresh-button");
  const status = $("#my-long-term-refresh-status");
  button.disabled = true;
  myRefreshRunning = true;
  let completed = 0, failed = 0;
  try {
    const items = await api("/research/long-term/watchlist");
    if (!items.length) { status.textContent = "분석할 내 후보가 없습니다."; return; }
    for (const item of items) {
      status.textContent = `내 후보 분석 중 · ${completed}/${items.length}개 · ${item.name}`;
      try { await api(`/research/long-term/${encodeURIComponent(item.symbol)}?refresh=true`); }
      catch (error) { failed += 1; }
      completed += 1;
      await loadMyCandidates();
    }
    status.textContent = `내 후보 분석 ${failed ? "종료 · 일부 조회 실패" : "완료"} · ${completed}/${items.length}개 검토 · 갱신 ${completed - failed}개 · 실패 ${failed}개${failed ? " · 실패한 후보는 이전 분석을 유지합니다." : ""}`;
  } catch (error) { status.textContent = `내 후보 분석 중단 · ${error.message}`; }
  finally { myRefreshRunning = false; myManualFinishedAt = Date.now(); button.disabled = false; }
}

async function loadMyScanStatus() {
  window.clearTimeout(myScanStatusTimer);
  try {
    const status = await api("/research/long-term/watchlist-status");
    if (!myRefreshRunning && (status.running || !myManualFinishedAt || new Date(status.last_finished_at).getTime() > myManualFinishedAt)) {
      const next = status.next_run_at ? `다음 갱신 ${new Date(status.next_run_at).toLocaleString("ko-KR", {month:"long", day:"numeric", hour:"2-digit", minute:"2-digit"})}` : "";
      $("#my-long-term-refresh-status").textContent = status.running || status.last_finished_at ? `${status.message}${next ? " · " + next : ""}` : next;
      $("#my-long-term-refresh-button").disabled = Boolean(status.running);
      if (status.running || status.last_finished_at) await loadMyCandidates();
    }
  } catch (error) {
    if (!myRefreshRunning) $("#my-long-term-refresh-status").textContent = `자동 분석 상태 확인 실패 · ${error.message}`;
  }
  myScanStatusTimer = window.setTimeout(loadMyScanStatus, 10000);
}

function selectLongTermPageTab(tabName) {
  document.querySelectorAll("[data-long-term-page-tab]").forEach((button) => button.classList.toggle("active", button.dataset.longTermPageTab === tabName));
  document.querySelectorAll("[data-long-term-page-panel]").forEach((panel) => { panel.hidden = panel.dataset.longTermPagePanel !== tabName; });
  history.replaceState(null, "", `#${tabName}`);
  $("[data-company-finder]").hidden = tabName === "etf";
  $("[data-company-guide]").hidden = tabName === "etf";
  if (tabName === "etf") window.loadEtfs?.();
}

function scanStatusText(status) {
  const reviewed = Number(status.completed || 0);
  const total = Number(status.target || 0);
  const failed = Number(status.failed || 0);
  const skipped = Number(status.skipped || 0);
  const counts = `${reviewed}/${total}개 검토 · 추천 ${status.selected || 0}개 · 기준 미달 ${status.excluded || 0}개 · 내 후보 제외 ${skipped}개 · 조회 실패 ${failed}개`;
  const finished = status.last_finished_at ? ` · ${new Date(status.last_finished_at).toLocaleTimeString("ko-KR")} 종료` : "";
  if (status.running) {
    const label = status.phase === "waiting" ? "요청 한도 대기" : "분석 중";
    return `${label} · ${total ? counts : "후보 목록을 준비하고 있습니다."}${status.current_name ? ` · ${status.current_name}` : ""}`;
  }
  if (status.phase === "complete") return `분석 완료 · ${counts}${finished} · ${status.message}`;
  if (status.phase === "partial") return `검토 종료 · 일부 조회 실패 · ${counts}${finished} · ${status.message}`;
  if (["failed", "cancelled"].includes(status.phase)) return `분석 중단 · ${counts}${finished} · ${status.message}`;
  // 이전 서버가 반환한 상태에는 전체 후보 수가 보존되지 않아 완주를 단정하지 않는다.
  if (status.last_finished_at) return `${status.message} · 검토 ${reviewed}개 · 조회 실패 ${failed}개${finished}`;
  return status.next_run_at ? `분석 대기 · 다음 자동 분석 ${new Date(status.next_run_at).toLocaleString("ko-KR")}` : status.message;
}

async function loadScanStatus() {
  window.clearTimeout(scanStatusTimer);
  try {
    const status = await api("/research/long-term/candidate-status");
    $("#long-term-scan-status").textContent = scanStatusText(status);
    $("#long-term-scan-status").classList.toggle("running", Boolean(status.running));
    const button = $("#long-term-refresh-button");
    button.disabled = Boolean(status.running);
    button.classList.toggle("running", Boolean(status.running));
    button.querySelector("strong").textContent = status.running ? "후보 분석 중" : "추천 갱신";
    button.querySelector("small").textContent = status.target
      ? `${status.completed}/${status.target}개 검토 · ${status.failed || 0}개 실패`
      : "최대 20개 후보 검토";
    if (!status.running && status.last_finished_at) await loadCandidates();
    if (status.running) scanStatusTimer = window.setTimeout(loadScanStatus, 2000);
  } catch (error) {
    $("#long-term-scan-status").textContent = `상태 확인 실패 · 분석 중단 여부를 확인할 수 없습니다. 자동 재확인합니다. (${error.message})`;
    scanStatusTimer = window.setTimeout(loadScanStatus, 5000);
  }
}

async function refreshRecommendations() {
  const button = $("#long-term-refresh-button");
  button.disabled = true;
  $("#long-term-message").textContent = "";
  try {
    await api("/research/long-term/candidate-refresh", { method: "POST" });
    await loadScanStatus();
  } catch (error) {
    $("#long-term-message").textContent = error.message;
    button.disabled = false;
  }
}

async function search(query) {
  const box = $("#long-term-search-results");
  box.classList.remove("hidden"); box.innerHTML = '<div class="empty">종목을 찾고 있습니다.</div>';
  try {
    const page = await api(`/live/stocks/search?q=${encodeURIComponent(query)}&page=1&page_size=8`);
    const items = page.results.filter((item) => item.security_type === "STOCK" && item.is_common_share);
    box.innerHTML = items.length ? items.map((item) => `<button type="button" data-symbol="${escapeHtml(item.symbol)}"><span><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.symbol)} · ${escapeHtml(item.market)}</small></span><b>${item.price == null ? "-" : won.format(Number(item.price))}</b></button>`).join("") : '<div class="empty">분석 가능한 국내 보통주를 찾지 못했습니다.</div>';
  } catch (error) { box.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`; }
}

for (const [id, event] of [["my-watch-query", "input"], ["my-watch-rank", "change"]]) $("#" + id).addEventListener(event, () => { myPage = 1; renderMyCandidates(myCandidates); });
for (const id of ["first", "prev", "next", "last"]) $("#my-watch-" + id).addEventListener("click", () => {
  if (id === "first") myPage = 1;
  else if (id === "prev") myPage = Math.max(1, myPage - 1);
  else if (id === "next") myPage += 1;
  else myPage = Number.MAX_SAFE_INTEGER;
  renderMyCandidates(myCandidates);
});

$("#long-term-search-form").addEventListener("submit", (event) => { event.preventDefault(); const query = $("#long-term-query").value.trim(); if (query) search(query); });
$("#long-term-search-results").addEventListener("click", (event) => { const button = event.target.closest("[data-symbol]"); if (button) window.openResearchReport(button.dataset.symbol, button.querySelector("strong").textContent); });
$("#long-term-page-tabs").addEventListener("click", (event) => { const button = event.target.closest("[data-long-term-page-tab]"); if (button) selectLongTermPageTab(button.dataset.longTermPageTab); });
$("#long-term-refresh-button").addEventListener("click", refreshRecommendations);
$("#my-long-term-refresh-button").addEventListener("click", refreshMyCandidates);
$("#logout-button").addEventListener("click", async () => { try { await api("/auth/logout", { method: "POST" }); } finally { location.replace("/login"); } });

(async () => {
  try {
    const session = await api("/auth/me"); csrfToken = session.csrf_token; $("#current-user").textContent = session.username;
    selectLongTermPageTab(["recommended", "etf"].includes(location.hash.slice(1)) ? location.hash.slice(1) : "mine");
    await Promise.all([loadCandidates(), loadMyCandidates(), loadScanStatus(), loadMyScanStatus()]);
  } catch (error) { $("#long-term-message").textContent = error.message; }
})();

document.addEventListener('click', event => {
  const link = event.target.closest('[data-report-symbol]');
  if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  window.openResearchReport(link.dataset.reportSymbol, link.dataset.reportName);
});
