# 20261125-Codex-PAPER-UI-15b

## 작업 정의

| 항목 | 기록 |
|---|---|
| 고유 작업 ID | 20261125-Codex-PAPER-UI-15b / task_51f275edb07f |
| 목표 / 구현 이유 | HTTP UI 검사 15b의 무조건 통과 제거 및 설정 저장 전후 현금·정지 상태 비교 누락 수정 |
| 담당 AI | Codex, 단독 구현 |
| GitHub 저장소 | https://github.com/YEOJUN-KIM/ai-VibeCoding-2026 |
| 브랜치 | feat/folio-paper-simplify-01 |
| Worktree 절대 경로 | C:/Users/User/orca/workspaces/ai-VibeCoding-2026/folio-paper-simplify-01 |
| 기준 커밋 전체 SHA | afa545594e402e17a7c4ad6900f75ce91288dfef |
| 수정 허용 파일 | scripts/paper_app_ui_e2e.py, 이 작업 기록 |
| 선행 작업 / commit / PR | afa5455 독립 검증의 P2 발견; 기존 PR #12는 별도 작업 |
| 공통 파일 담당자 / 작업 순서 | 대상 스크립트는 이번 dispatch의 Codex 단독 담당 |
| 완료 조건 | 해당 HTTP 스크립트 통과, 잘못된 state/running/cash/total fixture의 FAIL 검출, 자원 종료 및 로컬 commit |
| 승인된 검증 범위 | 대상 scripts/paper_app_ui_e2e.py만 실제 격리 HTTP 실행; 해당 15b AST fixture 검증; git diff --check |
| 실행 환경 격리 | 신규 PostgreSQL18 127.0.0.1:55437 / folio_mvp_test_ui15b, 합성 PAPER 앱 127.0.0.1:55438; .env 로딩 메모리 차단 및 파일/socket audit hook; 로그·저널은 신규 OS 임시 폴더 |
| 미실행 / 금지 작업 | 전체 Python/JS, 다른 HTTP suite, 제품 코드 변경, 실제 증권/계좌/주문, 운영 DB/서버, .env 접근, push/PR/merge, 사용량 설정 변경 |
| 승인 상태 / 근거 | 사용자 승인 dispatch ctx_6f3b913524f1: 두 검증만 수정, 해당 테스트·작은 fixture 실행 및 로컬 commit 허용 |
| 진행 상태 | 구현·대상 자동 검증 완료 / GPT 검토 대기 / 사용자 동작 검증·운영 적용 미실시 |

## 착수 확인

- 2026-11-25: branch/HEAD 지시값 일치, staged/unstaged/untracked 없음(clean).
- archive/folio-legacy-risk-settings = 8c3d66bbd0d08539aa4f4e39cc00524bb371f87c.
- preserve/paper-simplify-01-9a4eb0a = 9a4eb0ac53ce94a6423e66c79bacda4c102071fe.
- GitHub connector로 전체 5개 branch와 빈 다음 페이지, 열린 PR #12 확인. 현재 작업 branch는 원격에 없음. gh/ls-remote는 기존 proxy 127.0.0.1:9 실패이며 설정 변경 없이 connector로 읽음.
- 관련 PAPER-APP-UI-01/PAPER-FINALIZE-01 기록은 과거 작업이며 해당 변경은 기준 HEAD에 포함됨. 이번 범위와 활성 중복 작업은 확인되지 않음; 원격에 보이지 않는 작업 부재까지 보장하지 않음.
- 이전 보고서는 허용된 경로에서 일반 읽기로 참고했고, 실제 paper_mvp.py의 상태 및 account 계약, 설정 저장 경로를 재확인함.
- 시작 전 55437/55438 LISTEN 없음 확인. 기존 클러스터 재사용 없이 신규 임시 경로 생성.

## 범위 변경과 인계

- 범위 변경 없음. 제품 코드·API·DB 계약 변경 없음.
- 후속 담당: 감독 coordinator/GPT 검토. push/PR/merge 금지 유지.

## 결과

- 변경 파일: `scripts/paper_app_ui_e2e.py` 및 이 기록. 제품 코드·API·DB 계약 변경 없음. 공통 스크립트를 추후 수정하는 작업과는 순차 인계 필요.
- 15b는 `or True`를 제거하고 저장 전후 `run.running is False`, 정지 허용 상태 및 상태 보존, 저장 전 cash=total_asset=저장 후 cash=total_asset를 검사한다. 저장 전 HTTP 응답은 별도 참조로 보존한다.
- 금액 문자열을 `Decimal`로 비교하므로 표시 자릿수 차이는 허용하고 float로 사라질 수 있는 미세한 금액 차이는 검출한다. 기존 15b의 항상 통과를 실제 검증으로 바꾸며 HTTP 요청/제품 동작은 바꾸지 않는다. fixture/실제 HTTP를 분리하는 개념과 정확한 십진 금액 비교가 핵심 학습 사항이다.
- 신규 DB fingerprint: `127.0.0.1|55437|folio_mvp_test_ui15b|folio_review`. 테스트 계정은 신규 disposable DB의 합성 fixture만 사용했다.
- 실제 앱 대상 HTTP: `SUMMARY 22/22`, exit0. 설정 금액500만원 저장 전후 현금/총자산은 모두 `10000000`, 상태 `idle`, running=False였다.
- 작은 fixture: 실제 소스의 check 함수·15b AST·최종 SUMMARY/exit를 그대로 추출하여 HTTP/앱 import 없이 실행. 14/14 PASS(정상2, 부정12). before/after 각각 cash·total_asset·state·running 오류, 정지 상태의 임의 변경, before/after 한쪽 금액 두 개의 동일 변경, float 정밀도 아래의 현금 차이를 각각 `FAIL 15b`, `SUMMARY 0/1`, exit1로 검출했다. 정상 금액 표현 `10000000.00`은 통과한다.
- `git diff --check` 통과. 전체 Python/JS, 다른 HTTP suite 및 브라우저 검증은 승인 범위에 따라 실행하지 않았다. 실제 증권·계좌·실주문·운영 DB 검증 없음. 사용자 직접 확인과 운영 적용은 미완료다.
- 앱 실행 세션을 Ctrl+C로 종료하고 이 작업 소유 pgdata만 `pg_ctl -m fast -w stop`으로 종료(`server stopped`, `status: no server running`)했다. 절대 부모 경로 대조 후 신규 pgdata를 삭제했다. 127.0.0.1:55437/55438의 socket bind 성공으로 두 포트가 비었음을 확인했다. 사용자용 앱을 계속 실행하지 않는다.
- PostgreSQL 시작의 Windows restricted-token 오류는 승인된 동일 신규 테스트 클러스터 시작 명령을 escalated 실행하여 해결했다. 운영/기존 자원은 건드리지 않았다.
- 증거/안전 하네스는 `C:/Users/User/AppData/Local/Temp/folio-ui15b-20261125/`에 보존: `sitecustomize.py`, `server.py`, `http-ui.log`, `http-ui.json`, `fixture_15b.py`, `fixture-15b.log`, `postgres.log`, `REPORT.md`. OS 임시 폴더이므로 장기 보존을 보장하지 않는다. fixture는 제품 테스트 파일에 추가하지 않았다.
- 로컬 commit 후 최종 SHA/clean/ref는 감독 coordinator에게 보고한다. push 미실행, 신규 PR 없음, merge/배포 없음. GitHub에서 확인 가능한 변경 이력 게시도 이번 승인 범위 밖이다.
- 남은 위험/GPT 검토: 이 스크립트는 신규 계정·미실행 상태를 전제로 한다. 기존 준비된 DB 재사용 시 start_guard 검사가 실패하므로 항상 새 disposable DB가 필요하다. 누락/비정상 형식의 응답은 기존 스크립트와 같이 예외로 비정상 종료할 수 있으며 성공 처리하지 않는다. Decimal 비교와 정지 상태 보존이 의도한 계약인지 검토한다.
- 사용량: 기존 admin denied에 따라 UNKNOWN 유지, 조회 재시도나 설정 변경 없음. main 통합·운영 적용은 별도 사용자 승인 대상이며 이번에는 수행하지 않는다.

## 사용자 직접 확인용 안전한 PAPER 실행 절차

아래는 이번에 실제 실행한 준비/실행/종료 명령이다. PostgreSQL18, 현재 Python 의존성, 보존된 두 하네스 파일이 필요하다. 하네스가 없으면 일반 앱으로 대체 실행하지 않는다. 하네스는 `.env` 로딩을 메모리에서 차단하고 운영 파일/외부 socket 접근 차단, 증권 자격증명 제거, PAPER·LIVE 잠금·수집 비활성, 신규 DB·저널 경로를 적용한다. 서버도 하네스가 활성화되지 않으면 import 전에 중단한다.

다른 프로세스가 두 포트를 점유하거나 `pgdata`가 이미 있으면 중단한다. 다른 자원을 종료하거나 기존 DB를 재사용하지 않는다. 새 PowerShell 창에서 다음 준비를 수행한다(경로는 정확한 이번 worktree).

```powershell
Set-Location 'C:/Users/User/orca/workspaces/ai-VibeCoding-2026/folio-paper-simplify-01'
$taskArt = 'C:/Users/User/AppData/Local/Temp/folio-ui15b-20261125'
if (!(Test-Path "$taskArt/sitecustomize.py") -or !(Test-Path "$taskArt/server.py")) { throw 'isolation harness missing' }
if (Test-Path "$taskArt/pgdata") { throw 'use a fresh disposable cluster; do not reuse' }
@'
import socket
for port in (55437, 55438):
    with socket.socket() as s:
        s.bind(('127.0.0.1', port))
    print('loopback port free:', port)
'@ | python -B -
if ($LASTEXITCODE -ne 0) { throw 'port unavailable' }
& 'C:/Program Files/PostgreSQL/18/bin/initdb.exe' -D "$taskArt/pgdata" -U folio_review --auth=trust --encoding=UTF8 --locale=C
& 'C:/Program Files/PostgreSQL/18/bin/pg_ctl.exe' -D "$taskArt/pgdata" -l "$taskArt/postgres.log" -o '-h 127.0.0.1 -p 55437' -w start
& 'C:/Program Files/PostgreSQL/18/bin/createdb.exe' -h 127.0.0.1 -p 55437 -U folio_review folio_mvp_test_ui15b
& 'C:/Program Files/PostgreSQL/18/bin/psql.exe' -h 127.0.0.1 -p 55437 -U folio_review -d folio_mvp_test_ui15b -Atc 'SELECT inet_server_addr(),inet_server_port(),current_database(),current_user;'
# 예상: 127.0.0.1|55437|folio_mvp_test_ui15b|folio_review
$env:PYTHONPATH="$taskArt;$PWD"
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONIOENCODING='utf-8'
python -B "$taskArt/server.py"
```

각 준비 명령이 실패하면 다음 명령을 실행하지 않는다. Windows restricted-token 오류가 있으면 이 신규 테스트 클러스터의 해당 명령에 한해 실행 권한을 확인한다. 앱 URL은 `http://127.0.0.1:55438/paper-mvp`, 로그인 URL은 `http://127.0.0.1:55438/login`이다. `reviewtester` / `review-test-2026`은 실제 자격증명이 아닌 서버 하네스가 이 신규 DB에 생성하는 공개 합성 fixture다.

자동 재현을 원하면 별도 PowerShell 창에서 같은 worktree로 이동 후 다음 대상 스크립트만 실행한다. 이 단계가 계정을 준비하므로 동일 DB에서 반복 실행하지 않는다.

```powershell
$env:PYTHONPATH="$env:TEMP/folio-ui15b-20261125;$PWD"
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONIOENCODING='utf-8'
python -B scripts/paper_app_ui_e2e.py http://127.0.0.1:55438 reviewtester review-test-2026 "$env:TEMP/folio-ui15b-20261125/http-ui.json"
python -B "$env:TEMP/folio-ui15b-20261125/fixture_15b.py"
```

예상: HTTP22/22, fixture14/14(부정12개의 FAIL은 기대한 탐지 증거). 브라우저 직접 확인은 이 작업에서 실행하지 않았으며, 위 URL에서 합성 데이터 표시·금액500만원·시작 대기 상태·현금/총자산1000만원을 확인할 수 있다. 새로고침 후 같은 값이 기대된다. 추가 시작/주문 동작은 이번 검증 결과에 포함하지 않는다.

확인 후 서버 창에서 Ctrl+C로 이 앱만 종료하고 다음 명령으로 이 클러스터만 종료한다. `status`의 `no server running`은 정상적인 종료 확인이며 exit1일 수 있다.

```powershell
& 'C:/Program Files/PostgreSQL/18/bin/pg_ctl.exe' -D "$env:TEMP/folio-ui15b-20261125/pgdata" -m fast -w stop
& 'C:/Program Files/PostgreSQL/18/bin/pg_ctl.exe' -D "$env:TEMP/folio-ui15b-20261125/pgdata" status
```

삭제는 종료 확인 후 이번 소유 `C:/Users/User/AppData/Local/Temp/folio-ui15b-20261125/pgdata`와 해당 하네스가 생성한 `journals`만 대상으로 한다. 실제 경로가 이 절대 부모 아래의 정확한 경로인지 확인하고 PowerShell `Remove-Item -LiteralPath <확인한 경로> -Recurse -Force`를 사용한다. 포트 bind 확인으로 두 listener가 없는지 다시 확인한다. 기존 운영 서버/DB·백업·실행 기록은 정리하지 않는다.
