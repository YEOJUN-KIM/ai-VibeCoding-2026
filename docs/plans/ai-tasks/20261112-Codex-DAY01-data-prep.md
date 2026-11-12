# 20261112-Codex-DAY01-data-prep

## 작업 정의

| 항목 | 기록 |
|---|---|
| 고유 작업 ID | 20261112-Codex-DAY01-data-prep |
| 목표 / 구현 이유 | 운영 데이터 접근 없는 입력 준비·커버리지 진단과 명시적 연구용 추정 시가 결과 모델 |
| 담당 AI | Codex 구현; Claude 독립 검증; coordinator 검토 |
| GitHub 저장소 | https://github.com/YEOJUN-KIM/ai-VibeCoding-2026 |
| 브랜치 | YEOJUN-KIM/day01-codex-20261112 |
| Worktree 절대 경로 | C:\Users\User\orca\workspaces\ai-VibeCoding-2026\day01-codex-20261112 |
| 기준 후보 SHA | 4a385b1c0fb6a42d0a0cc0c3126e9c00cfbdb060 |
| 동기화 merge SHA | cfeb5a7eb3d988e64601b4ae7602397e6b4b99fe |
| 수정 허용 파일 | auto_trader/ml/day01.py; auto_trader/ml/day01_research.py; tests/test_day01.py; tests/test_day01_research.py; docs/specs/DAY01_BACKTEST.md; docs/technical/DAY01_DATA_READINESS.md; 본 기록 |
| 선행 작업 | strict next-open/legacy 검증 후보, Claude 계약 비판 |
| 공통 파일 담당자 / 순서 | 지정7파일 Codex; Claude 소유 review 테스트/문서/새 기록 읽기 전용. 구현 인계 후 독립 검증 |
| 완료 조건 | code/doc 데이터 조사, 원본 가용시각 보존 입력/390분+15:30분리 진단, 명시적 research mode, 기존175+신규 합성·안전55회귀, 로컬 commit 및 인계 |
| 승인된 검증 | python -B -m unittest discover -s tests -p test_day01.py; 같은 명령 -p test_day01_review.py 및 -p test_day01_research.py; python -B tests/day01_safe_regression_runner.py tests.test_ml_backtest tests.test_strategy_engine tests.test_paper_management tests.test_paper_feed; git diff --check |
| 환경 격리 | caller synthetic 입력, 표준 라이브러리+RiskPolicy, -B; 기존 안전 runner의 DB/network/env/운영기록 차단 |
| 금지 | 운영 데이터/RAW/DB/env/비밀정보/설정 import/실제 주문/API/서버/워커/설치/push/PR/main 병합/새 통신체계 |
| 승인 상태 | 승인: task_3fd246ee5bec / ctx_d77988c38bdb |
| 진행 상태 | 검토 대기: 구현·합성/안전 회귀 완료, 독립 리뷰·통합·사용자 확인·운영 미완료 |

## 착수와 계약

- coordinator가 양쪽 clean·정확 review 기준과 원격 UI/docs 범위 무중복을 확인했다. 로컬 HEAD348f342 clean 확인; ff-only 불가 후 소유 baseline 세 파일 동일성을 확인하고 승인된 일반 로컬 기능 merge, 충돌 없음. 다른 worktree/main 변경 없음.
- AGENTS 및 TEMPLATE·협업 절차를 읽고 새 기록을 생성했다. 기존 소유7파일 밖 수정은 하지 않는다. 실제 저장 날짜 범위와 현재22종목은 코드/문서로 확정 불가하여 UNKNOWN이다.
- msg_91a093fb3a5d로 사전 계약/코드 증거를 전달했다. Claude 비판8개와15:30 입력 불일치 요구를 msg_b24d79242a95/msg_55303551919c로 받았다. msg_226b3fc8821e에서 신호 예약·관측 정산·자원 초과 시 무효 연구부채 계약을 명시했다.
- 기존175개는 수정 중 현재67+108개를 모두 재실행해 통과했다. 운영 파일·시장 데이터는 조사하지 않았다.

## 결과

- 코드·문서 조사만으로 현재 저장 범위와 현재22개 목록을 확인할 수 없으므로 UNKNOWN으로 기록했다. RAW1분 OHLCV/실제수신 available_at은 존재하지만 분별 거래대금·시가 실행 잔량/가용성은 증명하지 못한다. 근거 경로와 의미는 [데이터 준비 문서](../../technical/DAY01_DATA_READINESS.md)에 정리했다. 과거 수집 문서 수치를 현재 실측으로 보고하지 않았다.
- 신규 순수 helper `adapt_rows/coverage/run_input`: caller mapping과 고정 universe만 사용한다. aware datetime/ISO·원래 available/source/optional collected/run ID/turnover 보존, 필수 availability/1m/손상/중복 검증. 정규390분과15:30 closing/시간외를 원본·사유와 분리해 보고한다. 명시적 expected_session_dates에 대해서만 min/max/count/missing days/minutes/symbols/가용시각 품질을 진단하며 휴장일을 만들지 않는다. NO_ROWS/MISSING은 제공입력 진단, 실제저장범위는UNKNOWN이다.
- 명시적 `research_estimated_open` 추가. 기본 strict next_open 및 legacy 수치 동작은 유지했다. 대상 시가를 관측된 역사적 가격 결과로 사용하되 account/PnL는 관측 정산 시점에만 바뀐다. estimated_execution_at은 반사실적 시가, observation_at은 원래수신, execution_at/settlement_at은 장부반영이며 실제 과거 체결을 주장하지 않는다. 가격/존재/positive-volume veto/lag hindsight를 명시한다.
- 연구 설정: finite research_max_observation_lag_minutes 기본2,0~30분. 최신 완성5분·개장범위가 허용 지연 안에서 새로 알려질 때 실제시각에 결정한다. target open 나이<기본2분, 정산 grace=1분+lag, 사건 순서대로 앞 후보의 관측/timeout을 기다린다. 수량은 signal order_quantity 가정이며 target volume/participation은 실행 수량 증거가 아니다. missing/zero/late 사유,15:10 우선 동결 및 뒤늦은 관측/익일 무정산을 보존한다.
- 선택 편향 방지: pending BUY의 현금·노출·slot을 신호 시 예약해 grace까지 유지한다. open보다 엄격히 이전에 알려진 risk block만 증거 시각과 함께 취소하며 이후 급락/손실로 losing BUY를 지우지 않는다. 정산 후 이미 수신된 entry/이후 범위를 모두 따라잡아 지금 STOP-first 신호를 만들고 후속 추정 가격을 기다린다. 과거 equity는 고치지 않는다.
- 갭 자원초과는 무효 결과다. 거래와 반사실적 손실을 남기고 INVALID_RESOURCE_OVERRUN·breaches·실제조달 아닌 non_funded_liability를 equity에서 차감한다. cash는 음수가 아니며 매도 순대금은 부족액을 상환한다. 실제 차입/거래 기능이나 한도 준수 주장은 없고 추가 진입은 차단한다. 모든 결과/거래에 mode·비용/슬리피지·uncertain·unfilled 정보가 있다.
- 실제 수정/생성은6파일: auto_trader/ml/day01.py, 신규 auto_trader/ml/day01_research.py, 신규 tests/test_day01_research.py, docs/specs/DAY01_BACKTEST.md, 신규 docs/technical/DAY01_DATA_READINESS.md, 본 신규기록. 허용된 tests/test_day01.py는 수정하지 않았다. Claude 소유 파일 및 기존 RAW/ML/MA/PAPER/backtest/risk helper/API/DB 계약은 변경하지 않았다.
- 자동 테스트: `python -B -m unittest discover -s tests -p test_day01.py`67개, `-p test_day01_review.py`108개, `-p test_day01_research.py`27개 통과. 기존175개(legacy124/strict51) 수치 회귀와 신규27개를 구분했다. 새 테스트는391→390+closing, provenance/availability/turnover/입력오류/NO_ROWS·missing, 순수run helper, 실제관측 정산/미래 OHLCV 비선택, 지연신호·catch-up STOP/신호자원예약/이전vs이후risk시각, gap invalid/liability·손익 보존, 유한lag·0/누락/지연/마감·익일·결정성을 검증한다.
- 안전 기존 회귀: 아래 명령으로55개 통과, 실패/오류/skip0, 보호된 journal writer1개는 기존 runner 정책대로 EXCLUDED. 최초 실행은 runner가 Orca 자식 프로세스의 runtime token 환경변수 이름을 감지해 거부했다(값 출력/읽기 없음). 테스트 전용 자식 PowerShell 환경에서 해당 키만 제거해 부모/운영 환경을 변경하지 않고 동일한 기존 guard를 그대로 재실행했다. runner는 수정하지 않았고 env파일/DB/network/운영기록 차단을 유지했다.

```powershell
Remove-Item Env:ORCA_AGENT_SESSION_SPAWN_TOKEN -ErrorAction SilentlyContinue
python -B tests/day01_safe_regression_runner.py tests.test_ml_backtest tests.test_strategy_engine tests.test_paper_management tests.test_paper_feed
```

- 최종 합계 **257개 통과**(175+27+55), expectedFailure0; `git diff --check` 통과. guard 최초거부를 테스트 통과로 세지 않았다. 필요한 좁은 범위만 실행했고 전체 suite/설치/실제데이터 백테스트/운영 검증은 하지 않았다.
- 이유와 기술 장단점: 표준 라이브러리·순수 helper는 운영 의존성과 가용시각 조작을 피하고 재현을 쉽게 한다. 관측 시점 장부는 반사실적 가격을 실제 과거체결로 잘못 해석하는 것을 막지만 실제 물리적 거래 시퀀스/유동성/성과를 재현하지 않는다. lag와 zero-volume veto는 hindsight 조건이며, configured quantity와 resource-invalid 결과를 실제 한도 준수/수익성으로 해석할 수 없다.
- 사용자 재현: 운영 자료 없는 독립 worktree에서 위 합성3개 명령은67/108/27 OK를 기대한다. 순수helper에 synthetic 매핑과 명시적 날짜/종목을 주면 actual_stored_date_range=UNKNOWN, 제공행coverage 및 제외15:30을 확인할 수 있다. research는 명시적으로 선택해야 하며 기본 strict Candle-only는 체결하지 않는다. 실제 연구에는 별도 승인된 read-only export/격리/날짜범위/수신시각/turnover 검토가 먼저 필요하다.
- GPT/Claude 검토: 자원 예약/후보 사건 순서/관측 grace, open 전후 risk 정보 경계와 catch-up, invalid gap ledger가 손실을 숨기지 않는지,390/391 경계와 provenance, mode별 불확실성을 검토한다. 핵심 개발 개념은 정보 가용시각과 반사실적 결과의 분리, missing 데이터와 조사하지 않은 UNKNOWN 상태의 분리다.
- 상태: AI 구현·합성/안전 회귀 완료. 수정본 독립 검증/사용자 확인/실제 데이터 백테스트/통합/운영 적용 미완료. 로컬commit 전체SHA는 완료보고에서 제공하며 자기참조 반복commit은 하지 않는다. GitHub/branch/worktree는 위 표와 동일하고 push/PR/main병합/배포는 없음. 통합/운영은 사용자 승인 필요. 토큰/비용 표시는 없어 확인 불가다.

## 독립 리뷰 D-R1/D-R2 후속 수정

- 작업/승인: task_ea33081526f6 / ctx_f16acbc23433. Claude review commit19e763c5e36c12bddc49262f7aeda84ead4b2fd1의 신규43개·기술문서9절을 git show로 읽었다. 현재 e4128405363348b9e87927d1da67d92773df8f4f의 전용 branch/worktree clean을 확인하고 같은 소유6파일만 수정한다. Claude 소유 파일은 수정/복사/cherry-pick하지 않는다. 이번은 기존 구현에 배정된 두 결함 수정이며 새로운 실행·데이터 범위는 없다.
- D-R1: run_input 상위 run_status가 INVALID_RESOURCE_OVERRUN을 그대로 전달한다. 부채가 상환된 flat 결과여도 원래 무효 상태는 지속한다. 일반 strict/legacy 및 ESTIMATED_ONLY는 기존 RAN, 무입력은 NO_REPLAY_ROWS를 유지한다. 향후 기타 미결 연구 상태도 전달하고 비어 있는 상태는 INCONCLUSIVE_RESEARCH_STATUS다. 결과를 읽는 계층마다 무효 판정이 일관되게 보이도록 했다.
- D-R2: 동결 주문의 같은 symbol/원래 세션에서 signal보다 엄격히 이후, 동결 cursor(pending_candidate_from) 이상,15:10 미만인 남은 후보 범위에 속하는 event만 UNRESOLVED_OBSERVED_AFTER_1510에 귀속한다. BUY에는 원래 진입 창과 추정 나이도 적용한다. unrelated09:40, timeout 처리한 이전 후보, 마감 후 event 및 다른 세션 event를 제외한다. 반면 원래 후보가 다음날 도착한 경우와 원래 미청산 잔량 보고는 유지한다. 실패에 candidate_event_at/pending_candidate_from/order_signal_at을 추가해 근거를 확인할 수 있게 했다. 남은 후보 범위의 행이 당시 즉시 체결 가능했다는 주장은 하지 않는다.
- 회귀4개를 기존 소유 tests/test_day01_research.py에 추가했다: 무효 상태 상위 전달·상환 후 지속, 정상 상태 보존, 무관/timeout/마감후/다른날 event 배제와 잔량 보존, 실제 관련 후보의 주문/event/cursor 근거. 기존27개는 의미를 바꾸지 않고 모두 보존했다. 테스트 import 정리는 범위에 포함하지 않으며 실제 명령은 아래 discovery다.
- 실행: `python -B -m unittest discover -s tests -p test_day01_research.py -v` **31개 통과**. 기존 `-p test_day01.py` **67개**, `-p test_day01_review.py` **108개** 통과. 위와 동일한 테스트 자식 환경 token 키 제외+기존 guarded runner로 **55개 통과**, failure/error/skip0, 보호 journal writer1개 EXCLUDED. 합계 **261개**, expectedFailure0. git diff --check 통과. 기존 실행기11 자체 점검은 변경하지 않아 이번에는 반복하지 않았으며 과거 결과를 현재 통과에 합산하지 않았다.
- Claude 신규 독립43개는 이 브랜치에 가져오지 않았고 expectedFailure를 제거하거나 우회하지 않았다. 41개 통과+2 expectedFailure는 받은 이전 리뷰 증거이며 수정본 독립 검증 결과가 아니다. Claude가 두 표식을 제거하고 재실행하는 단계가 남는다.
- 파일: day01.py, day01_research.py, tests/test_day01_research.py, 사양/데이터준비 문서, 본 작업 기록의6파일. 원래 strategy/PAPER/RAW/API/DB 계약 및 strict/legacy 체결 수치 변경 없음. 새 입력·인프라·실데이터·운영·서버·DB/API/주문·설치·push/PR/main병합 없음. 현재 실제 저장기간/22개 목록 UNKNOWN은 유지한다.
- 장점은 최상위 무효 전파와 후보 근거의 정확성이다. 알려진 한계는 연구 모드의 반사실적 가격·수량/지연 및 미청산 동결이며 이번 수정이 실제 체결 검증을 추가하지 않는다. 개발 개념은 결과 상태의 계층 간 전파, 사건과 주문의 provenance 연결이다. 사용자 확인은 위 명령에서67/108/31 OK 및 무효 예제의 상위 INVALID_RESOURCE_OVERRUN을 확인하는 것이다. GPT/Claude는 실패 후보 범위와 무효 지속을 검토한다.
- 구현·로컬 검증 완료, 독립 재검증/사용자 확인/통합·운영 적용 미완료. 후속 로컬 commit SHA는 완료 보고에서 제공한다. GitHub/branch/worktree는 위 표와 동일, main 병합/배포는 별도 승인 대상이다. 토큰/비용은 표시되지 않아 확인 불가다.
