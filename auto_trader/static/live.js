const won = new Intl.NumberFormat("ko-KR", { style: "currency", currency: "KRW", maximumFractionDigits: 0 });
const dollar = new Intl.NumberFormat("ko-KR", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
const number = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 4 });
const $ = (selector) => document.querySelector(selector);
let csrfToken = "";
let portfolioLoading = false;
let candidateLoading = false;
let favoriteLoading = false;
let orderLoading = false;
let activeLiveSection = "portfolio";

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

function holdingStockCell(item) {
  const content = stockCell(item);
  const autoQuantity = Number(item.auto_managed_quantity || 0);
  const ownership = autoQuantity > 0
    ? `<span class="holding-source auto">자동관리 ${number.format(autoQuantity)}주</span><span class="holding-source existing">기존·수동 ${number.format(Number(item.existing_quantity || 0))}주</span>`
    : `<span class="holding-source existing">기존·수동 보유</span>`;
  const body = `${content}<span class="holding-source-row">${ownership}</span>`;
  if (!item.symbol || item.market_country !== "KR") return body;
  return `<a class="candidate-stock-link" href="/stocks/${encodeURIComponent(item.symbol)}?from=live&section=portfolio" title="${escapeHtml(item.name)} 상세 보기">${body}</a>`;
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
    <td>${holdingStockCell(item)}</td><td>${number.format(Number(item.quantity))}주</td>
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
    return `<tr><td>${number.format(item.rank)}위</td><td><a class="candidate-stock-link" href="/stocks/${encodeURIComponent(item.symbol)}?from=live&section=scanner">${stockCell({ ...item, market_country: "KR" })}</a></td>
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
    const detailUrl = `/stocks/${encodeURIComponent(item.symbol)}?from=live&section=favorites`;
    return `<article class="favorite-card">
      <div class="favorite-card-head"><a class="favorite-stock-link" href="${detailUrl}" title="${escapeHtml(item.name)} 상세 보기"><span class="stock-name">${escapeHtml(item.name)}</span><span class="stock-code">${escapeHtml(item.symbol)} · ${escapeHtml(item.market)}</span></a>${favoriteButton(item.symbol, true, item.name)}</div>
      <a class="favorite-card-body" href="${detailUrl}" aria-label="${escapeHtml(item.name)} 상세 보기">
        <strong class="favorite-price">${item.price === null ? "가격 정보 없음" : won.format(Number(item.price))}</strong>
        <div class="favorite-meta"><span class="${rateClass}">${rate === null ? "등락률 집계 없음" : `${rate > 0 ? "+" : ""}${rate.toFixed(2)}%`}</span><span>${rank}</span></div>
      </a>
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

function selectLiveSection(section) {
  activeLiveSection = section;
  document.querySelectorAll("[data-live-tab]").forEach((button) => {
    const active = button.dataset.liveTab === section;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll("[data-live-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.livePanel !== section;
  });
  window.history.replaceState(null, "", `${window.location.pathname}#${section}`);
}

function refreshActiveLiveSection() {
  if (activeLiveSection === "portfolio") return refreshPortfolio();
  if (activeLiveSection === "favorites") return refreshFavorites();
  if (activeLiveSection === "scanner") return refreshCandidates();
  if (activeLiveSection === "orders") return refreshDryRunOrders();
  return Promise.resolve();
}

function setLiveAuthButton(authorized) {
  const button = $("#unlock-live-button");
  button.textContent = authorized ? "LIVE 인증 완료" : "LIVE 주문 잠금 해제";
  button.classList.toggle("success", authorized);
  button.classList.toggle("danger", !authorized);
}

function formatOrderSide(side) { return side === "BUY" ? "매수" : "매도"; }

function renderOrders(orders) {
  $("#live-orders-body").innerHTML = orders.length ? orders.map((order) => {
    const conditional = order.mode === "SINGLE";
    const priceText = order.order_type === "MARKET" ? "시장가" : won.format(Number(order.order_price));
    const conditionText = conditional
      ? `감시 ${won.format(Number(order.trigger_price))}<br><small>만료 ${escapeHtml(order.expire_date || "-")}</small>`
      : priceText;
    const checks = (order.validation_snapshot?.checks || []).map((check) =>
      `<li class="${check.passed ? "passed" : "failed"}">${check.passed ? "✓" : "!"} ${escapeHtml(check.message)}</li>`
    ).join("");
    const isCancelled = ["CANCELLED", "CANCELED"].includes(order.status);
    const canCancelReal = !order.dry_run && ["SUBMITTED", "PENDING", "PARTIAL_FILLED"].includes(order.status);
    const management = isCancelled
      ? `<span class="cancelled-order-message">취소되었습니다</span>`
      : `<details class="order-check-details"><summary>검사 결과</summary><ul>${checks || "<li>저장된 검사 결과가 없습니다.</li>"}</ul></details>${order.dry_run ? `<button class="text-button cancel-dry-run-button" type="button" data-cancel-order-id="${order.id}">취소</button>` : (canCancelReal ? `<button class="text-button cancel-dry-run-button" type="button" data-real-cancel-order-id="${order.id}">실제 주문 취소</button>` : "")}`;
    const statusText = order.dry_run ? (isCancelled ? "취소됨" : "DRY RUN") : (order.broker_status || order.status);
    const syncText = !order.dry_run
      ? `<small class="reconciliation-status ${order.reconciliation_status === "MATCHED" ? "matched" : "warning"}">${order.reconciliation_status === "MATCHED" ? "토스 대조 완료" : `대조 ${escapeHtml(order.reconciliation_status)}`}</small>`
      : "";
    const executionText = !order.dry_run && Number(order.filled_quantity || 0) > 0
      ? `<br><small>체결 ${number.format(Number(order.filled_quantity))}주 · 평균 ${won.format(Number(order.average_filled_price || 0))}</small>`
      : "";
    return `<tr class="${isCancelled ? "cancelled-order-row" : ""}">
      <td><span class="order-status ${isCancelled ? "cancelled" : "open"}">${escapeHtml(statusText)}</span>${syncText}</td>
      <td><a class="candidate-stock-link" href="/stocks/${encodeURIComponent(order.symbol)}?from=live&section=orders"><span class="stock-name">${escapeHtml(order.stock_name)}</span><span class="stock-code">${escapeHtml(order.symbol)} · ${escapeHtml(order.account_label)}</span></a></td>
      <td>${formatOrderSide(order.side)} ${number.format(Number(order.quantity))}주<br><small>${order.dry_run ? "연습" : (order.order_source === "AUTO" ? "자동" : "수동")} · ${conditional ? "목표가 도달" : "일반"} · ${order.order_type === "MARKET" ? "시장가" : "지정가"}</small>${executionText}</td>
      <td>${conditionText}</td><td>${won.format(Number(order.estimated_amount))}</td>
      <td>${new Date(order.created_at).toLocaleString("ko-KR")}</td>
      <td>${management}</td>
    </tr>`;
  }).join("") : `<tr><td class="empty" colspan="7">저장된 DRY RUN 주문이 없습니다.</td></tr>`;
}

async function refreshDryRunOrders() {
  if (orderLoading) return;
  orderLoading = true;
  try {
    const [dryOrders, realOrders] = await Promise.all([
      api("/live/orders/dry-run"), api("/live/orders/real"),
    ]);
    renderOrders([...realOrders, ...dryOrders].sort((a, b) => new Date(b.created_at) - new Date(a.created_at)));
    $("#live-orders-message").textContent = `실제 주문 ${realOrders.length}건 · DRY RUN ${dryOrders.length}건`;
  } catch (error) {
    $("#live-orders-message").textContent = error.message;
  } finally {
    orderLoading = false;
  }
}

$("#live-section-tabs").addEventListener("click", (event) => {
  const button = event.target.closest("[data-live-tab]");
  if (button) {
    selectLiveSection(button.dataset.liveTab);
    refreshActiveLiveSection();
  }
});

$("#live-pin-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("#live-pin");
  try {
    const status = await api("/auth/live-pin/verify", { method: "POST", body: JSON.stringify({ pin: input.value }) });
    $("#live-pin-status").textContent = `인증 완료 · ${new Date(status.authorized_until).toLocaleTimeString("ko-KR")}까지 유효`;
    setLiveAuthButton(true);
    const remaining = new Date(status.authorized_until).getTime() - Date.now();
    if (remaining > 0) setTimeout(() => setLiveAuthButton(false), remaining);
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
    const requestedSection = window.location.hash.slice(1);
    if (["portfolio", "favorites", "scanner", "orders"].includes(requestedSection)) selectLiveSection(requestedSection);
    const pin = await api("/auth/live-pin");
    setLiveAuthButton(pin.authorized);
    if (pin.authorized && pin.authorized_until) {
      const remaining = new Date(pin.authorized_until).getTime() - Date.now();
      if (remaining > 0) setTimeout(() => setLiveAuthButton(false), remaining);
    }
    await refreshActiveLiveSection();
    setInterval(() => { if (activeLiveSection === "portfolio") refreshPortfolio(); }, 10000);
    setInterval(() => { if (activeLiveSection === "favorites") refreshFavorites(); }, 60000);
    setInterval(() => { if (activeLiveSection === "scanner") refreshCandidates(); }, 60000);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) refreshActiveLiveSection();
    });
  } catch (_) { window.location.replace("/login"); }
}

$("#favorite-grid").addEventListener("click", (event) => {
  const button = event.target.closest("[data-favorite-symbol]");
  if (button) toggleFavorite(button);
});

$("#live-orders-body").addEventListener("click", async (event) => {
  const realButton = event.target.closest("[data-real-cancel-order-id]");
  if (realButton) {
    if (!window.confirm("미체결 실제 주문을 토스증권에 취소 요청할까요? 이미 체결된 수량은 취소되지 않습니다.")) return;
    realButton.disabled = true;
    try {
      await api(`/live/orders/real/${encodeURIComponent(realButton.dataset.realCancelOrderId)}/cancel`, { method: "POST" });
      await refreshDryRunOrders();
      $("#live-orders-message").textContent = "토스증권에 실제 주문 취소를 요청했습니다.";
    } catch (error) {
      $("#live-orders-message").textContent = error.message;
      realButton.disabled = false;
    }
    return;
  }
  const button = event.target.closest("[data-cancel-order-id]");
  if (!button) return;
  button.disabled = true;
  try {
    await api(`/live/orders/dry-run/${encodeURIComponent(button.dataset.cancelOrderId)}/cancel`, { method: "POST" });
    await refreshDryRunOrders();
    $("#live-orders-message").textContent = "DRY RUN 주문을 취소했습니다. 실제 자산에는 변화가 없습니다.";
  } catch (error) {
    $("#live-orders-message").textContent = error.message;
    button.disabled = false;
  }
});

$("#delete-today-dry-runs").addEventListener("click", async () => {
  if (!window.confirm("오늘 생성한 DRY RUN 주문과 관련 이벤트 기록을 모두 삭제할까요? 사용자 계정과 설정은 유지됩니다.")) return;
  const button = $("#delete-today-dry-runs");
  button.disabled = true;
  try {
    const result = await api("/live/orders/dry-run/today", { method: "DELETE" });
    await refreshDryRunOrders();
    $("#live-orders-message").textContent = `오늘 테스트 기록 ${result.deleted}건을 삭제했습니다.`;
  } catch (error) {
    $("#live-orders-message").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

initialize();
