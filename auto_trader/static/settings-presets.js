(() => {
  const budget = document.querySelector('#preset-budget');
  const status = document.querySelector('#preset-status');
  const preview = document.querySelector('#preset-preview');
  const buttons = [...document.querySelectorAll('[data-default-preset]')];
  let sequence = 0, draft = null, previewButton = null, metadataVersion = 0;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function renderRefreshInfo(info){
    document.querySelector('#preset-refresh-state').textContent=info.current?'이번 주 데이터 사용 중':'첫 미리보기에서 갱신';
    document.querySelector('#preset-generated-at').textContent=info.generated_at?new Date(info.generated_at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'}):'아직 생성되지 않았습니다';
    document.querySelector('#preset-next-refresh').textContent=info.next_refresh_on;
    document.querySelector('#preset-refresh-explanation').textContent=info.current?'다음 주 첫 미리보기에서 후보 데이터를 갱신합니다. 정해진 시각에 예약 갱신하지 않습니다.':'이번 주 첫 미리보기에서 후보 데이터를 갱신합니다. 표시된 생성 시각은 이전 데이터 기준일 수 있습니다.';
  }
  async function loadRefreshInfo(){
    const version=metadataVersion;
    try{const info=await api('/settings/strategy-preset-status');if(version===metadataVersion)renderRefreshInfo(info);}
    catch(error){if(version===metadataVersion){document.querySelector('#preset-refresh-state').textContent='갱신 정보 조회 실패';document.querySelector('#preset-generated-at').textContent='미리보기 후 확인할 수 있습니다';document.querySelector('#preset-next-refresh').textContent='조회 정보 없음';}}
  }
  loadRefreshInfo();
  function clearDraft() { sequence++; draft=null; preview.close(); preview.hidden=true; preview.innerHTML=''; buttons.forEach(b=>b.disabled=false); }
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
      metadataVersion++;renderRefreshInfo({...result,current:true});
      previewButton=button;
      preview.innerHTML=`<h4>${esc(result.name)} · ${result.targets.length}종목</h4><p>${esc(result.basis)}</p><div class="table-wrap"><table><thead><tr><th>종목</th><th>참고 가격</th><th>예상 수량</th><th>거래대금 순위</th></tr></thead><tbody>${result.targets.map(t=>`<tr><td>${esc(t.stock_name)} <small>${esc(t.symbol)}</small></td><td>${esc(won.format(Number(t.price)))}</td><td>${esc(t.quantity_estimate)}주</td><td>${esc(t.rank)}위</td></tr>`).join('')}</tbody></table></div><p>선택 종목당 한 번씩 매수할 때 예산 합계: ${esc(won.format(Number(result.planned_budget)))} · 계좌 잔액·투자 한도 확인 필요</p><ul>${result.notes.map(n=>`<li>${esc(n)}</li>`).join('')}</ul><small>조회 ${esc(new Date(result.generated_at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'}))} · 장외에는 최근 제공된 시세 사용</small><div class="preset-preview-actions"><button class="button primary" type="button" data-apply-default-preset>이 설정으로 새 전략 만들기</button></div>`;
      preview.innerHTML=`<div class="preset-preview-heading"><strong id="preset-preview-title">프리셋 미리보기</strong><button class="button secondary" type="button" data-close-preset-preview>미리보기 닫기</button></div><p>주간 기준 ${esc(result.week_start)} · 다음 갱신 기준 ${esc(result.next_refresh_on)} (한국시간)<br>마음에 드는 구성은 이름을 바꿔 따로 저장하세요. 저장한 전략은 주간 갱신으로 바뀌지 않습니다.</p>${preview.innerHTML}`;
      preview.hidden=false;
      preview.showModal();
      status.textContent=result.targets.length<3?'조건을 충족한 종목만 표시합니다. 부족한 수를 다른 종목으로 채우지 않습니다.':'종목과 예산을 확인한 뒤 새 전략으로 저장하세요.';
    } catch(error) { if(request===sequence){status.textContent=error.message;draft=null;} }
    finally { if(request===sequence)buttons.forEach(b=>b.disabled=false); }
  }));
  function closePreview(){
    clearDraft();
    status.textContent='미리보기를 닫았습니다. 프리셋 버튼을 누르면 다시 확인할 수 있습니다.';
    previewButton?.focus();
  }
  preview.addEventListener('cancel',event=>{event.preventDefault();closePreview();});
  preview.addEventListener('click',event=>{
    if(event.target===preview){
      const rect=preview.getBoundingClientRect();
      if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom){closePreview();return;}
    }
    if(event.target.closest('[data-close-preset-preview]')){closePreview();return;}
    if(!event.target.closest('[data-apply-default-preset]')||!draft)return;
    if(window.applyDefaultPreset(draft)){clearDraft();document.querySelector('#strategy-form').scrollIntoView({behavior:'smooth',block:'start'});status.textContent='아래 입력창에서 이름을 바꿔 저장하면 내 전략으로 유지됩니다. 저장 후 PAPER에서 선택하세요.';}
  });
})();
