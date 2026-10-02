# 문서 안내

문서는 목적별로 한 가지 내용만 맡습니다. 같은 설명을 여러 파일에 복사하지 않고 필요한 문서를 링크합니다.

| 문서 | 역할 |
|---|---|
| [CURRENT_STATE.md](CURRENT_STATE.md) | 지금 실제로 동작하는 기능과 알려진 제한 |
| [PRD.md](PRD.md) | 제품이 지켜야 할 안정적인 요구사항과 원칙 |
| [ROADMAP.md](ROADMAP.md) | 지금부터 릴리스 전까지의 가까운 작업 |
| [BACKLOG.md](BACKLOG.md) | 지금은 구현하지 않을 후보 기능 |
| [PAPER_STRATEGY.md](PAPER_STRATEGY.md) | PAPER 전략의 현재 신호·체결·손익 계산 방식 |
| [DATABASE.md](DATABASE.md) | PostgreSQL, 메모리 상태, 파일 기록과 백업 정책 |
| [INSTALL_WINDOWS.md](INSTALL_WINDOWS.md) | Windows 설치와 실행 방법 |
| [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md) | 코드 위치와 책임 |
| [CHANGELOG.md](CHANGELOG.md) | 이미 완료된 변경 이력 |
| [VIBE_CODING_NOTES.md](VIBE_CODING_NOTES.md) | 프로젝트와 분리해 보관하는 개발 학습 메모 |
| [machine-learning/](machine-learning/README.md) | 자동매매 머신러닝의 설치·데이터·학습·운영·포트폴리오 과정 |

## 문서 갱신 규칙

- 기능이 완성되면 `CURRENT_STATE.md`와 `CHANGELOG.md`를 갱신합니다.
- 요구사항 자체가 바뀌면 `PRD.md`를 갱신합니다.
- 바로 진행할 작업만 `ROADMAP.md`에 둡니다.
- 보류된 아이디어는 `BACKLOG.md`에 두고 시작할 때 로드맵으로 옮깁니다.
- PAPER 계산식은 `PAPER_STRATEGY.md`, 저장 방식은 `DATABASE.md`만 기준으로 삼습니다.
- 머신러닝 학습과 구현 순서는 `machine-learning/README.md`를 기준으로 삼습니다.
- 과거 구현 과정과 일자별 기록을 PRD나 로드맵에 다시 적지 않습니다.
