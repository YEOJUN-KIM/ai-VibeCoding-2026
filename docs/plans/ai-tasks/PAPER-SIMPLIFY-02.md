# PAPER-SIMPLIFY-02 작업 기록

## 작업 정의

| 항목 | 기록 |
|---|---|
| 고유 작업 ID | PAPER-SIMPLIFY-02 |
| 목표 / 구현 이유 | 독립 설정 화면 제거와 계정 메뉴 이전, 수동 주문 투자정책 경고 4종·동의 제거(중립 전후 변화 안내), PAPER 설정을 운용 금액 하나로 축소, 구형 `/settings`·`/paper`·전략 CRUD·프리셋 정리 |
| 담당 AI | Claude Code (Orca 코디네이터 지시, 사용자 승인) |
| GitHub 저장소 | https://github.com/YEOJUN-KIM/ai-VibeCoding-2026 (이번 작업은 push·PR·merge 없음, 로컬 커밋만) |
| 브랜치 | `feat/folio-paper-simplify-01` (기준 `9a4eb0ac53ce94a6423e66c79bacda4c102071fe`) |
| 보존 ref | `archive/folio-legacy-risk-settings` = `8c3d66bbd0d08539aa4f4e39cc00524bb371f87c`(변경 없음), 커밋 `9a4eb0ac53ce94a6423e66c79bacda4c102071fe`(SIMPLIFY-01 완료 상태; 당시 로컬 ref `preserve/paper-simplify-01-9a4eb0a`는 이후 삭제되어 현재 없음, 커밋은 브랜치 이력에서 직접 복구 가능) |
| Worktree 절대 경로 | `C:\Users\User\orca\workspaces\ai-VibeCoding-2026\folio-paper-simplify-01` (새 worktree 없음) |
| 수정 허용 파일 | `auto_trader/{api,services,static}`·`paper_mvp.py`·`models.py` 등 위 범위, `scripts/build_paper_mvp_page.py`·e2e 스크립트, 관련 `tests/`, `docs/` |
| 선행 작업 | PAPER-SIMPLIFY-01 (PR12/main/기존 보존 ref 변경 금지) |
| 완료 조건 | 구현·회귀 테스트·가능한 격리 HTTP/UI 검증, 정리 목록·복원법 문서 |
| 승인된 검증 범위 | 격리 비-5432 loopback 테스트 DB + 합성 시세 `python -m unittest`, JS 테스트, 격리 서버 HTTP·Playwright. 운영 DB·`.env`·실제 증권 API·실주문 접근 금지 |
| 실행 환경 격리 확인 | 임시 PostgreSQL 127.0.0.1:55433(DB `folio_test`, 스크래치 디렉터리, 작업 후 종료), `.env` 없음 |
| 미실행 / 금지 작업 | 원격 push/PR/merge, 실제 시세·계좌·실주문, 운영 서버 |
| 승인 상태 | 승인(사용자 지시) |
| 진행 상태 | 검토 대기 |

상세는 `docs/technical/PAPER_SIMPLIFY_02.md` 참고.
