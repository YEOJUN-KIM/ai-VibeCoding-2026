(() => {
  const budget = document.querySelector('#preset-budget');
  const status = document.querySelector('#preset-status');
  const preview = document.querySelector('#preset-preview');
  const buttons = [...document.querySelectorAll('[data-default-preset]')];
  let sequence = 0, draft = null;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function clearDraft() { sequence++; draft=null; preview.hidden=true; preview.innerHTML=''; buttons.forEach(b=>b.disabled=false); }
  budget.addEventListener('input',()=>{clearDraft();status.textContent='예산이 바뀌었습니다. 프리셋을 다시 확인하세요.';});
  buttons.forEach(button=>button.addEventListener('click',async()=>{
    if(!budget.reportValidity())return;
    clearDraft();
    const request=sequence;
    buttons.forEach(b=>b.disabled=true);
    status.textContent='이번 주 후보 데이터와 예산을 확인하고 있습니다.';
    try {
      const result=await api(`/settings/strategy-presets/${button.dataset.defaultPreset}?order_amount=${encodeURIComponent(budget.value)}`);
      if(request!==sequence)return;
      draft=result;
      preview.innerHTML=`<h4>${esc(result.name)} · ${result.targets.length}종목</h4><p>${esc(result.basis)}</p><div class="table-wrap"><table><thead><tr><th>종목</th><th>참고 가격</th><th>예상 수량</th><th>거래대금 순위</th></tr></thead><tbody>${result.targets.map(t=>`<tr><td>${esc(t.stock_name)} <small>${esc(t.symbol)}</small></td><td>${esc(won.format(Number(t.price)))}</td><td>${esc(t.quantity_estimate)}주</td><td>${esc(t.rank)}위</td></tr>`).join('')}</tbody></table></div><p>선택 종목당 한 번씩 매수할 때 예산 합계: ${esc(won.format(Number(result.planned_budget)))} · 계좌 잔액·투자 한도 확인 필요</p><ul>${result.notes.map(n=>`<li>${esc(n)}</li>`).join('')}</ul><small>조회 ${esc(new Date(result.generated_at).toLocaleString('ko-KR'))} · 장외에는 최근 제공된 시세 사용</small><div><button class="button primary" type="button" data-apply-default-preset>이 설정으로 새 전략 만들기</button></div>`;
      preview.innerHTML=`<p>주간 기준 ${esc(result.week_start)} · 다음 갱신 기준 ${esc(result.next_refresh_on)} (한국시간)<br>마음에 드는 구성은 이름을 바꿔 따로 저장하세요. 저장한 전략은 주간 갱신으로 바뀌지 않습니다.</p>${preview.innerHTML}`;
      preview.hidden=false;
      status.textContent=result.targets.length<3?'조건을 충족한 종목만 표시합니다. 부족한 수를 다른 종목으로 채우지 않습니다.':'종목과 예산을 확인한 뒤 새 전략으로 저장하세요.';
    } catch(error) { if(request===sequence){status.textContent=error.message;draft=null;} }
    finally { if(request===sequence)buttons.forEach(b=>b.disabled=false); }
  }));
  preview.addEventListener('click',event=>{
    if(!event.target.closest('[data-apply-default-preset]')||!draft)return;
    if(window.applyDefaultPreset(draft))status.textContent='아래 입력창에서 이름을 바꿔 저장하면 내 전략으로 유지됩니다. 저장 후 PAPER에서 선택하세요.';
  });
})();
