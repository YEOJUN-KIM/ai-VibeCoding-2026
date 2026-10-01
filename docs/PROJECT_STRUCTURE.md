# 프로젝트 구조

## 루트

```text
ai-VibeCoding-2026/
├─ auto_trader/       애플리케이션 코드와 웹 자산
├─ docs/              제품·운영 문서
├─ tests/             자동 테스트
├─ .env.example       환경 변수 예시
├─ requirements.txt   Python 의존성
└─ README.md          프로젝트 시작 안내
```

로컬 실행 중 생성되는 `.env`, `.paper-history/`, `backups/`와 로그는 소스가 아닙니다.

## 서버와 공통 기능

- `main.py`: FastAPI 앱, 페이지·API 라우트, 시작 준비 상태와 예약 작업 조합
- `settings.py`: `.env` 기반 설정
- `models.py`: API 요청·응답 모델
- `database.py`: PostgreSQL 연결과 스키마 초기화
- `schema.sql`: 데이터베이스 스키마 기준
- `auth.py`: PIN 로그인, 세션과 화면 잠금

## 시장 데이터와 분석

- `toss.py`: 토스증권 인증, 계좌, 시세와 차트 API
- `dart.py`: OpenDART 기업·공시·재무 데이터
- `news.py`: 뉴스 수집과 유사 기사 그룹화
- `ai_news.py`: 선택형 AI 뉴스 처리
- `industries.py`: 종목 업종 분류 보조
- `favorites.py`: 관심종목 저장
- `long_term.py`: 장기 관찰 점수, 등급과 예약 분석
- `long_term_repository.py`: 장기 분석·추천·내 후보 저장

## PAPER와 전략

- `paper_feed.py`: PAPER용 실제 시세와 완료 봉 공급
- `paper.py`: 모의 계좌, 체결, 비용, 기록과 손익
- `strategy.py`: 이동평균 신호, 실행 제어와 청산 조건
- `strategy_presets.py`: 기본 인기·가성비 후보 선별, 주간 데이터 저장·재사용과 새 전략 초안
- `simulator.py`: 시뮬레이션 보조
- `risk.py`: PAPER/LIVE 공통 위험 한도

상세 동작은 [PAPER_STRATEGY.md](PAPER_STRATEGY.md)를 기준으로 합니다.

## LIVE 주문

- `live_strategies.py`: 저장 전략과 대상 종목
- `live_orders.py`: 실제 주문 검증, 멱등성, 저장과 브로커 대조
- `set_live_pin.py`: LIVE 주문 PIN 설정 도구

## 웹 자산

`auto_trader/static/`에 페이지별 HTML과 JavaScript, 공통 `styles.css`, 헤더 상태를 관리하는 `header-status.js`가 있습니다.

- `login`: 로그인과 준비 상태
- `index`: PAPER 트레이딩 랩
- `live`: LIVE 계좌와 주문 기록
- `stocks`: 국내주식 탐색
- `stock-detail`: 종목 상세·장기 분석·수동 주문
- `long-term`: 자동 추천과 내 장기 관찰 후보
- `news`: 뉴스
- `settings`: 전략과 위험 설정
- `settings-presets.js`: 주간 기본 프리셋 미리보기, 예산 변경과 별도 저장 안내

## 테스트

`tests/`에는 인증, 준비 상태, 토스·DART·뉴스, 관심종목, 장기 분석, PAPER, 전략, LIVE 주문·전략 테스트가 있습니다. 전체 확인은 `python -m unittest discover -s tests -v`로 실행합니다.
