# 문서 안내

문서는 목적별로 한 가지 내용만 맡습니다. 같은 설명을 여러 파일에 복사하지 않고 필요한 문서를 링크합니다.

| 문서 | 역할 |
|---|---|
| [CURRENT_STATE.md](CURRENT_STATE.md) | 지금 실제로 동작하는 기능과 알려진 제한 |
| [PRD.md](PRD.md) | 제품이 지켜야 할 안정적인 요구사항과 원칙 |
| [ROADMAP.md](ROADMAP.md) | 가까운 작업, 릴리스 전 확인과 후속 방향 요약 |
| [BACKLOG.md](BACKLOG.md) | 지금은 구현하지 않을 후보 기능 |
| [DEPLOYMENT_AND_MODEL_PLAN.md](DEPLOYMENT_AND_MODEL_PLAN.md) | 배포·기기 이동·모델 제공·사용자 데이터 참여의 합의와 검토 사항 |
| [PAPER_STRATEGY.md](PAPER_STRATEGY.md) | PAPER 전략의 현재 신호·체결·손익 계산 방식 |
| [DATABASE.md](DATABASE.md) | PostgreSQL, 메모리 상태, 파일 기록과 백업 정책 |
| [INSTALL_WINDOWS.md](INSTALL_WINDOWS.md) | Windows 설치와 실행 방법 |
| [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md) | 코드 위치와 책임 |
| [CHANGELOG.md](CHANGELOG.md) | 이미 완료된 변경 이력 |
| [VIBE_CODING_NOTES.md](VIBE_CODING_NOTES.md) | 프로젝트와 분리해 보관하는 개발 학습 메모 |
| [machine-learning/](machine-learning/README.md) | 자동매매 머신러닝의 설치·데이터·학습·운영·포트폴리오 과정 |

기준일: 2026-10-04. 현재는 개인용 Windows 로컬 웹앱입니다. 계정별 연결·모의계좌 분리와 로컬 API 키 보관은 구현되었으며, 휴대폰 연결 앱·토스 로그인·공개 서비스 운영은 구현하지 않았습니다. 현재 동작은 `CURRENT_STATE.md`, 저장·백업 기준은 `DATABASE.md`, 후속 계획은 `BACKLOG.md`에서 확인합니다. `CHANGELOG.md`의 과거 기록은 당시 동작이며 현재 사양을 대신하지 않습니다.

## 지금 읽을 문서

기본 기능 구현은 대부분 마쳤으며 현재는 데이터 축적과 모의 운용 검증 단계입니다. 다음 할 일은 [ROADMAP.md](ROADMAP.md), 데이터 준비와 모델 학습 상태는 [머신러닝 구현 현황](machine-learning/CURRENT_PROGRESS.md)에서 확인합니다. 모델 학습·실시간 모델 판단·실제 자동매매는 아직 완료되지 않았습니다. 실행 방법은 [Windows 안내](INSTALL_WINDOWS.md)의 `FOLIO.cmd` 설명을 따릅니다.

## 문서 갱신 규칙

- 기능이 완성되면 `CURRENT_STATE.md`와 `CHANGELOG.md`를 갱신하고 관련 사용·저장 문서도 함께 맞춥니다. `BACKLOG.md`·`ROADMAP.md`의 완료된 항목은 제거하거나 잔여 범위만 남깁니다.
- 요구사항 자체가 바뀌면 `PRD.md`를 갱신합니다.
- 가까운 작업은 `ROADMAP.md`에 두고, 사용자가 결정한 후속 방향은 구현 보류를 명시한 요약과 백로그 링크로만 남깁니다.
- 보류된 아이디어는 `BACKLOG.md`에 두고 시작할 때 로드맵으로 옮깁니다.
- 배포·기기 이동·모델 제공·데이터 참여에 관한 논의는 `DEPLOYMENT_AND_MODEL_PLAN.md`에 합의한 방향·검토 중·현재 구현을 구분해 기록합니다. 구현을 결정한 항목만 로드맵으로 옮깁니다.
- PAPER 계산식은 `PAPER_STRATEGY.md`, 저장 방식은 `DATABASE.md`만 기준으로 삼습니다.
- 머신러닝 학습과 구현 순서는 `machine-learning/README.md`를 기준으로 삼습니다.
- 과거 구현 과정과 일자별 기록을 PRD나 로드맵에 다시 적지 않습니다.
