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
              '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'self\'; style-src \'self\' \'unsafe-inline\'; '
              'img-src data:; connect-src \'self\'; font-src \'none\'; base-uri \'none\'; form-action \'none\'">', html, count=1)
html = re.sub(r'<meta name="description"[^>]*>', '<meta name="description" content="FOLIO 모의 자동매매 MVP. 모든 거래는 가상(PAPER)이며 실제 주문은 없습니다.">', html, count=1)
html = re.sub(r"<title>.*?</title>", "<title>모의 자동매매 · FOLIO</title>", html, count=1)

# --- sidebar / topbar: the real app menu. PAPER-SIMPLIFY-02: no "설정" entry and no link to the retired legacy /paper screen;
# account / password / PIN / broker connection / logout are in the shared account menu (static/account-menu.js, top right).
NAV_CSS = (".nav a{display:flex;align-items:center;gap:12px;color:#b5b6c2;text-decoration:none;padding:13px 12px;font-size:13px;border-radius:6px}"
           ".nav a:hover{background:var(--soft)}.nav a.active{background:#282237;color:var(--bright)}"
           ".nav a i{font-style:normal;font-size:11px;color:#777988;width:19px;text-align:center;flex-shrink:0}"
           ".nav a.active svg{width:19px;height:19px;stroke:currentColor;fill:none;stroke-width:1.6;flex-shrink:0}"
           "@media(max-width:640px){.nav a{flex:0 0 auto;padding:10px 8px;font-size:11px;gap:6px;white-space:nowrap}.nav a svg{width:15px;height:15px}}")
html = sub(html, ".skip{position:fixed;left:240px", NAV_CSS + "\n.skip{position:fixed;left:240px")
html = cut(html, '<nav class="nav"', '</nav>',
           '<nav class="nav" aria-label="주요 메뉴"><a href="/live"><i>01</i>자산 현황</a><a href="/stocks"><i>02</i>국내 주식</a>'
           '<a href="/paper-mvp" class="active" aria-current="page"><svg viewBox="0 0 24 24" aria-hidden="true">'
           '<path d="M8 3h8M10 3v6l-6 10q-1 2 2 2h12q3 0 2-2L14 9V3M7 15h10"/></svg>모의매매</a>'
           '<a href="/long-term"><i>04</i>장기 리서치</a><a href="/news"><i>05</i>주요 뉴스</a></nav>')
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

# PAPER-SIMPLIFY-02: the legacy /paper screen is gone, so account preparation (virtual 10,000,000 KRW) is a button here.
html = sub(html, '<button class="primary" id="start">', '<button id="prepare" hidden>가상 모의계좌 준비</button><button class="primary" id="start">')

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
           '<div class="notice settings-intro">저장하면 모의계좌에 보관되고 <strong>신규 매수부터 적용</strong>됩니다. 기존 보유분은 매수 당시 청산 기준을 유지합니다. '
           '자동매매 실행 중에는 설정을 바꿀 수 없습니다. 직접 하는 모의 주문에는 이 금액이 적용되지 않으며, 보유 현금과 수량 범위에서만 주문할 수 있습니다.</div>')
# PAPER-SIMPLIFY-02: the operating amount is the only user input. Market (Korea) stays fixed; the interest-sector buttons
# (never connected to candidate selection) and the user stop/take/holding-period inputs are removed. FOLIO's own exit
# criteria are recorded per lot and shown in the holding detail.
NEW_TARGET = (
    '    <article class="panel settings-section"><div class="section-title"><div class="step"><span class="index">02</span><h2>투자 대상</h2></div>'
    '<p>국내 시장에서<br>FOLIO가 후보를 찾습니다.</p></div><div>'
    '<div class="market"><span>투자 시장</span>대한민국 · 국내 주식 <span class="badge neutral" style="margin-left:8px">고정</span></div>'
    '<p class="context" id="sector-help">FOLIO 추천 · 후보는 거래대금 순위와 데이터 품질 검사를 통과한 종목에서 FOLIO가 고릅니다. '
    '업종·종목은 직접 고르지 않습니다.</p></div></article>\n'
    '    <article class="panel settings-section"><div class="section-title"><div class="step"><span class="index">03</span><h2>청산 기준</h2></div>'
    '<p>FOLIO 기본 기준을<br>사용합니다.</p></div><div>'
    '<p class="context" id="exit-help">손절·익절·최대 보유 기간은 직접 입력하지 않고 FOLIO 기본 기준을 사용합니다. 기준은 매수분마다 기록되며 '
    '보유 종목 상세에서 확인할 수 있습니다. 이미 보유한 분량은 매수 당시 기준을 유지합니다. 자동매매가 실행 중인 동안 보유분 감시가 켜져 있고, '
    '일시정지·긴급정지 중에는 감시도 멈춥니다. 최고가 매도를 보장하지 않으며, 종목당·주문당·보유 종목 수·하루 매수 횟수·하루 손실 같은 별도 한도는 없고 '
    '자동매매 운용 금액만 적용됩니다.</p></div></article>\n')
html, n = re.subn(r'(?m)^    <article class="panel settings-section"><div class="section-title"><div class="step"><span class="index">02</span>.*\n'
                  r'    <article class="panel settings-section"><div class="section-title"><div class="step"><span class="index">03</span>.*\n'
                  r'    <article class="panel settings-section"><div class="section-title"><div class="step"><span class="index">04</span>.*\n',
                  lambda m: NEW_TARGET, html, count=1)
assert n == 1
html = re.sub(r'<span class="badge" id="draft-summary">[^<]*</span>', '<span class="badge" id="draft-summary"></span>', html, count=1)
html = sub(html, '<span>100% · 10,258,000원</span>', '<span id="budget-max">100%</span>')
html = sub(html, 'max="10258000"', 'max="1000000000"')
html = sub(html, 'aria-describedby="budget-help"', 'aria-describedby="budget-help"')
html = sub(html, '100%를 지정해도 전액을 바로 매수하지 않습니다.', '자동매매가 보유·매수할 수 있는 최대 금액(원)입니다. 이 금액만 한도로 적용됩니다.')
html = sub(html, '<button class="primary" type="submit">설정 저장·적용</button>', '<button class="primary" type="submit" id="save-button">설정 저장·적용</button>')
html = sub(html, '<small id="save-status" style="display:block;margin-top:4px">현재 적용값 · 이 탭의 메모리에만 보관</small>', '<small id="save-status" style="display:block;margin-top:4px"></small>')

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

# --- PAPER-UI-POLISH-01: the operating amount is the only input, so the three section cards become one compact card plus two
# information rows. The amount field is a text input (inputmode numeric) so thousands separators can be shown while typing.
SETTINGS_NEW = (
    '<div class="notice settings-intro">저장하면 <strong>신규 매수부터 적용</strong>됩니다. 기존 보유분은 매수 당시 청산 기준을 유지하며, 자동매매 실행 중에는 설정을 바꿀 수 없습니다.</div>\n'
    '    <form id="settings-form" novalidate>\n'
    '    <section class="panel budget-card" aria-labelledby="budget-title"><div class="budget-top"><h2 id="budget-title">투자 자금</h2><p>자동투자에 사용할 최대 금액</p></div>'
    '<label class="field-label" for="budget">자동투자 최대 사용 금액</label>'
    '<div class="amount-input"><input id="budget" type="text" inputmode="numeric" autocomplete="off" aria-describedby="budget-help"><span>원</span></div>'
    '<input id="budget-slider" aria-label="자동투자 비율" type="range" min="0" max="100" step="1">'
    '<div class="range-labels"><span>0%</span><span id="budget-percent">50%</span><span id="budget-max">100%</span></div>'
    '<div class="ratio" aria-label="빠른 투자 비율"><button type="button" data-ratio="25">25%</button><button type="button" data-ratio="50">50%</button>'
    '<button type="button" data-ratio="75">75%</button><button type="button" data-ratio="100">100%</button></div>'
    '<p class="context" id="budget-help">모의 총자산의 0~100% 안에서 정할 수 있습니다. 자동매매가 보유·매수할 수 있는 최대 금액이며 직접 하는 모의 주문에는 적용되지 않습니다.</p></section>\n'
    '    <dl class="info-rows"><div><dt>투자 대상</dt><dd id="sector-help">국내 주식 · FOLIO 추천 (업종·종목은 직접 고르지 않습니다)</dd></div>'
    '<div><dt>청산 기준</dt><dd id="exit-help">FOLIO 기본 기준 · 매수분마다 기록되며 보유 종목 상세에서 확인할 수 있습니다</dd></div></dl>\n'
    '    ')
html, n = re.subn(r'<div class="notice settings-intro">.*?<div id="settings-error"', lambda m: SETTINGS_NEW + '<div id="settings-error"', html, count=1, flags=re.S)
assert n == 1
html = sub(html, '<span class="badge">MVP · 모의 자동매매</span>', '')
html = sub(html, '모의계좌 총자산의 기간 시작·현재 차이입니다. 아래 거래·판단 기록은 같은 기간으로 좁히며, 보유 내역과 누적 요약은 현재 기준입니다.',
           '기간 시작과 현재 총자산의 차이입니다. 아래 기록은 이 기간만 보여주고, 보유 종목과 누적 손익은 현재 기준입니다.')
html = sub(html, '<small>조건이 맞지 않으면 현금으로 유지</small>', '<small>현재 보유 현금</small>')
html = sub(html, '<small>매수분별 청산 기준 유지</small>', '<small>현재 보유 중인 종목 수</small>')
POLISH_CSS = """
/* PAPER-UI-POLISH-01: compact settings card, paged lists, tighter PC spacing. */
.settings-intro{margin-bottom:14px;padding:9px 14px}
.budget-card{max-width:760px;padding:20px 24px;margin-bottom:12px}
.budget-top{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin-bottom:14px}
.budget-top h2{font-size:16px}.budget-top p{font-size:12px;color:var(--muted)}
.budget-card .amount-input{max-width:none}.budget-card .ratio{margin-top:12px}
.info-rows{max-width:760px;margin:0 0 16px;border:1px solid var(--line);border-radius:9px;background:var(--panel)}
.info-rows>div{display:grid;grid-template-columns:96px minmax(0,1fr);gap:14px;padding:12px 24px;font-size:13px}
.info-rows>div+div{border-top:1px solid var(--line)}.info-rows dt{color:var(--muted)}.info-rows dd{margin:0;color:#c1c0cd}
.pagedbox{display:flex;flex-direction:column}

@media(min-width:851px){
 .panel{padding:20px;margin-bottom:16px}.heading{margin-bottom:14px}.tabs{margin-bottom:16px}.control{margin-bottom:16px;padding:11px 18px}
 .wealth{margin-bottom:16px}.pnl-grid,.summary-grid{margin-bottom:16px}.stat{padding:14px 18px}
 .record summary{padding:12px 0}td{padding:11px 12px}
}
@media(min-width:1600px){#paper-history:not([hidden]){align-items:stretch}#paper-history>.panel{display:flex;flex-direction:column}}
@media(max-width:850px){.info-rows>div{grid-template-columns:80px minmax(0,1fr);padding:11px 16px}.budget-card{padding:16px}}
"""
i = html.rindex("</style>")
html = html[:i] + POLISH_CSS + html[i:]
for ident in ("home-holdings", "all-holdings", "records"):
    html = sub(html, 'id="%s"' % ident, 'id="%s" class="pagedbox"' % ident) if ident == "records" else \
        sub(html, 'class="table-wrap" id="%s"' % ident, 'class="table-wrap pagedbox" id="%s"' % ident)

# --- the synthetic/status badge is the first span of .topbar .right; the preview hides it under 1200px, which would
# hide the "synthetic" warning on narrow screens. Never reintroduce that rule (checked-in page removed it on purpose).
html = sub(html, ".topbar .right span:first-child{display:none}", "")

# --- demo-only pages, footer, script
html = cut(html, '  <section id="page-assets"', '</section>\n </main>', ' </main>')
html = sub(html, '<footer class="footer">FOLIO UX-02 · 독립 HTML · 가상 예시 · 새로고침하면 모든 시연 설정과 거래가 초기화됩니다.</footer>',
           '<footer class="footer">FOLIO · 모든 거래는 가상이며 실제 주문은 없습니다 · 실제 시장 데이터 기반 운용은 아직 검증되지 않았습니다.</footer>')
html = sub(html, '</head>', '<link rel="stylesheet" href="/static/pagination.css">\n</head>')
html = sub(html, '<button data-filter="관망" aria-pressed="false">관망</button>', '<button data-filter="관망" aria-pressed="false">관망</button><button data-filter="제어" aria-pressed="false">제어</button>')
i = html.index("<script>")
html = html[:i] + '<script src="/static/account-menu.js"></script>\n<script src="/static/pagination.js"></script>\n<script src="/static/paper-mvp.js"></script>\n</body>\n</html>\n'
out_path.write_text(html, encoding="utf-8", newline="\n")
print("wrote", out_path.relative_to(root), len(html), "bytes")
