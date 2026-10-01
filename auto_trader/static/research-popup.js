(() => {
  const dialog = document.createElement('dialog');
  dialog.className = 'research-dialog';
  dialog.setAttribute('aria-labelledby', 'research-popup-title');
  dialog.innerHTML = `<div class="research-popup-header"><div><p class="section-kicker">FOLIO · RESEARCH</p><h2 id="research-popup-title">기업 분석</h2></div><button class="button secondary" data-report-close aria-label="분석 창 닫기">닫기 ×</button></div><div data-report-body></div><div class="research-popup-actions"><p role="status" data-report-message></p><a class="button secondary" data-report-detail>차트·주문 보기</a><button class="button primary" data-report-watch>관찰 목록에 추가</button></div>`;
  document.body.append(dialog);
  let sequence = 0, symbol = '', opener = null;
  const body = dialog.querySelector('[data-report-body]');
  const watch = dialog.querySelector('[data-report-watch]');
  const message = dialog.querySelector('[data-report-message]');
  const money = value => {
    if (value == null) return '-';
    const n = Number(value), size = Math.abs(n);
    return size >= 1e12 ? `${(n / 1e12).toFixed(1)}조원` : size >= 1e8 ? `${(n / 1e8).toFixed(1)}억원` : won.format(n);
  };
  function render(data) {
    const factors = (data.factors || []).map(f => {
      const ratio = f.score == null ? 0 : Math.max(0, Math.min(100, f.score / f.max_score * 100));
      return `<article class="long-term-factor ${ratio >= 75 ? 'good' : 'normal'}"><div><span>${escapeHtml(f.label)}</span><strong>${f.score == null ? '-' : `${f.score}/${f.max_score}`}</strong></div><div class="factor-meter"><i style="width:${ratio}%"></i></div><b>${escapeHtml(f.value)}</b><small>${escapeHtml(f.detail)}</small><em>${escapeHtml(f.status)}</em></article>`;
    }).join('');
    const list = (items, empty) => (items || []).length ? `<ul class="long-term-reason-list">${items.map(t => `<li>${escapeHtml(t)}</li>`).join('')}</ul>` : `<p class="subtitle">${empty}</p>`;
    const points = (data.candles || []).map(c => Number(c.close_price)).filter(Number.isFinite);
    let chart = '<p class="empty">가격 데이터가 없습니다.</p>';
    if (points.length > 1) {
      const min = Math.min(...points), range = Math.max(...points) - min || 1;
      const path = points.map((n, i) => `${i ? 'L' : 'M'}${(12 + i / (points.length - 1) * 596).toFixed(1)},${(218 - (n - min) / range * 206).toFixed(1)}`).join(' ');
      chart = `<svg viewBox="0 0 620 230" role="img" aria-label="최근 1년 가격 흐름"><path class="line" d="${path}"/></svg>`;
    }
    body.innerHTML = `<div class="research-popup-summary"><div><p class="subtitle">${escapeHtml(data.market)} · ${escapeHtml(data.symbol)} · ${new Date(data.generated_at).toLocaleDateString('ko-KR')} 분석</p><p>${escapeHtml(data.summary)}</p><small>${data.fiscal_year ? `${escapeHtml(data.fiscal_year)}년 사업보고서` : '공시 기준연도 미확인'} · ${escapeHtml(data.industry_name || '업종 미확인')}</small></div><div class="research-popup-grade"><strong>${escapeHtml(data.rank || '-')}</strong><span>${data.overall_score ?? '-'}점</span></div></div><div class="research-popup-factors">${factors}</div><div class="research-popup-grid"><section class="long-term-detail-surface"><h3>긍정적으로 볼 근거</h3>${list(data.opportunities, '추가 긍정 요인이 없습니다.')}</section><section class="long-term-detail-surface"><h3>확인할 위험</h3>${list(data.risks, '추가 정량 위험 신호가 없습니다.')}</section><section class="long-term-detail-surface"><h3>최근 연간 실적</h3><p class="subtitle">${escapeHtml(data.data_message)}</p><div class="table-wrap"><table><thead><tr><th>연도</th><th>매출</th><th>영업이익</th><th>순이익</th></tr></thead><tbody>${(data.financial_history || []).length ? data.financial_history.map(y => `<tr><td>${escapeHtml(y.year)}</td><td>${money(y.revenue)}</td><td>${money(y.operating_income)}</td><td>${money(y.net_income)}</td></tr>`).join('') : '<tr><td colspan="4" class="empty">재무정보가 없습니다.</td></tr>'}</tbody></table></div></section><section class="long-term-detail-surface"><h3>최근 1년 가격 흐름</h3><p class="subtitle">수익률 ${metric(data.price_return_1y_percent)} · 최대 낙폭 ${metric(data.max_drawdown_1y_percent)}</p><div class="long-term-chart">${chart}</div></section></div>`;
  }
  window.openResearchReport = async (code, name) => {
    const request = ++sequence;
    symbol = code;
    opener = document.activeElement;
    dialog.querySelector('#research-popup-title').textContent = name || '기업 분석';
    dialog.querySelector('[data-report-detail]').href = `/stocks/${encodeURIComponent(code)}#chart`;
    dialog.querySelector('[data-report-detail]').textContent = '차트·주문 보기';
    body.innerHTML = '<p class="empty" role="status">기업 분석을 불러오는 중입니다.</p>';
    message.textContent = '';
    watch.disabled = true;
    watch.textContent = '관찰 목록에 추가';
    if (!dialog.open) { dialog.showModal(); document.body.classList.add('research-popup-open'); }
    dialog.scrollTop = 0;
    try {
      const [data, items] = await Promise.all([api(`/research/long-term/${encodeURIComponent(code)}`), api('/research/long-term/watchlist')]);
      if (request !== sequence || !dialog.open) return;
      dialog.querySelector('#research-popup-title').textContent = data.name;
      render(data);
      const existing = items.some(item => item.symbol === code);
      watch.disabled = existing;
      watch.textContent = existing ? '✓ 관찰 중' : '관찰 목록에 추가';
    } catch (error) {
      if (request === sequence && dialog.open) body.innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`;
    }
  };
  dialog.querySelector('[data-report-close]').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => { if (event.target === dialog) { const r = dialog.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close(); } });
  dialog.addEventListener('close', () => { sequence++; document.body.classList.remove('research-popup-open'); if (opener?.isConnected) opener.focus({preventScroll:true}); });
  watch.addEventListener('click', async () => {
    const request = sequence, code = symbol;
    watch.disabled = true;
    try {
      await api(`/research/long-term/watchlist/${encodeURIComponent(code)}`, {method:'POST'});
      await loadMyCandidates();
      if (request !== sequence || !dialog.open) return;
      watch.textContent = '✓ 관찰 중'; message.textContent = '관찰 목록에 추가했습니다.';
    } catch (error) { if (request === sequence && dialog.open) { message.textContent = error.message; watch.disabled = false; } }
  });
})();
