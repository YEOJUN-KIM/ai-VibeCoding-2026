# 자동매매 프로젝트 파일 설명

이 문서는 현재 프로젝트의 주요 파일과 역할을 정리한다. 자동 생성되는 `__pycache__`, `.pyc`, 테스트 캐시 등은 생략한다.

## 전체 구조

```text
auto_trader/
├── __init__.py
├── __main__.py
├── main.py
├── models.py
├── settings.py
├── database.py
├── schema.sql
├── auth.py
├── create_admin.py
├── set_live_pin.py
├── toss.py
├── favorites.py
├── simulator.py
├── paper.py
├── risk.py
├── strategy.py
├── static/
│   ├── login.html
│   ├── login.js
│   ├── index.html
│   ├── app.js
│   ├── live.html
│   ├── live.js
│   └── styles.css
└── README.md

docs/
├── INSTALL_WINDOWS.md
├── DATABASE.md
├── PRD.md
├── PROJECT_STRUCTURE.md
└── ROADMAP.md

tests/
├── test_auth.py
├── test_favorites.py
├── test_paper.py
└── test_toss.py
```

## 서버와 공통 설정

### `__main__.py`

`python -m auto_trader` 명령의 진입점이다. Uvicorn으로 FastAPI 서버를 실행한다.

### `main.py`

FastAPI 앱, 웹 페이지와 API 라우트를 연결한다. 인증, PAPER 계좌, 위험 설정, 토스 LIVE 계좌, 국내 종목 검색, 관심종목 API를 한곳에서 조합한다.

### `models.py`

계좌·주문·전략·LIVE 자산·종목 검색·관심종목 등 요청과 응답 데이터의 형식을 Pydantic 모델로 정의한다.

### `settings.py`

프로젝트 루트의 `.env`를 읽는다. 서버, PAPER, 전략, PostgreSQL과 토스증권 연결 설정을 코드와 분리한다.

### `database.py`, `schema.sql`

PostgreSQL 연결과 트랜잭션을 관리하고, 앱 시작 시 필요한 테이블과 인덱스를 생성한다.

## 보안과 인증

### `auth.py`

관리자 비밀번호와 LIVE PIN의 scrypt 해시, 로그인 실패 잠금, 세션 쿠키와 CSRF 검사를 담당한다.

### `create_admin.py`

`python -m auto_trader.create_admin` 명령으로 단일 관리자 계정을 만든다.

### `set_live_pin.py`

`python -m auto_trader.set_live_pin` 명령으로 LIVE 잠금 해제용 숫자 6자리 PIN을 설정하거나 변경한다.

## PAPER 모의매매

### `simulator.py`

실제 주문과 분리된 가상 주식시장이다. 실제 시세는 시작값으로만 사용할 수 있으며 이후 가격 변화는 시뮬레이션이다.

### `paper.py`

가상 현금, 보유종목, 주문, 체결과 손익을 메모리에서 관리한다. 서버를 재시작하면 PAPER 거래 상태가 초기화된다.

### `risk.py`

보수적·기본·직접 설정 투자 한도를 관리하고 수동·자동 매수 전에 공통 위험 규칙을 검사한다. 선택한 설정은 PostgreSQL에 저장한다.

### `strategy.py`

이동평균 교차 전략, 자동매매 시작·중지와 긴급 정지를 담당한다. 현재 주문은 PAPER 계좌에만 반영된다.

## 토스 LIVE 조회

### `toss.py`

토스증권 OAuth 토큰, REST 요청, 압축 응답 해제, 토큰 무효화 재시도와 짧은 조회 캐시를 담당한다. 실제 계좌 잔고, 보유종목, 매수 가능 금액, 거래대금 순위와 종목 검색 데이터를 읽는다.

### `favorites.py`

사용자별 관심종목을 PostgreSQL에 추가·조회·삭제한다. 한 사용자는 최대 20개를 저장할 수 있다.

## 웹 화면

### `static/login.html`, `static/login.js`

`프라이빗 투자 데스크` 관리자 로그인 화면과 로그인 요청을 담당한다.

### `static/index.html`, `static/app.js`

PAPER 대시보드다. 가상 자산, 모의 주문, 전략 상태, 투자 한도와 자동매매 제어를 표시한다.

### `static/live.html`, `static/live.js`

읽기 전용 LIVE 자산 화면이다. 다음 내용을 표시한다.

- 총자산(보유주식 평가금액 + 원화 매수 가능 금액)
- 보유종목과 원화·외화 매수 가능 금액
- 현재 원화 예산으로 살 수 있는 거래대금 상위 국내 종목
- 인기순 국내 종목 부분 검색과 8개 단위 페이지 이동
- 종목 현재가, 오늘 등락률, 종목 유형과 인기 순위
- PostgreSQL에 저장되는 관심종목 추가·삭제

토스 공식 Open API 응답에 산업 분류 필드가 없어 산업/테마 표시는 아직 제공하지 않는다. 실제 주문 기능도 잠겨 있다.

### `static/styles.css`

로그인, PAPER와 LIVE 화면의 공통 바이올렛 테마, 간격, 카드, 표, 버튼과 반응형 레이아웃을 담당한다. 상승은 빨간색, 하락은 파란색, 관심종목은 금색으로 구분한다.

## 프로젝트 루트

- `requirements.txt`: FastAPI, Pydantic, Uvicorn, psycopg 등 고정된 Python 의존성
- `.env.example`: 비밀값이 없는 환경 변수 예시
- `.gitignore`: `.env`, 가상환경, 캐시 등 Git 제외 규칙
- `README.md`: 프로젝트 입구와 문서 링크
- `docs/INSTALL_WINDOWS.md`: 새 Windows PC 설치·실행 절차
- `docs/ROADMAP.md`: 구현 현황과 다음 작업 순서
- `docs/PRD.md`: 제품 목표와 요구사항
- `docs/DATABASE.md`: 영구 저장 데이터와 메모리 데이터 구분

## 테스트

- `test_auth.py`: 관리자 로그인, 세션, PIN과 인증 보호
- `test_favorites.py`: 관심종목 저장 제한과 사용자별 분리
- `test_paper.py`: PAPER 체결, 비용, 중복 요청, 동시성과 위험 한도
- `test_toss.py`: 토스 응답 처리, 검색, 캐시와 토큰 재시도

전체 테스트는 다음 명령으로 실행한다.

```powershell
python -m unittest discover -s tests -v
```

## 직접 수정하지 않는 파일

`__pycache__`, `.pyc`, 테스트 캐시와 가상환경 내부 파일은 도구가 자동 생성한다. 프로그램 동작을 바꿀 때는 원본 소스와 문서를 수정한다.
