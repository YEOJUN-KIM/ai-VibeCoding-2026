# 20261112-Codex-DAY01-capital-multiday

## 작업 정의

| 항목 | 기록 |
|---|---|
| 고유 작업 ID | 20261112-Codex-DAY01-capital-multiday |
| 목표 / 이유 | 자본·주문한도 두 필드만 다른 A/B를 실제 승인 시장 export로 실행하고 복수일 가용성과 미청산·비용을 정직하게 비교 |
| 담당 AI | Codex 실행·문서; Claude 독립 감사; coordinator 인계 |
| 저장소 | https://github.com/YEOJUN-KIM/ai-VibeCoding-2026 |
| 브랜치 | YEOJUN-KIM/day01-codex-20261112 |
| Worktree 절대 경로 | C:\Users\User\orca\workspaces\ai-VibeCoding-2026\day01-codex-20261112 |
| 기준 커밋 | a2ae6a691f2e9db4da9cef69ab6f2492839020ee; 실행 코드 후보293cc09360671e64327e8081b179eaceb8351190 |
| 수정 허용 파일 | docs/plans/ai-tasks/20261112-Codex-DAY01-capital-multiday.md 하나; 모든 source/tests/spec 및 이전 기록은 읽기 전용 |
| 선행 작업 | DAY01-real-validation, 실제11월3일 snapshot 및259개 통과 기록; coordinator가 보고한 c320461은 다른 브랜치 문서이며 병합 불필요 |
| 공통 파일 담당 / 순서 | 공통 코드 수정 없음; Codex 실행 증거 인계 후 Claude 독립 감사 |
| 완료 조건 | 사전고정 설정·중복정책, 원본·이전 증거 불변, 단일/복수일 A/B ledger·coverage·지표·한계 인계 |
| 승인 검증 / 명령 | 새 scratch의 표준라이브러리 one-off driver를 python -B로 실행; 기존 adapt_rows/run_input 및 순수 위험·비용 함수만 사용 |
| 격리 | 별도 scratch 출력, DB/API/settings/env/서버/워커/계좌·주문·비밀정보·백업 접근 없음 |
| 미실행 / 금지 | 코드·전략 개발, 기존 산출물 변경, 실제 주문, 설치, 전역 탐색, 전체 테스트 재실행, push/PR/main 병합 |
| 승인 상태 / 근거 | 승인: 사용자 task_14d05c07d7eb / ctx_06b54a5900de의 명시적 실행 범위 |
| 진행 상태 | 승인된 단일일·복수일 A/B 실행과 문서 기록 완료; Claude 독립 감사·사용자 확인·운영 적용은 별도 미완료 |

## 착수 확인과 사전 고정

- coordinator가 clean 상태와 PR11 비중복·변경 없음을 확인했다. 직접 HEAD a2ae6a6 및 clean 상태를 확인했고 새 문서만 소유한다. 다른 Worktree 수정·병합은 하지 않는다.
- scratch: `C:\Users\User\AppData\Roaming\orca\chat-scratch\376ad8c599508167fe6aca0b5c437566\day01-capital-multiday\codex`. 이전 scratch와 input은 변경하지 않는다.
- `frozen-plan.json`, `config-A.json`, `config-B.json`, `config-diff.json`을 결과 전에 기록했다. A는 초기현금1,000,000/주문한도100,000/1주, B는10,000,000/2,000,000/1주이다. 나머지는 이전 실행 전체 Config와 동일하다. 종목한도200,000·총투자500,000·3종목·현금10%·일손실20,000·ENTRY10회·2분 만료/관측지연·비용/stop/target를 확대하지 않는다.
- 원래22종목을 고정하며 37개 기존 metadata의 generated/backtest-*/input.json만 조사한다. producer의 RawCandle11필드 market-only 계약과 현재 사용자 승인을 근거로 하고 스키마가 다르면 멈추거나 부적격으로 보고한다. DB·백업·혼합 report는 읽지 않는다.
- 동일 symbol/event의11필드 전체가 같은 경우만 중복제거한다. source/available/collected를 포함한 값이 하나라도 충돌하면 해당 key의 모든 표현을 제외한다. earliest-available 선택이나 필드 조합을 하지 않는다. 전체 provenance·충돌·누락을 보고한다.
- 발견된 모든 정규장 event 날짜를 실행 결과 전에 고정한다. 날짜별150분 `[09:00,11:30)`과11:30 경계를 별도 계산한다. 종목별 완전한 opening15분과 morning150분을 보고하고, 적어도 한 종목이 둘 다 완전하면 day usable로 분류하되 품질 부족일도 연속 replay에서 임의 제거하지 않는다. 공휴일·미관측일을 추정하지 않는다.
- 기존6개 ENTRY는 원래 available_at 이하 정보의 최신 event close로 flat account 가정의 gate별 진단을 한다. 이 진단은 강제주문이나 B의 실제 재생 상태가 아니며 전체 재생에서 달라지는 signal과 구분한다.
- 기존35개 해시 index를 검증했고 이전 scratch의 모든 파일을 별도 before manifest에 기록했다. 자본/주문한도 외 사후 조정이나 체결을 만들기 위한 추가 실험은 하지 않는다.

## 결과와 인계

### 데이터 범위와 품질

- 원래 metadata583개 중 지정37개 export만 읽었다. 원본 `source-XX-before-read.json`은 해당 파일 내용을 파싱하기 전에 path/size/SHA256/producer provenance를 기록한다. 목록·정확한11필드 이외 schema는 차단했다. `source-manifest.json`과 `dedup-provenance.json`이 모든 원본 해시·행 위치를 연결한다. report/계좌/주문/DB/백업/환경은 읽지 않았다.
- 37개 총1,858,623행 모두11필드/TOSS/1m/aware timestamp/유효 OHLC이며 잘못된 행0, 충돌key0이다. 동일11필드 중복1,683,027개를 제거한175,596개 key를 사용한다. 충돌이 없어 특정 source를 우선하지 않았다. 원본 available/source/collected 값을 수정·합성하지 않는다. 원래 단일일 SHA256은 `4dfe33de48f858b804a679dab34d572ccbe46dba035c1666deb3b1dc99fbe030`이다.
- 실제 event 날짜는25개, 정규장 봉이 있는 날짜는21개(2026-10-02~2026-11-11)다. 10/08·10/19·10/29·11/05는 시간외 봉만 있어 **NO_REPLAY_ROWS**이며 거래일/휴일로 추정하지 않는다. 정규장21개 날짜 모두를 단일 연속 replay한다. 25개 날짜×22종목 보충표와 native390분 coverage를 함께 제공한다. DB에 이외 날짜가 있는지는 UNKNOWN이다.
- 정규장169,500봉,15:30별도406봉,시간외5,690봉으로 합계175,596봉이다. morning `[09:00,11:30)`은65,946봉이고11:30은 별도 경계다. 10월2일은 morning3,274개 누락으로 품질부족이다. 나머지20일은 각18종목 완전 morning,4종목(035720/036930/080220/403870)은 매일09:00 하나씩 없다. 모든22종목이 완전한 날은 없다. 품질 부족일을 손익 계산에서 제거하거나 하루씩 새 현금으로 재시작하지 않는다.
- 전체 봉 available 지연(봉 종료 기준)은0.001665~1,296,842.102502초, p50 45.515066/p95 365.479274초,120초초과17,767봉,일자를 넘긴 관측3,782봉이다. morning은p50 59.026788/p95 722.000276초,120초초과9,035봉,일자 넘김2,701봉이다. 여기서 p50/p95는 정렬배열의0-based index floor(n×0.50)/floor(n×0.95)이며 보간하지 않는다. 전체 zero volume8,139봉이다. 이들은 크기·유동성을 증명하지 않으며 기본2분 research 관측제한을 늘리지 않는다. source 전체 lag와 replay/morning lag의 분모를 구분한다.
- source/event/available/collected는 원래11필드와 adapter provenance로 보존한다. exact turnover가 없어 typical-price×volume VWAP 근사만 명시적으로 쓴다. quotes/depth는 없으며 physical_fill_claim=False이다. 비합성 근거는 문서화된 실제 archived export와 현재 schema/source/date 일치이고 거래소 재조회·운영 수집상태 검증은 아니다.

### 원래6개 진입의 고정 진단과 단일일 전체 replay

이 표의 기준가격은 해당 시각까지 실제 도착한 원래 입력 중 같은 날짜·종목의 최신 event close다. 이후 봉의 open/HLC/volume이나 나중 수신값은 사용하지 않았다. flat account라는 동일 가정으로 각 gate를 모두 계산하고 첫 거절 gate를 표시한 진단이며, 강제 주문·B의 실제 포트폴리오 진행을 뜻하지 않는다. 모든 gate의 boolean과 원가·cash ratio·event/availability는 original-six-gate-diagnostic.json에 있다.

| KST 시각 | 종목 | 알려진 close | adverse buy amount / fee | A 첫 거절 | B 첫 거절 |
|---|---|---:|---:|---|---|
| 10:10:04.898406 | 000660 | 1,420,000 | 1,420,710 /213 | ORDER_AMOUNT_LIMIT | SYMBOL_EXPOSURE_LIMIT |
| 10:20:12.436470 | 005930 | 222,500 | 222,611.25 /33 | ORDER_AMOUNT_LIMIT | SYMBOL_EXPOSURE_LIMIT |
| 10:26:04.579139 | 000660 | 1,418,000 | 1,418,709 /212 | ORDER_AMOUNT_LIMIT | SYMBOL_EXPOSURE_LIMIT |
| 10:40:02.211066 | 005930 | 221,250 | 221,360.625 /33 | ORDER_AMOUNT_LIMIT | SYMBOL_EXPOSURE_LIMIT |
| 10:55:10.463116 | 005930 | 222,250 | 222,361.125 /33 | ORDER_AMOUNT_LIMIT | SYMBOL_EXPOSURE_LIMIT |
| 11:15:02.215404 | 005930 | 222,500 | 222,611.25 /33 | ORDER_AMOUNT_LIMIT | SYMBOL_EXPOSURE_LIMIT |

기존 위험함수 순서상 order amount는 amount+fee, symbol/total은 amount 기준이다. B에서000660은 flat 가정 총투자50만원도 초과하지만 종목한도20만원이 먼저 거절한다. 실제 단일일 전체 replay에서도 A ENTRY6/ORDER_AMOUNT_LIMIT6, B ENTRY6/SYMBOL_EXPOSURE_LIMIT6이다. 두 실행 모두 accepted order0·체결0·노출0·미청산0·fee/tax/slippage0·순손익/ROI/MDD0·승률N/A(완결 round trip0)이며 cash는 각각1백만/1천만원이다. 주문한도만 키워 체결이 보장되지 않음을 확인했으며 다른 한도를 후속 완화하지 않는다.

### 지표 정의와 실행 증거

- 매수·매도·부분체결을 한 lot로 연결하고 완전 종료된 round trip만 승률 분모에 넣는다. 분모0이면 N/A다. 미종료 lot·동결 mark·미청산 수량·추정청산비용·비조달 부채를 별도 남긴다. net profit은 cash+보유자산 순청산가치−연구부채−초기현금이며 slippage는 체결가격에 포함되어 이중 차감하지 않는다.
- ROI=순손익/각 초기현금, MDD는 초기현금을 첫 peak로 하여 **관측/settlement 시각 순서**의 net liquidation equity로 계산한다. estimated_execution_at으로 곡선을 소급 정렬하지 않는다. exposure는 관측 도착·event highwater·동결정책으로 독립 재구성하고 모든 curve point의 순자산을 엔진과 대조한다. 비용 산식은 기존 함수의 adverse slippage0.0005,fee0.00015,sell tax0.002 그대로이며 수수료·세금에만 원 단위 ROUND_DOWN을 적용한다.
- `prepare_sources.py`와 `run_capital_multiday.py`는 새 scratch의 일회성 표준라이브러리 driver이며 production 기능이 아니다. 정확한 실행은 저장소 root에서 `python -B <scratch>/prepare_sources.py`, 이어 `python -B <scratch>/run_capital_multiday.py`였다. 기존 증거를 덮어쓰지 않도록 create-only이며 재현은 새 승인 출력 위치에서만 한다. source manifest·frozen plan·full Config·config diff·coverage·원래6개 진단·각 replay의 summary/trades/equity_curve/daily_results/research_assumptions/reasons/failures/metrics/exposure/closed lots와 raw stdout/stderr를 인계한다.
- source/tests 전체 tree는 후보293cc093과 동일(`auto_trader`7292df68b2f2e6523d7751923b29110841daa203, `tests`f4e140a196aafd227024ad928e583ffc579784d7)이다. 이번 작업은 코드변경이 없고 직전 fresh259개 PASS의 코드이므로 요청대로 테스트259개를 재실행하지 않았다. 과거 PASS를 이번 새 테스트로 표시하지 않는다. MA 비교는 기존 모델·수신/정산·안전 import 조건 차이로 NOTCOMPARABLE이며 실행하거나 새 전략을 만들지 않았다.

### 복수일 연속 실행

A는21일 전체 연속 replay를 완료했다. 초기현금 재설정 없이48개 BUY/48개 SELL, 완전 종료48 lot 중16개 양수, 순손익−12,012.75원(모두 실현), ROI−1.201275%, 관측 MDD1.4233125%(11월6일10:38:32.807445 KST), 승률33.333333…%이다. fee889원·tax6,206원·slippage3,117.75원이며 최종현금/순청산자산987,987.25원이다. 최소현금778,458.15원, 최대 gross marked exposure217,100원, 최대보유3종목, 최대예약현금174,662.275원/예약3 slot이다. 미청산·미체결수량·동결·부채·자원초과·일손실한도초과 표본은0이고 RAN/ESTIMATED_ONLY다.

A의 ENTRY 판단481회 중48개 승인, ORDER_AMOUNT_LIMIT250회·ALREADY_ENTERED_OR_PENDING128회·LATE_RISK_EXIT_PENDING16회·POSITION_LIMIT39회 거절이다. total_orders108은48 BUY와 exit signal/replacement60을 포함하므로 체결96과 다르다. 58,327개 관측 곡선의 현금·포지션·highwater mark·동결을 독립 재구성하여 엔진 순자산과 모두 일치시켰다. day별 결과는 누적현금에서 이어진 실제 simulation 진단이며 새로운 초기자본을 넣어 합산하지 않았다. 단일일과 복수일 결과를 혼합하지 않는다.

이 수치는 실제 원본에 대한 **연구용 관측정산 추정**이며 실제 주문·물리 체결·실행가능수량을 검증한 성과가 아니다. 자본10배인 B의 ROI/MDD 분모도10배이므로 비율만 낮아져도 전략 개선으로 해석하지 않는다. 사전 고정한 한도 두 개 이외에는 바꾸지 않는다. 독립 감사 전이며 전략 상태는 UNVALIDATED다.

| 복수일 지표 | A | B |
|---|---:|---:|
| 초기 가상현금 | 1,000,000 | 10,000,000 |
| 최종 현금 = 순청산자산 | 987,987.25 | 9,974,972.625 |
| 순손익(전부 실현) | −12,012.75 | −25,027.375 |
| ROI | −1.201275% | −0.25027375% |
| 관측표본 MDD | 1.4233125% | 0.291862% |
| 완결 왕복 / 양수 왕복 | 48 /16 | 68 /24 |
| 승률 | 33.333333…% | 35.294117…% |
| BUY / SELL 체결·수량 | 48 /48(각1주) | 68 /68(각1주) |
| accepted ENTRY / total order signal | 48 /108 | 68 /154 |
| fee / sell tax / adverse slippage | 889 /6,206 /3,117.75 | 2,006 /13,767 /6,904.375 |
| 최소 현금 | 778,458.15 | 9,560,558.975 |
| 최대 gross marked exposure | 217,100 | 423,300 |
| 최대보유 / 최대예약 slot | 3 /3 | 3 /3 |
| 최대예약현금 | 174,662.275 | 367,637.7 |
| 부분체결 / 최종 미체결 entry·exit 수량 | 모두0 | 모두0 |
| 미청산 / 동결 / 비조달 부채 / 자원위반 | 모두0 | 모두0 |
| 일손실20,000 초과 표본 | 0 | 0 |
| wrapper / research status | RAN /ESTIMATED_ONLY | RAN /ESTIMATED_ONLY |

B MDD 시점은11월11일10:13:10.154095 KST이다. B도58,327개 관측/정산 표본 모두 독립 현금·순자산 대조를 통과했다. B의 원화 손실은 A보다 크다. 낮은 ROI/MDD 비율이나 승률 차이를 성과 개선·수익성 근거로 주장하지 않는다. 비용은 과거 동작과 동일한 가정 요율이며 실제 중개사 비용 검증은 아니다.

| ENTRY 판단 이후 거절 사유(횟수) | A | B |
|---|---:|---:|
| ORDER_AMOUNT_LIMIT | 250 | 0 |
| SYMBOL_EXPOSURE_LIMIT | 0 | 134 |
| ALREADY_ENTERED_OR_PENDING | 128 | 177 |
| POSITION_LIMIT | 39 | 84 |
| LATE_RISK_EXIT_PENDING | 16 | 18 |
| TOTAL_EXPOSURE_LIMIT / DAILY_LOSS_LIMIT / DAILY_ENTRY_ORDER_LIMIT / INSUFFICIENT_CASH / MIN_CASH_RATIO / UNRESOLVED_EXPOSURE | 각0 | 각0 |
| ENTRY 판단 / 승인 | 481 /48 | 481 /68 |

위 횟수는 reason event이며 고유 주문 건수와 다르다. 전략 판단 사유는 두 실행 모두 OUTSIDE_ENTRY_WINDOW58,956·NO_RANGE_BREAKOUT6,071·INCOMPLETE_OPENING_RANGE7,154·NO_FRESH_FIVE_MINUTE_BAR14,769·INSUFFICIENT_VOLUME_INCREASE1,026·NO_RISING_VWAP10·INCOMPLETE_SESSION_VWAP198·ENTRY481이다. 정규장 행 전체169,500개가 late이며 lag120초 초과12,253개다. A/B의 late-risk observation77/111, stop에 의한 exit 교체12/18, older-mark 보존80/109도 그대로 보존했다. B에는11월6일09:53에09:50 event의 RESEARCH_MISSING_OUTCOME1회가 있으나 이는 이유 기록이고 최종 미청산/미체결은0이다. missing/late를 정상 physical fill evidence로 바꾸지 않았다. 모든 원인·시각·symbol·phase는 각 reasons.json에 있으며 failures는 두 실행 모두 빈 목록이다.

두 simulation의 판단·포지션 흐름이 달라질 수 있으므로 원래6개 고정 진단을 강제하지 않았다. 이 데이터에서는 ENTRY 판단481회는 같지만 포지션·예약·위험 거절 및 체결이 달라졌다. 전체 기간 단일 replay이며 daily_results는 연속 계좌의15:10 진단으로만 해석한다.

## 최종 검증·안전·인계

- 실행 command는 앞 절의 두 scratch driver이고 run driver 시작2026-11-12 07:32:29.161685 UTC, 종료07:48:56.853649 UTC, exit0·stderr0bytes다. `execution-manifest.json`에 실행별 소요시간·전체 metrics·Python 경로/버전·코드 SHA가 있다. run driver SHA256은 `d6e906847b6e27226121f397f25d4820720c3b3ae8de5e8c2f587d13786810d6`이다. selected175,596행의 결정적 canonical hash는 `b567b46c4d277e8b5107b89e7e1a82702e54ac18d7ef4e2a71c1705bc5673703`이며 원본37개 해시와 구분한다.
- `integrity-after.json`: 원본37개 모두 SHA/mtime 불변, 기존 scratch36파일(기존35개 해시목록+index 자체) 모두 byte/해시 불변이다. `single-A-prior-reproduction.json`에서 이전 결과10개(summary/trades/curve/daily/research/signals/reasons/failures/coverage/config)가 새 single-A와 의미상 일치했다. `code-config-verification.json`에서 code/test tree는293cc093 및 c320461과 동일하고 config diff는 정확히2필드다.
- 문서 이외 repo 수정은 없다. API·DB 계약·기존 MA/PAPER/LIVE 코드와 동작을 변경하지 않았다. 앱/DB/API/settings/.env/계좌/주문/서버/워커/백업/설치/원격 push/PR/main 병합을 수행하지 않았다. 실제 quote/depth·유동성·운영 데이터 전체기간·현재 설정22종목은 검증하지 않았다. available_at은 저장값을 보존했으며 거래소의 최초 가용시각을 독립 입증한 것은 아니다.
- coordinator에게 사전정책 msg_932e19e72f0f, 고정 복수일 입력 msg_683944d9e261, single-A/B 및6개 진단 msg_8bb355a6855d, multiday-A msg_c7e903e661b9, B·불변 검사 msg_a67709b61f75로 증거 위치를 순차 인계했다. Claude는 이 증거로 독립 원장·비용·MDD·승률·인과성을 감사하며 데이터 재검색이나 전략 개발은 필요하지 않다.
- 사용자가 확인할 절차는 새 scratch의 frozen-plan/config-diff,25일coverage와 native390분coverage, 네 metrics 및 matched-closed-roundtrips/curve/exposure/reasons, 원본·이전 증거 integrity를 서로 대조하는 것이다. 기대 결과는 단일일 A/B0체결·승률N/A, 복수일 위 표, resource-invalid/미청산0이다. scratch driver는 create-only이므로 기존 출력에 재실행하지 않는다. 원본37개가 같은 해시인 새 승인 출력 위치에서만 재현하며 DB/API로 재추출하지 않는다.
- 기술 선택은 기존 순수 API와 일회성 driver를 이용해 운영 부작용을 피하고, 원래 자료·수신시각과 실행 증거를 보존하는 방식이다. 한계는 고정22종목·불완전/지연 데이터·typical VWAP·가정1주·관측정산 추정이며 수익성을 입증하지 않는다. 핵심 개념은 자본 분모와 원화손익의 차이, availability와 event-time 중복제거를 구분하는 것이다.
- 구현 개발은 없고 승인 범위 실행·문서 검토만 완료다. 새 테스트 실행은0이며 기존 fresh259 PASS의 unchanged source를 사용했다. Claude 독립 감사·사용자 확인·운영 적용은 미완료다. 코드 결함을 새로 확인해 고친 사항은 없으며 GPT 검토 항목은 두 필드 외 설정 불변,25일/21일 분모, 실제 원본 provenance, 비용·왕복 매칭·관측 MDD, 위험 거절과 불확실성 표시다. 문서 통합·main 병합·운영 배포는 사용자 별도 승인 대상이다.
- 작업 branch/worktree는 상단과 같고 단일 새 기록 파일만 로컬 commit한다. 최종 SHA·clean 상태는 완료 보고와 scratch 최종상태에 기록하며 push/새 PR은 없다. 사용량 토큰·비용은 표시되지 않아 unavailable이다.
