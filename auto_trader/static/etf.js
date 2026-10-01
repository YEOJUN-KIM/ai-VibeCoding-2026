(() => {
  let page = 1, pages = 1, sequence = 0, loaded = false, loading = false;
  let items = [], detailSequence = 0;
  const compact = value => value == null ? '-' : Math.abs(Number(value)) >= 1e12 ? `${(Number(value)/1e12).toFixed(1)}조원` : `${(Number(value)/1e8).toFixed(1)}억원`;
  function render() {
    $('#etf-body').innerHTML = items.length ? items.map(item => `<tr><td><button type="button" class="favorite-button ${item.is_favorite?'active':''}" data-etf-favorite="${escapeHtml(item.symbol)}" aria-label="${escapeHtml(item.name)} 관심종목 ${item.is_favorite?'제거':'추가'}">${item.is_favorite?'♥':'♡'}</button></td><td><button class="etf-name" type="button" data-etf-detail="${escapeHtml(item.symbol)}">${escapeHtml(item.name)}</button><small class="stock-code">${escapeHtml(item.symbol)} · ${escapeHtml(item.market)}</small></td><td>${item.price==null?'-':won.format(item.price)}</td><td class="${Number(item.change_rate_percent)>0?'positive':'negative'}">${metric(item.change_rate_percent)}</td><td>${compact(item.trading_amount)}</td><td>${compact(item.market_cap)}</td></tr>`).join('') : '<tr><td colspan="6" class="empty">조건에 맞는 ETF가 없습니다.</td></tr>';
    $('#etf-page').textContent = `${page} / ${pages}`;
    $('#etf-prev').disabled = page <= 1;
    $('#etf-next').disabled = page >= pages;
  }
  async function load(force = false) {
    if ((loaded || loading) && !force) return;
    loading = true;
    const request = ++sequence;
    $('#etf-state').textContent = 'ETF를 불러오는 중입니다.';
    $('#etf-prev').disabled = $('#etf-next').disabled = true;
    $('#etf-body').setAttribute('aria-busy','true');
    try {
      const query = new URLSearchParams({security_type:'ETF',page:String(page),page_size:'10',q:$('#etf-query').value.trim(),sort:$('#etf-sort').value});
      const data = await api(`/live/stocks/list?${query}`);
      if (request !== sequence) return;
      items = data.results.filter(item => item.security_type === 'ETF');
      page = data.page; pages = data.total_pages; loaded = true;
      render();
      $('#etf-state').textContent = `국내 상장 ETF ${data.total}개 · 10개씩 표시 · ${new Date().toLocaleTimeString('ko-KR',{hour:'2-digit',minute:'2-digit'})} 조회`;
    } catch (error) {
      if (request !== sequence) return;
      loaded = false; items = []; render(); $('#etf-state').textContent = error.message;
    } finally { if (request === sequence) { loading = false; $('#etf-body').removeAttribute('aria-busy'); } }
  }
  window.loadEtfs = load;
  $('#etf-search-form').addEventListener('submit',event=>{event.preventDefault();page=1;load(true);});
  $('#etf-sort').addEventListener('change',()=>{page=1;load(true);});
  document.querySelectorAll('[data-etf-query]').forEach(button=>button.addEventListener('click',()=>{
    $('#etf-query').value=button.dataset.etfQuery;page=1;load(true);
  }));
  $('#etf-prev').addEventListener('click',()=>{page=Math.max(1,page-1);load(true);});
  $('#etf-next').addEventListener('click',()=>{page=Math.min(pages,page+1);load(true);});
  const dialog=document.createElement('dialog');
  dialog.className='research-dialog';dialog.setAttribute('aria-labelledby','etf-detail-title');
  dialog.innerHTML='<div class="research-popup-header"><div><p class="section-kicker">FOLIO · ETF</p><h2 id="etf-detail-title">ETF 가격 흐름</h2></div><button class="button secondary" data-etf-close>닫기 ×</button></div><div data-etf-content></div><div class="research-popup-actions"><p>총보수·분배금·편입자산은 운용사 자료에서 확인하세요.</p><a class="button primary" data-etf-chart>차트·주문 보기</a></div>';
  document.body.append(dialog);
  let opener;
  dialog.querySelector('[data-etf-close]').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('close',()=>{detailSequence++;document.body.classList.remove('research-popup-open');opener?.focus({preventScroll:true});});
  async function detail(symbol) {
    const request=++detailSequence;opener=document.activeElement;
    dialog.querySelector('#etf-detail-title').textContent=items.find(item=>item.symbol===symbol)?.name || 'ETF 가격 흐름';
    dialog.querySelector('[data-etf-chart]').href=`/stocks/${encodeURIComponent(symbol)}#chart`;
    const content=dialog.querySelector('[data-etf-content]');
    content.innerHTML='<p class="empty">가격 흐름을 불러오는 중입니다.</p>';
    dialog.showModal();dialog.scrollTop=0;document.body.classList.add('research-popup-open');
    try {
      const data=await api(`/live/stocks/${encodeURIComponent(symbol)}/detail?period=1Y`);
      if(request!==detailSequence||!dialog.open)return;
      if(data.security_type!=='ETF')throw new Error('ETF 정보를 확인할 수 없습니다.');
      const rows=data.candles.filter(c=>Number(c.close_price)>0).sort((a,b)=>new Date(a.timestamp)-new Date(b.timestamp));
      const points=rows.map(c=>Number(c.close_price));
      let chart='<p class="empty">가격 데이터가 부족합니다.</p>', stats='기간 수익률 - · 최대 낙폭 -';
      if(points.length>1){
        const low=Math.min(...points),range=Math.max(...points)-low||1;
        let peak=points[0],drawdown=0;for(const price of points){peak=Math.max(peak,price);drawdown=Math.min(drawdown,(price/peak-1)*100);}
        stats=`기간 수익률 ${metric((points.at(-1)/points[0]-1)*100)} · 관측 최대 낙폭 ${metric(drawdown)}`;
        const path=points.map((n,i)=>`${i?'L':'M'}${12+i/(points.length-1)*596},${218-(n-low)/range*206}`).join(' ');
        chart=`<svg viewBox="0 0 620 230" role="img" aria-label="ETF 조회 기간 가격 흐름"><path class="line" d="${path}"/></svg><p class="subtitle">${new Date(rows[0].timestamp).toLocaleDateString('ko-KR')}–${new Date(rows.at(-1).timestamp).toLocaleDateString('ko-KR')} · 분배금 미포함 가격 기준</p>`;
      }
      content.innerHTML=`<div class="etf-detail-metrics"><div><span>현재가</span><strong>${data.price==null?'-':won.format(data.price)}</strong></div><div><span>거래대금</span><strong>${compact(data.trading_amount)}</strong></div><div><span>시가총액</span><strong>${compact(data.market_cap)}</strong></div></div><section class="long-term-detail-surface"><h3>최근 1년 조회 가격 흐름</h3><p class="subtitle">${stats}</p><div class="long-term-chart">${chart}</div></section><p class="subtitle">시가총액은 순자산총액과 다를 수 있습니다. 가격 흐름은 투자 대상이나 상품 구조의 적합성을 평가하는 점수가 아닙니다.</p>`;
    }catch(error){if(request===detailSequence&&dialog.open)content.innerHTML=`<p class="empty">${escapeHtml(error.message)}</p>`;}
  }
  $('#etf-body').addEventListener('click',async event=>{
    const button=event.target.closest('[data-etf-favorite]');
    if(button){
      const item=items.find(item=>item.symbol===button.dataset.etfFavorite);if(!item)return;
      button.disabled=true;
      try{
        if(item.is_favorite)await api(`/live/favorites/${encodeURIComponent(item.symbol)}`,{method:'DELETE'});
        else await api('/live/favorites',{method:'POST',body:JSON.stringify({symbol:item.symbol})});
        item.is_favorite=!item.is_favorite;render();
      }catch(error){$('#etf-state').textContent=error.message;button.disabled=false;}
      return;
    }
    const stock=event.target.closest('[data-etf-detail]');if(stock)detail(stock.dataset.etfDetail);
  });
  if(location.hash==='#etf')load();
})();
