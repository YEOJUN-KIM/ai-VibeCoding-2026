const chartViewport = { count: null, end: null, drag: null };

function chartVisibleRange(candles) {
  const count = Math.min(candles.length, chartViewport.count ?? candles.length);
  let end = candles.length;
  if (chartViewport.end != null) {
    const index = candles.findIndex(candle => Date.parse(candle.timestamp) > chartViewport.end);
    if (index >= 0) end = index;
  }
  end = Math.max(count, Math.min(candles.length, end));
  return { start: Math.max(0, end - count), end, count };
}

function chartVisibleCandles(candles) {
  const range = chartVisibleRange(candles);
  const label = document.querySelector('#chart-viewport-status');
  if (label) label.textContent = range.count < candles.length
    ? `${range.start + 1}–${range.end} / ${candles.length}개 봉${chartViewport.end == null ? ' · 최신' : ''}`
    : `전체 ${candles.length}개 봉`;
  return candles.slice(range.start, range.end);
}

function resetChartViewport() {
  chartViewport.count = null;
  chartViewport.end = null;
  chartViewport.drag = null;
  chartViewport.hover = null;
}

function zoomChartViewport(candles, factor, anchor = 1) {
  if (candles.length < 2) return;
  const range = chartVisibleRange(candles);
  const count = Math.max(Math.min(12, candles.length), Math.min(candles.length, Math.round(range.count * factor)));
  const end = Math.max(count, Math.min(candles.length,
    Math.round(range.start + range.count * anchor + count * (1 - anchor))));
  chartViewport.count = count === candles.length ? null : count;
  chartViewport.end = end === candles.length ? null : Date.parse(candles[end - 1].timestamp);
}

function panChartViewport(candles, offset, initialEnd) {
  const range = chartVisibleRange(candles);
  const end = Math.max(range.count, Math.min(candles.length, initialEnd + offset));
  chartViewport.end = end === candles.length ? null : Date.parse(candles[end - 1].timestamp);
}
