"""Minimum, reproducible edits to the supplied standalone HTML. No app imports."""
from pathlib import Path
import re

root = Path(__file__).resolve().parent
source = (root / 'reference/folio-ux02-original.html').read_text(encoding='utf-8')
html = source
# Only desktop rules change; supplied mobile rules are left untouched and untested.
css = '''
/* UX-02 PC: bounded, left-aligned information area. */
@media(min-width:851px){
 .shell{max-width:1224px;padding-left:36px;padding-right:36px}
 .chart-panel,.pnl-grid,.summary-grid,.holdings{max-width:950px}
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
}
'''
html = html.replace('</style>', css + '</style>')
start = html.index('    <article class="panel chart-panel">')
end = html.index('</article>', start) + len('</article>')
html = html[:start] + '''    <article class="panel chart-panel" aria-label="가상 자산 변화">
      <div class="row"><div class="segmented" role="group" aria-label="성과 기간"><button data-period="day" aria-pressed="true" class="active">1일</button><button data-period="week" aria-pressed="false">1주</button><button data-period="month" aria-pressed="false">1개월</button><button data-period="year" aria-pressed="false">1년</button></div></div>
      <div class="chart-frame"><svg class="chart" id="asset-chart" viewBox="0 0 1000 240" preserveAspectRatio="none" role="slider" tabindex="0" aria-label="자산 표본 탐색" aria-describedby="chart-desc chart-help" aria-valuemin="0" aria-valuemax="1" aria-valuenow="0"><title id="chart-title">1일 가상 자산 변화</title><desc id="chart-desc"></desc><defs><linearGradient id="fill" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#aa90e6" stop-opacity=".21"/><stop offset="1" stop-color="#aa90e6" stop-opacity="0"/></linearGradient></defs><g id="plot"></g><g id="chart-cursor" hidden><line id="chart-guide" y1="12" y2="228" stroke="#c6b3f2" stroke-dasharray="3 4"/><circle id="chart-dot" r="4" fill="#c6b3f2"/></g></svg>
      <div class="chart-tip" id="chart-tip" hidden></div></div>
      <p class="sr-only" id="chart-help">좌우 화살표로 표본 이동, Home과 End로 양 끝 탐색, Escape로 도움말 닫기. 선은 확인 가능한 두 표본을 연결하며 중간 시점의 측정값을 뜻하지 않습니다.</p>
      <div class="chart-bottom"><span id="chart-from"></span><span id="chart-to"></span></div>
      <div class="chart-footer"><span>가상 데이터 · 비실시간 · 확인 가능한 시점 2개</span><span id="chart-range"></span></div>
    </article>''' + html[end:]
start = html.index('const periods=')
end = html.index('function holdingTable()', start)
html = html[:start] + '''// Source has aggregate endpoints and undated intermediate offsets, not a timestamped history.
// Only explicit source endpoint date/time anchors are plotted. Date-only anchors are
// represented at 00:00 KST for positioning, labeled as time unavailable (not a measurement time).
// Do not interpolate timestamps for undated offsets or add pre-inception year history.
const sample=(date,amount,time=null)=>({date,time,amount,t:Date.parse(date+'T'+(time||'00:00')+':00+09:00')});
const inception=sample('2026-10-11',10000000), latest=sample('2026-11-11',TOTAL,'14:30');
const periods={
 day:{name:'오늘 손익',label:'1일',basis:'오늘 시작 자산 대비',samples:[sample('2026-11-11',DAY_START,'09:00'),latest]},
 week:{name:'최근 1주 손익',label:'1주',basis:'1주 전 자산 대비',samples:[sample('2026-11-04',10130000),latest]},
 month:{name:'최근 1개월 손익',label:'1개월',basis:'1개월 전 자산 대비',samples:[inception,latest]},
 year:{name:'최근 1년 손익',label:'1년',basis:'운용 시작 자산 대비 · 1년 미만 운용',samples:[inception,latest]}
};
const stamp=s=>s.date.replaceAll('-','.')+' '+(s.time?s.time+' KST':'시각 미제공');
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
 $('#chart-desc').textContent=stamp(first)+' '+won(first.amount)+'에서 '+stamp(last)+' '+won(last.amount)+'으로 변화. 확인 가능한 양 끝 표본만 연결. 날짜만 있는 표본의 시각은 미제공. 입출금 없음.';
 $('#chart-range').textContent=won(first.amount)+' → '+won(last.amount);
 $('#chart-from').textContent=stamp(first);$('#chart-to').textContent=stamp(last);
 $$('[data-period]').forEach(e=>{let on=e.dataset.period===k;e.classList.toggle('active',on);e.setAttribute('aria-pressed',on)});
 let vals=p.samples.map(s=>s.amount),min=Math.min(...vals),max=Math.max(...vals);
 // Minimum 2% of starting assets, centered on observed range: a 0.34% day is not full-height.
 let span=Math.max(max-min,first.amount*.02)*1.2,mid=(min+max)/2,lo=mid-span/2;
 chartPoints=p.samples.map(s=>[16+(s.t-first.t)/(last.t-first.t)*968,220-(s.amount-lo)/span*200]);
 let d=chartPoints.map((pt,i)=>(i?'L':'M')+pt.join(',')).join(' '),area=d+' L984,228 L16,228 Z';
 let grid=[20,120,220].map(y=>'<line x1="16" x2="984" y1="'+y+'" y2="'+y+'" stroke="#2a2b35" stroke-dasharray="3 5"/>').join('');
 $('#plot').innerHTML=grid+'<path d="'+area+'" fill="url(#fill)"/><path d="'+d+'" fill="none" stroke="#b5a0ef" stroke-width="2.5" vector-effect="non-scaling-stroke"/>'+chartPoints.map(pt=>'<circle cx="'+pt[0]+'" cy="'+pt[1]+'" r="3" fill="#c6b3f2"/>').join('');
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
(root/'folio-ux02-pc.html').write_text(html,encoding='utf-8',newline='\n')
print('Built folio-ux02-pc.html from unchanged supplied reference')
