# AI VibeCoding 2026

국내주식 조회, 장기 관찰 후보 분석, PAPER 전략 검증, 토스증권 LIVE 계좌·주문을 한곳에서 다루는 개인용 투자 도구입니다.

## 현재 제공하는 기능

- PIN 로그인, 12시간 슬라이딩 세션, 수동 화면 잠금
- 국내주식 탐색, 상세 차트, 기업정보, 뉴스와 관심종목
- 장기 관찰 점수·등급, 자동 추천 후보와 내 관찰 후보
- 저장한 DRY RUN 전략을 실제 시세로 검증하는 PAPER 자동매매
- 토스증권 LIVE 자산 조회와 안전장치가 적용된 수동 주문
- 전략 설정, 위험 한도, 주문·판단 기록

현재 구현 범위와 제한은 [문서 안내](docs/README.md)에서 확인할 수 있습니다.
프로젝트를 만들며 정리한 학습 메모는 [바이브 코딩 학습 노트](docs/VIBE_CODING_NOTES.md)로 분리했습니다.
자동매매 머신러닝 학습 과정은 [머신러닝 문서](docs/machine-learning/README.md)에 단계별로 정리했습니다.

## 로컬 실행

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python -m auto_trader.create_admin
python -m uvicorn auto_trader.main:app --reload
```

Windows PostgreSQL 준비와 환경 변수 설정은 [Windows 설치 안내](docs/INSTALL_WINDOWS.md)를 따릅니다. 기본 주소는 `http://127.0.0.1:8000`입니다.

## 개발 확인

```powershell
python -m unittest discover -s tests -v
```

실계좌 주문은 별도 PIN과 서버 안전 잠금을 모두 통과해야 합니다. 개발 중에는 안전 잠금을 해제하지 않는 것을 기본으로 합니다.
