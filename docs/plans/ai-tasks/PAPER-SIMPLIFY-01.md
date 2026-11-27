# PAPER-SIMPLIFY-01 작업 기록

## 작업 정의

| 항목 | 기록 |
|---|---|
| 고유 작업 ID | PAPER-SIMPLIFY-01 |
| 목표 / 구현 이유 | 모의(PAPER) 자동매매의 사용자 설정 한도를 "자동매매 운용 금액(원)" 하나로 단순화하고, 수동 PAPER 주문의 투자정책 한도를 없앤 상태를 고정한다. 이전 한도 로직은 보존 브랜치에 격리한다. |
| 담당 AI | Claude Code (Orca 코디네이터 지시) |
| GitHub 저장소 | https://github.com/YEOJUN-KIM/ai-VibeCoding-2026 (이번 작업은 push·PR 없음) |
| 브랜치 | `feat/folio-paper-simplify-01` (기준: PR12 head `8c3d66bbd0d08539aa4f4e39cc00524bb371f87c`) |
| 보존 브랜치 | `archive/folio-legacy-risk-settings` = `8c3d66bbd0d08539aa4f4e39cc00524bb371f87c` (로컬 전용) |
| Worktree 절대 경로 | `C:\Users\User\orca\workspaces\ai-VibeCoding-2026\folio-paper-simplify-01` |
| 수정 허용 파일 | `auto_trader/risk.py`, `paper_mvp.py`, `paper.py`, `trading/engine.py`, `static/{paper-mvp.js,paper-mvp.html,app.js,index.html,settings.html}`, `scripts/build_paper_mvp_page.py`, 관련 `tests/`, `docs/technical/PAPER_SIMPLIFY_01.md`, 이 기록 |
| 선행 작업 | PR12(PAPER-SAFETY-FIX 등) — 변경·병합 금지 |
| 완료 조건 | 운용 금액만 자동 한도로 적용, 수동 한도 없음, 이전 로직은 보존 브랜치에서 복원 가능, 격리 DB 테스트 결과 보고 |
| 승인된 검증 범위 | 격리 비-5432 loopback 테스트 DB + 합성 시세 `python -m unittest`, `node --check`/JS 테스트, 격리 데모 서버 HTTP·UI |
| 실행 환경 격리 확인 | 임시 PostgreSQL(127.0.0.1:55433, DB `folio_mvp_test`, 작업 후 폐기), `.env` 없음, Toss·운영 DB·운영 워커 미사용 |
| 미실행 / 금지 작업 | 원격 push/PR, main 병합, 실제 시세·계좌·API, 운영 서버 시작 |
| 승인 상태 | 승인(사용자 지시, 코디네이터 경유) |
| 진행 상태 | 검토 대기 |

결과·테스트 증거는 완료 보고와 `docs/technical/PAPER_SIMPLIFY_01.md`를 참고한다.
