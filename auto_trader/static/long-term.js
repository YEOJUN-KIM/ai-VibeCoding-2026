const $ = (selector) => document.querySelector(selector);
const won = new Intl.NumberFormat("ko-KR", { style: "currency", currency: "KRW", maximumFractionDigits: 0 });
let csrfToken = "";
let scanStatusTimer = null;

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
    grid.innerHTML = '<div class="empty long-term-board-empty"><strong>자동 후보를 분석하고 있습니다.</strong><span>거래대금 상위 보통주를 한 종목씩 천천히 확인하므로 첫 결과까지 잠시 걸릴 수 있습니다.</span></div>';
    $("#long-term-board-date").textContent = "추천 기준을 통과한 종목을 점수순으로 최대 5개 표시합니다.";
    return;
  }
  const newest = new Date(Math.max(...items.map((item) => new Date(item.generated_at).getTime())));
  $("#long-term-board-date").textContent = `${newest.toLocaleDateString("ko-KR")} 저장 분석 기준 · 점수순 ${items.length}개`;
  grid.innerHTML = items.map((item) => {
    const opportunity = (item.opportunities || []).find((text) => !text.includes("없습니다")) || "핵심 재무 기준을 통과했습니다.";
    const risk = (item.risks || []).find((text) => !text.includes("없습니다")) || "추가 정량 위험 신호가 없습니다.";
    return `<a class="long-term-candidate-card rank-${String(item.rank || "d").toLowerCase()}" href="/stocks/${encodeURIComponent(item.symbol)}#long-term"><div class="candidate-rank"><b>${escapeHtml(item.rank || "-")}</b></div><div class="candidate-main"><small>${escapeHtml(item.market)} · ${escapeHtml(item.symbol)}</small><strong>${escapeHtml(item.name)}</strong></div><div class="candidate-score"><strong>${item.overall_score ?? "-"}</strong><span>점</span></div><div class="candidate-metrics"><span>매출 변화 <b>${metric(item.revenue_growth_percent)}</b></span><span>영업이익률 <b>${metric(item.operating_margin_percent)}</b></span><span>1년 수익률 <b>${metric(item.price_return_1y_percent)}</b></span></div><div class="candidate-rationale"><span><b>선정 근거</b>${escapeHtml(opportunity)}</span><span class="caution"><b>확인할 점</b>${escapeHtml(risk)}</span></div><small class="candidate-date">${new Date(item.generated_at).toLocaleDateString("ko-KR")} 분석 · ${escapeHtml(item.fiscal_year || "공시연도 미확인")}년 공시</small></a>`;
  }).join("");
}

async function loadCandidates() {
  try { renderCandidates(await api("/research/long-term/candidates?limit=5")); }
  catch (error) { $("#long-term-candidates").innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`; }
}

function renderMyCandidates(items) {
  $("#my-long-term-count").textContent = items.length;
  const grid = $("#my-long-term-candidates");
  if (!items.length) {
    grid.innerHTML = '<div class="empty long-term-board-empty"><strong>아직 내가 관찰하는 후보가 없습니다.</strong><span>관심종목에 하트를 표시하거나 종목 상세 장기분석에서 후보로 추가하세요.</span></div>';
    return;
  }
  grid.innerHTML = items.map((item) => {
    const analysis = item.analysis;
    const sources = [item.is_favorite ? "♥ 관심종목" : "", item.added_manually ? "직접 추가" : ""].filter(Boolean).join(" · ");
    const rank = analysis?.rank || "?";
    const score = analysis?.overall_score ?? "-";
    const metrics = analysis
      ? `<span>매출 변화 <b>${metric(analysis.revenue_growth_percent)}</b></span><span>영업이익률 <b>${metric(analysis.operating_margin_percent)}</b></span><span>1년 수익률 <b>${metric(analysis.price_return_1y_percent)}</b></span>`
      : '<span class="candidate-not-analyzed">상세 페이지에서 장기분석을 열면 점수와 랭크가 저장됩니다.</span>';
    const date = analysis ? `${new Date(analysis.generated_at).toLocaleDateString("ko-KR")} 분석` : "아직 분석하지 않음";
    return `<a class="long-term-candidate-card rank-${String(analysis?.rank || "d").toLowerCase()}" href="/stocks/${encodeURIComponent(item.symbol)}#long-term"><div class="candidate-rank"><span>MY</span><b>${escapeHtml(rank)}</b></div><div class="candidate-main"><small>${escapeHtml(item.market)} · ${escapeHtml(item.symbol)}</small><strong>${escapeHtml(item.name)}</strong><em>${escapeHtml(sources)}</em></div><div class="candidate-score"><strong>${score}</strong><span>점</span></div><div class="candidate-metrics">${metrics}</div><small class="candidate-date">${escapeHtml(date)}</small></a>`;
  }).join("");
}

async function loadMyCandidates() {
  try { renderMyCandidates(await api("/research/long-term/watchlist")); }
  catch (error) { $("#my-long-term-candidates").innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`; }
}

function selectLongTermPageTab(tabName) {
  document.querySelectorAll("[data-long-term-page-tab]").forEach((button) => button.classList.toggle("active", button.dataset.longTermPageTab === tabName));
  document.querySelectorAll("[data-long-term-page-panel]").forEach((panel) => { panel.hidden = panel.dataset.longTermPagePanel !== tabName; });
  history.replaceState(null, "", tabName === "mine" ? "#mine" : location.pathname);
}

async function loadScanStatus() {
  try {
    const status = await api("/research/long-term/candidate-status");
    $("#long-term-scan-status").textContent = status.message;
    $("#long-term-scan-status").classList.toggle("running", Boolean(status.running));
    const button = $("#long-term-refresh-button");
    button.disabled = Boolean(status.running);
    button.classList.toggle("running", Boolean(status.running));
    button.querySelector("strong").textContent = status.running ? "후보 분석 중" : "추천 새로 분석";
    button.querySelector("small").textContent = status.running && status.target
      ? `${status.completed}/${status.target}개 검토 · ${status.selected || 0}개 통과`
      : "최대 20개 후보 검토";
    if (status.running) {
      window.clearTimeout(scanStatusTimer);
      scanStatusTimer = window.setTimeout(async () => { await loadCandidates(); await loadScanStatus(); }, 10000);
    }
  } catch (error) { $("#long-term-scan-status").textContent = error.message; }
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

$("#long-term-search-form").addEventListener("submit", (event) => { event.preventDefault(); const query = $("#long-term-query").value.trim(); if (query) search(query); });
$("#long-term-search-results").addEventListener("click", (event) => { const button = event.target.closest("[data-symbol]"); if (button) location.assign(`/stocks/${encodeURIComponent(button.dataset.symbol)}#long-term`); });
$("#long-term-page-tabs").addEventListener("click", (event) => { const button = event.target.closest("[data-long-term-page-tab]"); if (button) selectLongTermPageTab(button.dataset.longTermPageTab); });
$("#long-term-refresh-button").addEventListener("click", refreshRecommendations);
$("#logout-button").addEventListener("click", async () => { try { await api("/auth/logout", { method: "POST" }); } finally { location.replace("/login"); } });

(async () => {
  try {
    const session = await api("/auth/me"); csrfToken = session.csrf_token; $("#current-user").textContent = session.username;
    if (location.hash === "#mine") selectLongTermPageTab("mine");
    await Promise.all([loadCandidates(), loadMyCandidates(), loadScanStatus()]);
  } catch (error) { $("#long-term-message").textContent = error.message; }
})();
