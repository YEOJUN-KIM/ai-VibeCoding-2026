# 20261113-Claude-DAY01-dynamic-sizing

## 작업 정의

| 항목 | 기록 |
|---|---|
| 고유 작업 ID | 20261113-Claude-DAY01-dynamic-sizing |
| 목표 / 구현 이유 | 기존 고정 수량 연구 결과를 보존하면서, 위험 한도 안에서 가능한 최대 정수 수량을 계산하는 동적 수량 연구 모드를 추가한다 |
| 담당 AI | Claude Code (구현). Codex는 독립 위험·회계·회귀 검토 |
| GitHub 저장소 | YEOJUN-KIM/ai-VibeCoding-2026 |
| 브랜치 | YEOJUN-KIM/day01-scout-integration-20261113 (기존 통합 브랜치 재사용, 시작 HEAD 1650cc96ec4233c6ecb8a1b11a4dd541caf2342a) |
| Worktree 절대 경로 | C:\Users\User\orca\workspaces\ai-VibeCoding-2026\day01-scout-integration-20261113 |
| 기준 커밋 전체 SHA | 1650cc96ec4233c6ecb8a1b11a4dd541caf2342a |
| 수정 허용 파일 | auto_trader/ml/day01.py(`sizing` 설정과 `replay` 내부만), auto_trader/day01_scout_link.py(`scout_quantity_for`), tests/test_day01_dynamic_sizing.py(신규), docs/specs/DAY01_BACKTEST.md, docs/technical/DAY01_SCOUT_INTEGRATION.md(1문단), 이 기록 파일 |
| 선행 작업 / commit / PR | 20261113-Claude-DAY01-scout-stocks-live. PR #11 변경 없음 |
| 공통 파일 담당자 / 작업 순서 | 없음. `risk_policy.py`, 운영 `RiskManager`, `trading/sizing.py`는 읽기 전용 |
| 완료 조건 | FIXED 기본 동작·기존 테스트 무변경, 동적 모드 테스트 통과, 한도 값 불변, push·PR 없음 |
| 승인된 검증 범위 / 정확한 명령 | 합성 테스트만: `PYTHONPATH=tests python -B -m unittest tests.test_day01_dynamic_sizing tests.test_day01_scout_link tests.test_day01 tests.test_day01_review tests.test_day01_research tests.test_day01_research_review tests.test_day01_scout tests.test_day01_scout_review` 외 신규 모듈들. 실제 API 호출 없음 |
| 실행 환경 격리 확인 | 네트워크·DB·서버·워커·`.env` 미사용. 저장된 실제 스냅샷은 읽기 전용 분석에만 사용(시각 재도장 없음) |
| 미실행 / 금지 작업 | 실제 API, 주문, PAPER/LIVE, DB, 운영 RiskManager 변경, 한도 값 변경, push, PR, main 병합 |
| 승인 상태 | 승인 |
| 사용자 승인 근거 | 2026-11-13 사용자 지시(DAY-01 동적 매수 수량 연구 모드 구현) |
| 진행 상태 | 검토 대기 |

## 착수 확인

- 통합 Worktree clean, HEAD 일치.

## 범위 변경과 인계

- 없음.

## 결과

- PR 없음. 최종 SHA는 완료 보고에 기록.
- 상태: 구현·자동 테스트 완료. 실제 동작·운영 연결은 미실시.
