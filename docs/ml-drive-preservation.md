# FOLIO ML 결과 Drive 보존

2026-11-04 구현 및 시험 기록. 기존 검사·백테스트 코드, DB, 예약은 변경하지 않았다.

## 기존 구성과 확인된 원본

- `scripts/run-ml-backtest.ps1`: KST 시각과 무작위 접미사로 `artifacts/ml/generated/backtest-YYYYMMDD-HHmmss-xxxxxxxx`를 만들며 report.json, summary.md, input.json 및 4개 기준선의 daily/equity CSV 8개를 저장한다.
- `scripts/generate-ml-quality-report.ps1`: `auto_trader.ml.quality_report`의 JSON 표준출력을 전달한다. 스크립트 자체에 파일 저장 기능은 없다. 품질 함수는 DB 보고서도 저장하므로 이번 작업에서는 호출하지 않았다.
- 11월 3일 저장 원본: `daily-review-20261103-170023/quality-2026-11-03.json` 1개, `backtest-20261103-170024-aac70351` 당일 결과 11개, `backtest-20261103-170028-939d40cd` 누적 결과 11개. 모두 `artifacts/ml/generated` 아래에 실제 존재한다.
- 당일: 8,832봉, 22종목, 관측일 2026-11-03. 누적: 133,075봉, 22종목, 관측일 2026-10-02부터 2026-11-03까지 19일. 원본 report.json에서 확인했다. 품질은 WARN이다.
- 기존 Codex 예약 `folio`: ACTIVE, 한국시간 평일 17:00. Strategy ID 17, DRY_RUN 확인, next_open, 당일 Start=End=실행일 및 누적 Start 생략/End=실행일. 설정 파일은 읽기만 했다.
- 저장소의 scripts, auto_trader, tests, docs 및 requirements에서 재사용 가능한 Drive 업로드 코드나 Google 인증 설정은 발견하지 못했다. 브로커 OAuth는 Drive 인증으로 재사용하지 않는다. 프로젝트 .venv에는 google/requests가 없으며 새 의존성을 설치하지 않았다.
- Google 관련 인증 환경변수, gcloud/rclone 명령 및 표준 로컬 인증 파일 경로는 발견하지 못했다. 비밀 파일 내용이나 토큰을 출력하지 않았다. 이 조사는 모든 사용자 디렉터리의 자격증명 부재를 보장하지 않는다.

## 구현

`scripts/archive_ml_results.py`는 Python 표준 라이브러리만 사용한다. 애플리케이션 모듈을 불러오지 않으며 DB·학습·백테스트를 호출하지 않는다.

- `prepare`: 명시한 날짜 및 정확한 실행 폴더만 읽는다. 알려진 결과 파일 집합과 일치해야 하며 경로 이탈·링크·예상 외 파일·날짜/전략/체결방식 불일치를 차단한다. 원본 파일은 수정하지 않는다.
- JSON 민감 필드, 자격증명 대입문, Bearer/JWT/개인키/DB 접속문자열/이메일 등의 패턴을 검사한다. 민감정보 발견 시 전체 prepare를 실패시킨다. 이 검사는 일반적인 패턴 방어이며 임의 문자열에 숨긴 모든 비밀을 탐지하는 DLP 보장은 아니다. 보고서 스키마를 확장하면 새 필드와 자유문자열을 먼저 검토해야 한다.
- 실행별 결정적 ZIP: 이름에 날짜·원본 실행 폴더명·전체 SHA-256, 내부에 `날짜/실행폴더/원본파일`과 manifest.json. 원본 23개 모두 바이트 그대로 포함한다. Drive 상위 폴더의 하위 폴더를 별도로 생성하는 대신 파일명과 ZIP 내부 경로로 날짜·실행을 구분한다.
- 각 원본의 SHA-256과 크기 및 report_run_id를 매니페스트에 남긴다. 동일 실행·동일 바이트는 ZIP 해시가 같아 재사용한다. 다른 실행의 동일 원본은 독립 실행 기록에 포함될 수 있다. 원격의 같은 이름은 바이트를 다시 검증해야 하며 자동으로 덮어쓰지 않는다.
- `verify`: 원격 다운로드 ZIP의 전체 해시·크기, 정확한 멤버 집합, 매니페스트, 내부 각 파일 해시·크기, 현재 로컬 원본 바이트까지 대조한다. 압축을 파일시스템에 풀지 않는다.
- `upload`: 직접 Drive v3 REST OAuth 경로. 전체 페이지를 조회해 중복 이름을 찾고 신규 업로드 또는 재사용 후 원격 바이트를 내려받아 검증한다. plan별 배타 로컬 잠금과 영수증을 사용한다. 실패는 독립 종료코드 1이며 원본 삭제 기능이 없다. 다중 컴퓨터의 동시 실행은 Drive의 이름 유일성 제약이 없어 보장하지 않으므로 직렬 실행해야 한다.
- 결과 ZIP·계획·영수증은 기존 Git 제외 경로 `artifacts/ml/generated/drive-archive` 아래에 있다. 인증 파일은 업로드 대상에 들어가지 않는다.

## 실제 시험 결과

연결된 Google Drive 도구로 `ML_Daily_Reports` 폴더에 ZIP 3개를 업로드했다. 각 파일을 Drive에서 다시 다운로드하고 아래의 전체 SHA-256 및 내부 원본 23개 해시·바이트 일치를 확인했다. 메타데이터에서 대상 부모 폴더와 크기도 확인했다.

| 결과 | 원본 수 | ZIP 바이트 | ZIP SHA-256 | Drive |
|---|---:|---:|---|---|
| 품질 | 1 | 2,594 | c77d090bfcefdeebc627b75b5d528927cefa38e6322d035eb5f8f8b4cb71cff6 | [파일](https://drive.google.com/file/d/1Gptin9UOssEyLVW_ULlNZSnwRgJDx6xc/view?usp=drivesdk) |
| 당일 | 11 | 505,817 | d9fa46f1b428a82584b4916c56de33390451e5e829a9458d991d2e7d13d7d24b | [파일](https://drive.google.com/file/d/1Sc0k3sPUXpyiE3vVsD-yuSpRSHqD6RVW/view?usp=drivesdk) |
| 누적 | 11 | 3,250,344 | 9bbb635ffca90e9eac99bc8681a0a0082432a0de731ebebab43e077c21705050 | [파일](https://drive.google.com/file/d/1yQtZH6U85QETAf7oMSA5MSisFsM43HPF/view?usp=drivesdk) |

계획·영수증·재다운로드 검증 기록: `artifacts/ml/generated/drive-archive/2026-11-03/{plan,upload-receipt,verification}.json`. 인증 정보와 서명된 다운로드 링크는 기록하지 않았다.

안전 테스트 `tests/test_archive_ml_results.py` 9개 통과: 결정적 ZIP, 민감 필드/텍스트 차단, 예상 외 파일, 경로 이탈, 날짜 불일치, 다운로드 변조/원본 변경, 재실행 중복 방지, 업로드 실패 시 원본 유지, 이름만 같은 잘못된 원격 바이트 차단. 중복 생성 방지·실패 동작은 메모리 Drive 대역으로 검증했다. 실제 네트워크 실패나 직접 OAuth REST 업로드는 실인증 시험하지 않았다. DB 및 백테스트는 재실행하지 않았다.

Drive search는 방금 생성한 파일을 빈 결과로 반환했으나 폴더 fetch는 3개를 반환했다. 따라서 검색 인덱스의 빈 결과를 중복 없음의 증거로 삼지 않는다. 연결 도구의 폴더 fetch는 최대 100개이므로 완전한 목록 확인이 불가능하면 업로드를 보류하거나 페이지 처리하는 REST 경로를 사용한다.

## 인증 및 실행

현재 Codex heartbeat 예약은 에이전트 프롬프트 실행이므로 연결된 Drive 도구를 사용하는 방식이 기존 환경과 가장 잘 맞는다. 이번 대화에서 업로드·읽기는 성공했다. 향후 예약 실행에서도 플러그인 연결과 폴더 접근권한이 유지되어야 한다. 실제 예약 실행 컨텍스트에서의 도구 제공 여부까지 이번 시험으로 보장하지는 않는다. 플러그인 OAuth 토큰을 로컬 Python에서 추출하거나 재사용하지 않는다.

독립 쉘 실행에는 둘 중 하나를 별도 설정해야 한다.

1. `FOLIO_DRIVE_OAUTH_FILE`: 저장소 **밖**의 OAuth JSON 파일 절대 경로. 필수 필드는 client_id, client_secret, refresh_token이다. 같은 Windows 예약 실행 계정에만 읽기 권한을 준다. Google Cloud의 Drive API 활성화, 대상 폴더 접근권한 및 업로드·조회·다운로드 가능한 사용자 동의가 필요하다. drive.file 범위는 앱에서 접근 허용된 폴더/파일이어야 하므로 지정된 기존 폴더 접근까지 확인해야 한다. OAuth offline access로 갱신 토큰을 발급받는다.
2. `FOLIO_DRIVE_ACCESS_TOKEN`: 이미 발급한 짧은 수명의 토큰을 실행 환경에만 전달한다. 토큰 자동 갱신은 없으므로 상시 예약용으로는 OAuth 파일 방식을 사용한다.

인증 JSON을 저장소 안에 두면 스크립트가 거부한다. 토큰 값을 명령줄, .env 추적 파일, 코드, 보고서, Git에 쓰지 않는다. 이번 작업은 인증 파일이나 환경변수를 새로 만들지 않았다.

```powershell
.\.venv\Scripts\python.exe scripts/archive_ml_results.py prepare --date 2026-11-03 --source artifacts/ml/generated/daily-review-20261103-170023 --source artifacts/ml/generated/backtest-20261103-170024-aac70351 --source artifacts/ml/generated/backtest-20261103-170028-939d40cd
# 아래 직접 업로드는 별도 인증 설정 후에만 실행한다.
.\.venv\Scripts\python.exe scripts/archive_ml_results.py upload --plan artifacts/ml/generated/drive-archive/2026-11-03/plan.json
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_archive_ml_results.py -v
```

외부 프로세스 호출자는 검사/백테스트 상태를 먼저 보존하고 업로드 종료코드를 별도 변수로 기록해야 한다. 업로드 실패를 기존 품질 FAIL로 변환하거나 기존 결과 보고를 중단하면 안 된다. 스크립트가 기존 품질/백테스트 성공을 알 수 없으므로 종료코드로 두 결과를 합치지 않는다.

API 설계 근거: [Google Drive multipart 업로드](https://developers.google.com/workspace/drive/api/guides/manage-uploads), [OAuth offline access와 refresh token](https://developers.google.com/identity/protocols/oauth2/web-server).

## 예약 수정안 — 아직 미적용

`ml-drive-prompt-addition.md`는 추가할 후처리 절이며 `ml-drive-automation-prompt-draft.md`는 기존 전체 프롬프트의 결과 보고 직전에 이 절만 삽입한 전체 제안문이다. 현재 예약 시간·상태·대상 채팅과 기존 Strategy ID 17, DRY_RUN, next_open, 비용·위험·원본/계좌/주문/비밀정보 안전 제약을 그대로 유지한다. 예약 API나 설정 파일에 대한 쓰기는 수행하지 않았다.

조사 시 원본 `folio/automation.toml` SHA-256: `762912ff8433bc313718454d49b35205b2bd8a22024ec858f0fd260eeea28b5a`. 전체 제안문에서 추가 절을 제거하면 기존 프롬프트와 문자 단위로 같음을 확인했다.
