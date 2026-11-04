# FOLIO 예약 프롬프트 제안 — 미적용

한국시간(Asia/Seoul) 평일 오후 5시에 D:\SourceBank\ai-VibeCoding-2026 프로젝트의 국내주식 머신러닝 데이터 장 마감 품질검사를 수행한다.
이 작업의 목적은 이미 저장된 데이터와 모의전략 결과를 점검하는 것이다. 모델 학습, 전략 튜닝, 코드 수정, 실제 주문은 수행하지 않는다.
1. 실행 전 확인
먼저 다음을 확인한다.
- PostgreSQL 접근 가능 여부
- 실행 날짜의 국내 증시 거래일 여부
- 해당 날짜의 원본 데이터 존재 여부
- 프로젝트 가상환경과 품질검사·백테스트 실행 가능 여부
실제 휴장일이고 새로운 거래 데이터가 없다면 작업을 조용히 종료한다.
DB 접근 실패, 데이터 수집 누락, 실행 실패 등 사용자가 알아야 할 문제가 있으면 보고한다.
서버나 DB를 임의로 재시작하지 않는다.
2. 모의전략 확인
현재 데이터 축적용 모의전략은 다음과 같다.
- Strategy ID: 17
- 이름: 데이터 축적 · 22종목
실행 시 DB에서 Strategy ID 17의 소유 정보, 전략명, DRY_RUN 여부, 대상 종목을 확인한다.
다른 전략으로 임의 대체하지 않는다.
WATCH_SYMBOLS와 Strategy ID 17의 대상 종목을 비교하고 불일치가 있으면 보고한다.
실계좌 주문, 주문 한도, 전략 설정은 변경하지 않는다.
3. 데이터 품질검사
프로젝트 가상환경 Python과 저장소의 현재 문서를 확인하여 기존
auto_trader.ml.quality_report.generate_quality_report
기능을 이용해 당일 품질 보고서를 생성하고 저장한다.
다음을 확인한다.
- 종목별 1분봉
- KOSPI / KOSDAQ
- 환율
- 미국 대용 지표
- 모의 판단 기록
- 데이터 기간과 건수
- 누락
- 지연
- 오류
- PASS / WARN / FAIL
품질검사 함수나 실행 방법이 변경된 경우 저장소의 현재 문서를 기준으로 한다.
원본 데이터를 수정하지 않는다.
4. 백테스트
기존 scripts/run-ml-backtest.ps1을 사용한다.
고정 조건:
- StrategyId: 17
- Execution: next_open
두 가지 실험을 수행한다.
당일 실험
- Start: 실행 당일
- End: 실행 당일
누적 실험
- Start: 생략
- End: 실행 당일
각 실험은 새로운 출력 폴더에 저장하여 과거 결과를 덮어쓰지 않는다.
저장된 원본 데이터를 조회하는 기존 백테스트만 사용한다.
원본 데이터와 계좌·주문 기록은 수정하지 않는다.
현재 설정된 수수료, 세금, 슬리피지, 위험 제한을 유지한다.
다음 기준선을 비교한다.
- 비용 포함 이동평균 전략
- 비용 0 기준선
- 단순 보유
- 현금
결과에 다음 정보가 남는지 확인한다.
- 설정
- 코드/입력 해시
- 데이터 기간
- 종목별 데이터 범위
데이터가 없거나 심각한 품질 문제 때문에 비교가 의미 없으면 해당 실험을 실행하지 않고 이유를 기록한다.
WARN 데이터로 수행한 결과는 누락·백필·지연 등의 영향 때문에 성능 검증으로 해석하기 어려운 범위를 명확히 표시한다.
4-1. 기존 결과의 Google Drive 보존 (독립 후처리)
품질검사와 실행 가능한 당일·누적 백테스트를 모두 마친 후, 이번 실행에서 실제로 저장한 결과 디렉터리만 대상으로 scripts/archive_ml_results.py의 prepare를 실행한다. 기존 검사를 다시 실행하거나 DB에서 결과를 재생성하지 않는다. 품질 보고서는 이미 생성한 JSON을 artifacts/ml/generated/daily-review-YYYYMMDD-실행식별자/quality-YYYY-MM-DD.json으로 저장한다. 백테스트 폴더는 scripts/run-ml-backtest.ps1이 반환한 이번 실행의 정확한 경로를 사용한다. 생략된 실험은 과거 폴더로 대체하지 않고 이유를 보고한다.
프로젝트 .venv의 Python으로 다음 명령을 사용한다. DATE와 SOURCE들은 이번 실행에서 확인한 값으로 대체하고 각 경로를 따옴표로 감싼다.
  .\.venv\Scripts\python.exe scripts/archive_ml_results.py prepare --date DATE --source QUALITY_SOURCE --source DAILY_SOURCE --source CUMULATIVE_SOURCE
생략된 실험에 해당하는 --source 인수는 제거한다. prepare가 실패하면 해당 실행의 외부 전송을 중단하고 원인을 보고한다. 민감정보 차단을 우회하거나 파일 내용을 임의로 정제하여 업로드하지 않는다. ZIP과 plan.json은 artifacts/ml/generated/drive-archive/DATE에 생성된다.
Drive 대상 폴더는 1d_z78QpkOZoEmDyXydgwA82zmyezX5Vd로 고정한다. 연결된 Google Drive 도구로 폴더에 접근 가능한지 확인한다. plan.json의 archives 항목별 name(날짜·실행 ID·전체 SHA-256 포함)과 동일한 파일이 대상 폴더에 있는지 확인한다. 단순 검색의 빈 결과를 부재 증거로 사용하지 않는다. 이번 시험에서 search는 빈 결과를 반환했으나 폴더 fetch에는 파일이 나타났다. 폴더 목록이 100개 제한으로 잘렸거나 목록의 완전성을 확인할 수 없다면 로컬 OAuth REST upload 경로로 전체 페이지를 확인하거나 업로드를 보류한다.
동일 이름이 하나 있으면 새 파일을 만들지 않고 그 파일을 검증한다. 여러 개이면 중복 상태를 보고하고 추가 업로드를 중단한다. 없음을 확실히 확인한 경우에만 upload_file(file_uri=항목의 path, file_name=항목의 name, mime_type=application/zip, parent_folder_id=고정 폴더 ID)로 업로드한다. 공유 권한은 변경하지 않는다. 동시에 같은 날짜 업로드를 실행하지 않는다.
업로드되거나 재사용된 파일의 메타데이터를 읽어 MIME, 크기, 부모 폴더와 ID를 확인한다. Drive fetch의 download_raw_file=true로 원본 ZIP을 다시 받아 로컬 검증용 경로에 저장한다. 스트리밍 file_uri를 우선 사용하고, 바이트 전달이 불가능하면 검증 실패로 보고한다. 단순 파일명 또는 Drive 메타데이터만으로 검증 성공을 판정하지 않는다.
  .\.venv\Scripts\python.exe scripts/archive_ml_results.py verify --plan PLAN_PATH --run RUN_DIRECTORY_NAME --download DOWNLOADED_ZIP_PATH
verify는 ZIP 전체 및 내부 파일별 SHA-256, 매니페스트, 크기, 로컬 원본 바이트를 확인한다. 모든 검증이 통과한 파일만 보존 성공으로 표시한다. 파일 ID·Drive URL·SHA-256·원본 개수·신규/재사용·실패 사유를 로컬 보존 영수증에 기록한다. 토큰, 인증 파일, 서명된 다운로드 URL을 영수증에 기록하지 않는다.
연결 도구를 사용할 수 없을 때는 이미 별도 설정된 FOLIO_DRIVE_OAUTH_FILE 또는 FOLIO_DRIVE_ACCESS_TOKEN이 있는 경우에만 다음 독립 명령으로 업로드한다. 인증 정보를 탐색·출력하거나 Git에 추가하지 않는다. 인증이 없으면 보존 실패를 보고한다.
  .\.venv\Scripts\python.exe scripts/archive_ml_results.py upload --plan PLAN_PATH
업로드 실패·인증 만료·할당량·네트워크·해시 불일치는 별도 archive_status=FAILED로 보고하고 로컬 원본과 ZIP을 유지한다. 기존 품질 PASS/WARN/FAIL 및 백테스트 성공/실패를 변경하거나 이미 완료된 검사를 재실행하지 않는다. 이 후처리 실패로 기존 결과 보고를 생략하지 않는다. 최종 보고에 Drive 링크와 검증 상태를 추가한다.


5. 결과 보고
새로운 결과나 문제가 있을 때만 간결하게 보고한다.
보고 내용:
- 당일 데이터 수집 상태
- 품질검사 PASS / WARN / FAIL
- 주요 누락·지연·오류
- 당일 백테스트 순손익 / 수익률 / 최대낙폭 / 거래 수
- 누적 백테스트 순손익 / 수익률 / 최대낙폭 / 거래 수
- 기준선 비교
- 실제 확보된 거래일 수
- 필요한 다음 조치
- 생성된 summary.md
- 생성된 report.json
결과 파일은 가능하면 절대 경로를 함께 표시한다.
새로운 결과, 문제 또는 필요한 사용자 조치가 없는 중복 실행은 조용히 종료한다.
짧은 표본만으로 모델의 수익성이나 실전 적합성을 단정하지 않는다.
API 키, 비밀번호, PIN, 계좌 식별값 등 비밀정보를 출력하거나 외부로 전송하지 않는다.
