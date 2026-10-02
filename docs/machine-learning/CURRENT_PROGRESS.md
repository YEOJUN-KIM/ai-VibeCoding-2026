# 머신러닝 자동매매 구현 현황

기준 시각: **2026-10-02 15:02 KST**

이 문서는 머신러닝 자동매매 프로젝트에서 지금까지 구현하고 실제로 확인한 내용을 한곳에 정리합니다. 세부 설계와 실행 명령은 각 단계 문서를 참고합니다.

## 현재 단계

- 1단계 개발 환경 구성: 완료
- 2단계 데이터 수집 파이프라인: 구현 완료, 데이터 누적 중
- 3단계 비용 포함 백테스트: 다음 구현 대상
- 4단계 첫 머신러닝 모델: 데이터와 백테스트가 준비된 뒤 진행

현재 목표는 PAPER 자동매매를 실행하면서 학습에 필요한 원본 데이터와 전략 판단을 안정적으로 쌓는 것입니다. 아직 수집 기간이 짧으므로 이 데이터만으로 모델 수익성을 판단하지 않습니다.

## 구현한 기능

### 1. 재현 가능한 개발 환경

- 프로젝트 전용 Python 가상환경과 머신러닝 패키지 구성
- Windows 설치·실행 방법 문서화
- 환경 변수 예시와 PostgreSQL 연결 설정 정리

자세한 내용: [01_ENVIRONMENT.md](01_ENVIRONMENT.md)

### 2. 종목 1분봉 수집

- `WATCH_SYMBOLS` 종목의 완료된 1분봉 OHLCV를 PostgreSQL에 저장
- 서버 실행 중 평일 09:01:02~15:31:02 KST에 매분 자동 수집
- 최근 구간을 다시 요청해 일시적인 API 장애 뒤 누락을 보충
- 기본키로 중복 저장 방지
- 수집 실행별 신규·중복·오류 건수 기록

저장 테이블: `ml_raw_candles`, `ml_collection_runs`

### 3. 국내 시장 지표 수집

- KOSPI와 KOSDAQ 완료 1분봉 수집
- 종목 수집과 같은 주기로 자동 실행
- 종목 봉과 분리해 PostgreSQL에 원본 보존

저장 테이블: `ml_market_indicator_candles`

### 4. 환율과 미국 시장 지표 수집

- 토스증권 USD/KRW 환율 저장
- SPY, QQQ, DIA, VIXY, IEF 일봉을 미국 시장·변동성·국채 대용 지표로 저장
- 서버 시작 시 즉시 한 번 수집
- 평일 08:10과 13:10 KST에 자동 수집
- FRED의 S&P 500, NASDAQ Composite, DJIA, VIX, 미국 10년물 금리도 수집 시도
- FRED 장애가 발생해도 토스 대용 지표와 PAPER 자동매매는 계속 동작
- 같은 기준일의 수정값을 덮어쓰지 않고 버전별 보존

저장 테이블: `ml_macro_observations`

현재 FRED 요청은 이 환경에서 시간 초과가 발생하고 있습니다. 정확한 FRED 지표는 예약 실행마다 다시 시도하며, 그동안 토스 대용 지표를 계속 축적합니다.

### 5. PAPER 전략 판단 기록

- 새 완료 봉마다 전략의 `DATA_WAIT`, `WAIT`, 매수·매도 체결 및 제한 결과 저장
- 현재가, 이동평균, 추세, 보유 수량, 현금, 총자산과 판단 이유 보존
- 주문 루프와 분리한 메모리 큐에서 묶음 저장
- DB 장애 시 다음 주기에 재시도
- 판단 고유키로 재시도 중복 방지

저장 테이블: `ml_strategy_decisions`

기존 DB의 부분 고유 인덱스 때문에 판단 저장이 실패하던 문제도 수정했습니다. 현재 PAPER 판단이 PostgreSQL에 정상 누적됩니다.

### 6. 데이터 품질 보고서

- 평일 15:40 KST에 당일 품질 보고서 자동 생성
- 종목·지수 봉 누락, 5분 초과 지연, 거래량 0 집계
- 수집 성공·부분 성공·실패와 신규·중복 행 집계
- PAPER 판단 및 매수·매도 행동 집계
- USD/KRW와 미국 대용 지표 5종의 당일 존재 여부 검사
- 결과를 `PASS`, `WARN`, `FAIL`로 저장

저장 테이블: `ml_data_quality_reports`

## 실제 동작 확인 결과

2026-10-02 15:02 KST에 생성한 품질 보고서 기준입니다. 숫자는 수집이 계속되면서 증가합니다.

| 항목 | 확인 결과 |
|---|---:|
| 종목 1분봉 | 2,086건 |
| KOSPI·KOSDAQ 1분봉 | 104건 |
| 거시 지표 원본 | 77건 |
| 당일 핵심 거시 지표 | 6종 모두 존재 |
| PAPER 전략 판단 | 12건 |
| 실행 중 전략 | 기본 가성비 · 소액 분산 · 09/28주 |

당일 품질 상태는 `WARN`입니다. 시스템 오류 때문이 아니라 장 시작 후 수집을 시작해 오전 1분봉이 비어 있기 때문입니다. 다음 거래일부터 장 전체 자동 수집 결과를 기준으로 품질을 판단합니다.

## 운용 조건

- FastAPI 서버가 실행 중이어야 정기 수집 작업이 동작합니다.
- 종목·국내 지수·환율·해외 지표는 PAPER 전략이 멈춰도 수집됩니다.
- 전략 판단 데이터는 PAPER 전략을 실행할 때만 생성됩니다.
- 서버를 다시 시작하면 환율·해외 지표를 즉시 한 번 수집하고 이후 예약 시각에 반복합니다.
- 실제 주문 전송 없이 PAPER 모드에서 데이터를 충분히 축적합니다.

## 수동 실행과 상태 확인

```powershell
# 종목 1분봉
powershell -ExecutionPolicy Bypass -File .\scripts\collect-ml-data.ps1

# KOSPI·KOSDAQ
powershell -ExecutionPolicy Bypass -File .\scripts\collect-market-indicators.ps1

# 환율·미국 시장 지표
powershell -ExecutionPolicy Bypass -File .\scripts\collect-macro-context.ps1 -Days 14

# 오늘 품질 보고서
powershell -ExecutionPolicy Bypass -File .\scripts\generate-ml-quality-report.ps1
```

로그인 후 다음 API에서 자동 작업 상태를 확인할 수 있습니다.

- `GET /ml/data-collection/status`
- `GET /ml/macro-collection/status`
- `GET /ml/decision-recording/status`
- `GET /ml/data-quality/latest`

## 검증 결과

- 전체 Python 테스트 182개 통과
- PAPER 판단 저장 관련 테스트 6개 통과
- Python 소스 컴파일 검사 통과
- Git 공백 오류 검사 통과

## 다음 작업

1. PAPER 모드와 서버를 유지해 최소 5거래일, 가능하면 20거래일 이상 데이터를 모읍니다.
2. 종목·국내 지수·환율·미국 지표를 판단 시각 기준으로 결합합니다.
3. 미래 수익률에서 수수료·세금·슬리피지를 뺀 학습 정답을 만듭니다.
4. 기존 이동평균 전략을 기준선으로 삼아 비용 포함 백테스트를 구현합니다.
5. 시간순 검증으로 첫 단순 모델이 기준선보다 나은지 비교합니다.

뉴스와 공시 수집은 아직 구현하지 않았습니다. 먼저 가격·시장·판단 데이터의 누적과 백테스트를 안정화한 뒤 추가합니다.
