# 20261120-Claude-PAPER-APP-UI-01

| 항목 | 기록 |
|---|---|
| 고유 작업 ID | 20261120-Claude-PAPER-APP-UI-01 |
| 목표 | 기존 UX-02 PC 모의매매 화면 + PAPER-MVP 엔진을 FOLIO 실제 앱(메뉴·인증·API)에서 동작하도록 통합, 격리 앱에서 검증 |
| 담당 AI | Claude Code (Sonnet 5.5) |
| 브랜치 / Worktree | claude/paper-mvp-01-20261119 / C:/Users/User/orca/workspaces/ai-VibeCoding-2026/paper-mvp-01 |
| 기준 커밋 | 49d88b0 (UI a424f16은 이미 HEAD 조상) |
| 수정 허용 파일 | auto_trader/paper_mvp_isolated.py(신규), api/paper_mvp.py, lifecycle.py(훅), static/*.html 메뉴 링크, scripts/paper_app_ui_e2e.py, tests/test_paper_app_ui01.py, 이 기록, docs/previews/paper-app-ui-01/ |
| 검증 환경 | 임시 Docker Postgres folio-mvp-app-test-db (127.0.0.1:55432, folio_mvp_test, tmpfs), 실제 app 127.0.0.1:8766, Toss/.env/운영 DB 미사용 |
| 금지 | push, PR, main 병합, 운영 DB/서버/워커 |
| 승인 상태 | 사용자 지시서(PAPER-APP-UI-01): 로컬 수정·테스트·로컬 commit만 |
| 진행 상태 | 검토 대기 (Codex 독립 검토 미실시) |
