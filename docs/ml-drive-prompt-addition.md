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

