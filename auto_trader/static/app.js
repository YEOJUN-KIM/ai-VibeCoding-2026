const $=s=>document.querySelector(s);const won=new Intl.NumberFormat('ko-KR',{style:'currency',currency:'KRW',maximumFractionDigits:0});const number=new Intl.NumberFormat('ko-KR',{maximumFractionDigits:0});let csrfToken='';let strategies=[];let orderPage=1;let orderTotalPages=1;let latestOrders=[];let orderStockNames=new Map();let currentOrderStrategy={};const orderPageSize=20;let refreshVersion=0;let strategyChanging=false;
async function api(path,options={}){const method=(options.method||'GET').toUpperCase();const headers=!['GET','HEAD','OPTIONS'].includes(method)&&csrfToken?{'X-CSRF-Token':csrfToken}:{};const response=await fetch(path,{...options,headers:{'Content-Type':'application/json',...headers,...(options.headers||{})}});if(response.status===401){location.replace('/login');throw new Error('로그인이 필요합니다.');}if(!response.ok){const error=await response.json().catch(()=>({detail:'요청에 실패했습니다.'}));throw new Error(typeof error.detail==='string'?error.detail:'입력값을 확인하세요.');}return response.status===204?null:response.json();}
function formatReturn(rate,profit){if(rate==null)return '-';const n=Number(rate);return Math.abs(n)>0&&Math.abs(n)<0.005?(profit<0?'▼ 손실 0.005% 미만':'▲ 이익 0.005% 미만'):`${profit>0?'▲ ':profit<0?'▼ ':''}${n.toFixed(2)}%`;}
const escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const stockCell=(name,symbol)=>`<span class="stock-name">${name}</span><span class="stock-code">${symbol}</span>`;
function holdingRows(workspace,item){const rows=(workspace.holding_management||[]).filter(row=>row.symbol===item.symbol);if(rows.length)return rows;return [{id:`unknown:${item.symbol}`,symbol:item.symbol,quantity:item.quantity,managed:false,origin:'MANUAL',context:{}}];}
function holdingState(row,workspace){if(!row.managed)return '관리 제외';return (workspace.selected_symbols||[]).includes(row.symbol)&&(row.origin==='MANUAL'||row.context?.strategy_id===workspace.selected_strategy_id)?'현재 전략 운용':'청산만 감시';}
function renderPositions(account,workspace,strategy){const open=new Set([...document.querySelectorAll('.holding-detail[open]')].map(e=>e.dataset.symbol));$('#positions-body').innerHTML=account.positions.length?account.positions.map(item=>{const profit=Number(item.unrealized_profit);const rows=holdingRows(workspace,item);const owners=[...new Set(rows.map(row=>row.origin==='AUTO'?(row.context?.strategy_name||'자동매수 · 전략 미지정'):'수동·미지정'))];const states=[...new Set(rows.map(row=>holdingState(row,workspace)))];const details=rows.map(row=>{const context=row.context||{};return `<div class="holding-allocation"><div><strong>${escapeHtml(row.origin==='AUTO'?(context.strategy_name||'전략 미지정'):'수동·미지정')}</strong><span>${number.format(row.quantity)}주 · ${holdingState(row,workspace)}</span></div><dl><div><dt>손절</dt><dd>${context.stop_loss_rate==null?'-':escapeHtml(context.stop_loss_rate)+'%'}</dd></div><div><dt>익절</dt><dd>${context.take_profit_rate==null?'-':escapeHtml(context.take_profit_rate)+'%'}</dd></div><div><dt>최대 보유</dt><dd>${context.max_holding_days==null?'-':escapeHtml(context.max_holding_days)+'일'}</dd></div><div><dt>운영 시간</dt><dd>${context.trading_start?escapeHtml(context.trading_start)+'–'+escapeHtml(context.trading_end):'-'}</dd></div></dl>${!row.managed?'<small>현재 관리 범위에서 제외되어 자동 청산하지 않습니다.</small>':!strategy.running?'<small>자동매매가 정지되어 있습니다. 시작하면 청산 감시를 재개합니다.</small>':''}</div>`;}).join('');return `<tr><td>${stockCell(escapeHtml(item.name),escapeHtml(item.symbol))}</td><td>${number.format(item.quantity)}주</td><td>${won.format(Number(item.average_price))}</td><td>${won.format(Number(item.current_price))}</td><td>${won.format(Number(item.market_value))}</td><td class="${profit>0?'positive':profit<0?'negative':'neutral'}">${won.format(profit)}</td><td class="holding-owner">${owners.map(escapeHtml).join('<br>')}</td><td>${states.map(state=>`<span class="holding-state ${state==='관리 제외'?'excluded':''}">${state}</span>`).join('')}<details class="holding-detail" data-symbol="${escapeHtml(item.symbol)}" ${open.has(item.symbol)?'open':''}><summary>수량·청산 조건</summary>${details}</details></td></tr>`;}).join(''):'<tr><td class="empty" colspan="8">모의 보유 종목이 없습니다.</td></tr>';}
function renderAccountComparison(workspace,strategy){
 const expanded=new Set([...document.querySelectorAll('.paper-account-summary[data-account-mode] .paper-account-metrics[open]')].map(details=>details.closest('.paper-account-summary').dataset.accountMode));
 const label=mode=>mode==='EXPERIMENT'?'실험계좌':'기존 모의계좌';
 const summaries=workspace.account_summaries?.length?workspace.account_summaries:[
  {account_mode:workspace.account_mode,selected:true,loaded:true,running:strategy.running,emergency_stopped:strategy.emergency_stopped,strategy_name:workspace.selected_strategy_name,tick_count:strategy.tick_count,position_count:workspace.account.positions.length,total_profit:workspace.account.total_profit,strategy_profit:strategy.strategy_profit},
  ...(workspace.background_runs||[]).map(run=>({...run,selected:false,loaded:true,running:true}))
 ];
 $('#paper-account-comparison').innerHTML=['LIVE_COPY','EXPERIMENT'].map(mode=>{
  const item=summaries.find(summary=>summary.account_mode===mode)||{account_mode:mode,loaded:false};
  const state=!item.loaded?'미조회':item.emergency_stopped?'긴급 정지':item.running?'실행 중':'정지됨';
  const metric=(value,suffix='')=>value==null?'조회 정보 없음':`${value}${suffix}`;
  return `<article data-account-mode="${mode}" class="paper-account-summary ${item.selected?'is-selected':''}"><div class="paper-account-summary-heading"><h3>${label(mode)}</h3><span class="pill ${item.running?'running':'stopped'}">${state}</span></div><span class="paper-account-view-label">${item.selected?'현재 보고 있는 계좌':'다른 모의계좌'}</span><strong class="paper-account-summary-strategy" title="${escapeHtml(item.strategy_name||'선택한 전략 없음')}">${escapeHtml(item.strategy_name||(!item.loaded?'계좌를 선택하면 조회합니다.':'선택한 전략 없음'))}</strong><details class="paper-account-metrics" ${expanded.size?'open':''}><summary>보유 ${metric(item.position_count,'개')} · 계좌 손익 ${item.total_profit==null?'조회 정보 없음':won.format(Number(item.total_profit))}<span>상세</span></summary><dl><div><dt>보유 종목</dt><dd>${metric(item.position_count,'개')}</dd></div><div><dt>계좌 손익</dt><dd>${item.total_profit==null?'조회 정보 없음':won.format(Number(item.total_profit))}</dd></div><div><dt>현재 전략 손익</dt><dd>${!item.strategy_name?'선택 전':item.strategy_profit==null?'조회 정보 없음':won.format(Number(item.strategy_profit))}</dd></div></dl><small>${item.loaded?`판단 ${number.format(item.tick_count||0)}회`:'아직 표시하지 않은 계좌입니다.'}</small></details></article>`;
 }).join('');
 $('#paper-account-control-notice').textContent=`아래의 전략 선택·시작·일시정지·긴급 중지는 현재 보고 있는 ${label(workspace.account_mode)}에 적용됩니다. 계좌를 전환해도 다른 계좌의 실행 중인 전략은 계속 유지됩니다.`;
}
function syncStrategyPicker(workspace,strategy){
 const picker=$('#paper-strategy-select');
 const applied=String(workspace.selected_strategy_id??'');
 const account=workspace.account_mode||'';
 if(picker.dataset.accountMode!==account||picker.dataset.appliedId!==applied||strategy.running)picker.value=applied;
 picker.dataset.accountMode=account;picker.dataset.appliedId=applied;
 picker.disabled=Boolean(strategy.running||strategyChanging);
 const hint=$('#paper-strategy-selection-hint');
 hint.hidden=!strategy.running;
 hint.textContent=strategy.running?'현재 전략이 실행 중이므로 전략 선택이 잠겨 있습니다. 다른 전략으로 변경하려면 3단계의 자동매매 일시정지를 눌러주세요.':'';
}
let latestControlContext=null;
function renderControlGuide(workspace,strategy){
 latestControlContext={workspace,strategy};
 const pending=$('#paper-strategy-select').value;
 const applied=String(workspace.selected_strategy_id??'');
 const hasChange=Boolean(pending&&pending!==applied);
 let next,guide;
 if(strategy.running){next='모의매매가 실행 중입니다. 전략 판단과 주문 기록에서 결과를 확인하세요.';guide='실행 중에는 전략을 바꿀 수 없습니다. 변경하려면 먼저 일시정지하세요.';}
 else if(!workspace.snapshot_ready){next='1단계에서 계좌를 준비하세요. 처음이라면 실험계좌를 선택하세요.';guide='계좌 준비가 끝나면 전략을 적용하고 시작할 수 있습니다.';}
 else if(!strategies.length){next='2단계의 전략 만들기·수정하기에서 모의매매 전략을 만들어 주세요.';guide='저장된 모의매매 전략이 있어야 시작할 수 있습니다.';}
 else if(hasChange||strategyChanging){next='선택한 전략을 이 계좌에 적용하고 있습니다.';guide='전략 선택을 저장하는 동안 잠시 기다려주세요.';}
 else if(!applied){next='2단계에서 사용할 전략을 고르세요. 선택하면 바로 적용됩니다.';guide='이 계좌에 적용된 전략이 없어 시작할 수 없습니다.';}
 else{next='준비가 끝났습니다. 3단계의 전략 시작을 눌러주세요.';guide=strategy.emergency_stopped?'긴급 중지 상태입니다. 전략 시작을 누르면 실행과 보유분 감시를 재개합니다.':'시작하면 운영 시간에 맞춰 시세를 확인하고 모의매매합니다.';}
 $('#paper-next-step').textContent=next;
 $('#paper-execution-guide').textContent=guide;
 $('#start-button').disabled=Boolean(!workspace.snapshot_ready||!applied||hasChange||strategy.running||strategyChanging);
 $('#stop-button').disabled=Boolean(!strategy.running||strategyChanging);
 $('#paper-selection-help').textContent=strategyChanging?'선택한 전략을 저장하고 있습니다.':strategy.running?'아래에 현재 실행 중인 전략과 보유분 관리 범위를 표시합니다.':'목록에서 고르면 이 계좌에 바로 적용됩니다. 모의매매는 3단계의 전략 시작을 눌러야 시작됩니다.';
 $('#paper-snapshot-help').textContent=workspace.account_mode==='EXPERIMENT'?'실제 자산 복사는 기존 모의계좌에서 사용할 수 있습니다.':'';
}
function orderDay(createdAt){const time=Date.parse(createdAt);return Number.isFinite(time)?new Date(time+9*60*60*1000).toISOString().slice(0,10):'';}
function orderTime(createdAt){
 const date=new Date(createdAt);
 const day=date.toLocaleDateString('ko-KR',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit'});
 const time=date.toLocaleTimeString('ko-KR',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',second:'2-digit'});
 return `<time class="paper-order-time" datetime="${escapeHtml(createdAt)}"><span>${day}</span><small>${time}</small></time>`;
}
function orderStrategyDetails(item){
 const match=item.source==='STRATEGY'&&(item.reason||'').match(/^(.*) · (이동평균 하향 교차|익절률 도달|손절률 도달|최대 보유일 도달)$/);
 return {name:item.strategy_name||(match?match[1]:''),reason:match?match[2]:(item.reason||'수동 주문')};
}
function orderStrategyNames(orders){
 const names=new Map();
 for(const item of orders){const name=orderStrategyDetails(item).name;if(!item.run_id||!name)continue;const key=`${item.run_id}:${item.symbol}`;if(!names.has(key))names.set(key,new Set());names.get(key).add(name);}
 return names;
}
function orderStrategyCell(item,names){
 const details=orderStrategyDetails(item);const known=names.get(`${item.run_id}:${item.symbol}`);
 const current=item.run_id===currentOrderStrategy.runId&&currentOrderStrategy.symbols?.includes(item.symbol)?currentOrderStrategy.name:'';
 const name=details.name||(known?.size===1?[...known][0]:'')||(!known?current:'')||(item.source==='STRATEGY'?'자동 전략 · 이름 미기록':'수동 주문');
 return `<span class="paper-order-strategy" title="${escapeHtml(name)}">${escapeHtml(name)}</span>`;
}
function orderStockCell(item){const name=item.stock_name||orderStockNames.get(item.symbol)||item.symbol;return `<span class="paper-order-stock" title="${escapeHtml(name)}">${escapeHtml(name)}</span><small class="stock-code">${escapeHtml(item.symbol)}</small>`;}
function renderOrders(orders){latestOrders=orders;const strategyNames=orderStrategyNames(orders);const symbol=$('#order-symbol-filter').value.trim().toUpperCase();const side=$('#order-side-filter').value;const status=$('#order-status-filter').value;const from=$('#order-date-from').value;const to=$('#order-date-to').value;const invalidRange=from&&to&&from>to;const filtered=orders.filter(item=>!invalidRange&&(!from||orderDay(item.created_at)>=from)&&(!to||orderDay(item.created_at)<=to)&&(!symbol||String(item.symbol).toUpperCase().includes(symbol)||String(item.stock_name||orderStockNames.get(item.symbol)||'').toUpperCase().includes(symbol))&&(!side||item.side===side)&&(!status||item.status===status));$('#order-count').textContent=invalidRange?'시작일은 종료일보다 늦을 수 없습니다.':`전체 ${number.format(orders.length)}건 · 조건 일치 ${number.format(filtered.length)}건`;orderTotalPages=Math.max(1,Math.ceil(filtered.length/orderPageSize));orderPage=Math.min(orderPage,orderTotalPages);const start=(orderPage-1)*orderPageSize;const pageOrders=filtered.slice(start,start+orderPageSize);$('#orders-body').innerHTML=pageOrders.length?pageOrders.map(item=>`<tr><td>${orderTime(item.created_at)}</td><td class="${item.side==='BUY'?'buy':'sell'}">${item.side==='BUY'?'매수':'매도'}</td><td>${orderStockCell(item)}</td><td>${orderStrategyCell(item,strategyNames)}</td><td>${number.format(item.quantity)}주</td><td>${won.format(Number(item.price))}</td><td>${item.status==='FILLED'?'체결':'거절'}</td><td>${won.format(Number(item.fee||0)+Number(item.tax||0)+Number(item.slippage||0))}${item.realized_profit==null?'':` / ${won.format(Number(item.realized_profit))}`}</td><td>${escapeHtml(orderStrategyDetails(item).reason)}<br>${escapeHtml(item.message||'-')}</td></tr>`).join(''):filtered.length===0&&orders.length?'<tr><td class="empty" colspan="9">조건에 맞는 주문이 없습니다.</td></tr>':'<tr><td class="empty" colspan="9">이번 모의계좌의 주문 기록이 없습니다. 이전 거래는 전체 거래 기록 다운로드에서 확인하세요.</td></tr>';$('#order-page').textContent=`${number.format(orderPage)} / ${number.format(orderTotalPages)}`;$('#order-first').disabled=orderPage<=1;$('#order-prev').disabled=orderPage<=1;$('#order-next').disabled=orderPage>=orderTotalPages;$('#order-last').disabled=orderPage>=orderTotalPages;}
function decisionDisplay(item){
const decision=item.decision||'';
const waiting=decision.startsWith('시세 대기');
const label=waiting?'시세 대기':decision.startsWith('청산 감시')||/보유 중|청산 조건 감시/.test(decision)?'보유분 감시':decision.startsWith('매수 조건 충족')?'주문 제한 확인':decision.startsWith('상향 교차 확인')?'2봉 확인 중':item.trend==='ABOVE'?'단기선 우위':item.trend==='BELOW'?'장기선 우위':'가격 수집 중';
let reason=decision.replace(/^시세 대기\s*·\s*/, '').replace(/^[A-Za-z0-9]{6}:\s*/, '');
if(decision==='다음 완료 1분봉 대기')reason=item.trend==='BELOW'?'단기선이 장기선 아래 · 상향 교차 대기':'단기선이 장기선 위 · 다음 봉에서 교차·추세 재확인';
if(waiting){
 if(reason.includes('최근 거래량'))reason='최근 거래량 없음';
 else if(reason.includes('최신 완료 봉'))reason='최신 1분봉 없음';
 else if(reason.includes('유효한 현재가'))reason='현재가 확인 불가';
}
return `<div class="paper-decision-cell"><span class="trend ${waiting||label==='보유분 감시'||label==='주문 제한 확인'?'neutral':item.trend==='ABOVE'?'positive':item.trend==='BELOW'?'negative':'neutral'}">${label}</span><small>${escapeHtml(reason)}</small>${(item.entry_constraints||[]).map(text=>`<small class="paper-entry-constraint">${escapeHtml(text)}</small>`).join('')}${item.next_check?`<small class="paper-next-check">${escapeHtml(item.next_check)}</small>`:''}</div>`;
}
function recentSnapshots(strategy){return [...(strategy.snapshots||[])].sort((a,b)=>(Date.parse(b.data_at)||0)-(Date.parse(a.data_at)||0));}
function decisionContext(item,strategy,workspace,risk,orders,now=Date.now()){
 const constraints=[];
 const held=(workspace.managed_holdings||[]).some(lot=>lot.symbol===item.symbol&&Number(lot.quantity)>0);
 const selected=strategies.find(saved=>saved.id===workspace.selected_strategy_id);
 if(!held&&strategy.running){
  const last=(orders||[]).filter(order=>order.symbol===item.symbol&&order.run_id===strategy.run_id&&order.source==='STRATEGY'&&order.status==='FILLED').reduce((time,order)=>Math.max(time,Date.parse(order.created_at)||0),0);
  const ready=last+Number(selected?.cooldown_minutes||0)*60000;
  if(last&&ready>now)constraints.push(`재매수 대기 · 약 ${Math.ceil((ready-now)/60000)}분 남음`);
  if(risk.new_buys_allowed===false)constraints.push(`신규 매수 제한 · ${risk.block_reason||'공통 투자 한도 확인'}`);
 }
 const message=strategy.data_message||'';
 const next=!strategy.running?'다음 확인 · 전략을 시작하면 재개':message.startsWith('운영 시간 외')?'다음 확인 · 운영 시간 내 시세 수신 시':(item.decision?.startsWith('시세 대기')||/오류|지연|API 요청 한도/.test(message))?'다음 확인 · 유효한 시세 수신 시':held?'다음 확인 · 시세 수신 시 청산 조건 감시':'다음 확인 · 다음 완료 1분봉 수신 시';
 return {...item,entry_constraints:constraints,next_check:next};
}
function renderDecisions(stocks,strategy,workspace={},risk={},orders=[]){const names=new Map(stocks.map(s=>[s.symbol,s.name]));$('#watchlist-body').innerHTML=strategy.snapshots.length?recentSnapshots(strategy).map(item=>`<tr><td>${stockCell(names.get(item.symbol)||item.name,item.symbol)}</td><td>${won.format(Number(item.price))}</td><td>${item.short_average?number.format(Number(item.short_average)):'-'}</td><td>${item.long_average?number.format(Number(item.long_average)):'-'}</td><td>${decisionDisplay(decisionContext(item,strategy,workspace,risk,orders))}</td></tr>`).join(''):'<tr><td class="empty" colspan="5">전략을 시작하면 판단 기록이 표시됩니다.</td></tr>';}
function renderDataSummary(strategy){
 const message=strategy.data_message||'시세 수신 대기';
 const counts=message.match(/^(\d+)\/(\d+)종목 판단 가능/);
 const summary=counts?`<span class="paper-data-chip">판단 가능 <strong>${counts[1]}개</strong></span><span class="paper-data-chip waiting">대기 <strong>${Number(counts[2])-Number(counts[1])}개</strong></span>`:`<span>${escapeHtml(message)}</span>`;
 const received=strategy.last_data_at?`최근 조회 ${new Date(strategy.last_data_at).toLocaleTimeString('ko-KR')}`:'조회 전';
 $('#strategy-data-updated').textContent=received;
 const waiting=recentSnapshots(strategy).filter(item=>(item.decision||'').startsWith('시세 대기'));
 const row=item=>`<div class="paper-data-row"><span><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.symbol)}</small></span>${decisionDisplay(item)}</div>`;
 const status=$('#strategy-data-status');const opened=status.dataset.runId===String(strategy.run_id||'')&&(status.querySelector('details')?.open||false);status.dataset.runId=String(strategy.run_id||'');
 const list=waiting.length?`<div class="paper-data-list">${waiting.slice(0,3).map(row).join('')}</div>`:'';
 const more=waiting.length>3?`<details class="paper-data-more" ${opened?'open':''}><summary>자세히 보기 · 나머지 ${waiting.length-3}개 종목</summary><div class="paper-data-list">${waiting.slice(3).map(row).join('')}</div><button type="button" class="paper-data-collapse">간략히 보기</button></details>`:'';
 status.hidden=message==='실제 현재가 · 완료된 1분봉 · 모의 체결'&&!waiting.length;
 status.innerHTML=`<div class="paper-data-summary">${summary}</div>${waiting.length?'<p class="paper-data-hint">정상 종목은 계속 판단합니다. 아래는 시세를 기다리는 종목입니다.</p>':''}${list}${more}`;

}
function renderStrategyReturn(strategy){
renderDataSummary(strategy);
$('#strategy-performance').innerHTML=[['실현손익',won.format(Number(strategy.realized_profit||0))],['거래비용',won.format(Number(strategy.trading_costs||0))],['청산 횟수',`${strategy.completed_trades||0}회`],['이익 청산',`${strategy.winning_trades||0}회`],['관측 최대 낙폭',`${Number(strategy.max_drawdown_percent||0).toFixed(2)}%`]].map(([label,value])=>`<div><span>${label}</span><strong>${value}</strong></div>`).join('');
$('#strategy-return-basis').textContent=`계산 기준 ${won.format(Number(strategy.strategy_basis_amount||0))} · 최대 순투입금 포함`;
const profit=Number(strategy.strategy_profit||0);const direction=profit>0?'positive':profit<0?'negative':'neutral';const marker=profit>0?'▲ ':profit<0?'▼ ':'';const rate=strategy.strategy_return_percent==null?null:Number(strategy.strategy_return_percent);$('#strategy-return-percent').textContent=rate==null?'-':formatReturn(rate,profit);$('#strategy-return-percent').className=direction;$('#strategy-profit').textContent=rate==null?'전략 관리 보유분 없음':`전략 손익 ${profit>0?'+':''}${won.format(profit)}`;$('#strategy-profit').className=direction;}
function renderWorkspace(workspace,strategy){renderAccountComparison(workspace,strategy);syncStrategyPicker(workspace,strategy);const background=workspace.background_runs||[];const notice=$("#paper-background-runs");notice.hidden=!background.length;notice.innerHTML=background.map(run=>`<span><strong>${run.account_mode==='EXPERIMENT'?'실험계좌':'기존 모의계좌'}에서 전략 실행 중</strong> · ${escapeHtml(run.strategy_name||'선택 전략')} · 판단 ${number.format(run.tick_count)}회</span>`).join('');renderManagement(workspace,strategy);const account=workspace.account;$('#account-return-basis').textContent=`계산 기준 ${won.format(Number(account.initial_cash))} · 시작 자산`;const experiment=workspace.account_mode==='EXPERIMENT';$('#paper-account-title').textContent=experiment?'실험계좌':'모의계좌';$('#experiment-account-button').textContent=experiment?'기존 모의계좌로 돌아가기':'실험계좌 불러오기';$('#experiment-account-button').dataset.mode=experiment?'LIVE_COPY':'EXPERIMENT';$('#snapshot-button').disabled=experiment;$('#paper-memory-notice').textContent='잔액과 보유종목은 자동 저장됩니다. 재시작 후 마지막으로 선택한 전략을 확인하고 전략 시작을 누르세요. 초기화하면 시작 자산으로 돌아갑니다.';$('#total-asset').textContent=won.format(Number(account.total_asset));$('#cash').textContent=won.format(Number(account.cash));const profit=Number(account.total_profit);const direction=profit>0?'positive':profit<0?'negative':'neutral';const marker=profit>0?'▲ ':profit<0?'▼ ':'';const returnRate=account.return_percent==null?null:Number(account.return_percent);$('#return-percent').textContent=returnRate==null?'-':formatReturn(returnRate,profit);$('#return-percent').className=direction;$('#total-profit').textContent=`총손익 ${profit>0?'+':''}${won.format(profit)}`;$('#total-profit').className=direction;$('#tick-count').textContent=number.format(strategy.tick_count);$('#updated-at').textContent=`최근 갱신 ${new Date().toLocaleTimeString('ko-KR')}`;const badge=$('#paper-source-badge');if(workspace.snapshot_ready){badge.textContent=experiment?'실험계좌 · 시작 현금 1,000만원 · 초기 보유 0개':`실제계좌 ${workspace.source_account_label} · ${new Date(workspace.snapshot_at).toLocaleString('ko-KR')} 복사`;badge.classList.add('ready');$('#snapshot-button').textContent='현재 실제 자산 다시 복사';}else{badge.textContent='초기 가상자금 사용 중 · 실험계좌를 선택하거나 계좌 관리에서 실제 자산을 복사하세요';badge.classList.remove('ready');}const selected=strategies.find(s=>s.id===workspace.selected_strategy_id);const targets=selected?.targets?.length?selected.targets:[selected&&{symbol:selected.symbol,stock_name:selected.stock_name}].filter(Boolean);$('#selected-strategy-card').innerHTML=selected?`<span>선택된 전략 · ${targets.length}종목</span><strong>${escapeHtml(selected.name)}</strong><small>${targets.map(item=>escapeHtml(item.stock_name)).join(' · ')} · 1분봉 MA ${selected.short_period}/${selected.long_period} · ${selected.sizing_mode==='AMOUNT'?won.format(Number(selected.order_amount))+' 이내 매수':selected.order_quantity+'주씩 매수'}</small>`:'<span>선택된 전략</span><strong>없음</strong><small>저장된 전략을 선택하세요.</small>';const waitingOutside=strategy.running&&(strategy.data_message||'').startsWith('운영 시간 외');const waitingData=strategy.running&&!waitingOutside&&!/^[1-9][0-9]*\//.test(strategy.data_message||'')&&/대기|지연/.test(strategy.data_message||'');const pill=$('#strategy-pill');pill.className=`pill ${strategy.emergency_stopped?'emergency':strategy.running?'running':'stopped'}`;pill.textContent=strategy.emergency_stopped?'긴급 정지':waitingOutside?'운영 시간 외 대기':waitingData?'시세 수신 대기':strategy.running?'실행 중':'정지됨';$('#strategy-description').textContent=waitingOutside?'운영 시간이 되면 자동으로 판단을 시작합니다.':waitingData?'최신 시세를 기다리고 있습니다. 수신되면 판단을 재개합니다.':strategy.running?`${selected?.name||'선택 전략'}의 ${targets.length}개 종목을 모의 감시하고 있습니다.`:'실제 시세로 판단하고 모의 잔액으로만 거래합니다.';$('#strategy-operating-hours').textContent=selected?`운영 시간 ${selected.trading_start}–${selected.trading_end}`:'';$('#start-button').disabled=!workspace.snapshot_ready||!workspace.selected_strategy_id||strategy.running;renderPositions(account,workspace,strategy);}
const renderWorkspaceBase=renderWorkspace;
renderWorkspace=function(workspace,strategy){renderWorkspaceBase(workspace,strategy);renderStrategyReturn(strategy);renderControlGuide(workspace,strategy);};
function renderRisk(risk){
  const config=risk.settings;
  const invested=Number(risk.invested_amount), cashRatio=Number(risk.cash_ratio), profit=Number(risk.daily_profit);
  const minimumCash=Number(risk.effective_min_cash_ratio), loss=Math.max(0,-profit);
  const unlimitedOrders=risk.daily_order_limit_disabled||Number(config.daily_order_limit)===0;
  const preset={CONSERVATIVE:'보수적',DEFAULT:'기본',CUSTOM:'직접 설정'}[config.preset]||'현재 설정';
  const progress=(label,value,limit)=>`<progress aria-label="${escapeHtml(label)}" max="100" value="${Math.min(100,Math.max(0,limit>0?value/limit*100:0))}"></progress>`;
  const card=(title,value,detail,meter='',warning=false)=>`<article class="paper-risk-card${warning?' is-limited':''}"><span>${escapeHtml(title)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(detail)}</small>${meter}</article>`;
  const cashNote=minimumCash===0&&Number(config.min_cash_ratio)>0?`현재 최소 비율 제한 미적용 · 저장값 ${config.min_cash_ratio}%`:`매수 후 최소 ${minimumCash.toFixed(0)}% 유지`;
  const orderNote=unlimitedOrders?(Number(config.daily_order_limit)>0?`현재 횟수 제한 미적용 · 저장값 ${config.daily_order_limit}회`:'주문 횟수 제한 없음'):`하루 ${number.format(config.daily_order_limit)}회 한도`;
  const blocked=Boolean(risk.block_reason)||risk.new_buys_allowed===false;
  const status=blocked?'신규 매수 제한':'공통 한도 미도달';
  const reason=risk.block_reason||'손익·주문 횟수의 공통 제한에 도달하지 않았습니다. 금액·현금·전략 조건은 주문별로 추가 확인합니다.';
  $('#risk-usage').innerHTML=`<div class="paper-risk-status${blocked?' is-limited':''}"><div><span class="paper-risk-status-label">${status}</span><p>${escapeHtml(reason)}</p></div><span class="paper-risk-preset">${escapeHtml(preset)} 설정</span></div>
    <div class="paper-risk-grid">
      ${card('전체 투자금',won.format(invested),`전체 한도 ${won.format(Number(config.max_total_investment))}`,progress('전체 투자 한도 사용률',invested,Number(config.max_total_investment)),invested>=Number(config.max_total_investment))}
      ${card('현재 현금 비율',`${cashRatio.toFixed(1)}%`,cashNote,progress('현재 현금 비율',cashRatio,100),cashRatio<minimumCash)}
      ${card('오늘 손실 사용량',won.format(loss),`손실 한도 ${won.format(Number(config.daily_loss_limit))} · 오늘 손익 ${won.format(profit)}`,progress('일일 손실 한도 사용률',loss,Number(config.daily_loss_limit)),loss>=Number(config.daily_loss_limit))}
      ${card('오늘 체결 주문',`${number.format(risk.daily_orders)}회`,orderNote,unlimitedOrders?'<span class="paper-risk-unlimited">횟수 제한 없음</span>':progress('일일 주문 횟수 사용률',Number(risk.daily_orders),Number(config.daily_order_limit)),!unlimitedOrders&&Number(risk.daily_orders)>=Number(config.daily_order_limit))}
    </div>
    <h3 class="paper-risk-subheading">주문에 적용되는 기준</h3><div class="paper-risk-caps">
      ${card('1회 주문 한도',won.format(Number(config.max_order_amount)),'한 번의 매수 금액 · 수수료 포함')}
      ${card('종목별 투자 한도',won.format(Number(config.max_symbol_amount)),'기존 보유 평가액 + 추가 매수금액')}
      ${card('일일 수익 목표',won.format(Number(config.profit_target)),'오늘 손익이 목표에 도달하면 신규 매수 제한')}
    </div>`;
}
async function refresh(){if(strategyChanging)return;const version=++refreshVersion;try{const [workspace,strategy,stocks,orders,risk]=await Promise.all([api('/paper/workspace'),api('/strategy/status'),api('/paper/stocks'),api('/orders'),api('/risk')]);if(version!==refreshVersion||strategyChanging)return;orderStockNames=new Map([...strategies.flatMap(saved=>(saved.targets?.length?saved.targets:[saved]).map(target=>[target.symbol,target.stock_name])),...stocks.map(stock=>[stock.symbol,stock.name])].filter(([symbol,name])=>symbol&&name));currentOrderStrategy={runId:strategy.run_id,name:workspace.selected_strategy_name,symbols:workspace.selected_symbols};renderWorkspace(workspace,strategy);renderOrders(orders);renderDecisions(stocks,strategy,workspace,risk,orders);renderRisk(risk);}catch(error){$('#paper-action-message').textContent=error.message;}}
async function ensurePaperSnapshot(){const workspace=await api('/paper/workspace');if(workspace.snapshot_ready)return;$('#paper-memory-notice').textContent='현재 자산으로 모의계좌를 준비합니다. 이전 거래는 전체 거래 기록에서 확인하세요.';$('#paper-action-message').textContent='현재 실제 자산으로 모의계좌를 준비하고 있습니다.';await api('/paper/snapshot/live',{method:'POST'});$('#paper-action-message').textContent='현재 실제 자산으로 새 모의계좌를 준비했습니다. 저장된 전략을 다시 선택해 주세요.';}
async function loadStrategies(){strategies=(await api('/settings/strategies')).filter(s=>s.execution_mode==='DRY_RUN');$('#paper-strategy-select').innerHTML='<option value="">전략을 선택하세요</option>'+strategies.map(s=>`<option value="${s.id}">${s.name} · ${(s.targets?.length||1)}종목</option>`).join('');if(!strategies.length)$('#paper-action-message').textContent='저장된 모의매매 전략이 없습니다. 설정에서 전략을 추가하세요.';}
async function act(path,label){document.querySelectorAll('.paper-engine-actions button, #emergency-button').forEach(b=>b.disabled=true);try{await api(path,{method:'POST'});$('#paper-action-message').textContent=label;await refresh();}catch(error){$('#paper-action-message').textContent=error.message;}finally{$('#emergency-button').disabled=false;if(latestControlContext)renderControlGuide(latestControlContext.workspace,latestControlContext.strategy);}}
$('#experiment-account-button').addEventListener('click',async()=>{if(strategyChanging)return;const button=$('#experiment-account-button');const mode=button.dataset.mode||'EXPERIMENT';strategyChanging=true;refreshVersion++;button.disabled=true;try{await api(`/paper/accounts/${mode}/select`,{method:'POST'});orderPage=1;$('#paper-action-message').textContent='표시 계좌를 전환했습니다. 다른 계좌의 실행 중인 전략은 계속 유지됩니다.';}catch(error){$('#paper-action-message').textContent=error.message;}finally{strategyChanging=false;button.disabled=false;await refresh();}});
$('#snapshot-button').addEventListener('click',async()=>{if(!confirm('현재 실제 원화 현금과 국내 보유종목으로 모의계좌를 새로 만들까요? 기존 모의 주문과 손익은 초기화되며 실제 계좌에는 영향을 주지 않습니다.'))return;const button=$('#snapshot-button');button.disabled=true;try{await api('/paper/snapshot/live',{method:'POST'});$('#paper-action-message').textContent='현재 실제 자산을 모의계좌에 복사했습니다.';await refresh();}catch(error){$('#paper-action-message').textContent=error.message;}finally{button.disabled=false;}});
$('#reset-paper-button').addEventListener('click',async()=>{if(!confirm('마지막 기준 자산으로 모의 주문과 손익을 초기화할까요?'))return;try{await api('/paper/reset',{method:'DELETE'});$('#paper-action-message').textContent='모의 결과를 기준 자산으로 초기화했습니다.';await refresh();}catch(error){$('#paper-action-message').textContent=error.message;}});
$('#paper-account-comparison').addEventListener('click',event=>{
 const summary=event.target.closest('.paper-account-metrics summary');
 if(!summary)return;
 event.preventDefault();
 const open=!summary.closest('details').open;
 document.querySelectorAll('.paper-account-metrics').forEach(details=>details.open=open);
});
$('#paper-strategy-select').addEventListener('change',async()=>{
 const picker=$('#paper-strategy-select');const id=picker.value;
 const previous=String(latestControlContext?.workspace.selected_strategy_id??'');
 if(strategyChanging)return;
 if(latestControlContext?.strategy.running||!id){picker.value=previous;return;}
 if(id===previous)return;
 strategyChanging=true;refreshVersion++;picker.disabled=true;
 if(latestControlContext)renderControlGuide(latestControlContext.workspace,latestControlContext.strategy);
 $('#strategy-data-updated').textContent='조회 전';$('#strategy-data-status').hidden=false;
 $('#strategy-data-status').textContent='전략 변경 중 · 새 종목의 판단을 준비합니다.';
 $('#watchlist-body').innerHTML='<tr><td class="empty" colspan="5">새 전략을 적용하고 있습니다.</td></tr>';
 try{await api(`/paper/strategies/${id}/select`,{method:'POST'});$('#paper-action-message').textContent='선택한 전략을 적용했습니다. 전략 시작을 누르면 모의매매를 시작합니다.';}
 catch(error){picker.value=previous;$('#paper-action-message').textContent=`전략을 변경하지 못했습니다. ${error.message}`;}
 finally{strategyChanging=false;await refresh();}
});

$('#start-button').addEventListener('click',()=>act('/strategy/start','모의매매를 시작했습니다.'));$('#stop-button').addEventListener('click',()=>act('/strategy/stop','전략을 일시정지했습니다.'));$('#emergency-button').addEventListener('click',()=>act('/strategy/emergency-stop','전략을 긴급정지했습니다.'));
$('#order-first').addEventListener('click',()=>{orderPage=1;renderOrders(latestOrders);});$('#order-prev').addEventListener('click',()=>{orderPage=Math.max(1,orderPage-1);renderOrders(latestOrders);});$('#order-next').addEventListener('click',()=>{orderPage=Math.min(orderTotalPages,orderPage+1);renderOrders(latestOrders);});$('#order-last').addEventListener('click',()=>{orderPage=orderTotalPages;renderOrders(latestOrders);});
for(const [id,event] of [['order-date-from','change'],['order-date-to','change'],['order-symbol-filter','input'],['order-side-filter','change'],['order-status-filter','change']])$('#'+id).addEventListener(event,()=>{orderPage=1;renderOrders(latestOrders);});
$('#order-filter-reset').addEventListener('click',()=>{for(const id of ['order-date-from','order-date-to','order-symbol-filter','order-side-filter','order-status-filter'])$('#'+id).value='';orderPage=1;renderOrders(latestOrders);});
document.querySelectorAll('.paper-engine-action .help-tip').forEach(tip=>{tip.addEventListener('click',event=>{event.stopPropagation();const open=!tip.classList.contains('is-open');document.querySelectorAll('.help-tip.is-open').forEach(item=>item.classList.remove('is-open'));tip.classList.toggle('is-open',open);});tip.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();tip.click();}else if(event.key==='Escape'){tip.classList.remove('is-open');tip.blur();}});});document.addEventListener('click',()=>document.querySelectorAll('.help-tip.is-open').forEach(item=>item.classList.remove('is-open')));
function selectTab(tab){document.querySelectorAll('[data-paper-tab]').forEach(b=>b.classList.toggle('active',b.dataset.paperTab===tab));document.querySelectorAll('[data-paper-panel]').forEach(p=>p.hidden=p.dataset.paperPanel!==tab);history.replaceState(null,'',`#${tab}`);}$('#paper-section-tabs').addEventListener('click',e=>{const b=e.target.closest('[data-paper-tab]');if(b)selectTab(b.dataset.paperTab);});$('#logout-button').addEventListener('click',async()=>{try{await api('/auth/logout',{method:'POST'});}finally{location.replace('/login');}});
(async()=>{try{const session=await api('/auth/me');csrfToken=session.csrf_token;$('#current-user').textContent=session.username;const requestedTab=location.hash.slice(1);selectTab(['dashboard','decisions','orders','safety','controls'].includes(requestedTab)?requestedTab:requestedTab==='activity'?'decisions':'dashboard');await ensurePaperSnapshot();await loadStrategies();await refresh();setInterval(refresh,2500);}catch(error){$('#paper-action-message').textContent=error.message;}})();

$('#strategy-data-status').addEventListener('click',event=>{const button=event.target.closest('.paper-data-collapse');if(button){const details=button.closest('details');details.open=false;details.querySelector('summary').focus();}});

function renderManagement(workspace,strategy){const control=$('#paper-management-scope');control.value=workspace.management_scope||'AUTO';control.disabled=strategy.running||strategyChanging;const descriptions={CURRENT:'현재 전략 종목의 보유분만 관리합니다.',AUTO:'자동매매로 매수한 수량만 매수 당시 조건으로 청산 감시합니다. 수동 보유분은 제외합니다.',ALL:'모든 보유분을 관리합니다. 수동·미지정 보유분에는 현재 전략의 청산 조건을 적용합니다.'};$('#paper-management-description').textContent=descriptions[control.value]+' 신규 매수는 현재 전략 종목에서만 합니다. '+(strategy.running?'변경하려면 3단계에서 일시정지하세요.':'변경하면 이 계좌에 바로 저장됩니다.');$('#paper-managed-holdings').innerHTML=(workspace.managed_holdings||[]).map(lot=>`<div class="paper-managed-row"><strong>${escapeHtml(strategies.flatMap(s=>s.targets||[]).find(t=>t.symbol===lot.symbol)?.stock_name||lot.symbol)}</strong><span>${lot.quantity}주 · ${escapeHtml(lot.context?.strategy_name||'현재 전략')} · ${lot.origin==='AUTO'?'자동매수분':'수동·미지정 보유분'}</span></div>`).join('')||'<small>현재 관리 중인 보유분이 없습니다.</small>';}
$('#paper-management-scope').addEventListener('change',async e=>{if(strategyChanging)return;const scope=e.target.value;strategyChanging=true;refreshVersion++;e.target.disabled=true;try{await api('/paper/management',{method:'PUT',body:JSON.stringify({scope})});$('#paper-action-message').textContent='보유 종목 관리 범위를 저장했습니다.';}catch(error){$('#paper-action-message').textContent=error.message;}finally{strategyChanging=false;await refresh();}});

$('#paper-strategy-select').addEventListener('change',()=>{if(latestControlContext)renderControlGuide(latestControlContext.workspace,latestControlContext.strategy);});
document.querySelectorAll('[data-paper-open]').forEach(button=>button.addEventListener('click',()=>{selectTab(button.dataset.paperOpen);}));
