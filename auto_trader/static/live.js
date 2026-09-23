const won = new Intl.NumberFormat("ko-KR", { style: "currency", currency: "KRW", maximumFractionDigits: 0 });
const number = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 4 });
const $ = (selector) => document.querySelector(selector);
let csrfToken = "";
let portfolioLoading = false;

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
  return `<span class="stock-name">${item.name}</span><span class="stock-code">${item.symbol} · ${item.market_country}</span>`;
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

async function refreshPortfolio() {
  if (portfolioLoading || document.hidden) return;
  portfolioLoading = true;
  try {
    const data = await api("/live/portfolio");
    renderPortfolio(data);
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
    setInterval(refreshPortfolio, 5000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshPortfolio(); });
  } catch (_) { window.location.replace("/login"); }
}

initialize();
