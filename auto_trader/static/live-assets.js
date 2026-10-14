const LiveAssets = (() => {
  let snapshot = null, period = 30;
  const money = new Intl.NumberFormat('ko-KR',{style:'currency',currency:'KRW',maximumFractionDigits:0});
  function allocationRows(data) {
    const entries = data.portfolio.holdings.map(h=>({name:h.name,symbol:h.symbol,value:Number(h.market_value)}));
    entries.push({name:'원화 주문 가능',value:Number(data.buying_power.krw_cash_buying_power)});
    const valid=entries.filter(row=>Number.isFinite(row.value)&&row.value>=0);
    const total=valid.reduce((sum,row)=>sum+row.value,0);
    return valid.sort((a,b)=>b.value-a.value).map(row=>({...row,share:total>0?row.value/total*100:0}));
  }
  function historyPoints(data, days, now=Date.now()) {
    return (data.history||[]).filter(p=>new Date(p.at).getTime()>=now-days*86400000&&Number.isFinite(Number(p.total_assets))).sort((a,b)=>new Date(a.at)-new Date(b.at));
  }
  function addHistoryHover(chart,points,x,y) {
    const svg=chart.querySelector('svg');
    svg.setAttribute('role','group');
    const tooltip=document.createElement('div');tooltip.className='live-history-tooltip';tooltip.hidden=true;
    const date=document.createElement('span'),value=document.createElement('strong');tooltip.append(date,value);chart.append(tooltip);
    const line=document.createElementNS('http://www.w3.org/2000/svg','polyline');
    for(const [key,val] of Object.entries({points:points.map(p=>`${x(p)},${y(Number(p.total_assets))}`).join(' '),fill:'none',stroke:'transparent','stroke-width':24,'pointer-events':'stroke',class:'live-history-line','aria-hidden':'true'}))line.setAttribute(key,val);
    svg.append(line);
    const handlers=[];
    let active=-1;
    for(const point of points){
      const label=new Date(point.at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
      const amount=money.format(Number(point.total_assets));
      const target=document.createElementNS('http://www.w3.org/2000/svg','circle');
      for(const [key,val] of Object.entries({cx:x(point),cy:y(Number(point.total_assets)),r:3,fill:'transparent',class:'live-history-point',tabindex:0,'aria-label':`${label} · ${amount}`}))target.setAttribute(key,String(val));
      const show=()=>{
        if(active>=0)handlers[active].hide();
        active=handlers.findIndex(handler=>handler.target===target);
        date.textContent=label;value.textContent=amount;tooltip.hidden=false;
        const bounds=chart.getBoundingClientRect(),hit=target.getBoundingClientRect();
        const left=Math.max(8,Math.min(bounds.width-tooltip.offsetWidth-8,hit.left+hit.width/2-bounds.left-tooltip.offsetWidth/2));
        const above=hit.top-bounds.top-tooltip.offsetHeight-8;
        tooltip.style.left=`${left}px`;tooltip.style.top=`${Math.max(8,above>=8?above:hit.bottom-bounds.top+8)}px`;
        target.classList.add('is-active');
      };
      const hide=()=>{tooltip.hidden=true;target.classList.remove('is-active');};
      handlers.push({target,show,hide});
      target.addEventListener('pointerenter',show);target.addEventListener('pointerleave',hide);
      target.addEventListener('focus',show);target.addEventListener('blur',hide);target.addEventListener('click',show);
      target.addEventListener('keydown',event=>{if(event.key==='Escape')hide();});
      svg.append(target);
    }
    const showNearest=event=>{
      // Account for SVG viewBox letterboxing at narrow screen widths.
      const bounds=svg.getBoundingClientRect(),scale=Math.min(bounds.width/710,bounds.height/225);
      if(!scale)return;
      const cursorX=(event.clientX-bounds.left-(bounds.width-710*scale)/2)/scale;
      let low=0,high=points.length-1;
      while(low<high){const mid=Math.floor((low+high)/2);if(x(points[mid])<cursorX)low=mid+1;else high=mid;}
      const index=low>0&&Math.abs(x(points[low-1])-cursorX)<=Math.abs(x(points[low])-cursorX)?low-1:low;
      handlers[index].show();
    };
    line.addEventListener('pointermove',showNearest);line.addEventListener('click',showNearest);
    line.addEventListener('pointerleave',()=>{if(active>=0)handlers[active].hide();active=-1;});
  }
  function renderAllocation(container,data) {
    const rows=allocationRows(data).filter(row=>row.value>0);
    if(!rows.length){container.textContent='표시할 자산이 없습니다.';return;}
    const colors=['#aa90d2','#7d91b5','#799d95','#b5a080','#ad8797','#839fa9','#9892b5'];
    const svgNode=(tag,attrs={})=>{
      const node=document.createElementNS('http://www.w3.org/2000/svg',tag);
      for(const [key,value] of Object.entries(attrs))node.setAttribute(key,String(value));
      return node;
    };
    const wrap=document.createElement('div');wrap.className='allocation-pie-wrap';
    const svg=svgNode('svg',{viewBox:'0 0 240 240',class:'allocation-pie',role:'group','aria-label':'자산 구성 비율'});
    const tooltip=document.createElement('div');tooltip.className='allocation-tooltip';tooltip.hidden=true;
    const legend=document.createElement('div');legend.className='allocation-legend';
    const labels=svgNode('g',{'aria-hidden':'true',class:'allocation-pie-labels'});
    const center=svgNode('g',{'aria-hidden':'true',class:'allocation-pie-center'});
    const centerName=svgNode('text',{x:120,y:109,class:'allocation-center-name','text-anchor':'middle'});
    const centerValue=svgNode('text',{x:120,y:133,class:'allocation-center-value','text-anchor':'middle'});
    const total=money.format(rows.reduce((sum,row)=>sum+row.value,0));
    centerName.textContent='구성 합계';centerValue.textContent=total;center.append(centerName,centerValue);
    let angle=-Math.PI/2;
    rows.forEach((row,index)=>{
      const color=row.symbol?colors[index%colors.length]:'#696977';
      const share=`${row.share.toFixed(1)}%`,description=`${row.name} · ${share} · ${money.format(row.value)}`;
      const end=angle+row.share/100*Math.PI*2;
      const point=(a,r)=>[120+r*Math.cos(a),120+r*Math.sin(a)].join(' ');
      const slice=rows.length===1?svgNode('circle',{cx:120,cy:120,r:88,fill:'none',stroke:color,'stroke-width':36}):svgNode('path',{fill:color,d:`M ${point(angle,106)} A 106 106 0 ${row.share>50?1:0} 1 ${point(end,106)} L ${point(end,70)} A 70 70 0 ${row.share>50?1:0} 0 ${point(angle,70)} Z`});
      if(rows.length===1)slice.style.stroke=color;
      slice.setAttribute('class',`allocation-slice${rows.length===1?' allocation-single':''}`);slice.setAttribute('tabindex','0');slice.setAttribute('aria-label',description);
      const item=document.createElement('div');item.className='allocation-legend-item';item.tabIndex=0;item.setAttribute('aria-label',description);
      const swatch=document.createElement('span');swatch.className='allocation-swatch';swatch.style.background=color;
      const name=document.createElement('span');name.className='allocation-legend-name';name.textContent=row.name;name.title=row.name;
      const percentage=document.createElement('strong');percentage.textContent=share;
      item.append(swatch,name,percentage);
      const show=()=>{tooltip.textContent=description;tooltip.hidden=false;centerName.textContent=row.name.length>11?`${row.name.slice(0,10)}…`:row.name;centerValue.textContent=money.format(row.value);slice.classList.add('is-active');item.classList.add('is-active');};
      const hide=()=>{tooltip.hidden=true;centerName.textContent='구성 합계';centerValue.textContent=total;slice.classList.remove('is-active');item.classList.remove('is-active');};
      for(const target of [slice,item]){
        target.addEventListener('pointerenter',show);target.addEventListener('pointerleave',hide);
        target.addEventListener('focus',show);target.addEventListener('blur',hide);
        target.addEventListener('click',show);
        target.addEventListener('keydown',event=>{if(event.key==='Escape')hide();});
      }
      svg.append(slice);legend.append(item);
      // Small slices keep their percentage in the legend to avoid overlapping labels.
      if(row.share>=6){
        const mid=(angle+end)/2,radius=88;
        const label=svgNode('text',{x:120+radius*Math.cos(mid),y:120+radius*Math.sin(mid),'text-anchor':'middle','dominant-baseline':'central'});
        label.textContent=share;labels.append(label);
      }
      angle=end;
    });
    svg.append(labels,center);wrap.append(svg,tooltip);container.append(wrap,legend);
  }
  function render() {
    const chart=document.getElementById('live-asset-chart'),allocation=document.getElementById('live-allocation');
    if(!chart||!allocation||!snapshot)return;
    const hidden=AssetPrivacy.hidden('assets'), holdingsHidden=AssetPrivacy.hidden('holdings')||AssetPrivacy.hidden('krw');
    allocation.replaceChildren();
    if(hidden||holdingsHidden){allocation.textContent='자산 구성이 가려져 있습니다.';}
    else {
      renderAllocation(allocation,snapshot);
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
    const x=p=>last===first?370:60+(new Date(p.at).getTime()-first)/(last-first)*620,y=v=>20+(max-v)/(max-min)*170;
    const path=points.map(p=>`${x(p)},${y(Number(p.total_assets))}`).join(' ');
    // All interpolated values are numeric or generated numeric labels.
    chart.innerHTML=`<svg viewBox="0 0 710 225" role="img" aria-label="수집된 계좌 총자산 이력"><defs><linearGradient id="live-chart-fill" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#b5a0ef" stop-opacity=".2"/><stop offset="1" stop-color="#b5a0ef" stop-opacity="0"/></linearGradient></defs>${[0,1,2,3].map(i=>{const value=min+(max-min)*i/3;return `<line x1="60" x2="680" y1="${y(value)}" y2="${y(value)}" stroke="#30313b"/><text x="50" y="${y(value)+4}" text-anchor="end" fill="#9698a6" font-size="11">${(value/10000).toFixed(0)}만</text>`;}).join('')}<polygon points="60,190 ${path} 680,190" fill="url(#live-chart-fill)"/><polyline points="${path}" fill="none" stroke="#b5a0ef" stroke-width="2"/>${points.map(p=>`<circle cx="${x(p)}" cy="${y(Number(p.total_assets))}" r="3" fill="#c6b3f2"><title>${money.format(Number(p.total_assets))}</title></circle>`).join('')}</svg>`;
    addHistoryHover(chart,points,x,y);
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
