const won = new Intl.NumberFormat("ko-KR", { style: "currency", currency: "KRW", maximumFractionDigits: 0 });
const integer = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 });
const $ = (selector) => document.querySelector(selector);
let csrfToken = "";
let activePeriod = "1D";
let activeSymbol = "";
let detailSequence = 0;
let activeChartMode = "candle";
let lastDetailData = null;
let lastChartCandles = [];
let loadedNewsSymbol = "";
let loadedCompanySymbol = "";
let loadedLongTermSymbol = "";
let longTermWatchState = null;
let activeDetailSection = "chart";
let activeOrderSide = "BUY";
let retryOrderPreviewAfterPin = false;
const detailCache = new Map();
const detailRequests = new Map();
let approvedOrderPayload = null;
let approvedOrderPreview = null;
let pendingClientOrderId = null;
let liveTradingReady = false;
const periodLabels = {
  "1D": ["1일", "1 DAY"], "1W": ["7일", "7 DAYS"], "1M": ["1개월", "1 MONTH"],
  "3M": ["3개월", "3 MONTHS"], "1Y": ["1년", "1 YEAR"],
};
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
})[character]);

function isCorporateStock(data) {
  return String(data?.security_type || "").toUpperCase() === "STOCK";
}

function securityTypeLabel(data) {
  return isCorporateStock(data) && data.is_common_share ? "보통주" : (data.security_type || "종목");
}

function renumberDetailTabs() {
  [...document.querySelectorAll("[data-detail-tab]")]
    .filter((button) => !button.hidden)
    .forEach((button, index) => { button.querySelector("span").textContent = String(index + 1).padStart(2, "0"); });
}

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = !["GET", "HEAD", "OPTIONS"].includes(method) && csrfToken ? { "X-CSRF-Token": csrfToken } : {};
  const response = await fetch(path, { ...options, headers: { "Content-Type": "application/json", ...headers, ...(options.headers || {}) } });
  if (response.status === 401) { window.location.replace("/login"); throw new Error("로그인이 필요합니다."); }
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: "요청에 실패했습니다." }));
    const requestError = new Error(error.detail || "요청에 실패했습니다.");
    requestError.status = response.status;
    throw requestError;
  }
  return response.status === 204 ? null : response.json();
}

function compactKrw(value) {
  if (value == null) return "-";
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "-";
  if (Math.abs(amount) >= 1_0000_0000_0000) return `${(amount / 1_0000_0000_0000).toFixed(1)}조원`;
  if (Math.abs(amount) >= 1_0000_0000) return `${(amount / 1_0000_0000).toFixed(0)}억원`;
  if (Math.abs(amount) >= 1_0000) return `${(amount / 1_0000).toFixed(0)}만원`;
  return won.format(amount);
}

function aggregateCandles(candles, maxPoints) {
  if (candles.length <= maxPoints) return candles;
  const groupSize = Math.ceil(candles.length / maxPoints);
  const aggregated = [];
  for (let index = 0; index < candles.length; index += groupSize) {
    const group = candles.slice(index, index + groupSize);
    aggregated.push({
      timestamp: group.at(-1).timestamp,
      open_price: group[0].open_price,
      high_price: Math.max(...group.map((item) => Number(item.high_price))),
      low_price: Math.min(...group.map((item) => Number(item.low_price))),
      close_price: group.at(-1).close_price,
      volume: group.reduce((sum, item) => sum + Number(item.volume), 0),
    });
  }
  return aggregated;
}

function movingAveragePath(candles, period, xAt, priceY) {
  if (candles.length < period) return "";
  const points = [];
  for (let index = period - 1; index < candles.length; index += 1) {
    const window = candles.slice(index - period + 1, index + 1);
    const average = window.reduce((sum, item) => sum + Number(item.close_price), 0) / period;
    points.push([xAt(index), priceY(average)]);
  }
  return points.map(([x, y], index) => `${index ? "L" : "M"} ${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
}

function chartSvg(candles) {
  const candleLimits = { "1D": 60, "1W": 65, "1M": 68, "3M": 80, "1Y": 120 };
  const chartCandles = activeChartMode === "candle"
    ? aggregateCandles(candles, candleLimits[activePeriod] || 90)
    : candles;
  lastChartCandles = chartCandles;
  const closes = chartCandles.map((item) => Number(item.close_price));
  if (closes.length < 2) return `<div class="empty">표시할 일봉 데이터가 부족합니다.</div>`;
  const width = 1100;
  const height = 400;
  const paddingLeft = 18;
  const paddingRight = 82;
  const priceTop = 16;
  const priceBottom = 292;
  const volumeTop = 320;
  const volumeBottom = 390;
  const plotWidth = width - paddingLeft - paddingRight;
  const lows = chartCandles.map((item) => Number(item.low_price));
  const highs = chartCandles.map((item) => Number(item.high_price));
  const volumes = chartCandles.map((item) => Number(item.volume));
  const min = Math.min(...lows);
  const max = Math.max(...highs);
  const range = max - min || 1;
  const maxVolume = Math.max(...volumes, 1);
  const xAt = (index) => paddingLeft + ((index + 0.5) / chartCandles.length) * plotWidth;
  const priceY = (value) => priceBottom - ((value - min) / range) * (priceBottom - priceTop);
  const coordinatePairs = closes.map((value, index) => {
    const x = xAt(index);
    const y = priceY(value);
    return [x.toFixed(1), y.toFixed(1)];
  });
  const coordinates = coordinatePairs.map(([x, y]) => `${x},${y}`).join(" ");
  const trend = closes.at(-1) >= closes[0] ? "up" : "down";
  const color = trend === "up" ? "#ff716b" : "#67a0ff";
  const [endX, endY] = coordinatePairs.at(-1);
  const area = `${paddingLeft},${priceBottom} ${coordinates} ${paddingLeft + plotWidth},${priceBottom}`;
  const grid = [0, 0.25, 0.5, 0.75, 1].map((ratio) => {
    const y = priceTop + (priceBottom - priceTop) * ratio;
    const price = max - range * ratio;
    return `<line class="grid-line" x1="${paddingLeft}" y1="${y}" x2="${paddingLeft + plotWidth}" y2="${y}"></line><text class="chart-axis-label" x="${width - 6}" y="${y + 4}" text-anchor="end">${integer.format(price)}</text>`;
  }).join("");
  const candleSlot = plotWidth / chartCandles.length;
  const widthRatio = chartCandles.length <= 35 ? .84 : chartCandles.length <= 80 ? .86 : .68;
  const maxCandleWidth = chartCandles.length <= 35 ? 38 : chartCandles.length <= 80 ? 16 : 9;
  const candleWidth = Math.max(2, Math.min(maxCandleWidth, candleSlot * widthRatio));
  const averageRange = chartCandles.slice(-14).reduce(
    (sum, item) => sum + Number(item.high_price) - Number(item.low_price), 0
  ) / Math.min(14, chartCandles.length);
  const gaps = [];
  for (let index = 1; index < chartCandles.length; index += 1) {
    const previous = chartCandles[index - 1];
    const current = chartCandles[index];
    const previousHigh = Number(previous.high_price);
    const previousLow = Number(previous.low_price);
    const currentHigh = Number(current.high_price);
    const currentLow = Number(current.low_price);
    if (currentLow > previousHigh && currentLow - previousHigh >= averageRange * .3) {
      gaps.push({ index, from: previousHigh, to: currentLow, direction: "up" });
    } else if (currentHigh < previousLow && previousLow - currentHigh >= averageRange * .3) {
      gaps.push({ index, from: currentHigh, to: previousLow, direction: "down" });
    }
  }
  const gapShapes = gaps.slice(-6).map((gap) => {
    const x = xAt(gap.index) - candleSlot * .42;
    const y = priceY(Math.max(gap.from, gap.to));
    const gapHeight = Math.max(3, Math.abs(priceY(gap.from) - priceY(gap.to)));
    return `<g class="price-gap ${gap.direction}"><rect x="${x}" y="${y}" width="${candleSlot * .84}" height="${gapHeight}"></rect><line x1="${x}" y1="${priceY(gap.from)}" x2="${x + candleSlot * .84}" y2="${priceY(gap.from)}"></line><line x1="${x}" y1="${priceY(gap.to)}" x2="${x + candleSlot * .84}" y2="${priceY(gap.to)}"></line></g>`;
  }).join("");
  const candleShapes = chartCandles.map((item, index) => {
    const open = Number(item.open_price);
    const high = Number(item.high_price);
    const low = Number(item.low_price);
    const close = Number(item.close_price);
    const x = xAt(index);
    const openY = priceY(open);
    const closeY = priceY(close);
    const bodyY = Math.min(openY, closeY);
    const bodyHeight = Math.max(1.4, Math.abs(closeY - openY));
    const direction = close >= open ? "rise" : "fall";
    return `<g class="chart-candle ${direction}"><line x1="${x}" y1="${priceY(high)}" x2="${x}" y2="${priceY(low)}"></line><rect x="${x - candleWidth / 2}" y="${bodyY}" width="${candleWidth}" height="${bodyHeight}"></rect></g>`;
  }).join("");
  const volumeBars = chartCandles.map((item, index) => {
    const open = Number(item.open_price);
    const close = Number(item.close_price);
    const volumeHeight = (Number(item.volume) / maxVolume) * (volumeBottom - volumeTop);
    return `<rect class="volume-bar ${close >= open ? "rise" : "fall"}" x="${xAt(index) - candleWidth / 2}" y="${volumeBottom - volumeHeight}" width="${candleWidth}" height="${Math.max(1, volumeHeight)}"></rect>`;
  }).join("");
  const priceChart = activeChartMode === "candle"
    ? `${gapShapes}${candleShapes}`
    : `<polygon class="chart-area" points="${area}" fill="url(#detail-chart-fill)"></polygon><polyline class="chart-line" points="${coordinates}"></polyline><circle class="chart-end" cx="${endX}" cy="${endY}" r="4"></circle>`;
  const ma5Path = movingAveragePath(chartCandles, 5, xAt, priceY);
  const ma20Path = movingAveragePath(chartCandles, 20, xAt, priceY);
  const movingAverages = `${ma5Path ? `<path class="moving-average ma5" d="${ma5Path}"></path>` : ""}${ma20Path ? `<path class="moving-average ma20" d="${ma20Path}"></path>` : ""}`;
  const lastClose = Number(chartCandles.at(-1).close_price);
  const lastPriceY = priceY(lastClose);
  const lastPriceLine = `<line class="last-price-line" x1="${paddingLeft}" y1="${lastPriceY}" x2="${paddingLeft + plotWidth}" y2="${lastPriceY}"></line><g class="last-price-label"><rect x="${paddingLeft + plotWidth + 4}" y="${lastPriceY - 10}" width="74" height="20" rx="4"></rect><text x="${width - 5}" y="${lastPriceY + 4}" text-anchor="end">${integer.format(lastClose)}</text></g>`;
  return `<svg class="stock-detail-chart ${trend}" viewBox="0 0 ${width} ${height}" role="img" aria-label="선택 기간 가격과 거래량 차트" data-plot-left="${paddingLeft}" data-plot-width="${plotWidth}" data-price-top="${priceTop}" data-price-bottom="${priceBottom}" data-price-min="${min}" data-price-max="${max}">
    <defs><linearGradient id="detail-chart-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".28"></stop><stop offset="1" stop-color="${color}" stop-opacity="0"></stop></linearGradient></defs>
    ${grid}${priceChart}${movingAverages}${lastPriceLine}<line class="volume-divider" x1="${paddingLeft}" y1="${volumeTop - 10}" x2="${paddingLeft + plotWidth}" y2="${volumeTop - 10}"></line>${volumeBars}<text class="chart-volume-label" x="${paddingLeft}" y="${volumeTop - 16}">거래량</text>
    <g class="chart-crosshair" visibility="hidden"><line class="crosshair-x" x1="0" y1="${priceTop}" x2="0" y2="${volumeBottom}"></line><line class="crosshair-y" x1="${paddingLeft}" y1="0" x2="${paddingLeft + plotWidth}" y2="0"></line><circle class="crosshair-point" cx="0" cy="0" r="4"></circle></g>
  </svg><div class="chart-indicator-legend"><span class="ma5">MA 5</span><span class="ma20">MA 20</span>${gaps.length ? `<span class="gap">음영 · 가격 갭 ${gaps.length}개</span>` : ""}</div><div class="chart-tooltip" id="chart-tooltip" hidden></div>`;
}

function formatTimestamp(value) {
  const options = activePeriod === "1D"
    ? { hour: "2-digit", minute: "2-digit" }
    : { year: "numeric", month: "2-digit", day: "2-digit" };
  return new Date(value).toLocaleString("ko-KR", options);
}

function formatTooltipTimestamp(value) {
  const intraday = ["1D", "1W", "1M"].includes(activePeriod);
  return new Date(value).toLocaleString("ko-KR", intraday
    ? { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }
    : { year: "numeric", month: "2-digit", day: "2-digit" });
}

function formatNewsTime(value) {
  const date = new Date(value);
  const elapsedMinutes = Math.max(0, Math.floor((Date.now() - date.getTime()) / 60000));
  if (elapsedMinutes < 60) return `${Math.max(1, elapsedMinutes)}분 전`;
  if (elapsedMinutes < 1440) return `${Math.floor(elapsedMinutes / 60)}시간 전`;
  return date.toLocaleDateString("ko-KR", { month: "short", day: "numeric" });
}

function longTermFactorCard(factor) {
  const ratio = factor.score == null ? 0 : Math.round(factor.score / factor.max_score * 100);
  const tone = factor.score == null ? "unknown" : ratio >= 75 ? "good" : ratio >= 45 ? "normal" : "watch";
  return `<article class="panel long-term-factor ${tone}"><div><span>${escapeHtml(factor.label)}</span><strong>${factor.score == null ? "-" : `${factor.score}/${factor.max_score}`}</strong></div><div class="factor-meter"><i style="width:${ratio}%"></i></div><b>${escapeHtml(factor.value)}</b><small>${escapeHtml(factor.detail)}</small><em>${escapeHtml(factor.status)}</em></article>`;
}

function longTermPriceChart(candles) {
  const points = candles.map((item) => Number(item.close_price)).filter(Number.isFinite);
  if (points.length < 2) return '<div class="empty">표시할 1년 가격 데이터가 없습니다.</div>';
  const width = 620, height = 230, padding = 18;
  const min = Math.min(...points), max = Math.max(...points), range = max - min || 1;
  const coords = points.map((value, index) => [
    padding + index / (points.length - 1) * (width - padding * 2),
    height - padding - (value - min) / range * (height - padding * 2),
  ]);
  const path = coords.map(([x, y], index) => `${index ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `${path} L${coords.at(-1)[0].toFixed(1)},${height - padding} L${padding},${height - padding} Z`;
  const rising = points.at(-1) >= points[0];
  const color = rising ? "#ff716b" : "#67a0ff";
  return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="최근 1년 가격 흐름" class="${rising ? "up" : "down"}"><defs><linearGradient id="detail-long-term-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".28"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs><path class="area" style="fill:url(#detail-long-term-fill)" d="${area}"/><path class="line" d="${path}"/></svg>`;
}

function renderLongTermAnalysis(data) {
  $("#detail-long-term-loading").hidden = true;
  $("#detail-long-term-content").hidden = false;
  $("#detail-long-term-rank").textContent = data.rank || "-";
  $("#detail-long-term-score").textContent = data.overall_score == null ? "-" : data.overall_score;
  $("#detail-long-term-grade").textContent = data.grade;
  $("#detail-long-term-summary").textContent = data.summary;
  $("#detail-long-term-fiscal").textContent = data.fiscal_year ? `${data.fiscal_year}년 사업보고서 기준` : "공시 기준연도 없음";
  $("#detail-long-term-industry").textContent = data.industry_name || "업종 정보 없음";
  $("#detail-long-term-price-context").textContent = `${new Date(data.generated_at).toLocaleDateString("ko-KR")} 분석 기준`;
  $("#detail-long-term-factors").innerHTML = data.factors.map(longTermFactorCard).join("");
  $("#detail-long-term-opportunities").innerHTML = data.opportunities.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  $("#detail-long-term-risks").innerHTML = data.risks.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  $("#detail-long-term-data-message").textContent = data.data_message;
  $("#detail-long-term-financials").innerHTML = data.financial_history.length
    ? data.financial_history.map((item) => `<tr><td>${escapeHtml(item.year)}</td><td>${compactKrw(item.revenue)}</td><td class="${Number(item.operating_income) < 0 ? "negative" : ""}">${compactKrw(item.operating_income)}</td><td class="${Number(item.net_income) < 0 ? "negative" : ""}">${compactKrw(item.net_income)}</td></tr>`).join("")
    : '<tr><td colspan="4" class="empty">표시할 연간 재무정보가 없습니다.</td></tr>';
  const rate = data.price_return_1y_percent == null ? "-" : `${Number(data.price_return_1y_percent) >= 0 ? "+" : ""}${Number(data.price_return_1y_percent).toFixed(1)}%`;
  const drawdown = data.max_drawdown_1y_percent == null ? "-" : `${Number(data.max_drawdown_1y_percent).toFixed(1)}%`;
  $("#detail-long-term-price-caption").textContent = `기간 수익률 ${rate} · 관측 최대 낙폭 ${drawdown}`;
  $("#detail-long-term-chart").innerHTML = longTermPriceChart(data.candles);
  $("#detail-long-term-message").textContent = `점수는 하루 동안 저장해 재사용합니다 · ${new Date(data.generated_at).toLocaleString("ko-KR")} 기준`;
}

async function loadLongTermAnalysis(symbol) {
  if (loadedLongTermSymbol === symbol) return;
  loadedLongTermSymbol = symbol;
  $("#detail-long-term-loading").hidden = false;
  $("#detail-long-term-content").hidden = true;
  $("#detail-long-term-message").textContent = "";
  try {
    renderLongTermAnalysis(await api(`/research/long-term/${encodeURIComponent(symbol)}`));
    await loadLongTermWatchState(symbol);
  } catch (error) {
    loadedLongTermSymbol = "";
    $("#detail-long-term-loading").hidden = true;
    $("#detail-long-term-message").textContent = error.message;
  }
}

function renderLongTermWatchButton() {
  const button = $("#detail-long-term-watch-button");
  if (!longTermWatchState) {
    button.textContent = "내 관찰 후보에 추가";
    button.classList.remove("active");
    button.disabled = false;
    return;
  }
  if (longTermWatchState.added_manually) {
    button.textContent = "내 관찰 후보에서 제거";
    button.classList.add("active");
    button.disabled = false;
  } else if (longTermWatchState.is_favorite) {
    button.textContent = "♥ 관심종목으로 관찰 중";
    button.classList.add("active");
    button.disabled = true;
  }
}

async function loadLongTermWatchState(symbol) {
  const items = await api("/research/long-term/watchlist");
  longTermWatchState = items.find((item) => item.symbol === symbol) || null;
  renderLongTermWatchButton();
}

function updateOrderEstimate() {
  const quantity = Math.max(0, Number($("#order-quantity").value) || 0);
  const marketOrder = $("#order-type").value === "MARKET";
  const conditionalOrder = $("#order-mode").value === "SINGLE";
  const price = marketOrder ? Number(lastDetailData?.price || 0) : Math.max(0, Number($("#order-price").value) || 0);
  $("#order-price").disabled = marketOrder;
  $("#order-estimated-amount").textContent = price && quantity ? won.format(price * quantity) : "-";
  $("#order-estimate-note").textContent = marketOrder ? "현재가 기준 예상 · 실제 체결가와 다를 수 있음" : "지정가 기준 · 수수료·세금 제외";
  document.querySelectorAll(".conditional-order-field").forEach((field) => { field.hidden = !conditionalOrder; });
  const sideLabel = activeOrderSide === "BUY" ? "매수" : "매도";
  const typeLabel = marketOrder ? "시장가" : "지정가";
  if (conditionalOrder) {
    const triggerPrice = Math.max(0, Number($("#order-trigger-price").value) || 0);
    const triggerLabel = triggerPrice ? won.format(triggerPrice) : "감시 가격";
    $("#order-dry-run-preview").textContent = `${triggerLabel} 도달 시 ${typeLabel} ${sideLabel} · ${integer.format(quantity)}주 · 실제 주문`;
  } else {
    $("#order-dry-run-preview").textContent = `일반 ${typeLabel} ${sideLabel} · ${integer.format(quantity)}주 · 실제 주문`;
  }
}

function defaultConditionalExpiry() {
  const expiry = new Date();
  expiry.setDate(expiry.getDate() + 30);
  return `${expiry.getFullYear()}-${String(expiry.getMonth() + 1).padStart(2, "0")}-${String(expiry.getDate()).padStart(2, "0")}`;
}

function currentOrderPayload() {
  const conditionalOrder = $("#order-mode").value === "SINGLE";
  const marketOrder = $("#order-type").value === "MARKET";
  return {
    symbol: activeSymbol,
    side: activeOrderSide,
    mode: conditionalOrder ? "SINGLE" : "STANDARD",
    order_type: marketOrder ? "MARKET" : "LIMIT",
    quantity: Number($("#order-quantity").value),
    order_price: marketOrder ? null : Number($("#order-price").value),
    trigger_price: conditionalOrder ? Number($("#order-trigger-price").value) : null,
    expire_date: conditionalOrder ? $("#order-expire-date").value : null,
  };
}

function formatOrderSide(side) { return side === "BUY" ? "매수" : "매도"; }

async function loadDryRunHistory() {
  const list = $("#dry-run-history-list");
  try {
    const orders = await api("/live/orders/dry-run");
    list.replaceChildren();
    if (!orders.length) {
      const empty = document.createElement("span");
      empty.textContent = "아직 저장된 DRY RUN 주문이 없습니다.";
      list.append(empty);
      return;
    }
    orders.slice(0, 3).forEach((order) => {
      const row = document.createElement("div");
      const title = document.createElement("strong");
      const meta = document.createElement("small");
      title.textContent = `${order.stock_name} · ${formatOrderSide(order.side)} ${integer.format(Number(order.quantity))}주`;
      meta.textContent = `${won.format(Number(order.estimated_amount))} · ${new Date(order.created_at).toLocaleString("ko-KR")}`;
      row.append(title, meta);
      list.append(row);
    });
  } catch (error) {
    list.textContent = error.message;
  }
}

function financialKrw(value) {
  const amount = Number(String(value ?? "").replaceAll(",", ""));
  if (!Number.isFinite(amount)) return value || "-";
  const absolute = Math.abs(amount);
  const sign = amount < 0 ? "-" : "";
  if (absolute >= 1_0000_0000_0000) return `${sign}${(absolute / 1_0000_0000_0000).toFixed(1)}조원`;
  if (absolute >= 1_0000_0000) return `${sign}${(absolute / 1_0000_0000).toFixed(1)}억원`;
  if (absolute >= 1_0000) return `${sign}${(absolute / 1_0000).toFixed(1)}만원`;
  return `${integer.format(amount)}원`;
}

function selectDetailSection(section) {
  activeDetailSection = section;
  document.querySelectorAll("[data-detail-tab]").forEach((button) => {
    const active = button.dataset.detailTab === section;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll("[data-detail-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.detailPanel !== section;
  });
  if (section === "news" && lastDetailData) loadStockNews(lastDetailData.name, lastDetailData.symbol);
  if (section === "company" && lastDetailData && isCorporateStock(lastDetailData)) loadCompanyProfile(lastDetailData.symbol);
  if (section === "long-term" && lastDetailData?.is_common_share && isCorporateStock(lastDetailData)) loadLongTermAnalysis(lastDetailData.symbol);
  const url = new URL(window.location.href);
  url.hash = section;
  window.history.replaceState(null, "", url);
}

function formatListDate(value) {
  if (!value) return "-";
  const date = new Date(`${value}T00:00:00+09:00`);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString("ko-KR", {
    year: "numeric", month: "long", day: "numeric",
  });
}

function formatCompactDate(value) {
  if (!value || value.length !== 8) return value || "-";
  return `${value.slice(0, 4)}.${value.slice(4, 6)}.${value.slice(6, 8)}`;
}

function renderMetricGrid(selector, metrics, emptyMessage, financial = false) {
  const grid = $(selector);
  grid.replaceChildren();
  if (!metrics.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = emptyMessage;
    grid.append(empty);
    return;
  }
  metrics.forEach((metric) => {
    const card = document.createElement("div");
    const label = document.createElement("span");
    const value = document.createElement("strong");
    const previous = document.createElement("small");
    label.textContent = metric.label;
    value.textContent = financial ? financialKrw(metric.value) : metric.value;
    if (financial && ["영업이익", "당기순이익"].includes(metric.label)) {
      const amount = Number(String(metric.value).replaceAll(",", ""));
      value.className = amount > 0 ? "positive" : amount < 0 ? "negative" : "neutral";
    }
    const previousText = metric.previous_value && metric.previous_value !== "-"
      ? `전년 ${financial ? financialKrw(metric.previous_value) : metric.previous_value}` : "최근 사업보고서 기준";
    const rate = metric.change_rate_percent == null ? null : Number(metric.change_rate_percent);
    previous.textContent = rate == null ? previousText : `${previousText} · 전년 대비 ${rate > 0 ? "+" : ""}${rate.toFixed(1)}%`;
    if (rate != null) previous.className = rate > 0 ? "positive" : rate < 0 ? "negative" : "neutral";
    card.append(label, value, previous);
    grid.append(card);
  });
}

function renderFinancialHistory(history) {
  const container = $("#company-financial-history");
  container.replaceChildren();
  if (!history.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "비교할 최근 3개년 재무정보가 없습니다.";
    container.append(empty);
    return;
  }
  const series = [
    ["revenue", "매출액"], ["operating_income", "영업이익"], ["net_income", "당기순이익"],
  ];
  const values = history.flatMap((item) => series.map(([key]) => Math.abs(Number(item[key] || 0))));
  const max = Math.max(...values, 1);
  const heading = document.createElement("div");
  heading.className = "financial-history-heading";
  const title = document.createElement("strong");
  title.textContent = "최근 3개년 실적 추이";
  const legend = document.createElement("div");
  legend.className = "financial-history-legend";
  series.forEach(([key, label]) => {
    const item = document.createElement("span");
    item.className = key;
    item.textContent = label;
    legend.append(item);
  });
  heading.append(title, legend);
  const chart = document.createElement("div");
  chart.className = "financial-history-chart";
  history.forEach((period) => {
    const group = document.createElement("div");
    group.className = "financial-year-group";
    const bars = document.createElement("div");
    bars.className = "financial-year-bars";
    series.forEach(([key, label]) => {
      const amount = Number(period[key] || 0);
      const bar = document.createElement("div");
      bar.className = `financial-bar ${key}${amount < 0 ? " negative" : ""}`;
      bar.style.height = `${Math.max(amount ? 4 : 0, Math.abs(amount) / max * 100)}%`;
      bar.title = `${period.year}년 ${label} ${financialKrw(amount)}`;
      bar.setAttribute("aria-label", bar.title);
      bars.append(bar);
    });
    const year = document.createElement("strong");
    year.textContent = `${period.year}년`;
    group.append(bars, year);
    chart.append(group);
  });
  container.append(heading, chart);
}

function renderCompanyProfile(data) {
  $("#company-info-note").textContent = data.message;
  $("#company-ceo").textContent = data.ceo_name || "-";
  $("#company-industry").textContent = data.industry_name || "-";
  $("#company-industry-code").textContent = data.industry_code ? `한국표준산업분류 ${data.industry_code}` : "OpenDART 분류 기준";
  $("#company-established").textContent = formatCompactDate(data.established_date);
  $("#company-fiscal-month").textContent = data.fiscal_month ? `${data.fiscal_month}월` : "-";
  renderMetricGrid("#company-financial-grid", data.financials, data.configured ? "표시할 연간 재무 주요계정이 없습니다." : "OpenDART API 키를 설정하면 재무정보가 표시됩니다.", true);
  renderFinancialHistory(data.financial_history || []);
  renderMetricGrid("#company-dividend-grid", data.dividends, data.configured ? "최근 사업보고서에서 배당정보를 찾지 못했습니다." : "OpenDART API 키를 설정하면 배당정보가 표시됩니다.");
  const profile = $("#company-profile-detail");
  profile.replaceChildren();
  [["법인명", data.corporation_name], ["주소", data.address]].forEach(([labelText, valueText]) => {
    const card = document.createElement("div");
    const label = document.createElement("span");
    const value = document.createElement("strong");
    label.textContent = labelText;
    value.textContent = valueText || "-";
    card.append(label, value);
    profile.append(card);
  });
  if (data.homepage) {
    const card = document.createElement("div");
    const label = document.createElement("span");
    const link = document.createElement("a");
    label.textContent = "홈페이지";
    link.href = /^https?:\/\//i.test(data.homepage) ? data.homepage : `https://${data.homepage}`;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = data.homepage;
    card.append(label, link);
    profile.append(card);
  }
  const disclosures = $("#company-disclosure-list");
  disclosures.replaceChildren();
  if (!data.disclosures.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = data.configured ? "최근 공시가 없습니다." : "OpenDART API 키를 설정하면 최근 공시가 표시됩니다.";
    disclosures.append(empty);
  } else {
    data.disclosures.forEach((item) => {
      const link = document.createElement("a");
      link.className = "company-disclosure-item";
      link.href = `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${encodeURIComponent(item.receipt_no)}`;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      const title = document.createElement("strong");
      const date = document.createElement("span");
      title.textContent = item.title;
      date.textContent = formatCompactDate(item.receipt_date);
      link.append(title, date);
      disclosures.append(link);
    });
  }
}

async function loadCompanyProfile(symbol) {
  if (loadedCompanySymbol === symbol) return;
  loadedCompanySymbol = symbol;
  $("#company-info-note").textContent = "OpenDART 기업정보를 확인하고 있습니다.";
  try {
    renderCompanyProfile(await api(`/live/stocks/${encodeURIComponent(symbol)}/company`));
  } catch (error) {
    loadedCompanySymbol = "";
    $("#company-info-note").textContent = error.message;
  }
}

function renderStockNews(data) {
  $("#stock-news-overview").textContent = data.overview;
  const keywords = $("#stock-news-keywords");
  keywords.replaceChildren(...data.key_topics.map((topic) => {
    const span = document.createElement("span");
    span.textContent = `# ${topic}`;
    return span;
  }));
  const list = $("#stock-news-list");
  list.replaceChildren();
  if (!data.articles.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "이 종목의 최근 기사를 찾지 못했습니다.";
    list.append(empty);
    return;
  }
  data.articles.slice(0, 6).forEach((article) => {
    const link = document.createElement("a");
    link.className = "stock-news-item";
    link.href = article.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    const title = document.createElement("strong");
    title.textContent = article.title;
    const meta = document.createElement("span");
    meta.textContent = `${article.source} · ${formatNewsTime(article.published_at)}`;
    link.append(title, meta);
    list.append(link);
  });
}

async function loadStockNews(name, symbol) {
  if (loadedNewsSymbol === symbol) return;
  loadedNewsSymbol = symbol;
  $("#stock-news-more").href = `/news?q=${encodeURIComponent(name)}`;
  try {
    renderStockNews(await api(`/research/news?q=${encodeURIComponent(name)}&limit=8`));
  } catch (error) {
    $("#stock-news-overview").textContent = error.message;
    $("#stock-news-list").innerHTML = '<div class="empty"></div>';
    $("#stock-news-list .empty").textContent = "관련 뉴스를 불러오지 못했습니다.";
  }
}

function updateChartHover(event) {
  const chart = $("#detail-chart");
  const svg = chart.querySelector(".stock-detail-chart");
  const tooltip = $("#chart-tooltip");
  const crosshair = svg?.querySelector(".chart-crosshair");
  if (!svg || !tooltip || !crosshair || !lastChartCandles.length) return;
  const svgPoint = svg.createSVGPoint();
  svgPoint.x = event.clientX;
  svgPoint.y = event.clientY;
  const screenMatrix = svg.getScreenCTM();
  if (!screenMatrix) return;
  const viewPoint = svgPoint.matrixTransform(screenMatrix.inverse());
  const viewX = viewPoint.x;
  const plotLeft = Number(svg.dataset.plotLeft);
  const plotWidth = Number(svg.dataset.plotWidth);
  if (viewX < plotLeft || viewX > plotLeft + plotWidth) {
    tooltip.hidden = true;
    crosshair.setAttribute("visibility", "hidden");
    return;
  }
  const index = Math.max(0, Math.min(lastChartCandles.length - 1,
    Math.round(((viewX - plotLeft) / plotWidth) * lastChartCandles.length - .5)));
  const candle = lastChartCandles[index];
  const previous = lastChartCandles[Math.max(0, index - 1)];
  const close = Number(candle.close_price);
  const previousClose = Number(previous.close_price);
  const rate = previousClose ? (close / previousClose - 1) * 100 : 0;
  const gapRate = previousClose ? (Number(candle.open_price) / previousClose - 1) * 100 : 0;
  const isPriceGap = index > 0 && (
    Number(candle.low_price) > Number(previous.high_price)
    || Number(candle.high_price) < Number(previous.low_price)
  );
  const x = plotLeft + ((index + .5) / lastChartCandles.length) * plotWidth;
  const min = Number(svg.dataset.priceMin);
  const max = Number(svg.dataset.priceMax);
  const priceTop = Number(svg.dataset.priceTop);
  const priceBottom = Number(svg.dataset.priceBottom);
  const y = priceBottom - ((close - min) / (max - min || 1)) * (priceBottom - priceTop);
  const rateClass = rate > 0 ? "positive" : rate < 0 ? "negative" : "neutral";
  tooltip.innerHTML = `<strong>${formatTooltipTimestamp(candle.timestamp)}</strong><div><span>종가</span><b>${won.format(close)}</b></div><div><span>등락률</span><b class="${rateClass}">${rate > 0 ? "+" : ""}${rate.toFixed(2)}%</b></div>${isPriceGap ? `<div><span>가격 갭</span><b class="${gapRate > 0 ? "positive" : "negative"}">${gapRate > 0 ? "+" : ""}${gapRate.toFixed(2)}%</b></div>` : ""}<div class="tooltip-ohlc"><span>시 ${won.format(Number(candle.open_price))}</span><span>고 ${won.format(Number(candle.high_price))}</span><span>저 ${won.format(Number(candle.low_price))}</span></div><small>거래량 ${integer.format(Number(candle.volume))}주</small>`;
  tooltip.hidden = false;
  const chartRect = chart.getBoundingClientRect();
  const pointerX = event.clientX - chartRect.left;
  const pointerY = event.clientY - chartRect.top;
  const tooltipWidth = 218;
  tooltip.style.left = `${pointerX + tooltipWidth + 24 > chartRect.width ? pointerX - tooltipWidth - 12 : pointerX + 14}px`;
  tooltip.style.top = `${Math.max(8, Math.min(chartRect.height - 156, pointerY - 30))}px`;
  crosshair.setAttribute("visibility", "visible");
  crosshair.querySelector(".crosshair-x").setAttribute("x1", x);
  crosshair.querySelector(".crosshair-x").setAttribute("x2", x);
  crosshair.querySelector(".crosshair-y").setAttribute("y1", y);
  crosshair.querySelector(".crosshair-y").setAttribute("y2", y);
  crosshair.querySelector(".crosshair-point").setAttribute("cx", x);
  crosshair.querySelector(".crosshair-point").setAttribute("cy", y);
}

function clearChartHover() {
  const tooltip = $("#chart-tooltip");
  if (tooltip) tooltip.hidden = true;
  $("#detail-chart .chart-crosshair")?.setAttribute("visibility", "hidden");
}

function renderDetail(data) {
  lastDetailData = data;
  document.title = `${data.name} · 종목 대시보드`;
  $("#detail-market").textContent = `${data.market} · ${securityTypeLabel(data)}`;
  $("#detail-name").textContent = data.name;
  $("#detail-symbol").textContent = data.symbol;
  $("#order-stock-name").textContent = data.name;
  $("#order-stock-symbol").textContent = data.symbol;
  $("#detail-price").textContent = data.price == null ? "-" : won.format(Number(data.price));
  if (data.price != null && !$("#order-price").dataset.edited) $("#order-price").value = Math.round(Number(data.price));
  updateOrderEstimate();
  const rate = data.change_rate_percent == null ? null : Number(data.change_rate_percent);
  $("#detail-change").textContent = rate == null ? "-" : `${rate > 0 ? "+" : ""}${rate.toFixed(2)}%`;
  $("#detail-change").className = rate == null || rate === 0 ? "neutral" : rate > 0 ? "positive" : "negative";
  $("#detail-type").textContent = data.trading_amount_rank == null ? data.market : `거래대금 ${data.trading_amount_rank}위`;
  $("#company-english-name").textContent = data.english_name || "-";
  $("#company-market-type").textContent = `${data.market} · ${securityTypeLabel(data)}`;
  $("#company-list-date").textContent = formatListDate(data.list_date);
  $("#company-market-cap").textContent = compactKrw(data.market_cap);
  $("#company-shares").textContent = data.shares_outstanding == null ? "-" : `${integer.format(Number(data.shares_outstanding))}주`;
  $("#company-isin").textContent = data.isin_code || "-";
  $("#company-listing-status").textContent = data.listing_status === "ACTIVE" ? "정상 상장" : (data.listing_status || "상태 미확인");
  $("#company-krx-status").textContent = data.krx_trading_suspended == null
    ? "확인 불가" : data.krx_trading_suspended ? "거래 정지" : "정상 거래";
  $("#company-krx-status").className = data.krx_trading_suspended ? "negative" : "positive";
  $("#company-nxt-status").textContent = data.nxt_supported == null
    ? "확인 불가" : data.nxt_supported ? (data.nxt_trading_suspended ? "지원 · 거래 정지" : "지원") : "미지원";
  const corporateStock = isCorporateStock(data);
  document.querySelectorAll("[data-corporate-only]").forEach((element) => { element.hidden = !corporateStock; });
  const longTermEligible = corporateStock && data.is_common_share;
  document.querySelectorAll("[data-long-term-only]").forEach((element) => { element.hidden = !longTermEligible; });
  renumberDetailTabs();
  if (!longTermEligible && activeDetailSection === "long-term") selectDetailSection("company");
  if (!corporateStock) {
    document.querySelectorAll("[data-company-tab]").forEach((item) => item.classList.toggle("active", item.dataset.companyTab === "overview"));
    document.querySelectorAll("[data-company-panel]").forEach((panel) => { panel.hidden = panel.dataset.companyPanel !== "overview"; });
    $("#company-info-note").textContent = `${data.security_type || "해당 상품"}은 기업 공시 대상 정보 대신 종목 기본정보만 제공합니다.`;
  }
  const [periodLabel, periodKicker] = periodLabels[activePeriod];
  $("#detail-high-label").textContent = `${periodLabel} 최고가`;
  $("#detail-low-label").textContent = `${periodLabel} 최저가`;
  $("#detail-period-kicker").textContent = periodKicker;
  const highs = data.candles.map((item) => Number(item.high_price));
  const lows = data.candles.map((item) => Number(item.low_price));
  $("#detail-high").textContent = highs.length ? won.format(Math.max(...highs)) : "-";
  $("#detail-low").textContent = lows.length ? won.format(Math.min(...lows)) : "-";
  $("#detail-chart").innerHTML = chartSvg(data.candles);
  if (data.candles.length) {
    const first = data.candles[0];
    const last = data.candles.at(-1);
    $("#detail-price-date").textContent = `${formatTimestamp(last.timestamp)} 기준`;
    $("#chart-start").textContent = formatTimestamp(first.timestamp);
    $("#chart-end").textContent = formatTimestamp(last.timestamp);
    $("#chart-range").textContent = `${periodLabel} · ${won.format(Math.min(...lows))}–${won.format(Math.max(...highs))}`;
    $("#metric-open").textContent = won.format(Number(last.open_price));
    $("#metric-high").textContent = won.format(Number(last.high_price));
    $("#metric-low").textContent = won.format(Number(last.low_price));
    $("#metric-close").textContent = won.format(Number(last.close_price));
  }
  $("#metric-market-cap").textContent = compactKrw(data.market_cap);
  $("#metric-trading-amount").textContent = compactKrw(data.trading_amount);
  $("#metric-volume").textContent = data.trading_volume == null ? "-" : `${integer.format(Number(data.trading_volume))}주`;
  $("#metric-shares").textContent = data.shares_outstanding == null ? "-" : `${integer.format(Number(data.shares_outstanding))}주`;
  $("#detail-message").textContent = activePeriod === "1D"
    ? "1분봉 추세선이며 체결 시점에 따라 실제 가격과 차이가 날 수 있습니다."
    : "일봉 종가 추세선이며 장중 가격과 차이가 날 수 있습니다.";
  if (activeDetailSection === "news") loadStockNews(data.name, data.symbol);
  if (activeDetailSection === "company" && corporateStock) loadCompanyProfile(data.symbol);
  if (activeDetailSection === "long-term" && longTermEligible) loadLongTermAnalysis(data.symbol);
}

async function loadDetail(period) {
  activePeriod = period;
  const sequence = ++detailSequence;
  document.querySelectorAll("[data-period]").forEach((button) => button.classList.toggle("active", button.dataset.period === period));
  if (detailCache.has(period)) {
    renderDetail(detailCache.get(period));
    return;
  }
  $("#detail-message").textContent = `${periodLabels[period][0]} 차트를 불러오는 중입니다.`;
  $("#detail-chart").classList.add("loading");
  $("#detail-chart").setAttribute("aria-busy", "true");
  try {
    const data = await fetchDetail(period);
    if (sequence === detailSequence) renderDetail(data);
  } catch (error) {
    if (sequence !== detailSequence) return;
    $("#detail-message").textContent = error.message;
    $("#detail-chart").innerHTML = `<div class="empty"></div>`;
    $("#detail-chart .empty").textContent = error.message;
  } finally {
    if (sequence === detailSequence) {
      $("#detail-chart").classList.remove("loading");
      $("#detail-chart").removeAttribute("aria-busy");
    }
  }
}

function fetchDetail(period) {
  if (detailCache.has(period)) return Promise.resolve(detailCache.get(period));
  if (detailRequests.has(period)) return detailRequests.get(period);
  const request = api(`/live/stocks/${encodeURIComponent(activeSymbol)}/detail?period=${period}`)
    .then((data) => {
      detailCache.set(period, data);
      return data;
    })
    .finally(() => detailRequests.delete(period));
  detailRequests.set(period, request);
  return request;
}

function prefetchNearbyPeriods() {
  const start = async () => {
    // 사용자가 자주 누르는 순서대로 준비한다. 순차 실행해 토스 호출 한도를 보호한다.
    for (const period of ["1W", "1M", "1Y", "3M"]) {
      try { await fetchDetail(period); } catch (_) { /* 전환 시 화면에서 다시 시도한다. */ }
    }
  };
  if ("requestIdleCallback" in window) window.requestIdleCallback(start, { timeout: 1200 });
  else window.setTimeout(start, 350);
}

$("#logout-button").addEventListener("click", async () => {
  try { await api("/auth/logout", { method: "POST" }); } finally { window.location.replace("/login"); }
});
$("#detail-period-tabs").addEventListener("click", (event) => {
  const button = event.target.closest("[data-period]");
  if (button && button.dataset.period !== activePeriod) loadDetail(button.dataset.period);
});
$("#detail-chart-modes").addEventListener("click", (event) => {
  const button = event.target.closest("[data-chart-mode]");
  if (!button || button.dataset.chartMode === activeChartMode) return;
  activeChartMode = button.dataset.chartMode;
  document.querySelectorAll("[data-chart-mode]").forEach((item) => item.classList.toggle("active", item === button));
  if (lastDetailData) $("#detail-chart").innerHTML = chartSvg(lastDetailData.candles);
});
$("#detail-chart").addEventListener("pointermove", updateChartHover);
$("#detail-chart").addEventListener("pointerleave", clearChartHover);
$("#order-side-tabs").addEventListener("click", (event) => {
  const button = event.target.closest("[data-order-side]");
  if (!button) return;
  activeOrderSide = button.dataset.orderSide;
  document.querySelectorAll("[data-order-side]").forEach((item) => {
    item.classList.toggle("active", item === button);
    item.classList.toggle("buy", item === button && activeOrderSide === "BUY");
    item.classList.toggle("sell", item === button && activeOrderSide === "SELL");
  });
  updateOrderEstimate();
});
$("#order-mode").addEventListener("change", updateOrderEstimate);
$("#order-type").addEventListener("change", updateOrderEstimate);
$("#order-quantity").addEventListener("input", updateOrderEstimate);
$("#order-trigger-price").addEventListener("input", updateOrderEstimate);
$("#order-price").addEventListener("input", () => {
  $("#order-price").dataset.edited = "true";
  updateOrderEstimate();
});
$("#live-order-preview-button").addEventListener("click", async () => {
  const button = $("#live-order-preview-button");
  const result = $("#order-preview-result");
  const payload = currentOrderPayload();
  approvedOrderPayload = null;
  approvedOrderPreview = null;
  button.disabled = true;
  button.textContent = "검토 중...";
  result.hidden = false;
  result.className = "order-preview-result loading";
  result.textContent = "계좌와 위험 한도를 확인하고 있습니다.";
  try {
    const preview = await api("/live/orders/preview", {
      method: "POST",
      body: JSON.stringify({
        ...payload,
      }),
    });
    result.className = `order-preview-result ${preview.approved ? "approved" : "rejected"}`;
    result.replaceChildren();
    const heading = document.createElement("strong");
    heading.textContent = preview.approved ? "실제 주문 사전 검토 통과" : "실제 주문 사전 검토 보류";
    const message = document.createElement("p");
    message.textContent = preview.message;
    const checks = document.createElement("ul");
    preview.checks.forEach((check) => {
      const item = document.createElement("li");
      item.className = check.passed ? "passed" : "failed";
      item.textContent = `${check.passed ? "✓" : "!"} ${check.message}`;
      checks.append(item);
    });
    result.append(heading, message, checks);
    if (preview.approved) {
      approvedOrderPayload = payload;
      approvedOrderPreview = preview;
      const confirmButton = document.createElement("button");
      confirmButton.type = "button";
      confirmButton.className = "button order-final-confirm-button";
      confirmButton.textContent = "최종 확인으로 이동";
      confirmButton.addEventListener("click", openDryRunConfirmModal);
      result.append(confirmButton);
    }
  } catch (error) {
    if (error.status === 403 && error.message.includes("LIVE PIN")) {
      retryOrderPreviewAfterPin = true;
      $("#detail-live-auth-modal").classList.remove("hidden");
      $("#detail-live-pin").focus();
      result.hidden = true;
      return;
    }
    result.className = "order-preview-result rejected";
    result.textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = "주문 검토";
  }
});
function openDryRunConfirmModal() {
  if (!approvedOrderPayload || !approvedOrderPreview) return;
  pendingClientOrderId = crypto.randomUUID();
  const summary = $("#dry-run-confirm-summary");
  const values = [
    ["종목", `${approvedOrderPreview.name} (${approvedOrderPreview.symbol})`],
    ["매매", formatOrderSide(approvedOrderPayload.side)],
    ["방식", approvedOrderPayload.mode === "SINGLE" ? "목표가 도달 주문" : "일반 주문"],
    ["호가", approvedOrderPayload.order_type === "MARKET" ? "시장가" : `지정가 ${won.format(approvedOrderPayload.order_price)}`],
    ["수량", `${integer.format(approvedOrderPayload.quantity)}주`],
    ["예상 금액", won.format(Number(approvedOrderPreview.estimated_amount))],
  ];
  if (approvedOrderPayload.mode === "SINGLE") {
    values.push(["감시 가격", won.format(approvedOrderPayload.trigger_price)], ["만료일", approvedOrderPayload.expire_date]);
  }
  summary.replaceChildren(...values.map(([labelText, valueText]) => {
    const row = document.createElement("div");
    const label = document.createElement("span");
    const value = document.createElement("strong");
    label.textContent = labelText;
    value.textContent = valueText;
    row.append(label, value);
    return row;
  }));
  const realEligible = liveTradingReady && approvedOrderPayload.mode === "STANDARD"
    && approvedOrderPayload.order_type === "LIMIT" && Number.isInteger(Number(approvedOrderPayload.quantity));
  $("#dry-run-confirm-status").textContent = realEligible ? "실제 주문 전송 전 마지막 확인 단계입니다." : "실제 주문 안전 잠금이 해제되어야 전송할 수 있습니다.";
  $("#real-order-confirmation").classList.remove("hidden");
  $("#confirm-real-order").classList.remove("hidden");
  $("#real-order-confirm-checkbox").checked = false;
  $("#real-order-confirm-checkbox").disabled = !realEligible;
  $("#confirm-real-order").disabled = true;
  $("#dry-run-confirm-modal").classList.remove("hidden");
}

function closeDryRunConfirmModal() {
  $("#dry-run-confirm-modal").classList.add("hidden");
}
$("#close-dry-run-confirm").addEventListener("click", closeDryRunConfirmModal);
$("#cancel-dry-run-confirm").addEventListener("click", closeDryRunConfirmModal);
$("#dry-run-confirm-modal").addEventListener("click", (event) => {
  if (event.target === $("#dry-run-confirm-modal")) closeDryRunConfirmModal();
});
$("#confirm-dry-run-order").addEventListener("click", async () => {
  if (!approvedOrderPayload || !pendingClientOrderId) return;
  const button = $("#confirm-dry-run-order");
  const status = $("#dry-run-confirm-status");
  button.disabled = true;
  status.textContent = "안전 검사를 다시 실행하고 기록하고 있습니다.";
  try {
    const order = await api("/live/orders/dry-run", {
      method: "POST",
      body: JSON.stringify({ ...approvedOrderPayload, client_order_id: pendingClientOrderId }),
    });
    closeDryRunConfirmModal();
    const result = $("#order-preview-result");
    result.hidden = false;
    result.className = "order-preview-result approved";
    result.textContent = `DRY RUN 주문 #${order.id}을 저장했습니다. 실제 주문은 전송되지 않았습니다.`;
    approvedOrderPayload = null;
    approvedOrderPreview = null;
    pendingClientOrderId = null;
    await loadDryRunHistory();
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
$("#real-order-confirm-checkbox").addEventListener("change", (event) => {
  $("#confirm-real-order").disabled = !event.target.checked;
});
$("#confirm-real-order").addEventListener("click", async () => {
  if (!approvedOrderPayload || !pendingClientOrderId || !$("#real-order-confirm-checkbox").checked) return;
  if (!window.confirm("실제 토스증권 계좌로 지정가 주문을 전송할까요? 전송 후 체결되면 자산이 변합니다.")) return;
  const button = $("#confirm-real-order");
  const status = $("#dry-run-confirm-status");
  button.disabled = true;
  $("#confirm-dry-run-order").disabled = true;
  status.textContent = "안전 검사를 다시 실행하고 실제 주문을 한 번만 전송하고 있습니다.";
  try {
    const order = await api("/live/orders/real", {
      method: "POST",
      body: JSON.stringify({
        ...approvedOrderPayload, client_order_id: pendingClientOrderId, confirmation: "실제 주문",
      }),
    });
    closeDryRunConfirmModal();
    const result = $("#order-preview-result");
    result.hidden = false;
    result.className = "order-preview-result approved";
    result.textContent = `실제 주문 #${order.id} · 토스 상태 ${order.broker_status || order.status}`;
    approvedOrderPayload = null;
    approvedOrderPreview = null;
    pendingClientOrderId = null;
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = !$("#real-order-confirm-checkbox").checked;
    $("#confirm-dry-run-order").disabled = false;
  }
});
function closeDetailPinModal() {
  $("#detail-live-auth-modal").classList.add("hidden");
  $("#detail-live-pin").value = "";
}
$("#close-detail-live-modal").addEventListener("click", closeDetailPinModal);
$("#detail-live-auth-modal").addEventListener("click", (event) => {
  if (event.target === $("#detail-live-auth-modal")) closeDetailPinModal();
});
$("#detail-live-pin-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const status = $("#detail-live-pin-status");
  status.textContent = "PIN을 확인하고 있습니다.";
  try {
    await api("/auth/live-pin/verify", { method: "POST", body: JSON.stringify({ pin: $("#detail-live-pin").value }) });
    status.textContent = "인증되었습니다.";
    closeDetailPinModal();
    if (retryOrderPreviewAfterPin) {
      retryOrderPreviewAfterPin = false;
      $("#live-order-preview-button").click();
    }
  } catch (error) {
    status.textContent = error.message;
  }
});
$("#detail-back-button").addEventListener("click", () => {
  const params = new URLSearchParams(window.location.search);
  const source = params.get("from");
  const section = params.get("section");
  if (source === "live") {
    const liveSection = ["portfolio", "favorites", "scanner", "orders"].includes(section) ? section : "portfolio";
    window.location.assign(`/live#${liveSection}`);
    return;
  }
  const referrer = document.referrer;
  if (referrer) {
    try {
      const previous = new URL(referrer);
      if (previous.origin === window.location.origin && previous.pathname !== "/login") {
        window.history.back();
        return;
      }
    } catch (_) { /* 잘못된 referrer는 안전한 기본 경로로 이동한다. */ }
  }
  window.location.assign("/stocks");
});
$("#detail-section-tabs").addEventListener("click", (event) => {
  const button = event.target.closest("[data-detail-tab]");
  if (button) selectDetailSection(button.dataset.detailTab);
});
$("#detail-long-term-watch-button").addEventListener("click", async () => {
  if (!lastDetailData || !lastDetailData.is_common_share || !isCorporateStock(lastDetailData)) return;
  const button = $("#detail-long-term-watch-button");
  button.disabled = true;
  try {
    const remove = Boolean(longTermWatchState?.added_manually);
    await api(`/research/long-term/watchlist/${encodeURIComponent(lastDetailData.symbol)}`, { method: remove ? "DELETE" : "POST" });
    await loadLongTermWatchState(lastDetailData.symbol);
    $("#detail-long-term-message").textContent = remove
      ? "내 관찰 후보에서 제거했습니다." : "내 관찰 후보에 추가했습니다.";
  } catch (error) {
    $("#detail-long-term-message").textContent = error.message;
    button.disabled = false;
  }
});
document.querySelector(".company-category-tabs").addEventListener("click", (event) => {
  const button = event.target.closest("[data-company-tab]");
  if (!button) return;
  document.querySelectorAll("[data-company-tab]").forEach((item) => item.classList.toggle("active", item === button));
  document.querySelectorAll("[data-company-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.companyPanel !== button.dataset.companyTab;
  });
});

async function initialize() {
  try {
    $("#order-expire-date").value = defaultConditionalExpiry();
    const session = await api("/auth/me");
    csrfToken = session.csrf_token;
    $("#current-user").textContent = session.username;
    const risk = await api("/risk");
    $("#order-cash-policy").textContent = "실제 주문 안전 한도 적용";
    $("#order-cash-policy-detail").textContent = `실제 주문은 현금·보유 수량과 1회·종목별·전체 투자 한도, 주문 후 최소 현금 ${Number(risk.settings.min_cash_ratio).toFixed(0)}% 기준을 모두 검사합니다.`;
    const readiness = await api("/live/orders/real/readiness");
    liveTradingReady = readiness.configured && readiness.enabled;
    $("#order-lock-badge").textContent = liveTradingReady ? "LIVE ENABLED" : "LIVE LOCKED";
    $("#live-order-notice").textContent = readiness.message;
    activeSymbol = decodeURIComponent(window.location.pathname.split("/").filter(Boolean).at(-1) || "");
    const requestedSection = window.location.hash.slice(1);
    if (["chart", "company", "long-term", "market", "news"].includes(requestedSection)) selectDetailSection(requestedSection);
    await loadDetail(activePeriod);
    prefetchNearbyPeriods();
  } catch (error) {
    $("#detail-message").textContent = error.message;
    $("#detail-chart").innerHTML = `<div class="empty"></div>`;
    $("#detail-chart .empty").textContent = error.message;
  }
}

initialize();
