# 20261112-Codex-DAY01-next-open

## 작업 정의

| 항목 | 기록 |
|---|---|
| 고유 작업 ID | 20261112-Codex-DAY01-next-open |
| 목표 / 구현 이유 | 완성 봉의 미래 종가/거래량을 과거 시가 체결 근거로 사용하지 않는 합성 next-open 기본 모드 |
| 담당 AI | Codex 구현, Claude 독립 비판/재검증, coordinator 통합 검토 |
| GitHub 저장소 | https://github.com/YEOJUN-KIM/ai-VibeCoding-2026 |
| 브랜치 | YEOJUN-KIM/day01-codex-20261112 |
| Worktree 절대 경로 | C:\Users\User\orca\workspaces\ai-VibeCoding-2026\day01-codex-20261112 |
| 기준 후보 SHA | 6408858fa08e2e7e2ffcf4884fa069a246b27391 |
| 로컬 동기화 merge SHA | 6d8d2b192c3e8aca3b5d75b6d33696421ab2c735 |
| 수정 허용 파일 | auto_trader/ml/day01.py; tests/test_day01.py; docs/specs/DAY01_BACKTEST.md; 본 기록 |
| 선행 작업 | DAY01 구현·D1-D3 수정 및 Claude 독립45+79 검증 후보 |
| 공통 파일 담당자 / 순서 | 위 네 파일은 Codex 전용; Claude review 테스트/기술문서/새 review 기록은 읽기 전용, 구현 인계 후 독립 검증 |
| 완료 조건 | 명시적 인과적 open 증거 기반 기본 모드, 레거시 합성 모드 보존, 124개 기존 검증과 신규 모드 테스트, 문서·로컬 commit·인계 |
| 승인된 검증 | python -B -m unittest discover -s tests -p test_day01.py; python -B -m unittest discover -s tests -p test_day01_review.py (변경 전 기준); 변경 후 명시적 legacy wrapper와 next-open 개별 합성 테스트; git diff --check |
| 환경 격리 | 표준 라이브러리 + RiskPolicy만 사용; synthetic 입력; -B로 bytecode 쓰기 억제; DB/API/서버/설정 로딩 없음 |
| 금지 | .env/비밀정보/운영 ignored 파일, 실제시장 데이터, DB/증권/앱/워커/설치/PAPER·LIVE 주문, 새 orchestrator, push/PR/main 병합 |
| 승인 상태 | 승인: task_d4e94c6fc92f / ctx_aaefe0d4d5b0의 기능 브랜치 후보 동기화·네 파일 구현·로컬 commit |
| 진행 상태 | 검토 대기 (구현·로컬 자동 검증 완료, 독립 재검증·통합 미완료) |

## 착수 확인

- 2026-11-12 KST, 이전 HEAD bad9d2152879c512d3efa768c06287afd8a19537에서 branch/worktree clean 확인. coordinator는 양쪽 clean과 원격 PR11 및 UI/docs 작업의 범위 무중복을 확인해 인계했다.
- 지정 `git merge --ff-only 6408858...`는 cherry-pick된 분기 이력 때문에 불가능했다. HEAD와 후보의 구현/소유 테스트/사양 세 파일은 동일하고 차이는 Claude 소유 리뷰 세 파일의 추가뿐임을 확인했다.
- 지시에서 허용한 일반 로컬 merge로 정확 후보를 통합했다. 충돌 없음, 다른 worktree/main 수정 없음. merge SHA는 위 표와 같다. Claude 파일은 승인된 baseline 통합 결과이며 이번 구현에서 편집하지 않는다.
- AGENTS·TEMPLATE·협업 운영절차를 읽었다. 새 작업 기록은 기존 DAY01 기록과 별도다. 수정 전 안전 import 경로를 확인한 기준 합성 테스트45개와 독립 리뷰79개가 모두 통과(124개, expected failure0)했다.

## 계약 검토와 인계

- 코드 작성 전 msg_3979bdad08aa로 OpenQuote 별도 시가·가용시각·opening liquidity 증거와 엄격한 signal<open, 기본 next_open/명시적 legacy_close, 시간 phase 순서를 제안해 Claude 독립 비판을 요청했다. coordinator가 relay했고 독립 비판 전에는 의존 구현을 보류한다.
- Candle-only 이력은 시가 시점의 가용 가격/거래량을 증명하지 못한다. 신규 모드가 별도 synthetic 시가 증거 없이 과거 open에서 체결되었다고 주장해서는 안 된다.
- Claude가 계약 A~H를 비판해 coordinator msg_647723a2065a로 전달했다. 별도 시가 증거, entry-bar 범위 감시, 타이머→open→완성봉 순서, 용량·mark high-water·동결·불일치·legacy 정책을 반영했다.
- 기존 MA/backtest 최소 회귀 후보는 settings를 통해 .env 로딩으로 이어진다. unguarded 실행은 하지 않았다. 이후 Claude 문서 commit4bf3f8d의 의존성 정적 감사와 안전 실행기를 검토해, 사용자 허용 범위에서 엔진 로직을 스텁하지 않는 기존 backtest12개만 격리 실행했다. 아래에 재현 명령을 기록한다. 기존 코드 수정 없음.

## 결과

- 구현 이유: 완성 분봉 가격·용량을 과거 시가에 사용할 수 없으므로 `OpenQuote(symbol,event_at,available_at,price,executable_volume)`를 별도 입력으로 추가했다. Config 기본은 next_open, 이전 합성 종가 계산은 명시적 legacy_close다. 신규 설정을 숨겨 기존 assertions를 바꾸지 않았다. 이전 task 기록은 이번 소유 범위 밖이므로 수정하지 않았다.
- 물리 시각: 완성09:19봉이09:20에 가용하여 만든 신호의 첫 eligible open은 strict signal<event인09:21이다. quote.available_at==event_at인 합성 증거만 체결하며 execution_at=observation_at=observed_at=시가 관측 시각이다. 미래 OHLC·1분 거래량·같은 T에 늦은 phase로 도착한 Candle 위험/mark를 앞선 open 체결에 사용하지 않는다. same-time 타이머 우선, 시가 전체 mark 후 SELL→BUY·종목 정렬, 신규 갭 판단, 완성 봉 도착·판단 순서다.
- 용량과 실패: 당시에 알려진 quote.executable_volume×participation의 정수 내림만 사용한다. 누락·지연·0·subunit이면 가짜 체결하지 않는다. 기본2분 exclusive ENTRY 만료, 부분 매수 나머지 취소·부분 매도 잔량 유지, 14:50 신규 청산은14:51 이후,15:09시가는 가능/15:10은 동결 우선이다. 미청산 수량·순청산 평가·실현손익·갭 미모델링 플래그를 보존한다.
- 위험/판단: 진입 분봉(event_at==entry_at)은 완성되어 도착한 뒤 전체 고저가를 감시하고 dual-hit은STOP 우선으로 후속 open 주문을 만든다. open 갭을 새로 관측해 만든STOP을 같은 open에 체결하지 않는다. 대기 주문의 기준가는 실제로 관측한 후속 open이며 미래 종가/저가로 보정하지 않는다. 늦은 위험 정보의 우선순위·진입 차단, 최신 사건 mark와 같은 사건의 completed 정보 우선, 미청산 동결은 유지했다.
- 결과 스키마: trades에 decision_at, observation_at, price_source 추가; 기본 mode evidence_close=None 명시. 신규 execution_evidence에 source/capacity_source/quote 수·지연·용량부족·missing 표시, summary에 open_quote_fills/legacy_close_fills 카운터 추가. next_open valuation_marks에는 price_source가 추가된다. 이후 Candle.open과 quote 가격 불일치는 발견 시점 사유만 추가하고 과거 체결은 고치지 않는다. legacy에 선택된 quotes를 섞거나 모르는 execution 값을 주면 거절한다.
- 자동 검증1: `python -B -m unittest discover -s tests -p test_day01.py` → **67개 통과**(기존45개 명시적 legacy + 기본 next-open 신규22개), 실패/오류/expected failure0. 새22개는 Candle-only 무체결 보고, strict09:21·분리 시각·가격/비용, 미래OHLCV 불변, entry-bar dual-hit, gap 트리거와 다른 open 체결, 실제 target 후속 가격, 만료 경계, 늦은 quotes, 용량0/subunit·부분체결,14:50/15:09/15:10, 동시open 위험 검사, 같은T Candle crash의 비소급, mark 우선순위·불일치, validation/중복/충돌/universe/UTC/결정성, 모드 검증, 익일동결을 포함한다.
- 자동 검증2: `python -B -c "import sys; sys.path.insert(0,'tests'); from test_day01 import run_legacy_review; run_legacy_review()"` → **독립79개 통과**. 커밋된 wrapper는 Claude 원본 테스트 모듈의 Config 생성자만 명시적 legacy subclass로 바꾸고 복원한다. assertions·expectedFailure·발견 범위는 수정하지 않았고 Claude 파일도 편집하지 않았다. 기존124개 통과는 legacy 회귀이며 새 기본 모드22개와 구분한다.
- 자동 검증3: Claude 정적 감사 문서 `4bf3f8d:docs/technical/DAY01_REVIEW.md` 6.2의 검토된 실행기를 메모리에서 실행해 **기존 tests.test_ml_backtest 12개 통과**, 실패/오류/skip0. 엔진을 스텁하지 않고 `.env` 존재 시 시작거부, 관련 자격 환경변수 제거, 설정/운영 기록 파일 접근·DB 연결·외부socket 차단을 적용한다. asyncio를 위한 일회성 높은 포트 loopback 예외는 원본 감사 실행기와 동일하지만 해당12개는 네트워크가 필요 없다. 실행기/임시파일을 저장소에 만들지 않았다.
- 합계는 이번 최종 검사 기준 **158개 통과**(legacy124 + next_open22 + 기존backtest12). `git diff --check`도 통과했다. 설치·전체 테스트 탐색·시장데이터·실제 주문/DB/서버 검증 없음. 순수성은 production import가 기존 표준 라이브러리+RiskPolicy뿐임과 독립 static 검증으로 확인했다.

안전한 최소 회귀의 실제 PowerShell 재현 명령(원본 실행기 소스를 불변 commit에서 읽어 stdin으로만 실행):

```powershell
$day01RegressionCode = @'
import subprocess, sys
text = subprocess.run(['git', 'show', '4bf3f8d:docs/technical/DAY01_REVIEW.md'], capture_output=True, text=True, encoding='utf-8', check=True).stdout
source = text.rsplit('```python\n', 1)[1].split('```', 1)[0]
sys.argv = ['reviewed_safe_runner', '.', 'tests.test_ml_backtest']
exec(compile(source, '<Claude-reviewed-safe-runner-4bf3f8d>', 'exec'))
'@
$day01RegressionCode | python -B -
```

- 사용자가 직접 확인: 운영 파일 없는 현재 독립 worktree에서 위 합성67개와 legacy79개 명령을 실행하면 OK를 기대한다. 사양의 별도 OpenQuote 합성 예제는 BUY/SELL 후 FLAT이다. Candle-only 기본 입력은 가격을 소급하지 않고 fills0과 missing evidence를 반환한다. 실제 입력은 시가 순간의 별도 가용가격·용량 증거를 수집하는 별도 설계/검증이 필요하며 이번에 연결하지 않았다.
- 장점은 실제 정보 도착 순서에 따른 인과성·추적성과 명시적 모드 전환이다. 단점은 정확한 시가 순간 수신 증거 가정 때문에 실제 피드가 대체로 부적합하고 snapshot 용량이 실제 체결을 보장하지 않으며, 극단값·운영시장·호가순서·익일갭을 검증하지 않았다는 점이다. 수익성 주장은 없다.
- 기존 API/DB/MA/PAPER/backtest 소스 변경 없음. 새 day01 Config/replay/OpenQuote/result 계약만 위처럼 확장했다. 수정 파일은 지정 네 파일뿐이며 Claude 소유 파일은 baseline merge 이후 편집하지 않았다. 새 orchestrator·bridge 없음.
- 구현 및 자동 합성/안전 회귀 완료. Claude 수정본 독립 리뷰·사용자 동작 확인·통합·운영 적용은 미완료다. GPT/Claude는 same-T phase 순서, entry-bar 범위 감시, open 용량 출처, 모드별 결과 해석과 동결 미청산을 검토해야 한다. 개발 개념은 사건/정보/물리 실행 시각의 분리와 실행 증거의 독립성이다.
- GitHub/branch/worktree는 표와 같다. 로컬 commit SHA는 완료 보고로 제공하며 자기 참조 반복 commit은 하지 않는다. push/PR 없음; main 병합/배포/운영 적용은 별도 사용자 승인이 필요하다. 사용량 토큰/비용은 표시되지 않아 확인 불가다.
