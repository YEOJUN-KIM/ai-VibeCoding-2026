# ML Data Readiness Audit + Curated Dataset v1

## 실행과 보존 경계

```powershell
.venv\Scripts\python.exe -m auto_trader.ml.data_readiness --version v1
```

기본 출력은 metadata/report용 `artifacts/ml/datasets/v1/`과 대용량 CSV용 `artifacts/ml/generated/datasets/v1/`로 분리한다. PostgreSQL의 단일 `REPEATABLE READ READ ONLY` 트랜잭션으로 RAW·국내 지수·macro·decision·collection evidence를 읽는다. 스키마 초기화, migration, INSERT/UPDATE/DELETE, provider API, 주문 API를 호출하지 않는다. 실행 중 서버나 수집 스케줄을 변경하지 않는다. RAW는 원본 관측 계층이고 Curated는 사용 조건과 출처를 기록한 파생 artifact다. 보간·합성·forward fill은 없다.

`manifest.json`은 입력 테이블별 SHA-256, 코드 revision과 builder 파일 hash, 비용·시간·feature·label 규칙, 출력 CSV hash를 담는다. 생성 시각과 MVCC snapshot ID는 실행 메타데이터라 달라질 수 있다. `dataset_hash`는 설정·종목·정렬된 전체 sample 내용의 canonical JSON SHA-256이다. 변경 없는 DB를 다른 시각에 실행해도 grid가 늘어나지 않도록 감사 종료 시각은 스냅샷 안의 최신 저장 수신/작업 시각으로 정한다.

## Session과 sample

기존 V2 진단 grid인 KST 09:00~15:30 inclusive의 완료된 1분봉을 사용한다. 이것은 거래소 세션의 세밀한 의미를 추정한 규칙이 아니다. 시간외 RAW도 후보 audit에는 남기고 `OUTSIDE_SESSION`으로 제외한다. 관측된 `event_at` 날짜와 설정/DB의 전체 종목을 교차해, RAW 자체가 없는 timestamp도 sample 후보로 감사한다. 관측이 전혀 없는 날의 휴장/수집 장애는 거래소 캘린더 없이 추정하지 않는다.

필수 조건은 같은 날짜의 연속 **40개 completed stock candles**, 정확히 정렬된 국내 지수 각 **21개 candles**다. 종목 source는 TOSS, 지수 source는 TOSS_MARKET_INDICATOR로 고정하며 source를 섞지 않는다. 시간 순서·OHLC·volume 무결성이 필요하다. 거래량 0은 허용하고 0으로 나누는 상대 거래량은 null이다. 인접 종가 10% 초과 변동은 `candle-audit.csv`의 suspicious timestamps에만 기록한다. 자동 삭제하지 않는다.

`prediction_event_at`은 기준 stock candle event time이다. `prediction_at = max(event_at + 1분, 기준 candle available_at)`이다. 기본적으로 event+5분 이내이고 정규장 종료 전이어야 한다. 다른 lookback·지수가 늦으면 이를 기다리며 prediction 시각을 옮기지 않는다. 5분 한도는 데이터셋의 보수적인 운영 가정이며 manifest에 기록한다. 과거 며칠 뒤 받은 기준 봉을 그날의 예측으로 되살릴 수 없다.

모든 feature는 `available_at <= prediction_at`, `feature_available_at_max <= prediction_at`을 만족해야 한다. 누락·늦은 lookback·잘못된 OHLC·지수 결측·시간 순서 위반은 복수 exclusion reasons로 기록한다. V2.1의 `_prefer_provider_evidence`, `_final_provider_evidence`, `_classify_gaps`를 그대로 사용한다. 날짜 전체를 provider absence 하나로 제외하지 않는다. 09:00이 없어도 09:01부터 40개 연속 봉이 생기면 후보가 될 수 있다.

`usable`은 **core feature의 사용 가능성**이다. `NO_EXECUTABLE_NEXT_BAR`, `MISSING_FUTURE_LABEL`은 별도 outcome flags다. 각 학습 작업은 `usable`과 필요한 horizon label의 non-null 조건을 함께 선택해야 한다. 긴 horizon만 없는 sample을 일괄 삭제하지 않는다.

## Feature와 baseline context

- 가격 return: 1/3/5/10/20분, 비율 단위(0.01 = 1%).
- MA: 10/40 평균, spread, spread percent(이 컬럼만 % 단위).
- 변동성: 5/20개 1분 수익률의 population 표준편차, 해당 구간 high-low / 현재 close.
- 거래량: 현재 volume, 5/20 평균·상대 거래량.
- 장중 위치: minutes_since_open, price_vs_session_open, has_session_open. 당일 09:00 관측이 없거나 당시 이용 불가하면 session open feature만 null.
- 시장: 정확히 같은 event 기준 KOSPI/KOSDAQ 1/5/20분 수익률. 필요한 중간 봉도 존재하고 당시 알려져 있어야 한다.
- macro: USD/KRW, 미국 proxy 5종, 저장된 FRED indicator 값과 각 `has_*`. prediction 당시 알려진 observation 중 event가 가장 최신인 것을 사용한다. 같은 event의 수정치는 당시 알려진 최신 버전을 쓴다. macro는 선택 항목이며 없으면 null/false다. 출처·event/available/collected 시각을 provenance에 기록한다. 과거 macro를 최신값으로 결합하는 as-of join이며 OHLC 결측 보간과 다르다. 별도 freshness cutoff는 없으므로 향후 학습에서 stale macro 정책을 결정해야 한다.

40개만으로 이전 MA40까지 요구하는 slope는 만들지 않는다. 그 slope를 넣으려면 41개 이상의 history profile이 필요하다. `feature_list`와 `label_list`는 manifest에서 분리한다. 학습 시 samples.csv의 모든 숫자 컬럼을 자동 feature로 선택하지 말고 반드시 `feature_list`를 사용해야 한다.

PAPER decisions는 요구 필드별 null 수, action 분포, 시간 순서, RAW candle 연결을 감사한다. sample에는 prediction 당시 이용 가능한 strategy별 MA/trend/history context만 별도로 보존한다. 여러 전략을 임의로 하나로 합치지 않는다. pre-risk BUY candidate를 정확히 복원할 근거가 없으므로 `baseline_buy_candidate=null`이다. BUY/SELL action은 label로 사용하지 않는다.

기존 macro collector에서 USD/KRW의 event는 provider `validFrom`, 미국 proxy의 event는 provider 일봉 timestamp다. 가능한 FRED의 event는 관측 날짜의 뉴욕 16:00으로 부여한 내부 기준이며 실제 공표 시각을 뜻하지 않는다. 기존 macro `available_at`과 `collected_at`은 모두 **수집 주기 시작 시각**을 사용한다. 개별 응답 수신 시각으로 간주하면 주기 안에서 누수가 생길 수 있다. 따라서 Dataset은 RAW를 보존한 채 `max(RAW available_at, RAW collected_at, 연결된 MACRO_CONTEXT run finished_at)`을 보수적인 feature 사용 가능 시각으로 적용한다. 완료된 run 증거가 없으면 그 macro는 사용하지 않는다. provenance에 원래 `raw_available_at`·`raw_collected_at`과 보수적 경계를 함께 기록한다. run 종료까지 기다리는 만큼 실제보다 늦게 사용될 수 있지만 조기에 쓰지 않는다.

## Label과 historical backfill

예를 들어 10/15 event를 10/30에 수신한 실제 candle은 10/15 feature에 들어가지 않는다. 반면 원본 source의 실제 관측이며 가격·시간 무결성이 확인되면 10/15 이후의 **시장 결과**로 사용할 수 있다. feature는 당시 알던 정보, label은 실제 이후 결과다.

5/15/20/30/60분마다 다음을 분리한다.

- `future_return_*`: 기준 close → event+horizon close. horizon 사이의 실제 봉이 모두 있어야 한다.
- `future_net_return_*`: 같은 close 기준의 비용 차감 결과. 이 값은 실행 가능 체결 결과와 다르다.
- `executable_net_return_*`: prediction_at보다 엄격히 뒤의 첫 분 경계 open으로 진입, 진입 봉을 첫째로 세어 horizon번째 봉 close로 종료. 바로 다음 분의 실제 봉이 없으면 보수적으로 null 처리한다. 결측을 건너뛰어 진입하지 않는다.
- `max_favorable_excursion_*`, `max_adverse_excursion_*`: executable path의 최고 high/entry−1, 최저 low/entry−1. label/outcome 전용이다.

모든 경로는 같은 시장 날짜 정규장에만 있고, 실제 TOSS RAW만 사용한다. source가 없거나 무결성이 깨진 경로는 null이다. 장 마감으로 60분 경로가 없어도 5분 label은 유지한다. binary target/threshold는 만들지 않는다.

비용은 실행 당시 settings의 PAPER fee/tax/slippage를 읽는다. 1주 단위 entry에 매수 slippage/fee, exit에 매도 slippage/fee/tax를 적용하고, PAPER와 같이 각 fee/tax를 KRW 1 단위 ROUND_DOWN 한다. 수량에 따른 반올림 차이가 있으므로 manifest에 1주 가정을 기록한다. 실제 포트폴리오 backtest의 대체 결과가 아니다.

## 출력과 readiness

저장 구조는 다음을 표준으로 한다. `output_paths(version)`에서 버전별 기본 경로를 한 번에 결정하므로 향후 v2/v3 profile도 같은 배치를 사용한다. 현재 구현된 Dataset profile과 CLI `--version`은 v1이다.

```text
artifacts/ml/
├─ datasets/v1/                  # Git 관리 metadata/report
│  ├─ manifest.json
│  ├─ readiness-report.json
│  ├─ readiness-by-date.csv
│  ├─ readiness-by-symbol.csv
│  ├─ candle-audit.csv
│  ├─ determinism-check.json
│  └─ artifact-verification.json
└─ generated/datasets/v1/        # Git ignore, 재생성 가능한 대용량 파일
   ├─ samples.csv
   └─ excluded-samples.csv
```

`artifacts/ml/datasets/`는 Git 관리용 metadata/report/hash/audit이고 ignore하지 않는다. `artifacts/ml/generated/`는 대용량 dataset·feature/training matrix·prediction dump·intermediate artifact를 보관하며 기존 `.gitignore` 규칙 `artifacts/ml/generated/`가 모든 하위 파일을 제외한다. 새 PC에서 clone했을 때 두 대용량 CSV가 내려오지 않는 것이 정상이다. DB 연결과 의존성 준비 후 프로젝트 루트에서 로컬 재생성한다.

```powershell
.venv\Scripts\python.exe -m auto_trader.ml.data_readiness --verify-determinism
```

`--output`은 metadata 위치, `--generated-output`은 대용량 CSV 위치를 각각 재정의하며 기본값은 버전에서 독립적으로 결정한다. 임시 실행은 **두 옵션을 모두 임시 경로로 지정**하여 기본 Dataset을 덮어쓰지 않는다. 명시적인 재생성 명령은 선택한 출력 경로의 파일을 갱신한다. 이번 배치 정리에서는 기존 실제 Dataset을 재생성하지 않았다.

manifest의 `artifact_locations`는 기존 `artifacts` hash와 같은 파일명 key를 쓴다. 기본 경로는 저장소 루트 기준 `/` 경로이며, 저장소 밖의 사용자 지정 출력만 절대 경로로 기록한다. 기존 dataset/input/file hash와 determinism의 의미는 파일 위치에 의존하지 않는다. 생성 당시의 `builder_sha256`, git revision, snapshot과 created_at은 배치 정리 후에도 당시의 생성 provenance로 보존한다.

2026-10-30 배치 정리 검증: 사용자가 미리 이동한 `samples.csv` 349,803,463 bytes와 `excluded-samples.csv` 24,404,098 bytes는 복사·재생성·덮어쓰기 없이 유지했다. 작업 전후 두 파일의 SHA-256·크기·수정 시각이 일치하고 기존 manifest의 모든 필드 값과 hash를 보존했다. `.gitignore`의 전체 generated 규칙이 이미 있어 수정하지 않았으며 개별 CSV ignore 규칙도 없었다. generated 하위는 추적 파일/추적 후보가 없고 metadata는 계속 tracked 상태다. 전체 Python **364개**·JavaScript **22개**, readiness 테스트 **16개**, 기존 실제 CSV/metadata 5개의 hash 검사, 컴파일·공백 검사가 통과했다. 기존 감사·feature·label·usability 정의 17개의 AST도 동일하다. 아래 362개 테스트와 실제 Dataset 수치는 최초 생성 당시의 기록이다.

기존 파일의 위치와 hash만 검증하려면 DB 접근이나 재생성 없이 실행한다. clone 직후 generated CSV가 아직 없으면 이 검사는 누락 파일로 실패하므로 먼저 로컬 데이터를 준비해야 한다.

```powershell
.venv\Scripts\python.exe -m auto_trader.ml.data_readiness --verify-artifacts
```

`samples.csv`는 core usable, `excluded-samples.csv`는 제외 사유가 있는 후보다. 날짜·종목 요약 CSV, 정확한 누락 timestamp와 V2.1 gap 분류를 담은 `candle-audit.csv`, 상세 `readiness-report.json`, `manifest.json`을 만든다. CSV의 symbol은 문자열로 읽어 선행 0을 보존한다.

데이터 파이프라인 판정은 core usable 0개면 NOT_READY, 20개 이상 usable 날짜·전체 종목 usable·후보 대비 80% 이상 usable이면 ML_READY, 나머지는 PARTIALLY_READY다. 첫 탐색적 실험은 5개 이상 usable 날짜와 sample이 있을 때 시작 가능으로 표시한다. 이 기준은 거래 수익성·일반화 검증이 아니다. 샘플 수가 많아도 인접 lookback/label은 크게 겹친다.

manifest는 usable 날짜 목록을 시간순으로 제공한다. random split은 없다. 현 단계에서 안정적인 최종 test를 억지로 만들지 않는다. 이후 과거 train → 이후 validation → 최신 test로 분리하고 경계에서는 horizon overlap을 purge해야 한다. 다음 단계는 horizon별 sample 선택, baseline 후보를 재현 가능한 별도 profile로 정의, chronological validation으로 첫 단순 모델을 실험하는 것이다. 이번 단계는 모델을 학습하지 않는다.

실제 실행 수치와 검증 결과는 아래 실행 기록과 `readiness-report.json`을 참고한다.

## 실제 누적 DB 실행 결과 (2026-10-30)

감사 기준: 2026-10-30T10:22:32.187881+09:00 KST. 실행 중 수집기는 계속 작동하므로 이 값은 고정 MVCC 스냅샷의 수치다. PostgreSQL transaction_read_only=on.

| 항목 | 값 |
| --- | --- |
| 관측 시장 날짜 | 17 |
| usable 날짜 | 14 |
| 종목 coverage | 22/22 |
| 종목 RAW | 110664 |
| 국내 지수 RAW | 9770 |
| 감사 sample | 142774 |
| usable | 89368 |
| excluded | 53406 |
| usable ratio | 62.5940% |
| feature point-in-time 위반 | 0 |
| historical backfill feature 사용 | 0 |
| data pipeline readiness | PARTIALLY_READY |
| 판정 범위 | DATA_PIPELINE |
| 첫 실험 | READY FOR FIRST ML EXPERIMENT |
| 수익성/일반화 신뢰 | NOT_READY |

제외 사유는 중복 집계한다. 미래 label 전부 결측인 usable sample은 1,104개, executable next bar 부재는 282개다. 학습 시 필요한 label의 non-null 조건을 추가해야 한다.

| 제외 사유 | sample 수 |
| --- | --- |
| LATE_LOOKBACK_DATA | 9722 |
| MISSING_KOSDAQ | 47394 |
| MISSING_KOSPI | 47394 |
| MISSING_LOOKBACK | 47028 |
| MISSING_STOCK_DATA | 32110 |
| OUTSIDE_SESSION | 3338 |
| UNVERIFIED_GAP | 33924 |

### 날짜별 품질

| KST 날짜 | RAW | usable | excluded | usable ratio |
| --- | --- | --- | --- | --- |
| 2026-10-02 | 2376 | 600 | 8002 | 6.98% |
| 2026-10-08 | 19 | 0 | 8621 | 0.00% |
| 2026-10-12 | 8769 | 7316 | 1457 | 83.39% |
| 2026-10-13 | 8976 | 7718 | 1262 | 85.95% |
| 2026-10-14 | 8904 | 7718 | 1190 | 86.64% |
| 2026-10-15 | 8670 | 5566 | 3108 | 64.17% |
| 2026-10-16 | 8901 | 6596 | 2309 | 74.07% |
| 2026-10-19 | 69 | 0 | 8671 | 0.00% |
| 2026-10-20 | 8893 | 7717 | 1180 | 86.74% |
| 2026-10-21 | 8602 | 7405 | 1201 | 86.04% |
| 2026-10-22 | 8646 | 7718 | 932 | 89.23% |
| 2026-10-23 | 8866 | 7718 | 1152 | 87.01% |
| 2026-10-26 | 8976 | 7718 | 1262 | 85.95% |
| 2026-10-27 | 8971 | 7715 | 1260 | 85.96% |
| 2026-10-28 | 8888 | 7716 | 1176 | 86.77% |
| 2026-10-29 | 69 | 0 | 8671 | 0.00% |
| 2026-10-30 | 2069 | 147 | 1952 | 7.00% |

10/08·10/19·10/29는 시간외 관측만 있고 정규장 core sample이 없다. 10/30은 장중 부분 스냅샷이다. 휴장 여부를 추정해 결측을 지우지 않는다. 날짜별 복수 제외 사유는 readiness-by-date.csv에 있다.


### 종목별 품질

| 종목 | RAW | usable | excluded | usable ratio |
| --- | --- | --- | --- | --- |
| 000660 | 5182 | 4101 | 2393 | 63.15% |
| 005380 | 5164 | 4100 | 2394 | 63.14% |
| 005490 | 5164 | 4101 | 2393 | 63.15% |
| 005930 | 5182 | 4101 | 2393 | 63.15% |
| 006400 | 5164 | 4101 | 2393 | 63.15% |
| 017670 | 4917 | 4031 | 2451 | 62.19% |
| 034020 | 4918 | 4037 | 2447 | 62.26% |
| 035420 | 5164 | 4100 | 2394 | 63.14% |
| 035720 | 5162 | 4088 | 2417 | 62.84% |
| 036930 | 4916 | 4025 | 2470 | 61.97% |
| 042700 | 4915 | 4036 | 2445 | 62.27% |
| 051910 | 5164 | 4101 | 2393 | 63.15% |
| 055550 | 5159 | 4096 | 2395 | 63.10% |
| 058470 | 4918 | 4037 | 2447 | 62.26% |
| 080220 | 4915 | 4024 | 2470 | 61.96% |
| 086520 | 4916 | 4031 | 2451 | 62.19% |
| 096770 | 4917 | 4037 | 2446 | 62.27% |
| 103590 | 4918 | 4036 | 2448 | 62.25% |
| 105560 | 5160 | 4097 | 2395 | 63.11% |
| 218410 | 4917 | 4031 | 2451 | 62.19% |
| 222800 | 4917 | 4035 | 2448 | 62.24% |
| 403870 | 4915 | 4022 | 2472 | 61.93% |

### RAW 및 지수 alignment

| 항목 | 종목 | 국내 지수 |
| --- | --- | --- |
| raw_rows | 110664 | 9770 |
| regular_session_rows | 107326 | 9770 |
| outside_session_rows | 3338 | 0 |
| duplicate_rows | 0 | 0 |
| time_order_violations | 0 | 0 |
| invalid_ohlc | 0 | 0 |
| delayed_over_5m | 7711 | 520 |
| zero_volume | 5126 | 314 |
| historical_backfill_rows | 3337 | 334 |
| provider_absent_bars | 16 | 8 |
| collection_gap_bars | 0 | 0 |
| unverified_bars | 32094 | 2898 |

usable 89,368개 모두 KOSPI/KOSDAQ exact alignment와 available_at 경계를 통과했다. 각 지수 결측/지연으로 제외된 후보는 47,394개다. suspicious timestamps는 candle-audit.csv에만 flag로 남긴다.


### Macro 및 decision

| 지표 | RAW | 완료 run 증거 | usable sample availability |
| --- | --- | --- | --- |
| USD_KRW | 43 | 43 | 89178 |
| US_DJIA_PROXY | 47 | 47 | 89108 |
| US_NASDAQ100_PROXY | 57 | 57 | 89108 |
| US_SP500_PROXY | 50 | 50 | 89108 |
| US_TREASURY_7_10Y_PROXY | 51 | 51 | 89108 |
| US_VOLATILITY_PROXY | 55 | 55 | 89108 |

FRED observation은 현재 DB에 없다. 미국 proxy와 USD/KRW는 optional as-of feature다. 각 관측의 원래 세 시각은 readiness-report.json에, sample별 보수적인 사용 시각과 원래 시각은 CSV provenance에 기록했다.

PAPER decision 87,681건, 시간 연결 순서 위반 0건, RAW candle 연결 부재 1,640건. 연결 부재는 감사에 남기고 baseline context가 없는 core sample을 일괄 제외하지 않는다. 필드별 null·action 집계는 JSON에 있다.


### Horizon별 label 수 (core usable sample 기준)

| 분 | market outcome | close 기준 net | executable net/MFE/MAE |
| --- | --- | --- | --- |
| 5 | 88264 | 88264 | 87990 |
| 15 | 85524 | 85524 | 85140 |
| 20 | 84154 | 84154 | 83764 |
| 30 | 81352 | 81352 | 80973 |
| 60 | 73055 | 73055 | 72737 |

비용: fee=0.00015, sell tax=0.002, 양방향 slippage=0.0005. 1주 PAPER rounding 가정.

Dataset hash: `94b7c402ed71a9e43a556f1a4a0d8bbe2f149938c55ac2be2baaa701a69d67e1`. metadata/report는 `artifacts/ml/datasets/v1/`, 두 대용량 sample CSV는 `artifacts/ml/generated/datasets/v1/`에 있다.


검증: 전체 Python **362개**, 전체 JavaScript **22개** 통과. 신규 readiness 테스트 **14개** 포함. CSV 별도 검증은 artifact-verification.json, 동일 실제 스냅샷 2회 빌드 비교는 determinism-check.json에 저장한다. 컴파일·Git 공백 검사 통과.


다음 단계: 20분 등 필요한 continuous label을 선택한 뒤 과거→미래 순서의 탐색적 실험을 시작할 수 있다. baseline_buy_candidate는 아직 null이므로 기존 MA 전략 필터 전용 학습 전에는 후보 정의를 별도로 재현해야 한다. 현재 14개 usable 날짜로 안정적인 최종 test/수익성 일반화를 확정하지 않는다. 추가 거래일을 누적하고 장중 공백과 decision 연결 부재를 관찰한다. 이번 변경은 Dataset/Audit 코드·테스트·문서와 파생 artifact에 한정되며 수집기·전략·리스크·PAPER/LIVE 주문 코드를 변경하지 않았다.

최종 실제 동일 스냅샷 재빌드 검증: determinism-check.json의 matches=true, 후보 142,774개·usable 89,368개·dataset hash 동일. 독립 CSV 검증: usable 89,368개와 excluded 53,406개 전체 확인, CSV 5개 hash 일치, 시간 위반·백필 feature 유입 0건.
