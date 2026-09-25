const won = new Intl.NumberFormat("ko-KR", { style: "currency", currency: "KRW", maximumFractionDigits: 0 });
const integer = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 });
const $ = (selector) => document.querySelector(selector);
let csrfToken = "";
let activePage = 1;
let totalPages = 1;
let loading = false;
let searchTimer = null;
let sparklineSequence = 0;

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

function compactWon(value) {
  if (value == null) return "-";
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "-";
  if (Math.abs(amount) >= 1_0000_0000_0000) return `${(amount / 1_0000_0000_0000).toFixed(1)}조원`;
  if (Math.abs(amount) >= 1_0000_0000) return `${(amount / 1_0000_0000).toFixed(0)}억원`;
  if (Math.abs(amount) >= 1_0000) return `${(amount / 1_0000).toFixed(0)}만원`;
  return won.format(amount);
}

function favoriteButton(item) {
  const label = item.is_favorite ? `${item.name} 관심 종목에서 제거` : `${item.name} 관심 종목에 추가`;
  return `<button class="favorite-button${item.is_favorite ? " active" : ""}" type="button"
    data-favorite-symbol="${escapeHtml(item.symbol)}" data-favorite-active="${item.is_favorite}"
    aria-label="${escapeHtml(label)}" title="${escapeHtml(label)}">${item.is_favorite ? "♥" : "♡"}</button>`;
}

function densifyTrend(points, targetLength = 32) {
  if (points.length < 2 || points.length >= targetLength) return points;
  return Array.from({ length: targetLength }, (_, index) => {
    const position = (index / (targetLength - 1)) * (points.length - 1);
    const left = Math.floor(position);
    const right = Math.min(points.length - 1, left + 1);
    const ratio = position - left;
    return points[left] + (points[right] - points[left]) * ratio;
  });
}

function simplifyTrend(points, targetLength = 48) {
  if (points.length <= targetLength) return points;
  return Array.from({ length: targetLength }, (_, index) => {
    if (index === 0) return points[0];
    if (index === targetLength - 1) return points.at(-1);
    const start = Math.floor((index / targetLength) * points.length);
    const end = Math.max(start + 1, Math.floor(((index + 1) / targetLength) * points.length));
    const group = points.slice(start, end);
    return group.reduce((sum, value) => sum + value, 0) / group.length;
  });
}

function sparklineSvg(values, symbol, period) {
  const sourcePoints = values.map(Number).filter(Number.isFinite);
  const points = period === "1D"
    ? simplifyTrend(sourcePoints, 48)
    : period === "1W" ? densifyTrend(sourcePoints, 48) : sourcePoints;
  if (points.length < 2) return `<span class="sparkline-empty">데이터 없음</span>`;
  const width = 140;
  const height = 38;
  const padding = 3;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const coordinatePairs = points.map((value, index) => {
    const x = padding + (index / (points.length - 1)) * (width - padding * 2);
    const y = height - padding - ((value - min) / range) * (height - padding * 2);
    return [x.toFixed(1), y.toFixed(1)];
  });
  const linePath = coordinatePairs.slice(1).reduce((path, [x, y], index) => {
    const [previousX, previousY] = coordinatePairs[index];
    const middleX = ((Number(previousX) + Number(x)) / 2).toFixed(1);
    return `${path} C ${middleX},${previousY} ${middleX},${y} ${x},${y}`;
  }, `M ${coordinatePairs[0][0]},${coordinatePairs[0][1]}`);
  const trend = sourcePoints.at(-1) >= sourcePoints[0] ? "up" : "down";
  const color = trend === "up" ? "#ff716b" : "#67a0ff";
  const safeId = String(symbol).replace(/[^a-zA-Z0-9]/g, "");
  const gradientId = `spark-fill-${safeId}`;
  const [endX, endY] = coordinatePairs.at(-1);
  const label = `선택 기간 ${trend === "up" ? "상승" : "하락"} 흐름`;
  const areaPath = `${linePath} L ${endX},${height - padding} L ${coordinatePairs[0][0]},${height - padding} Z`;
  return `<svg class="sparkline ${trend}" viewBox="0 0 ${width} ${height}" role="img" aria-label="${label}">
    <defs><linearGradient id="${gradientId}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".34"></stop><stop offset="1" stop-color="${color}" stop-opacity="0"></stop></linearGradient></defs>
    <path class="sparkline-area" d="${areaPath}" fill="url(#${gradientId})"></path>
    <path class="sparkline-line" d="${linePath}"></path>
    <circle class="sparkline-end" cx="${endX}" cy="${endY}" r="2.2"></circle>
  </svg>`;
}

async function loadSparklines(symbols) {
  if (!symbols.length) return;
  const sequence = ++sparklineSequence;
  const period = $("#stock-period").value;
  const chunks = [];
  for (let index = 0; index < symbols.length; index += 5) chunks.push(symbols.slice(index, index + 5));
  let nextChunk = 0;
  const worker = async () => {
    while (nextChunk < chunks.length) {
      const chunk = chunks[nextChunk++];
      try {
        const data = await api(`/live/stocks/sparklines?symbols=${encodeURIComponent(chunk.join(","))}&period=${period}`);
        if (sequence !== sparklineSequence) return;
        for (const symbol of chunk) {
          const container = document.querySelector(`[data-sparkline-symbol="${CSS.escape(symbol)}"]`);
          if (container) container.innerHTML = sparklineSvg(data[symbol] || [], symbol, period);
        }
      } catch (error) {
        if (sequence !== sparklineSequence) return;
        for (const symbol of chunk) {
          const container = document.querySelector(`[data-sparkline-symbol="${CSS.escape(symbol)}"]`);
          if (container) container.innerHTML = `<span class="sparkline-empty">불러오기 실패</span>`;
        }
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(3, chunks.length) }, worker));
}

function renderRows(items) {
  $("#stock-list-body").innerHTML = items.length ? items.map((item) => {
    const rate = item.change_rate_percent == null ? null : Number(item.change_rate_percent);
    const rateClass = rate == null ? "neutral" : rate > 0 ? "positive" : rate < 0 ? "negative" : "neutral";
    const rateText = rate == null ? "-" : `${rate > 0 ? "+" : ""}${rate.toFixed(2)}%`;
    const priceText = item.price == null ? "-" : won.format(Number(item.price));
    return `<tr>
      <td class="favorite-cell">${favoriteButton(item)}</td>
      <td><a class="stock-name stock-name-link" href="/stocks/${encodeURIComponent(item.symbol)}">${escapeHtml(item.name)}</a><span class="stock-code">${escapeHtml(item.symbol)}</span></td>
      <td><span class="market-badge ${item.market === "KOSDAQ" ? "kosdaq" : "kospi"}">${escapeHtml(item.market)}</span></td>
      <td>${priceText}</td><td class="${rateClass}">${rateText}</td>
      <td>${compactWon(item.market_cap)}</td><td>${compactWon(item.trading_amount)}</td>
      <td><div class="sparkline-slot" data-sparkline-symbol="${escapeHtml(item.symbol)}"><span class="sparkline-loading">차트 로딩</span></div></td>
    </tr>`;
  }).join("") : `<tr><td class="empty" colspan="8">조건에 맞는 국내 종목이 없습니다.</td></tr>`;
}

async function loadStocks(page = 1) {
  if (loading) return;
  loading = true;
  $("#stock-list-message").textContent = "국내 종목과 현재가를 불러오는 중입니다.";
  const params = new URLSearchParams({
    q: $("#stock-query").value.trim(), market: $("#stock-market").value,
    security_type: $("#stock-security-type").value, sort: $("#stock-sort").value,
    page: String(page), page_size: "20",
  });
  try {
    const data = await api(`/live/stocks/list?${params}`);
    activePage = data.page;
    totalPages = data.total_pages;
    renderRows(data.results);
    loadSparklines(data.results.map((item) => item.symbol));
    $("#stock-total").textContent = integer.format(data.total);
    $("#stock-page").textContent = `${integer.format(activePage)} / ${integer.format(totalPages)}`;
    $("#stock-first").disabled = activePage <= 1;
    $("#stock-prev").disabled = activePage <= 1;
    $("#stock-next").disabled = activePage >= totalPages;
    $("#stock-last").disabled = activePage >= totalPages;
    $("#stock-list-message").textContent = data.total
      ? `${integer.format(data.total)}개 종목 중 ${integer.format((activePage - 1) * data.page_size + 1)}–${integer.format(Math.min(activePage * data.page_size, data.total))} 표시`
      : "조건에 맞는 국내 종목이 없습니다.";
    $("#market-data-status").textContent = "토스 연결됨";
    $("#server-dot").classList.add("online");
  } catch (error) {
    $("#stock-list-message").textContent = error.message;
    $("#market-data-status").textContent = "연결 오류";
    $("#server-dot").classList.remove("online");
  } finally {
    loading = false;
  }
}

async function toggleFavorite(button) {
  const symbol = button.dataset.favoriteSymbol;
  const active = button.dataset.favoriteActive === "true";
  button.disabled = true;
  try {
    if (active) await api(`/live/favorites/${encodeURIComponent(symbol)}`, { method: "DELETE" });
    else await api("/live/favorites", { method: "POST", body: JSON.stringify({ symbol }) });
    await loadStocks(activePage);
  } catch (error) {
    $("#stock-list-message").textContent = error.message;
    button.disabled = false;
  }
}

$("#stock-filter-form").addEventListener("submit", (event) => { event.preventDefault(); loadStocks(1); });
$("#stock-market").addEventListener("change", () => loadStocks(1));
$("#stock-security-type").addEventListener("change", () => loadStocks(1));
$("#stock-sort").addEventListener("change", () => loadStocks(1));
$("#stock-period").addEventListener("change", () => {
  const symbols = [...document.querySelectorAll("[data-sparkline-symbol]")].map((item) => item.dataset.sparklineSymbol);
  document.querySelectorAll("[data-sparkline-symbol]").forEach((item) => { item.innerHTML = `<span class="sparkline-loading">차트 로딩</span>`; });
  loadSparklines(symbols);
});
$("#stock-query").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadStocks(1), 350);
});
$("#stock-list-body").addEventListener("click", (event) => {
  const button = event.target.closest("[data-favorite-symbol]");
  if (button) toggleFavorite(button);
});
$("#stock-first").addEventListener("click", () => loadStocks(1));
$("#stock-prev").addEventListener("click", () => loadStocks(Math.max(1, activePage - 1)));
$("#stock-next").addEventListener("click", () => loadStocks(Math.min(totalPages, activePage + 1)));
$("#stock-last").addEventListener("click", () => loadStocks(totalPages));
$("#logout-button").addEventListener("click", async () => {
  try { await api("/auth/logout", { method: "POST" }); } finally { window.location.replace("/login"); }
});

async function initialize() {
  try {
    const session = await api("/auth/me");
    csrfToken = session.csrf_token;
    $("#current-user").textContent = session.username;
    await loadStocks(1);
  } catch (error) {
    if (!window.location.pathname.startsWith("/login")) window.location.replace("/login");
  }
}

initialize();
