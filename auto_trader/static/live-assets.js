const LiveAssets = (() => {
  let snapshot = null, period = 30;
  const money = new Intl.NumberFormat('ko-KR',{style:'currency',currency:'KRW',maximumFractionDigits:0});
  function allocationRows(data) {
    const entries = data.portfolio.holdings.map(h=>({name:h.name,symbol:h.symbol,value:Number(h.market_value)}));
    entries.push({name:'원화 주문 가능',value:Number(data.buying_power.krw_cash_buying_power)});
    const total=entries.reduce((sum,row)=>sum+row.value,0);
    return entries.sort((a,b)=>b.value-a.value).map(row=>({...row,share:total>0?row.value/total*100:0}));
  }
  function historyPoints(data, days, now=Date.now()) {
    return (data.history||[]).filter(p=>new Date(p.at).getTime()>=now-days*86400000&&Number.isFinite(Number(p.total_assets))).sort((a,b)=>new Date(a.at)-new Date(b.at));
  }
  function render() {
    const chart=document.getElementById('live-asset-chart'),allocation=document.getElementById('live-allocation');
    if(!chart||!allocation||!snapshot)return;
    const hidden=AssetPrivacy.hidden('assets'), holdingsHidden=AssetPrivacy.hidden('holdings')||AssetPrivacy.hidden('krw');
    allocation.replaceChildren();
    if(hidden||holdingsHidden){allocation.textContent='자산 구성이 가려져 있습니다.';}
    else {
      for(const row of allocationRows(snapshot)){
        const item=document.createElement('div');item.className='live-allocation-row';item.dataset.cash=String(!row.symbol);
        const name=document.createElement('span');name.textContent=row.name;
        const bar=document.createElement('div');bar.className='allocation-bar';
        const fill=document.createElement('span');fill.style.width=`${Math.max(0,Math.min(100,row.share))}%`;bar.append(fill);
        const value=document.createElement('strong');value.textContent=money.format(row.value);
        item.append(name,bar,value);allocation.append(item);
      }
    }
    chart.replaceChildren();chart.classList.add('is-empty');document.getElementById('live-chart-caption').textContent='';
    if(hidden){chart.textContent='자산 흐름이 가려져 있습니다.';return;}
    if(snapshot.history_error){chart.textContent=snapshot.history_error;return;}
    const points=historyPoints(snapshot,period);
    if(points.length<2){
      chart.textContent=points.length?'자산 이력 수집을 시작했습니다. 다음 날짜의 기록이 쌓이면 그래프를 표시합니다.':'이 기간에 수집된 자산 이력이 없습니다.';
      return;
    }
    const values=points.map(p=>Number(p.total_assets)),low=Math.min(...values),high=Math.max(...values),pad=Math.max((high-low)*.15,Math.abs(high)*.005,1),min=low-pad,max=high+pad;
    chart.classList.remove('is-empty');
    const first=new Date(points[0].at).getTime(),last=new Date(points.at(-1).at).getTime();
    const x=p=>60+(new Date(p.at).getTime()-first)/(last-first)*620,y=v=>20+(max-v)/(max-min)*170;
    const path=points.map(p=>`${x(p)},${y(Number(p.total_assets))}`).join(' ');
    // All interpolated values are numeric or generated numeric labels.
    chart.innerHTML=`<svg viewBox="0 0 710 225" role="img" aria-label="수집된 계좌 총자산 이력"><defs><linearGradient id="live-chart-fill" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#b5a0ef" stop-opacity=".2"/><stop offset="1" stop-color="#b5a0ef" stop-opacity="0"/></linearGradient></defs>${[0,1,2,3].map(i=>{const value=min+(max-min)*i/3;return `<line x1="60" x2="680" y1="${y(value)}" y2="${y(value)}" stroke="#30313b"/><text x="50" y="${y(value)+4}" text-anchor="end" fill="#9698a6" font-size="11">${(value/10000).toFixed(0)}만</text>`;}).join('')}<polygon points="60,190 ${path} 680,190" fill="url(#live-chart-fill)"/><polyline points="${path}" fill="none" stroke="#b5a0ef" stroke-width="2"/>${points.map(p=>`<circle cx="${x(p)}" cy="${y(Number(p.total_assets))}" r="3" fill="#c6b3f2"><title>${money.format(Number(p.total_assets))}</title></circle>`).join('')}</svg>`;
    const date=p=>new Date(p.at).toLocaleDateString('ko-KR',{timeZone:'Asia/Seoul',month:'2-digit',day:'2-digit'});
    document.getElementById('live-chart-caption').textContent=`${date(points[0])} — ${date(points.at(-1))} · ${points.length}일 관측`;
  }
  document.querySelectorAll('[data-asset-period]').forEach(button=>button.addEventListener('click',()=>{
    period=Number(button.dataset.assetPeriod);
    document.querySelectorAll('[data-asset-period]').forEach(b=>{b.classList.toggle('active',b===button);b.setAttribute('aria-pressed',String(b===button));});render();
  }));
  AssetPrivacy.onChange(render);
  return {update:data=>{snapshot=data;render();},error:message=>{
    if(snapshot)return;
    for(const id of ['live-asset-chart','live-allocation']){const node=document.getElementById(id);if(node)node.textContent=message;}
  },allocationRows,historyPoints};
})();
