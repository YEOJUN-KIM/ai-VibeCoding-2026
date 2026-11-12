"""Finish the existing preview; build.py and historical evidence stay intact."""
from pathlib import Path
import runpy
root = Path(__file__).resolve().parent
runpy.run_path(str(root / 'build.py'))
f = root / 'folio-ux02-pc.html'
h = f.read_text(encoding='utf-8')
def replace(old, new):
    global h
    assert old in h, old
    h = h.replace(old, new)
replace('설정은 앞으로의 신규 매수에 적용됩니다.', '아래는 향후 동작을 설명하는 UI 예시입니다. 저장은 브라우저 메모리만 변경하며 실제 거래 로직에 적용되지 않습니다.')
replace('가상 설정 저장·적용 완료 · 신규 매수부터 적용됩니다.', '시연 설정을 메모리에 저장했습니다. 실제 거래에는 적용되지 않습니다.')
replace('시연 자동 주문과 보유분 청산 감시가 모두 일시정지되었습니다.', '일시정지 화면 예시입니다. 실제 주문·청산 감시와 연결되지 않았습니다.')
replace('시연 자동 주문과 보유분 청산 감시가 중단되었습니다.', '긴급정지 화면 예시입니다. 실제 주문 중단을 보장하지 않습니다.')
replace('가상 주문·청산 감시를 일시정지했습니다.', '일시정지 화면으로 전환했습니다. 실제 주문 중단 기능은 없습니다.')
replace('가상 긴급정지 · 보유 주식은 매도하지 않습니다.', '긴급정지 화면으로 전환했습니다. 실제 주문 중단·매도 기능은 없습니다.')
replace('가상 상태: 기존 보유분 위험·청산 감시 유지.', '화면 예시: 기존 보유분 감시 유지 설계이며 실제 감시는 없습니다.')
replace('aria-label="가상 자동매매 제어"', 'aria-label="전략 실행·정지 화면 시연"')
replace('#paper-history>.summary-grid{grid-column:1/-1}', '#paper-history>.summary-grid,#paper-history>.history-period{grid-column:1/-1}')
replace('   <section role="tabpanel" aria-labelledby="tab-history" id="paper-history" hidden>', '''   <section role="tabpanel" aria-labelledby="tab-history" id="paper-history" hidden>
    <article class="panel history-period"><div class="row wrap section-head"><h2>전체·기간별 손익</h2><div class="segmented" role="group" aria-label="내역 기간"><button data-history-period="all" aria-pressed="true" class="active">전체</button><button data-history-period="day" aria-pressed="false">1일</button><button data-history-period="week" aria-pressed="false">1주</button><button data-history-period="month" aria-pressed="false">1개월</button><button data-history-period="year" aria-pressed="false">1년</button></div></div><strong class="num up" id="history-period-value" style="font-size:24px"></strong><p class="context" id="history-period-basis"></p><p class="context">기존 가상 자산의 기간 시작·종료 차이입니다. 아래 거래 기록은 같은 기간으로 좁히며, 보유 내역과 누적 요약은 현재 기준으로 유지합니다.</p></article>''')
replace("function renderRecords(){let list=records.filter(r=>filter==='전체'||r.type===filter);", "function renderRecords(){let list=records.filter(r=>(filter==='전체'||r.type===filter)&&recordInPeriod(r));")
replace("'전체 '+records.length+'건 · '+filter+' '+list.length+'건 · 모두 가상 기록'", "'전체 '+records.length+'건 · 선택 기간 '+filter+' '+list.length+'건 · 모두 가상 기록'")
js = '''// History uses the same fictional asset samples as the home chart, with independent selection.
let historyPeriod='all';
function historySamples(){return periods[historyPeriod==='all'?'year':historyPeriod].samples}
function recordInPeriod(r){let samples=historySamples(),t=Date.parse('2026-'+r.time.replace('.', '-').replace(' ', 'T')+':00+09:00');return t>=samples[0].t&&t<=samples.at(-1).t}
function renderHistoryPeriod(){let s=historySamples(),first=s[0],last=s.at(-1),gain=last.amount-first.amount;
 $('#history-period-value').textContent=signedWon(gain)+' ('+signedPct(gain/first.amount*100)+')';
 $('#history-period-basis').textContent='가상 순자산 변화 · '+stamp(first)+' → '+stamp(last)+(historyPeriod==='year'?' · 1년 미만의 기존 표본만 표시':'');
 $$('[data-history-period]').forEach(e=>{let on=e.dataset.historyPeriod===historyPeriod;e.classList.toggle('active',on);e.setAttribute('aria-pressed',on)});renderRecords();
}
$$('[data-history-period]').forEach(e=>e.onclick=()=>{historyPeriod=e.dataset.historyPeriod;renderHistoryPeriod()});
renderHistoryPeriod();
'''
replace('renderSettings();renderSafety();period(\'day\');renderRecords();', js+"renderSettings();renderSafety();period('day');renderRecords();")
f.write_text(h, encoding='utf-8', newline='\n')
print('Integrated standalone preview built')
