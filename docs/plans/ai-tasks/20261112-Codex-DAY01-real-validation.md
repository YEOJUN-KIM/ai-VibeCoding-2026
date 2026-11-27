# 20261112-Codex-DAY01-real-validation

## 작업 정의

| 항목 | 기록 |
|---|---|
| 고유 작업 ID | 20261112-Codex-DAY01-real-validation |
| 목표 / 이유 | 최종 후보259개 신규 검증과 승인 가능한 기존 시장전용1분 export 확인 후 첫 실제 연구 실행 가능 여부 판정 |
| 담당 AI | Codex 검증·제한 파일 조사; Claude 별도 지표 감사; coordinator 검토 |
| GitHub 저장소 | https://github.com/YEOJUN-KIM/ai-VibeCoding-2026 |
| 브랜치 | YEOJUN-KIM/day01-codex-20261112 |
| Worktree 절대 경로 | C:\Users\User\orca\workspaces\ai-VibeCoding-2026\day01-codex-20261112 |
| 기준 후보 전체 SHA | 293cc09360671e64327e8081b179eaceb8351190 |
| 동기화 merge SHA | 7d72c09c275a444824e2cf52f009abccfce7211e |
| 수정 허용 파일 | 본 신규 문서만. 모든 source/tests/spec은 읽기 전용 |
| 허용 scratch | C:\Users\User\AppData\Roaming\orca\chat-scratch\376ad8c599508167fe6aca0b5c437566\day01-real-validation\codex\task_f8c51bfed1a5 |
| 선행 작업 | D-R1~D-R3 수정 및 독립 최종 후보 검증 |
| 공통 파일 / 순서 | 코드 변경 없음; Codex가 데이터 탐색 증거 전달 후 Claude는 중복 탐색 없이 독립 감사 |
| 완료 조건 | 정확259 raw log·SHA·환경 증거, 제한 파일 metadata/provenance inventory, 적격 데이터 있을 때만 기존 순수API 연구 실행·지표, 없으면 정직한 NO_ELIGIBLE_DATA/N/A 보고 |
| 승인된 명령/범위 | python -B scratch validate_day01.py가 네 지정 unittest 파일만 discover -s tests -p 파일명 -v. 파일 path/size metadata 조사 후 시장전용/출처 적격 파일만 승인 필드 읽기. 기존 순수 API만 허용 |
| 격리 | scratch 하위Codex폴더 독립 출력; 운영DB/API/settings/env import 없음; 실제 주문·워커/서버 없음 |
| 금지 | 새 기능/전략/생산코드, 전체 테스트 탐색, DB/백업DB/dump 추출, .env/비밀/계좌/주문/개인기록/백업/.paper-history 읽기, 시장파일 변경·repo복사, 설치/push/PR/main병합 |
| 승인 상태 | 승인: task_f8c51bfed1a5 / ctx_1dd7b4de1745. 기존 시장전용 로컬 CSV/JSON/JSONL/parquet 읽기를 새로 허용, 혼합/불명 자료는 내용 열기 전에 제외·보고 |
| 진행 상태 | 후보259 검증·제한 조사·첫 실제 export 연구 실행 완료; 거래0건으로 성과 평가 불가, 독립 지표 감사 대기 |

## 착수·코드 검증

- coordinator가 기준 후보·양쪽 clean·원격PR11/UI/docs 무중복을 확인해 인계했다. 현재9908454 clean 확인. ff-only 불가였고 차이는 Claude 리뷰3파일뿐이며 기존 source/tests는 동일했다. 승인된 일반 로컬 feature merge로 후보 통합, 충돌 없음. 다른 Worktree/main 수정 없음.
- 전체 auto_trader tree는 후보/HEAD 모두 `7292df68b2f2e6523d7751923b29110841daa203`, 전체 tests tree는 모두 `f4e140a196aafd227024ad928e583ffc579784d7`. 두 경로 전체 git diff도 없음. 일부 파일만 맞춘 검증이 아니다.
- 네 파일 import를 정적으로 확인했다. production은 표준 라이브러리+순수 RiskPolicy, 테스트는 synthetic fixture와 자기 소스 AST 읽기뿐이다. DB·settings·서버/계좌 모듈을 import하지 않는다.
- 새 실행 결과: test_day01.py67, test_day01_review.py108, test_day01_research.py33, test_day01_research_review.py51 = **259개 통과**. 각 returncode0·OK, expectedFailure 없음. 과거 결과를 재사용하지 않았다.
- scratch `validate_day01.py`, `test-manifest.json`, 네 `test_*.py.raw.log`에 정확한 command·새 UTC 시각·소요시간·Python/OS·후보/checkout/tree SHA·로그/driver SHA256을 남겼다. 환경변수 전체나 비밀값을 수집하지 않았다. 테스트 시작·종료 working state는 clean이다.
- msg_de12534dbc60으로 원시 검증 증거 경로를 조정자에게 즉시 전달했다. 최종 데이터 기록은 아래에 갱신한다.

## 제한 데이터 조사 / 결과

- 초기 content-open 이전 단계에서 동일 projectId의 Orca metadata로 확정한11개 root/Worktree만 조사했다. 개인 전체디스크를 검색하지 않았다. 583개 CSV/JSON/JSONL/parquet의 경로·크기·mtime만 수집했고 오류0건이었다. 보호/의존성 디렉터리와 민감 이름·reparse path는 건너뛰었다. `market-file-inventory.json`의 content_read_count0은 **이 초기 단계**의 상태이며 이후 아래1개만 적격으로 판정해 읽었다.
- 범위: `D:\SourceBank\ai-VibeCoding-2026`; `C:\Users\User\orca\workspaces\ai-VibeCoding-2026\` 아래 day01-codex-20261112, day01-claude-review-20261112, folio-ui-redesign, folio-ux02-pc-20261111, folio-ux02-pc-followup-20261111, paper-safety-01-stop-behavior, paper01-claude-tests, paper01-codex-ui, paper01-integration, ux01-pc-design. 전체 경로/branch는 scratch project-worktrees.json에 있다. 다른 프로젝트 경로는 검색하지 않았다.
- 메타데이터 분류: backtest input.json 후보37개, artifact metadata/provenance 확인 필요504개, 비식별 JSON/CSV40개, 독립1분 봉이 아닌 파생 ML samples2개. samples.csv349,803,463bytes·excluded-samples.csv24,404,098bytes는 ML행/판단·feature matrix로 문서화되어 있어 내용을 열거나 봉을 역구성하지 않았다. reports/계좌·주문/백업/DBdump·환경·운영기록은 읽지 않았다. 나머지36개 input 스냅샷도 성과에 따른 날짜 선택/중복 결합을 피하고자 읽지 않았다.

### 실제 입력 적격성 (내용 읽기 전 승인 근거)

- 선택 경로: `D:\SourceBank\ai-VibeCoding-2026\artifacts\ml\generated\backtest-20261103-170024-aac70351\input.json`, **2,781,046bytes**. 최초 메타데이터로 확인한 파일이며 현재 사용자 지시의 기존 시장전용 로컬 artifact 읽기 승인에 해당한다.
- `docs/ml-drive-preservation.md:7-10`은 이 정확한 폴더를 당시 실제 저장 원본으로 명시하며 당일8,832봉·22종목·2026-11-03과 누적 형제 폴더를 구분한다. 날짜 선택은 이 명시적 provenance 때문에 **실험 결과를 보기 전에** 고정했다. 최신/수익률 좋은 날짜를 찾는 탐색은 하지 않았다.
- 생산 코드 `auto_trader/ml/backtest.py`는 `asdict(RawCandle)` 배열만 input.json으로 저장하며 RawCandle은 source/symbol/interval/event_at/available_at/collected_at/OHLC/volume의11개 시장 필드뿐이다. 현재 source code를 읽었을 뿐 해당 DB CLI·예약·앱을 실행하지 않았다.
- scratch source-eligibility-decision.json에 경로/기대 크기·행·날짜·출처/허용11필드를 먼저 기록한 후 열었다. 실제11필드만 있는 JSON 배열,8,832행,source=TOSS,interval=1m,6자리 ticker22개,event KST날짜2026-11-03이 모두 문서와 일치했다. 다른 필드/행수/범위이면 중단하는 gate를 적용했다. synthetic 생성/보간/가용시각 조작/대체 입력은 없다.
- 원본 SHA256: `4dfe33de48f858b804a679dab34d572ccbe46dba035c1666deb3b1dc99fbe030`. 실행 전후 SHA·크기·mtime_ns 일치(source-integrity.json). 원본/운영RAW를 변경하거나 저장소로 복사하지 않았다. 증거는 문서화된 실제 archive와 현재 파일의 스키마/행/출처/날짜 일치까지이며 거래소에 재접속해 가격을 재검증한 것은 아니다.
- producer와 날짜·크기 근거는 msg_52910959cfcc으로 **읽기 전** 조정자에게, 실제 결과/원본무변경은 msg_50d6dc1a8ca6으로 즉시 전달했다. Claude는 중복 데이터 탐색 없이 공유 scratch로 독립 지표 확인할 수 있다.

### 입력/커버리지

| 항목 | 실제 확인 결과 |
|---|---|
| 선택 파일 event 범위 | 2026-11-03 08:47~15:30 KST |
| available_at/collected_at | 두 필드 모두 09:05:02.040596~15:31:48.868012 KST, 원본 시각 보존 |
| 시간대 | event/available/collected 전8,832행이 UTC+09:00 aware |
| 정규 replay 입력 | 8,576행 / 명시한390분×22=8,580 grid |
| 제외 | 256행 =15:30 closing22 + 시간외234; 원본은 보존하고 제외 메타데이터만 scratch 기록 |
| missing | 4분:035720/036930/080220/403870의09:00 각1개. missing symbol/day0 |
| availability | ontime0, late8,576, 분 끝 기준 max lag524.56357초, event와 다른 날에 수신된 backfill0 |
| 거래대금 | 입력 스키마에 turnover 없음. typical_approximation 명시; 정확 VWAP 주장 없음 |
| 더 넓은 저장 범위 | 다른36개 스냅샷/운영DB 전체는 조사하지 않아UNKNOWN. 현재 운영22종목 설정도UNKNOWN |

고정 universe는 선택한 기존 snapshot의22개 전체를 성과를 보기 전에 고정한 caller 입력이다. 현재 설정/과거 top50를 재구성한 것이 아니며 snapshot에 없는 원래 의도 종목의 누락 여부까지 증명하지 않는다.

`000660,005380,005490,005930,006400,017670,034020,035420,035720,036930,042700,051910,055550,058470,080220,086520,096770,103590,105560,218410,222800,403870`

### 미리 고정한 실행 설정

- 기존 순수 adapt_rows→run_input만 사용했다. Config 기본값에서 execution=research_estimated_open, vwap_mode=typical_approximation, order_quantity=1만 변경했다. 1주 가정은 실제 가용 수량이 아니며 초기 현금은 실제 계좌가 아닌 가상의1,000,000원이다. 데이터/결과를 보기 전에 msg_52910959cfcc으로 설정을 공개했고 결과 뒤에 조정하거나 재실행하지 않았다.
- fee0.00015, sell tax0.002, adverse slippage0.0005; 주문100,000원/종목200,000원/전체500,000원/보유3종목/현금10%/일손실20,000원/ENTRY10회; stop1%/target2%/RR2/거래량배수1.2; ENTRY 나이2분, 연구 관측 지연2분, configured participation1(연구 수량 증거로 쓰지 않음). 전체 field는 scratch config.json에 있다.
- 후보시각 available_at 기반·원래15:10 동결/미청산 정책·비용 수식 그대로다. code/strategy/risk parameter를 결과에 맞춰 고치지 않았다. 실제 원천 입력은 있지만 execution은 uncertainty가 붙은 추정·관측 정산이며 실제 주문/체결이 아니다.

### 실제 연구 결과 (체결 없음)

| 지표 | 결과 / 분모 |
|---|---|
| wrapper / research 상태 | RAN / ESTIMATED_ONLY, uncertain=True, physical_fill_claim=False |
| 순손익(미청산/가상청산비용/연구부채 포함) | 0원 |
| 최종 가상 현금/순청산자산 | 각1,000,000원, non_funded_liability0 |
| ROI | 0% =순손익/초기가상현금 |
| MDD | 0%, observation/settlement 순청산자산6,871개 sample, 초기peak1,000,000원 |
| 곡선 시각 | 2026-11-03 09:00~15:32 KST; estimated_execution_at으로 재배열하지 않음 |
| 완전히 닫힌 matched round trip | 0건, **승률 N/A**(분모0; 개별 SELL 건수를 승률 분모로 쓰지 않음) |
| 매수/매도 체결·수량 | 모두0, partial fill0 |
| accepted ENTRY/전체 modeled order | 모두0 |
| 미체결 entry/exit 수량 | 모두0(승인된 주문 자체가 없기 때문) |
| 보유/미청산/동결종목 | 모두 없음, invalid resource 없음 |
| 실제 modeled fill fee/tax/slippage | 모두0. slippage를 순손익에서 중복 차감하지 않음 |

ENTRY라는 전략 판단 사유는6건 있으나 **모두 SIGNAL 단계 ORDER_AMOUNT_LIMIT**으로 거절되어 signals/승인주문은0이다.000660이10:10:04.898406/10:26:04.579139,005930이10:20:12.436470/10:40:02.211066/10:55:10.463116/11:15:02.215404에 해당한다. 이 결과를 이익/손실 없는 전략 또는 위험 없는 전략이라고 해석할 수 없다. **실제 입력 실행 경로만 확인했으며 체결이 없어 수익성·손절·청산 성과는 평가 불가**다. 더 큰 주문한도나 다른 날짜로 결과를 만들기 위한 추가 실험은 하지 않았다.

사유 event 수: RESEARCH_OBSERVATION_LAG_EXCEEDED570, LATE_OBSERVATION_NOT_EXECUTABLE8,576, OUTSIDE_ENTRY_WINDOW2,788, NO_RANGE_BREAKOUT431, INCOMPLETE_OPENING_RANGE288, NO_FRESH_FIVE_MINUTE_BAR840, INSUFFICIENT_VOLUME_INCREASE18, ENTRY6, ORDER_AMOUNT_LIMIT6. 이는 event 사유 수이며 고유 주문 수와 다르다. 추가 failure 사유는 없다.

MA 비교는 **NOTCOMPARABLE**이다. 기존 MA/backtest의 settings/DB import와 다른 next-open/close·reservation/관측 정산 모델 때문에 이번 승인 범위에서 동일 실행 조건의 안전한 비교를 입증하지 못했다. 해당 모듈이나 혼합 report/주문 파일을 읽거나 새 전략을 구현하지 않았다. 과거 MA 수치를 이번 비교로 인용하지 않는다.

## 증거·재현·완료 상태

- scratch 원시 테스트: 네 raw.log, test-manifest.json, validate_day01.py. 실제 실행: source-eligibility-decision.json, source-assessment.json, source-integrity.json, config.json, coverage.json, result.json, run-input-status.json, summary/trades/equity_curve/daily_results/research_assumptions/signals/reasons/failures JSON, matched-closed-roundtrips/unclosed-lots/open-position-valuation/metrics JSON, real-run-manifest.json, raw stdout/stderr 및 one-off run_real_snapshot.py. 원래 raw market input은 scratch/repo로 복제하지 않았다.
- 테스트 시작UTC 2026-11-12T06:32:25.713318, 종료06:33:04.364395. Python3.14.7, C:\Program Files\Python314\python.exe, Windows-11-10.0.26300-SP0. 각 raw log/driver SHA와 정확 command/시각은 manifest를 따른다. 비밀 환경값은 수집하지 않았다.
- 사용자 확인은 새 raw logs의259 OK와 metrics·빈 trades·6개 주문금액 거절·source-integrity unchanged를 대조하는 것이다. 재현 실행은 기존 승인된 입력이 같은 SHA일 때만 가능하고, 기존 감사출력을 덮어쓰지 않도록 새 허용 scratch 하위에 one-off driver 사본을 두어 실행한다. DB/API로 자료를 다시 생성하거나 원본을 복사/수정하지 않는다.
- 수정된 repo 파일은 본 문서1개뿐이다(승인된 feature sync의 리뷰 baseline 통합은 별도). production/tests/specs/API/DB 계약 변경 없음, 앱/서버/워커/실제계좌·주문/설치/push/PR/main병합 없음. 기존 LIVE/PAPER 동작에 실행 영향을 주지 않았다.
- 현재 후보 검증·제한 조사·단일 실제 export 연구 실행은 완료했다. Claude의 독립 지표/증거 확인·사용자 확인·운영 적용은 미완료이며 성과 판단은 체결0건으로 불가다. 실제 저장/설정 전체·다른 날짜·gap/부분청산의 실제 데이터 경로·거래소 가격 재확인·MA 비교는 미검증이다.
- 기술 선택은 기존 순수 API와 scratch one-off driver로 운영 의존성을 차단하는 장점이 있다. 단점은 documented snapshot의 한 날짜/추정가격·configured quantity/근사VWAP·수신지연 제약이다. 개발 개념은 코드 검증과 성과 검증의 분리, 분모가 없는 승률과 주문 거절의 해석이다. GPT/Claude 검토 대상은259 신선성·전체tree일치·market-only 출처와 원본무변경·0체결 이유·observation 곡선 지표·승률N/A다.
- GitHub/branch/worktree는 위 표와 동일하다. 본 문서만 로컬 commit할 수 있으며 최종 SHA는 완료 보고에서 전달한다. main 통합·배포는 사용자 승인 대상이다. 토큰/비용은 표시되지 않아 확인 불가다.
