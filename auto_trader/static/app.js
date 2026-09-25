const won = new Intl.NumberFormat("ko-KR", { style: "currency", currency: "KRW", maximumFractionDigits: 0 });
const number = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 });

const $ = (selector) => document.querySelector(selector);
let csrfToken = "";

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const securityHeaders = !["GET", "HEAD", "OPTIONS"].includes(method) && csrfToken
    ? { "X-CSRF-Token": csrfToken }
    : {};
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...securityHeaders, ...(options.headers || {}) },
  });
  if (response.status === 401) {
    window.location.replace("/login");
    throw new Error("로그인이 필요합니다.");
  }
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: "요청에 실패했습니다." }));
    throw new Error(error.detail || "요청에 실패했습니다.");
  }
  return response.status === 204 ? null : response.json();
}

function stockCell(name, symbol) {
  return `<span class="stock-name">${name}</span><span class="stock-code">${symbol}</span>`;
}

function renderWatchlist(stocks, quotes, strategy) {
  const quoteMap = new Map(quotes.map((item) => [item.symbol, item]));
  const snapshotMap = new Map(strategy.snapshots.map((item) => [item.symbol, item]));
  $("#watchlist-body").innerHTML = stocks.map((stock) => {
    const quote = quoteMap.get(stock.symbol);
    const snapshot = snapshotMap.get(stock.symbol);
    const trend = snapshot?.trend || "COLLECTING";
    const trendLabel = trend === "ABOVE" ? "단기 우위" : trend === "BELOW" ? "장기 우위" : "수집 중";
    const trendClass = trend === "ABOVE" ? "positive" : trend === "BELOW" ? "negative" : "neutral";
    return `<tr>
      <td>${stockCell(stock.name, stock.symbol)}</td>
      <td>${won.format(Number(quote?.price || 0))}</td>
      <td>${snapshot?.short_average ? number.format(Number(snapshot.short_average)) : "-"}</td>
      <td>${snapshot?.long_average ? number.format(Number(snapshot.long_average)) : "-"}</td>
      <td><span class="trend ${trendClass}">${trendLabel}</span></td>
    </tr>`;
  }).join("");
}

function renderPositions(account) {
  $("#positions-body").innerHTML = account.positions.length ? account.positions.map((item) => {
    const profit = Number(item.unrealized_profit);
    const profitClass = profit > 0 ? "positive" : profit < 0 ? "negative" : "neutral";
    return `<tr>
      <td>${stockCell(item.name, item.symbol)}</td>
      <td>${number.format(item.quantity)}주</td>
      <td>${won.format(Number(item.market_value))}</td>
      <td class="${profitClass}">${won.format(profit)}</td>
    </tr>`;
  }).join("") : `<tr><td class="empty" colspan="4">아직 보유한 종목이 없습니다.</td></tr>`;
}

function renderOrders(orders) {
  $("#orders-body").innerHTML = orders.length ? orders.slice(0, 8).map((item) => `<tr>
    <td class="${item.side === "BUY" ? "buy" : "sell"}">${item.side === "BUY" ? "매수" : "매도"}</td>
    <td>${item.symbol}</td>
    <td>${number.format(item.quantity)}주</td>
    <td>${won.format(Number(item.price))}</td>
    <td>${item.status === "FILLED" ? "체결" : "거절"}</td>
    <td>${item.message || "-"}</td>
  </tr>`).join("") : `<tr><td class="empty" colspan="6">아직 주문 내역이 없습니다.</td></tr>`;
}

function renderStrategy(strategy) {
  const pill = $("#strategy-pill");
  pill.className = "pill";
  if (strategy.emergency_stopped) {
    pill.classList.add("emergency");
    pill.textContent = "긴급 정지";
  } else if (strategy.running) {
    pill.classList.add("running");
    pill.textContent = "실행 중";
  } else {
    pill.classList.add("stopped");
    pill.textContent = "정지됨";
  }
  $("#strategy-description").textContent = strategy.running
    ? `${strategy.interval_seconds}초마다 시세를 갱신하고 있습니다.`
    : "자동매매를 시작하면 가상 시세 수집과 전략 계산이 진행됩니다.";
  $("#tick-count").textContent = number.format(strategy.tick_count);
  $("#strategy-title").textContent = `${strategy.short_period} / ${strategy.long_period} 이동평균 전략`;
  const form = $("#strategy-settings-form");
  if (!form.contains(document.activeElement)) {
    $("#strategy-interval").value = strategy.interval_seconds;
    $("#strategy-short").value = strategy.short_period;
    $("#strategy-long").value = strategy.long_period;
    $("#strategy-quantity").value = strategy.order_quantity;
  }
  form.querySelectorAll("input, button").forEach((element) => { element.disabled = strategy.running; });
}

const riskFields = ["max-order-amount", "max-symbol-amount", "max-total-investment", "min-cash-ratio", "daily-loss-limit", "daily-order-limit", "profit-target"];
const riskKeys = ["max_order_amount", "max_symbol_amount", "max_total_investment", "min_cash_ratio", "daily_loss_limit", "daily_order_limit", "profit_target"];
const riskPresets = {
  CONSERVATIVE: [500000, 1000000, 3000000, 50, 200000, 30, 500000],
  DEFAULT: [1000000, 2000000, 7000000, 30, 500000, 100, 1000000],
};

function fillRiskFields(values) {
  riskFields.forEach((id, index) => { $("#" + id).value = values[index]; });
}

function updateRiskFieldState() {
  const custom = $("#risk-preset").value === "CUSTOM";
  riskFields.forEach((id) => { $("#" + id).disabled = !custom; });
}

function renderRisk(risk) {
  if (!$("#risk-form").contains(document.activeElement)) {
    $("#risk-preset").value = risk.settings.preset;
    riskFields.forEach((id, index) => { $("#" + id).value = risk.settings[riskKeys[index]]; });
    updateRiskFieldState();
  }
  const pill = $("#risk-pill");
  pill.className = `pill ${risk.new_buys_allowed ? "running" : "emergency"}`;
  pill.textContent = risk.new_buys_allowed ? "신규 매수 가능" : "신규 매수 중지";
  const reason = risk.block_reason ? ` · 중지 사유: ${risk.block_reason}` : "";
  $("#risk-usage").textContent = `현재 투자 ${won.format(Number(risk.invested_amount))} · 현금 ${Number(risk.cash_ratio).toFixed(1)}% · 오늘 손익 ${won.format(Number(risk.daily_profit))} · 오늘 체결 ${risk.daily_orders}회${reason}`;
}

async function refresh() {
  try {
    const [health, stocks, quotes, account, orders, strategy, risk] = await Promise.all([
      api("/health"), api("/paper/stocks"), api("/quotes"), api("/account"), api("/orders"), api("/strategy/status"), api("/risk"),
    ]);
    $("#server-dot").classList.toggle("online", health.status === "ok");
    $("#total-asset").textContent = won.format(Number(account.total_asset));
    $("#cash").textContent = won.format(Number(account.cash));
    $("#position-count").textContent = `${account.positions.length}개`;
    $("#updated-at").textContent = `최근 갱신 ${new Date().toLocaleTimeString("ko-KR")}`;
    for (const [id, value] of [["total-profit", account.total_profit], ["realized-profit", account.realized_profit], ["unrealized-profit", account.unrealized_profit]]) {
      const element = $("#" + id);
      const amount = Number(value);
      element.textContent = won.format(amount);
      element.className = amount > 0 ? "positive" : amount < 0 ? "negative" : "neutral";
    }
    $("#return-percent").textContent = account.return_percent == null ? "초기 자금 0원 · 수익률 계산 불가" : `초기 자금 대비 ${Number(account.return_percent).toFixed(2)}%`;
    $("#total-costs").textContent = won.format(Number(account.total_fees) + Number(account.total_taxes));
    $("#cost-breakdown").textContent = `수수료 ${won.format(Number(account.total_fees))} · 세금 ${won.format(Number(account.total_taxes))}`;
    $("#cost-policy").textContent = `모의 계산용 예시: 매수·매도 수수료 ${Number(account.fee_rate) * 100}%, 매도 세금 ${Number(account.sell_tax_rate) * 100}%. 건별 원 미만 절사. 실제 수수료·현행 세율이 아닙니다. 기존 거래 비용은 소급하지 않습니다.`;
    $("#strategy-settings-message").textContent = health.paper_watchlist === "toss_daily_trading_amount"
      ? "감시 종목: 토스 국내 일일 거래대금 상위 10개"
      : "감시 종목: 토스 API를 사용할 수 없어 기본 대표 종목 10개";
    renderStrategy(strategy);
    renderWatchlist(stocks, quotes, strategy);
    renderPositions(account);
    renderOrders(orders);
    renderRisk(risk);

    const select = $("#order-symbol");
    if (!select.options.length) {
      select.innerHTML = stocks.map((stock) => `<option value="${stock.symbol}">${stock.name} (${stock.symbol})</option>`).join("");
    }
  } catch (error) {
    $("#server-dot").classList.remove("online");
    $("#strategy-description").textContent = `서버 연결 오류: ${error.message}`;
  }
}

async function strategyAction(path) {
  const buttons = document.querySelectorAll(".control-actions button");
  buttons.forEach((button) => { button.disabled = true; });
  try {
    await api(path, { method: "POST" });
    await refresh();
  } catch (error) {
    alert(error.message);
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
  }
}

$("#start-button").addEventListener("click", () => strategyAction("/strategy/start"));
$("#stop-button").addEventListener("click", () => strategyAction("/strategy/stop"));
$("#emergency-button").addEventListener("click", () => strategyAction("/strategy/emergency-stop"));

$("#strategy-settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/strategy/settings", { method: "PUT", body: JSON.stringify({
      interval_seconds: Number($("#strategy-interval").value),
      short_period: Number($("#strategy-short").value),
      long_period: Number($("#strategy-long").value),
      order_quantity: Number($("#strategy-quantity").value),
    }) });
    $("#strategy-settings-message").textContent = "전략 설정을 저장했습니다.";
    await refresh();
  } catch (error) { $("#strategy-settings-message").textContent = error.message; }
});

$("#risk-preset").addEventListener("change", async () => {
  const preset = $("#risk-preset").value;
  updateRiskFieldState();
  if (preset === "CUSTOM") {
    $("#risk-message").textContent = "원하는 값을 입력한 뒤 한도 저장을 누르세요.";
    return;
  }
  fillRiskFields(riskPresets[preset]);
  $("#risk-message").textContent = "프리셋을 적용하는 중...";
  try {
    await api("/risk/settings", { method: "PUT", body: JSON.stringify({ preset }) });
    $("#risk-message").textContent = `${preset === "CONSERVATIVE" ? "보수적" : "기본"} 프리셋을 적용했습니다.`;
    await refresh();
  } catch (error) {
    $("#risk-message").textContent = error.message;
    $("#risk-preset").blur();
    await refresh();
  }
});
$("#risk-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const preset = $("#risk-preset").value;
  const payload = { preset };
  if (preset === "CUSTOM") {
    riskFields.forEach((id, index) => { payload[riskKeys[index]] = Number($("#" + id).value); });
  }
  try {
    await api("/risk/settings", { method: "PUT", body: JSON.stringify(payload) });
    $("#risk-message").textContent = "한도를 저장했습니다.";
    await refresh();
  } catch (error) {
    $("#risk-message").textContent = error.message;
  }
});

$("#reset-practice-button").addEventListener("click", async () => {
  const confirmed = window.confirm("자동매매를 중지하고 모의 주문, 보유종목, 손익 기록을 초기화할까요? 관리자 계정과 한도 설정은 유지됩니다.");
  if (!confirmed) return;
  const button = $("#reset-practice-button");
  button.disabled = true;
  try {
    await api("/paper/reset", { method: "DELETE" });
    $("#risk-message").textContent = "오늘 연습 기록을 초기화했습니다.";
    await refresh();
  } catch (error) {
    $("#risk-message").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

$("#logout-button").addEventListener("click", async () => {
  try {
    await api("/auth/logout", { method: "POST" });
  } finally {
    window.location.replace("/login");
  }
});

$("#order-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = $("#order-message");
  message.textContent = "주문 처리 중...";
  try {
    const order = await api("/orders", {
      method: "POST",
      body: JSON.stringify({
        symbol: $("#order-symbol").value,
        side: $("#order-side").value,
        quantity: Number($("#order-quantity").value),
      }),
    });
    message.textContent = `${order.side === "BUY" ? "매수" : "매도"} ${order.quantity}주 · ${order.message}`;
    await refresh();
  } catch (error) {
    message.textContent = error.message;
  }
});

async function initializeDashboard() {
  try {
    const session = await api("/auth/me");
    csrfToken = session.csrf_token;
    $("#current-user").textContent = session.username;
    await refresh();
    setInterval(refresh, 2000);
  } catch (error) {
    if (!window.location.pathname.startsWith("/login")) window.location.replace("/login");
  }
}

initializeDashboard();
