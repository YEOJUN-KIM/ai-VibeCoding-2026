# 20261119-Claude-PAPER-MVP-01

| 항목 | 기록 |
|---|---|
| 고유 작업 ID | 20261119-Claude-PAPER-MVP-01 |
| 목표 | DAY-01 Scout 후보를 기존 PAPER 엔진에 연결하고 PC 페이지에서 설정·시작·정지·결과 확인 (합성 데이터 격리 검증) |
| 담당 AI | Claude Code (Sonnet 5.5) |
| 브랜치 / Worktree | claude/paper-mvp-01-20261119 / C:/Users/User/orca/workspaces/ai-VibeCoding-2026/paper-mvp-01 |
| 기준 커밋 | 505c05d28e51daf1d43d34486fa0c997ef441c85 (DAY-01 통합) + a424f1680216888cae89228cea393a93d847d8a0 (UI, 로컬 병합) |
| 수정 허용 파일 | auto_trader/{paper_mvp,paper_mvp_synthetic}.py, api/paper_mvp.py, api/pages.py, main.py, paper.py, trading/engine.py(훅), static/paper-mvp.*, scripts/*paper_mvp*, tests/test_paper_mvp.py, tests/fixtures/api_before_refactor.json, 이 기록, docs/previews/paper-mvp-01/ |
| 검증 환경 | 임시 Docker Postgres(127.0.0.1:55432, folio_mvp_test, 메모리 저장), 합성 시장, 운영 DB·.env·Toss 미사용 |
| 금지 | push, PR, main 병합, 운영 DB/서버/워커 |
| 승인 상태 | 사용자 지시서(PAPER-MVP-01)로 승인: 로컬 수정·테스트·로컬 commit만 |
| 진행 상태 | 검토 대기 |
