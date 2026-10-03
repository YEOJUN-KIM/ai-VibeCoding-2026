# 프로젝트 구조

기준일: 2026-10-04. 현재 코드 위치를 안내하며, 휴대폰 앱·외부 접속 모듈은 아직 없습니다.

## 루트

```text
ai-VibeCoding-2026/
├─ auto_trader/       애플리케이션 코드와 웹 자산
│  └─ ml/             ML 환경 검증, 원본 데이터 수집과 이후 학습 코드
├─ docs/              제품·운영 문서
│  └─ machine-learning/ 머신러닝 설치·데이터·학습·운영 학습 과정
├─ FOLIO.cmd          Windows 간편 실행 진입점
├─ scripts/           실행 준비·ML 설치·수집 등 반복 작업
├─ tests/             자동 테스트
├─ .env.example       환경 변수 예시
├─ requirements-ml.txt ML 패키지와 애플리케이션 의존성
├─ requirements.txt   Python 의존성
└─ README.md          프로젝트 시작 안내
```

로컬 실행 중 생성되는 `.env`, `.local-secrets/`, `.paper-history/`, `.backups/`, `.runtime-logs/`와 로그는 소스가 아닙니다. API 키는 배포·Git에 포함하지 않습니다.

## 서버와 공통 기능

- `main.py`: FastAPI 앱, 페이지·API 라우트, 시작 준비 상태와 예약 작업 조합
- `launcher.py`·`scripts/start.ps1`: 최초 준비, DB 서비스·계정 확인, 서버 실행·중복 재사용·종료
- `settings.py`: `.env` 기반 설정
- `models.py`: API 요청·응답 모델
- `database.py`: PostgreSQL 연결과 스키마 초기화
- `schema.sql`: 데이터베이스 스키마 기준
- `auth.py`: 아이디·비밀번호 로그인, 세션·CSRF·화면 잠금·주문 PIN과 자격 정보 변경
- `user_connections.py`: 최초 관리자 연결 귀속, 사용자별 증권 클라이언트와 로컬 키 저장·이전
- `connection_vault.py`: Windows DPAPI 암호화·복호화와 로컬 키 파일의 안전한 교체
- `user_workspace.py`: 요청·실행 작업별 사용자 컨텍스트, 계좌·엔진·연결 분리
- `asset_history.py`: 실제 계좌 자산 관측 저장과 날짜별 이력 조회
- `market_hours.py`: 국내·미국 시장 캘린더와 상태 표시
- `stock_search.py`: 종목 별명·영어·초성·두벌식 입력 검색 규칙

## 시장 데이터와 분석

- `toss.py`: 토스증권 인증, 계좌, 시세와 차트 API
- `quote_stream.py`: 사용자 연결별 토스 WebSocket 구독과 상세 페이지 시세 전달
- `dart.py`: OpenDART 기업·공시·재무 데이터
- `news.py`: 뉴스 수집과 유사 기사 그룹화
- `ai_news.py`: 선택형 AI 뉴스 처리
- `industries.py`: 종목 업종 분류 보조
- `favorites.py`: 관심종목 저장
- `long_term.py`: 장기 리서치 점수, 등급과 예약 분석
- `long_term_repository.py`: 장기 분석·추천·저장한 기업·메모·저장 해제 상태

## 머신러닝

- `docs/machine-learning/CURRENT_PROGRESS.md`: 현재 구현·운용·검증 현황과 다음 작업
- `ml/verify_environment.py`: 패키지와 모델 저장·재로딩 확인
- `ml/data_pipeline.py`: 종목·시장 지표 원본 1분봉 검증·저장, 시점 제한 조회와 품질 통계
- `ml/collect_candles.py`: 토스 완료 1분봉 수집 명령
- `ml/collect_market_indicators.py`: 코스피·코스닥 완료 1분봉 수집 명령
- `ml/collection_worker.py`: 평일 장중 매분 자동 수집과 실행 상태
- `ml/decision_pipeline.py`: PAPER 판단의 비동기 큐와 PostgreSQL 저장
- `ml/quality_report.py`: 일별 원본·판단 데이터 품질 검사와 보고서 저장
- `ml/quality_worker.py`: 장 마감 후 품질 보고서 자동 생성
- `ml/macro_pipeline.py`: 토스 환율·미국 대용 ETF·FRED 일별 지표 수집과 버전 저장
- `ml/macro_worker.py`: 한국시간 08:10·13:10 거시 지표 자동 수집
- `ml/backtest.py`: 저장된 봉의 시점별 재생, 이동평균·단순 보유·현금·비용 0 기준선 비교와 결과 저장
- `scripts/setup-ml.ps1`: 가상환경과 ML 패키지 설치
- `scripts/collect-ml-data.ps1`: 원본 1분봉 수집 실행
- `scripts/collect-market-indicators.ps1`: 코스피·코스닥 원본 1분봉 수집 실행
- `scripts/generate-ml-quality-report.ps1`: ML 일별 품질 보고서 수동 생성
- `scripts/collect-macro-context.ps1`: 환율과 미국 시장 지표 수동 수집
- `scripts/run-ml-backtest.ps1`: 누적 데이터·기간·저장 전략 기준의 반복 백테스트

## PAPER와 전략

- `paper_feed.py`: PAPER용 실제 시세와 완료 봉 공급
- `paper.py`: 모의 계좌, 체결, 비용, 기록과 손익·마지막 선택 전략 저장
- `strategy.py`: 이동평균 신호, 실행 제어와 청산 조건
- `strategy_presets.py`: 기본 인기·가성비 후보 선별, 주간 데이터 저장·재사용과 새 전략 초안
- `simulator.py`: 시뮬레이션 보조
- `risk.py`: PAPER/LIVE 공통 위험 한도

상세 동작은 [PAPER_STRATEGY.md](../specs/PAPER_STRATEGY.md)를 기준으로 합니다.

## LIVE 주문

- `live_strategies.py`: 저장 전략과 대상 종목
- `live_orders.py`: 실제 주문 검증, 멱등성, 저장과 브로커 대조
- `set_live_pin.py`: LIVE 주문 PIN 설정 도구

## 웹 자산

`auto_trader/static/`에 페이지별 HTML과 JavaScript, 공통 `styles.css`·`theme.css`, 헤더 상태를 관리하는 `header-status.js`가 있습니다. `preview/`는 예시 데이터 디자인 시안이며 실제 계좌 연동 화면과 구분합니다.

- `login`: 로그인과 준비 상태
- `index.html`·`app.js`: PAPER 화면, 계좌별 전략 선택 즉시 적용·상세 동시 펼침·기록 필터
- `live`: LIVE 계좌와 주문 기록
- `live-privacy.js`: 전체·카드별 자산 가림과 브라우저 가림 선택 저장
- `live-assets.js`: 실제 자산 이력 차트·자산 구성과 빈 상태
- `site-header.js`: 공통 입력형 종목 검색·자동완성·화면 잠금·주문 인증 배치
- `company-icons.js`·`company-logos/`: 로컬 기업 로고 4개와 짧은 이름 대체 아이콘
- `stocks`: 국내주식 탐색
- `stock-detail`: 종목 상세·장기 분석·수동 주문
- `long-term`: 추천 기업과 저장한 기업
- `watch-notes.js`: 사용자별 기업 메모 입력·저장과 목록 검색
- `news`: 뉴스
- `settings`: 계정·연결, 자동매매 전략과 투자 한도
- `settings-account.js`: 계정 정보, 비밀번호·PIN·증권 연결 변경 팝업
- `settings-summary.js`: 전략 입력 요약과 관계 검증 안내
- `settings-presets.js`: 주간 기본 프리셋 미리보기, 예산 변경과 별도 저장 안내

## 테스트

`tests/`에는 인증, 준비 상태, 토스·DART·뉴스, 관심종목, 장기 분석, PAPER, 전략, LIVE 주문·전략과 사용자별 연결·로컬 키 파일·자산 이력·검색·시장 상태·백테스트 검증이 있습니다. Python 확인은 `python -m unittest discover -s tests -v`, JavaScript 확인은 해당 `tests/test_*.js`를 Node.js로 실행합니다. 문서 갱신만으로 전체 테스트를 다시 실행한 것으로 간주하지 않습니다.
