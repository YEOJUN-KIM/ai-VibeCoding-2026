const won = new Intl.NumberFormat("ko-KR", { style: "currency", currency: "KRW", maximumFractionDigits: 0 });
const dollar = new Intl.NumberFormat("ko-KR", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
const number = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 4 });
const $ = (selector) => document.querySelector(selector);
let csrfToken = "";
let portfolioLoading = false;
let candidateLoading = false;
let favoriteLoading = false;

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
  const referenceDate = String(data.daily_profit_reference_date || "").replaceAll("-", ".");
  if (data.market_open_today === false) {
    $("#live-daily-profit-label").textContent = "최근 거래일 평가손익";
    $("#live-daily-profit-basis").textContent = `${referenceDate} 기준 · 오늘 휴장`;
  } else if (data.market_open_today === true) {
    $("#live-daily-profit-label").textContent = "오늘 평가손익";
    $("#live-daily-profit-basis").textContent = `${referenceDate} 거래일 기준`;
  } else {
    $("#live-daily-profit-label").textContent = "최근 일일 평가손익";
    $("#live-daily-profit-basis").textContent = `${referenceDate} 장 상태 확인 불가`;
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
    return `<tr><td>${number.format(item.rank)}위</td><td><a class="candidate-stock-link" href="/stocks/${encodeURIComponent(item.symbol)}">${stockCell({ ...item, market_country: "KR" })}</a></td>
      <td>${won.format(Number(item.price))}</td><td class="${rateClass}">${rate.toFixed(2)}%</td>
      <td>${number.format(item.max_quantity)}주</td><td>${escapeHtml(item.reason)}</td></tr>`;
  }).join("") : `<tr><td class="empty" colspan="6">현재 스캐너 조건에 맞는 종목이 없습니다.</td></tr>`;
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
  }).join("") : `<div class="favorite-empty"><span>♡</span><strong>아직 관심 종목이 없습니다</strong><small>국내주식 페이지에서 하트를 눌러 추가하세요.</small></div>`;
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

$("#favorite-grid").addEventListener("click", (event) => {
  const button = event.target.closest("[data-favorite-symbol]");
  if (button) toggleFavorite(button);
});

initialize();
