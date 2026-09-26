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
const periodLabels = {
  "1D": ["1일", "1 DAY"], "1W": ["7일", "7 DAYS"], "1M": ["1개월", "1 MONTH"],
  "3M": ["3개월", "3 MONTHS"], "1Y": ["1년", "1 YEAR"],
};

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
  const rect = svg.getBoundingClientRect();
  const viewX = ((event.clientX - rect.left) / rect.width) * 1100;
  const plotLeft = Number(svg.dataset.plotLeft);
  const plotWidth = Number(svg.dataset.plotWidth);
  if (viewX < plotLeft || viewX > plotLeft + plotWidth) {
    tooltip.hidden = true;
    crosshair.setAttribute("visibility", "hidden");
    return;
  }
  const index = Math.max(0, Math.min(lastChartCandles.length - 1,
    Math.floor(((viewX - plotLeft) / plotWidth) * lastChartCandles.length)));
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
  const pointerX = event.clientX - rect.left;
  const tooltipWidth = 218;
  tooltip.style.left = `${pointerX + tooltipWidth + 24 > rect.width ? pointerX - tooltipWidth - 12 : pointerX + 14}px`;
  tooltip.style.top = `${Math.max(8, Math.min(rect.height - 156, event.clientY - rect.top - 30))}px`;
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
  $("#detail-market").textContent = `${data.market} · ${data.is_common_share ? "보통주" : data.security_type}`;
  $("#detail-name").textContent = data.name;
  $("#detail-symbol").textContent = data.symbol;
  $("#detail-price").textContent = data.price == null ? "-" : won.format(Number(data.price));
  const rate = data.change_rate_percent == null ? null : Number(data.change_rate_percent);
  $("#detail-change").textContent = rate == null ? "-" : `${rate > 0 ? "+" : ""}${rate.toFixed(2)}%`;
  $("#detail-change").className = rate == null || rate === 0 ? "neutral" : rate > 0 ? "positive" : "negative";
  $("#detail-type").textContent = data.trading_amount_rank == null ? data.market : `거래대금 ${data.trading_amount_rank}위`;
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
  loadStockNews(data.name, data.symbol);
}

async function loadDetail(period) {
  activePeriod = period;
  const sequence = ++detailSequence;
  document.querySelectorAll("[data-period]").forEach((button) => button.classList.toggle("active", button.dataset.period === period));
  $("#detail-message").textContent = `${periodLabels[period][0]} 차트를 불러오는 중입니다.`;
  try {
    const data = await api(`/live/stocks/${encodeURIComponent(activeSymbol)}/detail?period=${period}`);
    if (sequence === detailSequence) renderDetail(data);
  } catch (error) {
    if (sequence !== detailSequence) return;
    $("#detail-message").textContent = error.message;
    $("#detail-chart").innerHTML = `<div class="empty"></div>`;
    $("#detail-chart .empty").textContent = error.message;
  }
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

async function initialize() {
  try {
    const session = await api("/auth/me");
    csrfToken = session.csrf_token;
    $("#current-user").textContent = session.username;
    activeSymbol = decodeURIComponent(window.location.pathname.split("/").filter(Boolean).at(-1) || "");
    await loadDetail(activePeriod);
  } catch (error) {
    $("#detail-message").textContent = error.message;
    $("#detail-chart").innerHTML = `<div class="empty"></div>`;
    $("#detail-chart .empty").textContent = error.message;
  }
}

initialize();
