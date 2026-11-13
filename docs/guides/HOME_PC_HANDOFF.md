# 집 PC 개발 재개 안내 (HOME-HANDOFF-01)

기준: 2026-11-13. 이 문서는 개발 재개 절차만 다룹니다. 설치 상세는 [Windows 설치 안내](INSTALL_WINDOWS.md), 공통 규칙은 `AGENTS.md`를 따릅니다.

## 1. Clone 및 브랜치 Checkout

```powershell
git clone https://github.com/YEOJUN-KIM/ai-VibeCoding-2026.git
cd ai-VibeCoding-2026
git fetch origin
git checkout YEOJUN-KIM/day01-scout-integration-20261113
git log --oneline -3
```

- 이 브랜치는 `origin/main`(38b44be)에서 갈라져 DAY-01 Scout·후보 봉 준비·호가 연동 작업(합성, fail-closed)을 담고 있습니다. main에는 병합되지 않았습니다.
- main 병합, force push, 기존 PR 변경은 사용자 승인 없이 하지 않습니다.

## 2. Python 환경

- Python 3.12 이상(현재 PC는 3.14.7로 확인).
- DAY-01 코드와 테스트는 **표준 라이브러리만** 사용합니다. 합성 테스트만 돌린다면 `pip install`이 필수는 아닙니다.
- 앱 전체(`auto_trader`)를 쓰려면 다음을 설치합니다. 앱 실행은 승인 전까지 하지 않습니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# ML 학습 패키지는 requirements-ml.txt, docs/machine-learning/01_ENVIRONMENT.md 참고
```

## 3. 합성 테스트 실행

`.env`가 없고 민감 환경변수가 없는 상태에서 실행합니다. 가드 러너는 DB·네트워크·`.env` 접근을 차단합니다.

```powershell
python -B tests/day01_safe_regression_runner.py --selftest
python -B tests/day01_safe_regression_runner.py tests.test_day01_scout_pipeline tests.test_day01_scout tests.test_day01_review
```

- 러너는 이름이 `*_TOKEN`, `*_SECRET`, `*_API_KEY`, `POSTGRES_*`, `TOSS_*`로 시작/끝나는 환경변수가 있으면 실행을 거부합니다. 에이전트 도구가 주입한 토큰 변수가 있으면 해당 터미널에서만 제거하고 실행합니다.
- `tests.test_day01_scout_orderbook`은 `127.0.0.1` 임시 포트에 로컬 모의 서버를 열기 때문에 가드 러너에서는 setUpClass 오류가 납니다. 이 모듈은 일반 방식으로 실행합니다(외부 접속 없음).

```powershell
python -B -m unittest tests.test_day01_scout_orderbook
```

- 모듈을 한 프로세스에 모두 모아 실행하면 `test_day01_scout_review.test_no_app_settings_db_broker_imports`가 다른 모듈이 import한 `auto_trader.brokers` 때문에 실패합니다. 모듈별로 실행하면 통과합니다.

## 4. Orca·Codex·Claude Code 연결 시 확인

- Orca Worktree 경로는 PC마다 다릅니다. 작업 기록(`docs/plans/ai-tasks/*.md`)의 절대 경로는 과거 PC 기준 기록이므로 그대로 사용하지 않습니다.
- 새 작업은 `docs/plans/ai-tasks/TEMPLATE.md`로 기록을 만들고 담당 AI·브랜치·수정 허용 파일을 확인한 뒤 착수합니다.
- 착수 전 GitHub의 활성 브랜치와 열린 PR을 확인합니다. 다른 AI와 범위가 겹치면 중단하고 보고합니다.
- Claude Code·Codex 세션은 `CLAUDE.md`와 `AGENTS.md`를 읽습니다. 로컬 Worktree가 서로 독립인지 확인합니다.

## 5. 집 PC에서 별도 설정해야 하는 인증정보

Git에는 올라가지 않습니다. `.env.example` 기준 변수 **이름**만 안내합니다. 필요할 때만 `.env`로 복사해 직접 입력합니다.

- `TOSS_CLIENT_ID`, `TOSS_CLIENT_SECRET`, `TOSS_ACCOUNT`(선택): 증권 API. 공인 IP 등록이 PC마다 필요합니다.
- `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
- `DART_API_KEY`, `OPENAI_API_KEY`(선택)
- `LIVE_TRADING_ENABLED`는 `false`로 유지합니다.
- GitHub 로그인(`gh auth login` 또는 Git Credential Manager).

운영 DB 덤프, `.backups`, `.paper-history`, 수집 RAW 데이터는 Git에 없으며 복사하지 않습니다.

## 6. 실제 DB·API·PAPER 실행 전 승인 조건

다음이 모두 확인되기 전에는 앱 서버, DB 마이그레이션, 수집 워커, 통합 테스트, 실제 Toss 호출을 하지 않습니다.

1. 운영 DB와 분리된 개발·테스트 DB
2. 모의 증권 API 또는 외부 주문 차단
3. 독립 포트·로그·캐시·산출물
4. 운영 API 키 미사용
5. 자동 수집 워커·예약 작업 비활성화
6. 사용자가 승인한 테스트 명령과 범위

`scripts/day01_scout_live_check.py`, `scripts/day01_scout_stocks_live_check.py`는 외부 시세 호출을 하는 스크립트입니다. 승인 없이 실행하지 않습니다.

## 7. 미해결 과제

- **공용 PC 시간 동기화 문제**가 미해결입니다. 시각을 기준으로 하는 수집·시세 수신 시각 검증은 동기화가 확인되기 전까지 신뢰하지 않습니다.
- 위 합성 테스트 결과는 DAY-01 합성 검증입니다. 사용자 동작 검증, 실제 PAPER 검증, 운영 적용은 아직 아닙니다.
- 다른 Worktree의 브랜치(`day01-claude-review`, `day01-codex`, `day01-scout-claude`, `day01-scout-codex-review` 등)는 원격에 올라가 있지 않으며 이번 Push 대상이 아닙니다. 필요하면 별도로 승인받아 Push합니다.
