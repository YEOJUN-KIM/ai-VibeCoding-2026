"""Minimum, reproducible edits to the supplied standalone HTML. No app imports."""
from pathlib import Path
import re

root = Path(__file__).resolve().parent
source = (root / 'reference/folio-ux02-original.html').read_text(encoding='utf-8')
html = source
# Only desktop rules change; supplied mobile rules are left untouched and untested.
css = '''
/* UX-02 PC follow-up: content width scales with the viewport (1920 -> 1560px, 2560 -> 1800px), left-aligned. */
@media(min-width:851px){
 .shell{max-width:calc(37.5vw + 936px);padding-left:48px;padding-right:48px}
 .chart-panel>.row{justify-content:flex-end}
 .chart-frame{position:relative;height:240px;margin-top:12px}
 .chart-frame .chart{height:240px;margin:0;overflow:hidden;cursor:crosshair;outline-offset:3px}
 .chart-bottom{margin:0 14px;display:flex;justify-content:space-between}
 .chart-footer{flex-wrap:wrap}
 .chart-tip{position:absolute;z-index:2;pointer-events:none;max-width:calc(100% - 16px);width:264px;padding:10px 12px;background:#25212f;border:1px solid #65527e;border-radius:7px;box-shadow:0 8px 24px #0005;font-size:12px;line-height:1.8}
 .chart-tip time,.chart-tip strong,.chart-tip span{display:block}
 .chart-tip time{color:var(--muted);font-size:11px}
 .chart-tip strong{font-weight:550}
 .chart-tip span{color:var(--red)}
 .chart-tip span.negative{color:var(--blue)}
 /* Sticky save bar: opaque, flush to the viewport bottom; focused fields are scrolled clear of it. */
 html{scroll-padding-bottom:120px}
 .savebar{bottom:0;border-radius:8px 8px 0 0;margin-bottom:0}
 #settings-form{padding-bottom:0}
}
@media(min-width:1600px){
 #paper-home:not([hidden]){display:grid;grid-template-columns:minmax(0,1.5fr) minmax(0,1fr);gap:20px}
 #paper-home>*{margin:0;min-width:0}
 #paper-home>.wealth{grid-column:1/-1}
 #paper-home>.pnl-grid{grid-column:1;grid-row:2}
 #paper-home>.summary-grid{grid-column:2;grid-row:2}
 #paper-home>.chart-panel{grid-column:1;grid-row:3}
 #paper-home>.holdings{grid-column:2;grid-row:3}
 #paper-home>.summary-grid{gap:12px}
 #paper-home>.summary-grid .stat{padding:16px 16px}
 .settings-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px;margin-bottom:20px}
 .settings-grid>.settings-section{margin:0;grid-template-columns:1fr;gap:16px}
 #paper-history:not([hidden]){display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px;align-items:start}
 #paper-history>*{margin:0;min-width:0}
 #paper-history>.summary-grid{grid-column:1/-1}
}
'''
html = html.replace('</style>', css + '</style>')
start = html.index('    <article class="panel chart-panel">')
end = html.index('</article>', start) + len('</article>')
html = html[:start] + '''    <article class="panel chart-panel" aria-label="가상 자산 변화">
      <div class="row"><div class="segmented" role="group" aria-label="성과 기간"><button data-period="day" aria-pressed="true" class="active">1일</button><button data-period="week" aria-pressed="false">1주</button><button data-period="month" aria-pressed="false">1개월</button><button data-period="year" aria-pressed="false">1년</button></div></div>
      <div class="chart-frame"><svg class="chart" id="asset-chart" viewBox="0 0 1000 240" preserveAspectRatio="none" role="slider" tabindex="0" aria-label="자산 표본 탐색" aria-describedby="chart-desc chart-help" aria-valuemin="0" aria-valuemax="0" aria-valuenow="0"><title id="chart-title">1일 가상 자산 변화</title><desc id="chart-desc"></desc><defs><linearGradient id="fill" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#aa90e6" stop-opacity=".21"/><stop offset="1" stop-color="#aa90e6" stop-opacity="0"/></linearGradient></defs><g id="plot"></g><g id="chart-cursor" hidden><line id="chart-guide" y1="12" y2="228" stroke="#c6b3f2" stroke-dasharray="3 4"/><circle id="chart-dot" r="4" fill="#c6b3f2"/></g></svg>
      <div class="chart-tip" id="chart-tip" hidden></div></div>
      <p class="sr-only" id="chart-help">좌우 화살표로 표본 이동, Home과 End로 양 끝 탐색, Escape로 도움말 닫기. 표본은 이 미리보기 전용 가상 시계열입니다.</p>
      <div class="chart-bottom"><span id="chart-from"></span><span id="chart-to"></span></div>
      <div class="chart-footer"><span>가상 시계열 · 독립 미리보기 전용 · 실제 FOLIO 데이터 아님 · <span id="chart-count"></span></span><span id="chart-range"></span></div>
    </article>''' + html[end:]
start = html.index('const periods=')
end = html.index('function holdingTable()', start)
html = html[:start] + '''// FICTIONAL, timestamped series for this standalone preview only (fixtures/asset-series.fictional.json).
// Not FOLIO RAW/ML/trading/portfolio data. Nothing is invented before the fictional start date 2026-10-11 KST.
const SERIES=__SERIES__.samples.map(([date,time,amount,kind])=>({date,time,amount,kind,t:Date.parse(date+'T'+time+':00+09:00')}));
console.assert(SERIES.at(-1).amount===TOTAL&&SERIES.find(s=>s.date==='2026-11-11').amount===DAY_START);
const latest=SERIES.at(-1), dailyView=s=>s.kind!=='intraday'||s===latest;
const periods={
 day:{name:'오늘 손익',label:'1일',basis:'오늘 시작 자산 대비',samples:SERIES.filter(s=>s.date===latest.date)},
 week:{name:'최근 1주 손익',label:'1주',basis:'1주 전 자산 대비',samples:SERIES.filter(s=>s.t>=Date.parse('2026-11-04T15:30:00+09:00')&&dailyView(s))},
 month:{name:'최근 1개월 손익',label:'1개월',basis:'1개월 전 자산 대비',samples:SERIES.filter(dailyView)},
 year:{name:'최근 1년 손익',label:'1년',basis:'운용 시작 자산 대비 · 1년 미만 운용',samples:SERIES.filter(dailyView)}
};
const stamp=s=>s.date.replaceAll('-','.')+' '+s.time+' KST';
const signedWon=n=>(n>0?'+':n<0?'−':'')+won(Math.abs(n));
const signedPct=n=>(n>0?'+':n<0?'−':'')+Math.abs(n).toFixed(2)+'%';
let chartPoints=[],chartIndex=0;
function hideChartTip(){$('#chart-tip').hidden=true;$('#chart-cursor').setAttribute('hidden','')}
function inspectSample(index,show=true){
 let p=periods[currentPeriod],s=p.samples[index],first=p.samples[0],delta=s.amount-first.amount,pt=chartPoints[index];
 chartIndex=index;
 let value=stamp(s)+' · 총자산 '+won(s.amount)+' · 기간 시작 대비 '+signedWon(delta)+' ('+signedPct(delta/first.amount*100)+')';
 $('#asset-chart').setAttribute('aria-valuenow',index);$('#asset-chart').setAttribute('aria-valuetext',value);
 if(!show)return;
 $('#chart-guide').setAttribute('x1',pt[0]);$('#chart-guide').setAttribute('x2',pt[0]);
 $('#chart-dot').setAttribute('cx',pt[0]);$('#chart-dot').setAttribute('cy',pt[1]);$('#chart-cursor').removeAttribute('hidden');
 let tip=$('#chart-tip');tip.innerHTML='<time>'+stamp(s)+'</time><strong>총자산 '+won(s.amount)+'</strong><span class="'+(delta<0?'negative':'')+'">기간 시작 대비 '+signedWon(delta)+' ('+signedPct(delta/first.amount*100)+')</span>';tip.hidden=false;
 let frame=$('.chart-frame'),width=frame.clientWidth,x=pt[0]/1000*width;
 tip.style.left=Math.max(8,Math.min(width-tip.offsetWidth-8,x-tip.offsetWidth/2))+'px';
 let y=pt[1]/240*frame.clientHeight;
 tip.style.top=Math.max(8,Math.min(frame.clientHeight-tip.offsetHeight-8,y>tip.offsetHeight+24?8:y+16))+'px';
}
function period(k){
 currentPeriod=k;let p=periods[k],first=p.samples[0],last=p.samples.at(-1),gain=last.amount-first.amount;
 $('#period-title').textContent=p.name;$('#period-value').innerHTML=signedWon(gain)+' <span>'+signedPct(gain/first.amount*100)+'</span>';
 $('#period-basis').textContent=p.basis;$('#chart-title').textContent=p.label+' 가상 자산 변화';
 $('#chart-desc').textContent=stamp(first)+' '+won(first.amount)+'에서 '+stamp(last)+' '+won(last.amount)+'으로 변화. 가상 시계열 표본 '+p.samples.length+'개. 입출금 없음.';
 $('#chart-count').textContent='표본 '+p.samples.length+'개';$('#asset-chart').setAttribute('aria-valuemax',p.samples.length-1);
 $('#chart-range').textContent=won(first.amount)+' → '+won(last.amount);
 $('#chart-from').textContent=stamp(first);$('#chart-to').textContent=stamp(last);
 $$('[data-period]').forEach(e=>{let on=e.dataset.period===k;e.classList.toggle('active',on);e.setAttribute('aria-pressed',on)});
 let vals=p.samples.map(s=>s.amount),min=Math.min(...vals),max=Math.max(...vals);
 // Minimum 2% of starting assets, centered on observed range: a 0.34% day is not full-height.
 let span=Math.max(max-min,first.amount*.02)*1.2,mid=(min+max)/2,lo=mid-span/2;
 chartPoints=p.samples.map(s=>[16+(s.t-first.t)/(last.t-first.t)*968,220-(s.amount-lo)/span*200]);
 let d=chartPoints.map((pt,i)=>(i?'L':'M')+pt.join(',')).join(' '),area=d+' L984,228 L16,228 Z';
 let grid=[20,120,220].map(y=>'<line x1="16" x2="984" y1="'+y+'" y2="'+y+'" stroke="#2a2b35" stroke-dasharray="3 5"/>').join('');
 $('#plot').innerHTML=grid+'<path d="'+area+'" fill="url(#fill)"/><path d="'+d+'" fill="none" stroke="#b5a0ef" stroke-width="2.5" vector-effect="non-scaling-stroke"/>';
 hideChartTip();inspectSample(0,false);
}
$('#asset-chart').addEventListener('pointermove',e=>{
 let r=e.currentTarget.getBoundingClientRect(),x=Math.max(16,Math.min(984,(e.clientX-r.left)/r.width*1000));
 let index=chartPoints.reduce((best,pt,i)=>Math.abs(pt[0]-x)<Math.abs(chartPoints[best][0]-x)?i:best,0);inspectSample(index);
});
$('#asset-chart').addEventListener('pointerleave',()=>{if(document.activeElement!==$('#asset-chart'))hideChartTip()});
$('#asset-chart').addEventListener('focus',()=>inspectSample(chartIndex));
$('#asset-chart').addEventListener('blur',hideChartTip);
$('#asset-chart').addEventListener('keydown',e=>{
 if(e.key==='Escape'){hideChartTip();return}
 if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();
 let last=periods[currentPeriod].samples.length-1;
 inspectSample(e.key==='Home'?0:e.key==='End'?last:Math.max(0,Math.min(last,chartIndex+(e.key==='ArrowRight'?1:-1))));
});
window.addEventListener('resize',()=>{if(!$('#chart-tip').hidden)inspectSample(chartIndex)});
$$('[data-period]').forEach(e=>e.onclick=()=>period(e.dataset.period));
''' + html[end:]
import json
fixture=(root/'fixtures/asset-series.fictional.json').read_text(encoding='utf-8')
assert html.count('__SERIES__')==1
html=html.replace('__SERIES__',json.dumps(json.loads(fixture),ensure_ascii=False,separators=(',',':')))
wrap_open='<form id="settings-form" novalidate>'; assert html.count(wrap_open)==1
html = html.replace(wrap_open, wrap_open+'<div class="settings-grid">')
err='<div id="settings-error"'; assert html.count(err)==1
html = html.replace(err, '</div>'+err)
(root/'folio-ux02-pc.html').write_text(html,encoding='utf-8',newline='\n')
print('Built folio-ux02-pc.html from unchanged supplied reference')
