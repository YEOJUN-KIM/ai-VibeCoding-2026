'use strict';
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const won=n=>'₩'+Math.round(n).toLocaleString('ko-KR');
const stocks=[
 {code:'005930',name:'삼성전자',short:'삼성',qty:50,average:198000,price:215600,change:1.24,auto:20,aliases:['삼전','samsung','ㅅㅅㅈㅈ','ㅅㅅ','tt','ttww','tkatjdwjswk']},
 {code:'000660',name:'SK하이닉스',short:'SK',qty:10,average:250000,price:233700,change:-.72,auto:0,aliases:['hynix','skhynix','하이닉스','ㅎㅇㄴㅅ']},
 {code:'035420',name:'NAVER',short:'N',qty:5,average:400000,price:411000,change:2.05,auto:0,aliases:['네이버','ㄴㅇㅂ']},
 {code:'005380',name:'현대차',short:'현대',qty:8,average:150000,price:136000,change:-.36,auto:0,aliases:['현차','hyundai','ㅎㄷㅊ']}
];
let activeTab='portfolio',filter='all',sortDirection=0,period='1개월',hideAll=false,hiddenGroups=new Set(),state='normal',favorites=new Set(['005930','000660','035420']),cancelled=false,toastTimer;
const initialOrders=[{code:'005930',kind:'실제 · 매수 10주',status:'체결 완료',cls:'done',price:198000,qty:10,time:'10.02 10:24',manage:'내역 보기'}, {code:'035420',kind:'실제 · 매수 1주',status:'접수 대기',cls:'pending',price:405000,qty:1,time:'10.02 11:08',manage:'취소'}];
function icons(){ $$('[data-icon]').forEach(el=>el.style.setProperty('--icon',`url("vendor/${el.dataset.icon}.svg")`)); }
function toast(message){clearTimeout(toastTimer);$('#toast').textContent=message;$('#toast').classList.add('visible');toastTimer=setTimeout(()=>$('#toast').classList.remove('visible'),3000);}
function isHidden(group){return hideAll||hiddenGroups.has(group);}
function stockButton(s,ownership=true){return `<button class="stock-button" data-stock="${s.code}"><span class="stock-avatar" data-company-symbol="${s.code}" data-company-name="${s.name}">${s.short}</span><span><strong>${s.name}</strong><small>${s.code}${ownership?(s.auto?'<span class="owner">자동관리 '+s.auto+'주</span>':' · 기존·수동 보유'):' · KOSPI'}</small></span></button>`;}
function privacy(){
 $$('[data-private]').forEach(el=>{if(!el.dataset.raw)el.dataset.raw=el.innerHTML;const hidden=isHidden(el.dataset.private);el.innerHTML=hidden?'••••••':el.dataset.raw;el.classList.toggle('masked',hidden);el.setAttribute('aria-label',hidden?'가려진 자산 정보':el.textContent);});
 $$('[data-hide]').forEach(el=>{const hidden=isHidden(el.dataset.hide);el.setAttribute('aria-pressed',String(hidden));el.querySelector('i').dataset.icon=hidden?'eye-off':'eye';});
 $('#privacy-all').setAttribute('aria-pressed',String(hideAll));$('#privacy-all span').textContent=hideAll?'자산 보기':'자산 가리기';$('#privacy-all i').dataset.icon=hideAll?'eye-off':'eye';
 renderHoldings();renderAllocation();drawChart();icons();
}
function setValue(id,value){const el=$(id);el.innerHTML=value;el.dataset.raw=value;}
function renderHoldings(){
 let visible=state==='empty'?[]:stocks.filter(s=>filter==='all'||(filter==='gain'?s.price>=s.average:s.price<s.average));
 if(sortDirection)visible=[...visible].sort((a,b)=>sortDirection*(a.price*a.qty-b.price*b.qty));
 $('#holding-count').textContent=isHidden('holdings')?'—':String(state==='empty'?0:stocks.length);
 if(isHidden('holdings')){$('#holdings-body').innerHTML='<tr><td colspan="6" class="empty">보유 종목 정보가 가려져 있습니다.</td></tr>';return;}
 $('#holdings-body').innerHTML=visible.length?visible.map(s=>{const pnl=(s.price-s.average)*s.qty,rate=(s.price/s.average-1)*100;return `<tr><td>${stockButton(s)}</td><td>${s.qty}주</td><td>${won(s.average)}</td><td>${won(s.price)}</td><td>${won(s.price*s.qty)}</td><td class="${pnl>=0?'positive':'negative'}">${pnl>=0?'+':'−'}${won(Math.abs(pnl))}<br><small>${rate>=0?'+':''}${rate.toFixed(2)}%</small></td></tr>`;}).join(''):'<tr><td colspan="6" class="empty"><strong>아직 표시할 종목이 없어요.</strong>종목 탐색에서 국내 주식을 확인할 수 있습니다.<br><button class="button" data-go="scanner">종목 탐색하기</button></td></tr>';
}
function renderAllocation(){
 if(isHidden('holdings')||isHidden('assets')){$('#allocation').innerHTML='<p class="empty">자산 구성이 가려져 있습니다.</p>';return;}
 const entries=state==='empty'?[]:stocks.map(s=>({name:s.name,value:s.price*s.qty}));entries.push({name:'현금',value:8420000});const total=entries.reduce((a,s)=>a+s.value,0);
 $('#allocation').innerHTML=entries.map((s,i)=>`<div class="allocation-row"><span>${s.name}</span><div class="bar-track"><span style="width:${s.value/total*100}%;background:${s.name==='현금'?'#777f90':['#b6a0eb','#9582c6','#7c709f','#675f80'][i]}"></span></div><strong>${won(s.value)}</strong></div>`).join('');
}
function drawChart(){
 const container=$('#chart');container.onpointermove=null;container.onpointerleave=null;if(isHidden('assets')){container.innerHTML='<p class="empty">자산 흐름이 가려져 있습니다.</p>';$('#chart-tip').textContent='';$('#chart-range').textContent='';return;}
 if(state==='empty'){container.innerHTML='<p class="empty">자산 흐름이 쌓이면 여기에 표시됩니다.</p>';$('#chart-tip').textContent='';$('#chart-range').textContent='';return;}
 const bases={'1주':[24.1,24.02,24.34,24.21,24.44,24.496,24.68],'1개월':[23.1,23.22,23.08,23.4,23.26,23.7,23.23,23.35,22.7,22.53,22.21,22.29,22.8,22.6,23.1,23.42,23.17,23.46,23.6,23.56,24.02,23.86,24.15,24.43,24.38,24.68],'3개월':[21.1,21.8,21.3,21.5,22.2,22.35,21.7,22.4,22.1,22.6,23.2,22.8,23.4,23.15,24.2,24.05,24.68],'1년':[18.1,18.7,18.9,18.4,19.8,19.55,20.8,20.1,21.4,22.1,21.4,22.2,22.6,22.3,23.5,23.1,24.68]};
 const data=bases[period],lo=Math.floor(Math.min(...data)),hi=Math.ceil(Math.max(...data)),w=690,h=210,x=i=>58+i/(data.length-1)*616,y=v=>15+(hi-v)/(hi-lo)*166;
 const points=data.map((v,i)=>`${x(i)},${y(v)}`).join(' '),path='M'+data.map((v,i)=>`${x(i)},${y(v)}`).join(' L');
 const grids=Array.from({length:4},(_,i)=>{const v=lo+(hi-lo)*i/3,yy=y(v);return `<line x1="58" y1="${yy}" x2="674" y2="${yy}" stroke="#30313b" stroke-width=".65"/><text x="46" y="${yy+4}" fill="#9295a5" text-anchor="end" font-size="10">${v.toFixed(1)}백만</text>`;}).join('');
 container.innerHTML=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${period} 예시 총자산 흐름, 마지막 금액 24,680,000원"><defs><linearGradient id="chart-fill" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#af97e8" stop-opacity=".15"/><stop offset="1" stop-color="#af97e8" stop-opacity="0"/></linearGradient></defs>${grids}<path d="${path} L674,181 L58,181Z" fill="url(#chart-fill)"/><polyline points="${points}" fill="none" stroke="#b6a0ed" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/><circle cx="674" cy="${y(data.at(-1))}" r="3.5" fill="#cab4fa"/><text x="670" y="${y(data.at(-1))-12}" fill="#c4b0ef" text-anchor="end" font-size="11">24,680,000</text><line id="chart-cross" y1="10" y2="183" stroke="#898292" stroke-dasharray="3 4" visibility="hidden"/></svg>`;
 $('#chart-range').textContent=({'1주':'09.26 — 10.02','1개월':'09.03 — 10.02','3개월':'07.03 — 10.02','1년':'2025.10 — 2026.10'})[period];$('#chart-tip').textContent='차트에 마우스를 올려 살펴보세요';
 container.onpointermove=e=>{const bounds=container.querySelector('svg').getBoundingClientRect(),local=(e.clientX-bounds.left)/bounds.width*w,index=Math.max(0,Math.min(data.length-1,Math.round((local-58)/616*(data.length-1))));const line=$('#chart-cross');if(!line)return;line.setAttribute('x1',x(index));line.setAttribute('x2',x(index));line.setAttribute('visibility','visible');$('#chart-tip').textContent=`예시 구간 ${index+1} · ${won(data[index]*1e6)}`;};
 container.onpointerleave=()=>{$('#chart-cross')?.setAttribute('visibility','hidden');$('#chart-tip').textContent='차트에 마우스를 올려 살펴보세요';};
}
function renderFavorites(){
 const items=stocks.filter(s=>favorites.has(s.code));$('#tab-favorites span').textContent=items.length;
 $('#favorite-list').innerHTML=items.length?items.map(s=>`<div class="favorite-row">${stockButton(s,false)}<strong>${won(s.price)}</strong><span class="${s.change>=0?'positive':'negative'}">${s.change>=0?'+':''}${s.change.toFixed(2)}%</span><button class="icon-button heart" data-favorite="${s.code}" aria-label="${s.name} 관심종목에서 제거"><i data-icon="heart"></i></button></div>`).join(''):'<div class="empty"><strong>등록한 관심종목이 없습니다.</strong>종목 추가 버튼으로 검색해 등록하세요.</div>';icons();
}
function renderOrders(){
 $('#orders-body').innerHTML=initialOrders.map((o,i)=>{const s=stocks.find(s=>s.code===o.code),isCancelled=cancelled&&o.cls==='pending';return `<tr><td>${stockButton(s,false)}<small class="muted">${o.kind}</small></td><td><span class="tag ${isCancelled?'practice':o.cls}">${isCancelled?'취소 완료':o.status}</span></td><td>지정가 · ${won(o.price)}</td><td>${won(o.qty*o.price)}</td><td class="muted">${o.time}</td><td>${o.cls==='pending'&&!isCancelled?'<button class="button" id="cancel-order">예시 취소</button>':`<button class="text-button" data-stock="${s.code}">내역 보기</button>`}</td></tr>`;}).join('');
}
function selectTab(tab){if(!['portfolio','favorites','scanner','orders'].includes(tab))return;activeTab=tab;$$('[data-tab]').forEach(b=>{const active=b.dataset.tab===tab;b.setAttribute('aria-selected',String(active));b.tabIndex=active?0:-1;});$$('[role=tabpanel]').forEach(p=>p.hidden=p.id!=='panel-'+tab);history.replaceState(null,'','#'+tab);$('#sidebar').classList.remove('open');$('#mobile-menu').setAttribute('aria-expanded','false');}
function openSearch(){if($('dialog[open]'))return;$('#search-dialog').showModal();$('#stock-search').value='';renderSearch();$('#stock-search').focus();}
function renderSearch(){const q=$('#stock-search').value.toLowerCase().replace(/\s/g,'');const items=stocks.filter(s=>[s.name.toLowerCase(),s.code,...s.aliases].some(v=>v.includes(q)));$('#search-results').innerHTML=items.length?items.map(s=>`<button class="search-result" data-stock="${s.code}"><span>${s.name}<small>${s.code} · KOSPI</small></span><span>${won(s.price)}</span></button>`).join(''):'<p class="empty">미리보기 종목에서 찾지 못했습니다.</p>';}
function detail(code){const s=stocks.find(s=>s.code===code);if(!s)return;$('#search-dialog').close();$('#detail-code').textContent=s.code+' · KOSPI · 예시';$('#detail-title').textContent=s.name;const hidden=isHidden('holdings');$('#detail-content').innerHTML=`<div class="detail-price">${won(s.price)}</div><p class="${s.change>=0?'positive':'negative'}">전일 대비 ${s.change>=0?'+':''}${s.change.toFixed(2)}%</p><div class="detail-list"><div><span>보유 수량</span>${hidden?'••••':s.qty+'주'}</div><div><span>평균 매입가</span>${hidden?'••••':won(s.average)}</div><div><span>자동관리</span>${hidden?'••••':s.auto+'주'}</div><div><span>기존·수동 보유</span>${hidden?'••••':s.qty-s.auto+'주'}</div></div><div class="detail-actions"><button class="button" data-favorite="${s.code}">${favorites.has(s.code)?'관심종목 해제':'관심종목 추가'}</button><a class="button primary" href="/stocks/${s.code}" target="_blank" rel="noopener">기존 종목 상세·주문 열기 <i data-icon="arrow-up-right"></i></a></div><p class="dialog-note">미리보기의 가격·보유 내역은 예시입니다.</p>`;icons();if(!$('#detail-dialog').open)$('#detail-dialog').showModal();}
function setState(){state=$('#demo-state').value;$('#connection-error').hidden=state!=='error';$('.connection').innerHTML=state==='error'?'<b style="background:var(--up)"></b><span class="positive">예시 연결 오류</span>':'<b></b>예시 연결 정상';setValue('#total-assets',won(state==='empty'?8420000:24680000));setValue('#purchase',won(state==='empty'?0:15600000));setValue('#market',won(state==='empty'?0:16260000));setValue('#profit',state==='empty'?'₩0':'+₩660,000');setValue('#profit-rate',state==='empty'?'—':'+4.23% <small>매입금액 기준</small>');privacy();}
document.addEventListener('click',e=>{
 const tab=e.target.closest('[data-tab],[data-go]');if(tab){selectTab(tab.dataset.tab||tab.dataset.go);return;}
 const close=e.target.closest('[data-close]');if(close){close.closest('dialog').close();return;}
 const stock=e.target.closest('[data-stock]');if(stock){detail(stock.dataset.stock);return;}
 const fav=e.target.closest('[data-favorite]');if(fav){const code=fav.dataset.favorite;favorites.has(code)?favorites.delete(code):favorites.add(code);renderFavorites();if($('#detail-dialog').open)detail(code);toast('미리보기 관심종목을 변경했습니다.');return;}
 const hide=e.target.closest('[data-hide]');if(hide){const group=hide.dataset.hide;hiddenGroups.has(group)?hiddenGroups.delete(group):hiddenGroups.add(group);privacy();return;}
 const selectedPeriod=e.target.closest('[data-period]');if(selectedPeriod){period=selectedPeriod.dataset.period;$$('[data-period]').forEach(b=>{b.classList.toggle('selected',b===selectedPeriod);b.setAttribute('aria-pressed',String(b===selectedPeriod));});drawChart();return;}
 const selectedFilter=e.target.closest('[data-filter]');if(selectedFilter){filter=selectedFilter.dataset.filter;$$('[data-filter]').forEach(b=>{b.classList.toggle('selected',b===selectedFilter);b.setAttribute('aria-pressed',String(b===selectedFilter));});renderHoldings();}
 if(e.target.closest('#cancel-order')){cancelled=true;renderOrders();toast('예시 주문을 취소했습니다. 실제 주문에는 영향이 없습니다.');}
});
$('.tabs').addEventListener('keydown',e=>{const tabs=$$('[data-tab]'),current=tabs.indexOf(document.activeElement);if(current<0)return;let next;if(e.key==='ArrowRight')next=(current+1)%tabs.length;if(e.key==='ArrowLeft')next=(current+tabs.length-1)%tabs.length;if(e.key==='Home')next=0;if(e.key==='End')next=tabs.length-1;if(next!==undefined){e.preventDefault();selectTab(tabs[next].dataset.tab);tabs[next].focus();}});
$('#privacy-all').onclick=()=>{hideAll=!hideAll;privacy();};$('#sort').onclick=()=>{sortDirection=sortDirection===-1?1:-1;$('#sort').textContent='평가금액 '+(sortDirection===-1?'↓':'↑');$('#sort').closest('th').setAttribute('aria-sort',sortDirection===-1?'descending':'ascending');renderHoldings();};
$('#search-open').onclick=openSearch;$('#favorite-add').onclick=openSearch;$('#stock-search').oninput=renderSearch;$('#lock').onclick=()=>$('#lock-dialog').showModal();$('#demo-state').onchange=setState;
$('#refresh').onclick=()=>{setState();toast('예시 화면을 갱신했습니다. 실제 계좌를 조회하지 않습니다.');};$('#retry').onclick=()=>{$('#demo-state').value='normal';setState();toast('정상 연결 예시로 돌아왔습니다.');};
$('#mobile-menu').onclick=()=>{const open=$('#sidebar').classList.toggle('open');$('#mobile-menu').setAttribute('aria-expanded',String(open));};
document.addEventListener('keydown',e=>{if(e.key==='/'&&!e.ctrlKey&&!e.metaKey&&!['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName)&&!$('dialog[open]')){e.preventDefault();openSearch();}if(e.key==='Escape'){$('#sidebar').classList.remove('open');$('#mobile-menu').setAttribute('aria-expanded','false');}});
$$('dialog').forEach(dialog=>dialog.addEventListener('click',e=>{if(e.target!==dialog)return;const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dialog.close();}));
$('#scanner-body').innerHTML=stocks.map((s,i)=>`<tr><td>${stockButton(s,false)}<small class="muted">거래대금 ${i+1}위 · 예시</small></td><td>${won(s.price)}</td><td class="${s.change>=0?'positive':'negative'}">${s.change>=0?'+':''}${s.change}%</td><td>${Math.floor(8420000/s.price)}주</td><td class="muted">예시 현금 내 1주 이상 매수 가능</td></tr>`).join('');
$$('[data-period],[data-filter]').forEach(b=>b.setAttribute('aria-pressed',String(b.classList.contains('selected'))));renderFavorites();renderOrders();setState();selectTab(location.hash.slice(1)||'portfolio');icons();
