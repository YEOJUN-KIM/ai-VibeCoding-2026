# 국내주식 PAPER·LIVE 투자 관리 화면

FastAPI 웹 화면에서 모의 자동매매를 연습하고 토스증권 실계좌 자산을 읽기 전용으로 확인하는 개인용 프로젝트다. 전체 Windows 설치 과정은 [설치 가이드](../docs/INSTALL_WINDOWS.md)를 따른다.

## 실행

프로젝트 루트에서 가상환경을 활성화한 뒤 실행한다.

```powershell
.\.venv\Scripts\Activate.ps1
python -m auto_trader
```

- 로그인: `http://127.0.0.1:8000/login`
- PAPER 모의매매: `http://127.0.0.1:8000/paper`
- LIVE 실제계좌 조회: `http://127.0.0.1:8000/live`
- API 문서: `http://127.0.0.1:8000/docs`

`.env`가 없다면 `.env.example`을 복사하고 PostgreSQL과 토스 API 값을 입력한다. 기존 `.env`는 예시 파일로 덮어쓰지 않는다.

## 최초 설정

관리자 계정과 LIVE 숫자 6자리 PIN은 웹이 아닌 터미널에서 설정한다.

```powershell
python -m auto_trader.create_admin
python -m auto_trader.set_live_pin
```

관리자 비밀번호와 PIN 원문은 저장하지 않는다. 로그인 5회 실패 시 기본 15분 동안 잠기며 세션은 기본 30분 후 만료된다. 서버를 재시작하면 기존 로그인 세션은 만료된다.

## 현재 구현 범위

### PAPER

- 가상 시세와 가상 현금 1,000만 원
- 수동 모의 주문과 즉시 체결
- 이동평균 교차 자동매매
- 시작·중지·긴급 정지
- 보수적·기본·직접 설정 위험 한도
- 주문, 보유종목, 수수료·세금과 손익 표시

PAPER 거래 상태는 메모리에 있으므로 서버 재시작 시 초기화된다. 관리자, PIN, 관심종목과 위험 설정은 PostgreSQL에 유지된다.

### LIVE 읽기 전용

- 토스 OAuth 인증과 실제 계좌 조회
- 총자산, 보유주식 평가금액, 원화·외화 매수 가능 금액
- 원화 예산 안에서 살 수 있는 거래대금 상위 국내 종목 후보
- 종목명 일부 또는 종목코드 검색, 인기순 정렬과 8개 단위 페이지 이동
- 현재가, 오늘 등락률, 종목 유형과 인기 순위
- PostgreSQL 기반 관심종목 추가·삭제

실제 주문은 잠겨 있다. 화면의 종목 후보는 당일 거래대금과 예산 조건에 따른 탐색 자료이며 투자 추천이나 수익 보장이 아니다. 산업·테마 정보는 토스 공식 Open API에 제공되지 않아 아직 표시하지 않는다.

## 테스트

```powershell
python -m unittest discover -s tests -v
```

## 관련 문서

- [개발 현황과 다음 작업](../docs/ROADMAP.md)
- [데이터 저장 방식](../docs/DATABASE.md)
- [파일 구조](../docs/PROJECT_STRUCTURE.md)
- [제품 요구사항](../docs/PRD.md)
