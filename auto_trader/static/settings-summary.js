function strategySummaryText(payload) {
  const count=payload.symbols.length || (/^\d{6}$/.test(payload.symbol)?1:0);
  const size=payload.sizing_mode==='AMOUNT'
    ? `종목당 1회 최대 ${Number(payload.order_amount).toLocaleString('ko-KR')}원 (수수료 포함)`
    : `종목당 1회 ${payload.order_quantity}주`;
  return `${payload.execution_mode==='DRY_RUN'?'모의매매':'실제매매'} · ${count}종목 · ${size} · 완료 1분봉 ${payload.short_period}/${payload.long_period}개 평균 · 익절 ${payload.take_profit_rate}% / 손절 ${payload.stop_loss_rate}% · 최대 보유 ${payload.max_holding_days}일 · ${payload.trading_start}–${payload.trading_end} (한국시간) · 재매수 대기 ${payload.cooldown_minutes}분 · ${payload.daily_order_limit===0?'하루 주문 횟수 무제한':`하루 주문 한도 ${payload.daily_order_limit}회`}`;
}
(() => {
  const form=document.querySelector('#strategy-form');
  const summary=document.querySelector('#strategy-input-summary');
  const error=document.querySelector('#strategy-summary-error');
  const long=document.querySelector('#strategy-long');
  const endHour=document.querySelector('[data-time-picker="strategy-end"] [data-time-hour]');
  function render() {
    const payload=strategyPayload();
    const averageError=payload.short_period>=payload.long_period?'장기 이동평균은 단기 이동평균보다 커야 합니다.':'';
    const timeError=payload.trading_start>=payload.trading_end?'운영 종료는 시작보다 늦어야 합니다.':'';
    long.setCustomValidity(averageError);
    endHour.setCustomValidity(timeError);
    long.setAttribute('aria-invalid',String(Boolean(averageError)));
    endHour.setAttribute('aria-invalid',String(Boolean(timeError)));
    error.textContent=[averageError,timeError].filter(Boolean).join(' ');
    error.hidden=!error.textContent;
    summary.textContent=strategySummaryText(payload);
  }
  form.addEventListener('input',render);
  form.addEventListener('change',render);
  // Programmatic preset/edit/reset flows all update the target chips.
  new MutationObserver(render).observe(document.querySelector('#strategy-target-list'),{childList:true});
  render();
})();
