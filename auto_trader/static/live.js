const won = new Intl.NumberFormat("ko-KR", { style: "currency", currency: "KRW", maximumFractionDigits: 0 });
const dollar = new Intl.NumberFormat("ko-KR", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
const number = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 4 });
const $ = (selector) => document.querySelector(selector);
let csrfToken = "";
let portfolioLoading = false;
let candidateLoading = false;
let favoriteLoading = false;
let searchTimer = null;
let searchSequence = 0;
let activeSearchQuery = "";
let activeSearchPage = 1;

const escapeHtml = (value) => String(value).replace(/[&<>'"]/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
})[character]);

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = !["GET", "HEAD", "OPTIONS"].includes(method) && csrfToken ? { "X-CSRF-Token": csrfToken } : {};
  const response = await fetch(path, { ...options, headers: { "Content-Type": "application/json", ...headers, ...(options.headers || {}) } });
  if (response.status === 401) { window.location.replace("/login"); throw new Error("로그인이 필요합니다."); }
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: "요청에 실패했습니다." }));
    throw new Error(error.detail || "요청에 실패했습니다.");
  }
  return response.status === 204 ? null : response.json();
}

function stockCell(item) {
  return `<span class="stock-name">${escapeHtml(item.name)}</span><span class="stock-code">${escapeHtml(item.symbol)} · ${escapeHtml(item.market_country)}</span>`;
}

function renderPortfolio(data) {
  $("#live-account-label").textContent = `${data.account_label} · 5초 자동 갱신`;
  $("#live-purchase").textContent = won.format(Number(data.total_purchase_krw));
  $("#live-market-value").textContent = won.format(Number(data.market_value));
  for (const [id, amount, rateId, rate] of [["live-profit", data.profit_loss, "live-profit-rate", data.profit_rate], ["live-daily-profit", data.daily_profit_loss, "live-daily-rate", data.daily_profit_rate]]) {
    const element = $("#" + id);
    element.textContent = won.format(Number(amount));
    element.className = Number(amount) > 0 ? "positive" : Number(amount) < 0 ? "negative" : "neutral";
    $("#" + rateId).textContent = `${Number(rate).toFixed(2)}%`;
  }
  $("#live-holdings-body").innerHTML = data.holdings.length ? data.holdings.map((item) => `<tr>
    <td>${stockCell(item)}</td><td>${number.format(Number(item.quantity))}주</td>
    <td>${won.format(Number(item.average_purchase_price))}</td><td>${won.format(Number(item.last_price))}</td>
    <td>${won.format(Number(item.market_value))}</td>
    <td class="${Number(item.profit_loss) > 0 ? "positive" : Number(item.profit_loss) < 0 ? "negative" : "neutral"}">${won.format(Number(item.profit_loss))} (${Number(item.profit_rate).toFixed(2)}%)</td>
  </tr>`).join("") : `<tr><td class="empty" colspan="6">실제 보유 종목이 없습니다.</td></tr>`;
}

function renderBuyingPower(data) {
  $("#live-buying-power-krw").textContent = won.format(Number(data.krw_cash_buying_power));
  $("#live-buying-power-usd").textContent = dollar.format(Number(data.usd_cash_buying_power));
}

function renderCandidates(data) {
  $("#candidate-basis").textContent = data.basis;
  $("#candidate-message").textContent = data.disclaimer;
  $("#candidate-body").innerHTML = data.candidates.length ? data.candidates.map((item) => {
    const rate = Number(item.change_rate_percent);
    const rateClass = rate > 0 ? "positive" : rate < 0 ? "negative" : "neutral";
    return `<tr><td>${number.format(item.rank)}위</td><td>${stockCell({ ...item, market_country: "KR" })}</td>
      <td>${won.format(Number(item.price))}</td><td class="${rateClass}">${rate.toFixed(2)}%</td>
      <td>${number.format(item.max_quantity)}주</td><td>${escapeHtml(item.reason)}</td></tr>`;
  }).join("") : `<tr><td class="empty" colspan="6">현재 원화 한도 안에서 조건을 충족한 후보가 없습니다.</td></tr>`;
}

function favoriteButton(symbol, active, label) {
  const title = active ? `${label} 관심 종목에서 제거` : `${label} 관심 종목에 추가`;
  return `<button class="favorite-button${active ? " active" : ""}" type="button"
    data-favorite-symbol="${escapeHtml(symbol)}" data-favorite-active="${active}"
    aria-label="${escapeHtml(title)}" title="${escapeHtml(title)}">${active ? "♥" : "♡"}</button>`;
}

function renderFavorites(items) {
  $("#favorite-count").textContent = `${number.format(items.length)} / 20`;
  $("#favorite-grid").innerHTML = items.length ? items.map((item) => {
    const rate = item.change_rate_percent === null ? null : Number(item.change_rate_percent);
    const rateClass = rate === null ? "neutral" : rate > 0 ? "positive" : rate < 0 ? "negative" : "neutral";
    const rank = item.trading_amount_rank === null ? "거래대금 100위 밖" : `거래대금 ${number.format(item.trading_amount_rank)}위`;
    return `<article class="favorite-card">
      <div class="favorite-card-head"><div><span class="stock-name">${escapeHtml(item.name)}</span><span class="stock-code">${escapeHtml(item.symbol)} · ${escapeHtml(item.market)}</span></div>${favoriteButton(item.symbol, true, item.name)}</div>
      <strong class="favorite-price">${item.price === null ? "가격 정보 없음" : won.format(Number(item.price))}</strong>
      <div class="favorite-meta"><span class="${rateClass}">${rate === null ? "등락률 집계 없음" : `${rate > 0 ? "+" : ""}${rate.toFixed(2)}%`}</span><span>${rank}</span></div>
    </article>`;
  }).join("") : `<div class="favorite-empty"><span>♡</span><strong>아직 관심 종목이 없습니다</strong><small>아래 종목 검색에서 하트를 눌러 추가하세요.</small></div>`;
}

async function refreshFavorites() {
  if (favoriteLoading || document.hidden) return;
  favoriteLoading = true;
  try {
    const items = await api("/live/favorites");
    renderFavorites(items);
    $("#favorite-message").textContent = items.length
      ? `관심 종목 ${items.length}개 · 30초마다 현재가 갱신`
      : "관심 종목은 이 컴퓨터의 PostgreSQL에 저장됩니다.";
  } catch (error) {
    $("#favorite-message").textContent = error.message;
  } finally {
    favoriteLoading = false;
  }
}

function updateFavoriteButtons(symbol, active) {
  document.querySelectorAll("[data-favorite-symbol]").forEach((button) => {
    if (button.dataset.favoriteSymbol !== symbol) return;
    button.dataset.favoriteActive = String(active);
    button.classList.toggle("active", active);
    button.textContent = active ? "♥" : "♡";
  });
}

async function toggleFavorite(button) {
  const symbol = button.dataset.favoriteSymbol;
  const active = button.dataset.favoriteActive === "true";
  button.disabled = true;
  try {
    if (active) {
      await api(`/live/favorites/${encodeURIComponent(symbol)}`, { method: "DELETE" });
    } else {
      await api("/live/favorites", { method: "POST", body: JSON.stringify({ symbol }) });
    }
    updateFavoriteButtons(symbol, !active);
    await refreshFavorites();
  } catch (error) {
    $("#favorite-message").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function refreshCandidates() {
  if (candidateLoading || document.hidden) return;
  candidateLoading = true;
  try {
    renderCandidates(await api("/live/candidates"));
  } catch (error) {
    $("#candidate-message").textContent = error.message;
  } finally {
    candidateLoading = false;
  }
}

function renderSearchResults(results) {
  const typeLabels = {
    FOREIGN_STOCK: "외국주식", DEPOSITARY_RECEIPT: "예탁증서", INFRASTRUCTURE_FUND: "인프라펀드",
    REIT: "리츠", ETF: "ETF", FOREIGN_ETF: "해외 ETF", ETN: "ETN", STOCK_WARRANTS: "신주인수권",
  };
  $("#stock-search-body").innerHTML = results.length ? results.map((item) => {
    const type = item.security_type === "STOCK"
      ? (item.is_common_share ? "보통주" : "우선주")
      : (typeLabels[item.security_type] || item.security_type);
    const rate = item.change_rate_percent === null ? null : Number(item.change_rate_percent);
    const rateClass = rate === null ? "neutral" : rate > 0 ? "positive" : rate < 0 ? "negative" : "neutral";
    return `<tr>
      <td class="rank-cell">${item.trading_amount_rank === null ? "100+" : number.format(item.trading_amount_rank)}</td>
      <td class="stock-cell"><span class="stock-name">${escapeHtml(item.name)}</span><span class="stock-code">${escapeHtml(item.symbol)}</span></td>
      <td class="type-cell"><span class="security-type">${escapeHtml(type)}</span></td>
      <td class="price-cell">${item.price === null ? "-" : won.format(Number(item.price))}</td>
      <td class="change-cell ${rateClass}">${rate === null ? "-" : `${rate > 0 ? "+" : ""}${rate.toFixed(2)}%`}</td>
      <td class="favorite-cell">${favoriteButton(item.symbol, item.is_favorite, item.name)}</td>
    </tr>`;
  }).join("") : `<tr><td class="empty" colspan="6">일치하는 국내 종목이 없습니다.</td></tr>`;
}

function renderSearchPagination(data) {
  const pagination = $("#stock-search-pagination");
  pagination.classList.toggle("hidden", data.total === 0);
  $("#stock-search-page").textContent = `${number.format(data.page)} / ${number.format(data.total_pages)}`;
  $("#stock-search-prev").disabled = data.page <= 1;
  $("#stock-search-next").disabled = data.page >= data.total_pages;
}

async function searchStocks(query, page, sequence) {
  try {
    const data = await api(`/live/stocks/search?q=${encodeURIComponent(query)}&page=${page}&page_size=8`);
    if (sequence !== searchSequence) return;
    activeSearchPage = data.page;
    renderSearchResults(data.results);
    renderSearchPagination(data);
    $("#stock-search-message").textContent = `총 ${number.format(data.total)}개 · 당일 시장 거래대금 인기순 · 페이지당 8개`;
  } catch (error) {
    if (sequence === searchSequence) $("#stock-search-message").textContent = error.message;
  }
}

async function refreshPortfolio() {
  if (portfolioLoading || document.hidden) return;
  portfolioLoading = true;
  try {
    const [portfolio, buyingPower] = await Promise.all([
      api("/live/portfolio"),
      api("/live/buying-power"),
    ]);
    renderPortfolio(portfolio);
    renderBuyingPower(buyingPower);
    $("#live-total-assets").textContent = won.format(
      Number(portfolio.market_value) + Number(buyingPower.krw_cash_buying_power)
    );
    const time = new Date().toLocaleTimeString("ko-KR");
    $("#live-connection-status").textContent = `토스 연결됨 · ${time}`;
    $("#account-dot").classList.add("online");
    $("#server-dot").classList.add("online");
    $("#live-portfolio-message").textContent = `마지막 자동 갱신 ${time} · 실제 주문은 잠겨 있습니다.`;
  } catch (error) {
    $("#account-dot").classList.remove("online");
    $("#live-connection-status").textContent = "연결 오류";
    $("#live-portfolio-message").textContent = error.message;
  } finally {
    portfolioLoading = false;
  }
}

async function openAuthModal() {
  $("#live-auth-modal").classList.remove("hidden");
  $("#live-pin").focus();
  $("#toss-status").textContent = "토스 API 연결을 확인하는 중...";
  try {
    const result = await api("/toss/test-connection", { method: "POST" });
    $("#toss-status").textContent = `API 정상 · 연결 계좌 ${result.account_count}개`;
  } catch (error) {
    $("#toss-status").textContent = error.message;
  }
}

function closeAuthModal() {
  $("#live-auth-modal").classList.add("hidden");
  $("#live-pin").value = "";
}

$("#unlock-live-button").addEventListener("click", openAuthModal);
$("#close-live-modal").addEventListener("click", closeAuthModal);
$("#live-auth-modal").addEventListener("click", (event) => { if (event.target === $("#live-auth-modal")) closeAuthModal(); });
document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeAuthModal(); });

$("#live-pin-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("#live-pin");
  try {
    const status = await api("/auth/live-pin/verify", { method: "POST", body: JSON.stringify({ pin: input.value }) });
    $("#live-pin-status").textContent = `인증 완료 · ${new Date(status.authorized_until).toLocaleTimeString("ko-KR")}까지 유효`;
    $("#unlock-live-button").textContent = "LIVE 인증 완료";
    setTimeout(closeAuthModal, 700);
  } catch (error) {
    $("#live-pin-status").textContent = error.message;
  } finally { input.value = ""; }
});

$("#logout-button").addEventListener("click", async () => {
  try { await api("/auth/logout", { method: "POST" }); } finally { window.location.replace("/login"); }
});

async function initialize() {
  try {
    const session = await api("/auth/me");
    csrfToken = session.csrf_token;
    $("#current-user").textContent = session.username;
    const pin = await api("/auth/live-pin");
    if (pin.authorized) $("#unlock-live-button").textContent = "LIVE 인증 완료";
    await refreshPortfolio();
    refreshFavorites();
    refreshCandidates();
    setInterval(refreshPortfolio, 5000);
    setInterval(refreshFavorites, 30000);
    setInterval(refreshCandidates, 30000);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) { refreshPortfolio(); refreshFavorites(); refreshCandidates(); }
    });
  } catch (_) { window.location.replace("/login"); }
}

$("#live-stock-search").addEventListener("input", (event) => {
  clearTimeout(searchTimer);
  const query = event.target.value.trim();
  activeSearchQuery = query;
  activeSearchPage = 1;
  searchSequence += 1;
  const sequence = searchSequence;
  if (!query) {
    $("#stock-search-body").innerHTML = `<tr><td class="empty" colspan="6">검색어를 입력하세요.</td></tr>`;
    $("#stock-search-message").textContent = "당일 시장 거래대금 상위 100위가 먼저 표시됩니다.";
    $("#stock-search-pagination").classList.add("hidden");
    return;
  }
  $("#stock-search-message").textContent = "검색 중...";
  searchTimer = setTimeout(() => searchStocks(query, 1, sequence), 350);
});

$("#stock-search-prev").addEventListener("click", () => {
  if (!activeSearchQuery || activeSearchPage <= 1) return;
  searchSequence += 1;
  searchStocks(activeSearchQuery, activeSearchPage - 1, searchSequence);
});

$("#stock-search-next").addEventListener("click", () => {
  if (!activeSearchQuery) return;
  searchSequence += 1;
  searchStocks(activeSearchQuery, activeSearchPage + 1, searchSequence);
});

$("#stock-search-body").addEventListener("click", (event) => {
  const button = event.target.closest("[data-favorite-symbol]");
  if (button) toggleFavorite(button);
});

$("#favorite-grid").addEventListener("click", (event) => {
  const button = event.target.closest("[data-favorite-symbol]");
  if (button) toggleFavorite(button);
});

initialize();
