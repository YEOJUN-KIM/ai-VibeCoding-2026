"""Build auto_trader/static/paper-mvp.html from the approved UX-02 PC preview (docs/previews/folio-ux02-pc).

The preview's CSS and layout are reused unchanged. Only the markup that showed fictional numbers is replaced with
elements that paper-mvp.js fills from /paper/mvp/status, and the demo-only pages (assets/stocks/research/news) are
dropped. Run:  python scripts/build_paper_mvp_page.py
"""
from pathlib import Path
import re

root = Path(__file__).resolve().parent.parent
src = (root / "docs/previews/folio-ux02-pc/folio-ux02-pc.html").read_text(encoding="utf-8")
out_path = root / "auto_trader/static/paper-mvp.html"


def cut(text, start, end, replacement):
    i = text.index(start)
    j = text.index(end, i) + len(end)
    return text[:i] + replacement + text[j:]


def sub(text, old, new, count=1):
    assert text.count(old) == count, (old[:60], text.count(old))
    return text.replace(old, new)


html = src
html = re.sub(r'<meta http-equiv="Content-Security-Policy"[^>]*>',
              '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'self\'; style-src \'unsafe-inline\'; '
              'img-src data:; connect-src \'self\'; font-src \'none\'; base-uri \'none\'; form-action \'none\'">', html, count=1)
html = re.sub(r'<meta name="description"[^>]*>', '<meta name="description" content="FOLIO 모의 자동매매 MVP. 모든 거래는 가상(PAPER)이며 실제 주문은 없습니다.">', html, count=1)
html = re.sub(r"<title>.*?</title>", "<title>모의 자동매매 · FOLIO</title>", html, count=1)

# --- sidebar / topbar: only the PAPER page is real here
html = cut(html, '<nav class="nav"', '</nav>',
           '<nav class="nav" aria-label="주요 메뉴"><button data-page="paper" class="active" aria-current="page"><svg viewBox="0 0 24 24" aria-hidden="true">'
           '<path d="M8 3h8M10 3v6l-6 10q-1 2 2 2h12q3 0 2-2L14 9V3M7 15h10"/></svg>모의매매</button></nav>')
html = re.sub(r'<div class="sidebar-bottom">.*?</aside>',
              '<div class="sidebar-bottom"><div class="offline"><i class="dot"></i>PAPER · 가상 거래</div><small>실제 주문 없음<br>실제 증권 계좌와 연결되지 않습니다</small></div>\n</aside>', html, count=1, flags=re.S)
html = re.sub(r'<div class="right">.*?</div></header>',
              '<div class="right"><span class="badge neutral" id="source-badge">PAPER</span></div></header>', html, count=1, flags=re.S)
html = sub(html, '<span class="badge">UX-02 · FINAL PREVIEW</span>', '<span class="badge">MVP · 모의 자동매매</span>')
html = sub(html, '<div class="left">WORKSPACE <span style="color:#555765;margin:0 10px">/</span><span id="breadcrumb">모의매매</span></div>',
           '<div class="left">WORKSPACE <span style="color:#555765;margin:0 10px">/</span><span id="breadcrumb">모의매매</span></div>')

# --- control strip
html = sub(html, 'aria-label="전략 실행·정지 화면 시연"', 'aria-label="자동매매 실행·정지"')
html = sub(html, '<p class="control-note" id="run-next">버튼은 화면 상태만 바꿉니다. 주문·시세 수신은 발생하지 않습니다.</p>', '<p class="control-note" id="run-next"></p>')
html = sub(html, '<small id="run-reason">저장된 투자 기준으로 가상 운용을 시작할 수 있어요.</small>', '<small id="run-reason"></small>')

# --- home: replace fictional numbers with ids filled by the script
html = cut(html, '<div class="wealth">', '</small></div>',
           '<div class="wealth"><div class="wealth-label">모의 총자산 <span class="badge">PAPER</span></div><div class="wealth-value num" id="wealth-value">—<span>원</span></div><small id="wealth-note"></small></div>')
html = cut(html, '<div class="pnl-grid">', '</small></article></div>',
           '<div class="pnl-grid"><article class="pnl-card"><h2 id="period-title">오늘 손익</h2><strong class="value num" id="period-value">—</strong><small id="period-basis"></small></article>'
           '<article class="pnl-card"><h2>전체 누적 손익</h2><strong class="value num" id="total-profit">—</strong><small>운용 시작 이후 · 비용 반영 · 입출금 없음</small></article></div>')
html = sub(html, '<div class="chart-footer"><span>가상 시계열 · 독립 미리보기 전용 · 실제 FOLIO 데이터 아님 · <span id="chart-count"></span></span>',
           '<div class="chart-footer"><span>모의계좌의 실제 기록(가상 거래) · <span id="chart-count"></span></span>')
html = sub(html, '표본은 이 미리보기 전용 가상 시계열입니다.', '표본은 모의계좌에 기록된 총자산입니다.')
html = cut(html, '<div class="summary-grid"><article class="stat"><span class="field-label">모의 보유 자산', '</small></article></div>\n    <article class="panel holdings">',
           '<div class="summary-grid"><article class="stat"><span class="field-label">모의 보유 자산</span><strong class="value num" id="holdings-value">—</strong><small>보유 주식의 현재 평가금액</small></article>'
           '<article class="stat"><span class="field-label">남은 현금</span><strong class="value num" id="cash-value">—</strong><small>조건이 맞지 않으면 현금으로 유지</small></article>'
           '<article class="stat"><span class="field-label">보유 종목</span><strong class="value num" id="holdings-count">—</strong><small>매수분별 청산 기준 유지</small></article></div>\n    <article class="panel holdings">')

# --- settings
html = cut(html, '<div class="notice settings-intro">', '</div>',
           '<div class="notice settings-intro">저장하면 모의계좌에 보관되고 <strong>신규 매수부터 적용</strong>됩니다. 기존 보유분은 매수 당시 청산 기준을 유지하며, 계좌 전체의 안전 제한은 최신 설정을 따릅니다. 자동매매 실행 중에는 설정을 바꿀 수 없습니다. 투자 성향별 수치는 UX-02 시연 기준을 그대로 쓰며 최종 정책이 아닙니다.</div>')
html = sub(html, '<span>100% · 10,258,000원</span>', '<span id="budget-max">100%</span>')
html = sub(html, 'max="10258000"', 'max="1000000000"')
html = sub(html, 'aria-describedby="budget-help"', 'aria-describedby="budget-help"')
html = sub(html, '100%를 지정해도 전액을 바로 매수하지 않습니다.', '100%를 지정해도 전액을 바로 매수하지 않으며, 종목당 한도·보유 종목 수·하루 손실 한도가 함께 적용됩니다.')
html = sub(html, '<button class="primary" type="submit">설정 저장·적용</button>', '<button class="primary" type="submit" id="save-button">설정 저장·적용</button>')
html = sub(html, '<small id="save-status" style="display:block;margin-top:4px">현재 적용값 · 이 탭의 메모리에만 보관</small>', '<small id="save-status" style="display:block;margin-top:4px"></small>')
html = sub(html, '시연 기준: ', '기준: ', count=3)
html = sub(html, '투자 제한과 같은 값을 사용합니다.', '투자 제한과 같은 값을 사용합니다. 원금 보장은 아닙니다.')
html = sub(html, '<label class="field-label" for="daily-buys">하루 신규 매수 횟수 한도</label>', '<label class="field-label" for="daily-buys">하루 매수 횟수 한도 (0=제한 없음)</label>')
html = sub(html, '일일 손실 한도는 신규·추가 매수만 제한합니다. 기존 매수분의 위험 감시·손절·익절 판단은 유지하는 설계입니다. 최고가 매도를 보장하지 않습니다.',
           '일일 손실 한도는 신규·추가 매수만 제한합니다. 자동매매가 실행 중인 동안 기존 매수분의 손절·익절은 계속 감시하지만, 일시정지·긴급정지 중에는 이 감시도 멈춥니다. 최고가 매도를 보장하지 않습니다. 하루 매수 횟수 한도는 신규 매수만 세며, 보유분의 매도(청산)는 횟수 제한을 받지 않습니다.')

# --- history
html = cut(html, '<p class="context">기존 가상 자산의 기간 시작·종료 차이입니다.', '</p></article>',
           '<p class="context">모의계좌 총자산의 기간 시작·현재 차이입니다. 아래 거래·판단 기록은 같은 기간으로 좁히며, 보유 내역과 누적 요약은 현재 기준입니다.</p></article>')
html = html.replace('<strong class="num up" id="history-period-value" style="font-size:24px"></strong>', '<strong class="num" id="history-period-value" style="font-size:24px"></strong>')
html = cut(html, '<div class="summary-grid history-summary">', '</small></article></div>\n    <article class="panel"><div class="row section-head"><div><h2>전체 보유 종목',
           '<div class="summary-grid history-summary"><article class="stat"><span class="field-label">미실현 평가손익</span><strong class="value num" id="h-unrealized">—</strong><small>매수 비용 포함 · 매도 예상 비용 제외</small></article>'
           '<article class="stat"><span class="field-label">실현 손익</span><strong class="value num" id="h-realized">—</strong><small id="h-realized-note"></small></article>'
           '<article class="stat"><span class="field-label">누적 손익</span><strong class="value num" id="h-total">—</strong><small>실현 + 미실현 · 입출금 없음</small></article></div>\n'
           '    <article class="panel"><div class="row section-head"><div><h2>전체 보유 종목')
html = sub(html, '<span class="badge neutral">PAPER · 3종목</span>', '<span class="badge neutral" id="holdings-badge">PAPER</span>')
html = sub(html, '<p class="sub">가상 기록 · 행을 펼쳐 거래 또는 보류 이유를 확인하세요.</p>', '<p class="sub">가상 기록 · 행을 펼쳐 거래·관망·판단 보류 이유를 확인하세요.</p>')

# --- the synthetic/status badge is the first span of .topbar .right; the preview hides it under 1200px, which would
# hide the "synthetic" warning on narrow screens. Never reintroduce that rule (checked-in page removed it on purpose).
html = sub(html, ".topbar .right span:first-child{display:none}", "")

# --- demo-only pages, footer, script
html = cut(html, '  <section id="page-assets"', '</section>\n </main>', ' </main>')
html = sub(html, '<footer class="footer">FOLIO UX-02 · 독립 HTML · 가상 예시 · 새로고침하면 모든 시연 설정과 거래가 초기화됩니다.</footer>',
           '<footer class="footer">FOLIO · 모의 자동매매 MVP · 모든 거래는 가상이며 실제 주문은 없습니다 · 실제 시장 데이터 기반 운용은 아직 검증되지 않았습니다.</footer>')
i = html.index("<script>")
html = html[:i] + '<script src="/static/paper-mvp.js"></script>\n</body>\n</html>\n'
out_path.write_text(html, encoding="utf-8", newline="\n")
print("wrote", out_path.relative_to(root), len(html), "bytes")
