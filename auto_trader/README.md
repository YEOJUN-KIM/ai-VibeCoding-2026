# 애플리케이션 코드 안내

이 폴더는 FOLIO의 서버·모의 엔진·증권 연동·웹 자산·학습용 수집 코드를 담습니다. 기능·설치 설명은 중복 관리하지 않고 아래 문서를 기준으로 합니다.

- [프로젝트 소개](../docs/PROJECT_OVERVIEW.md)
- [현재 기능과 제한](../docs/CURRENT_STATE.md)
- [코드 구조와 파일별 책임](../docs/technical/PROJECT_STRUCTURE.md)
- [Windows 설치·실행](../docs/guides/INSTALL_WINDOWS.md)
- [모의 전략·체결·손익 기준](../docs/specs/PAPER_STRATEGY.md)
- [저장·복원 정책](../docs/technical/DATABASE.md)

평소 실행은 프로젝트 루트의 `FOLIO.cmd`를 사용합니다. 서버만 직접 실행하려면 루트에서 `.\.venv\Scripts\python.exe -m auto_trader`를 실행합니다.
