# Windows 개발 환경 설치 가이드

이 문서는 새 Windows PC에서 이 프로젝트를 실행하기 위한 설치 순서를 정리한 문서다. 실제 비밀번호, 토스증권 Client Secret 등 비밀값은 문서나 Git에 기록하지 않는다.

기준일: 2026-10-04. 현재는 개인용 로컬 웹앱이며 설치형 프로그램·휴대폰 앱·외부 접속은 제공하지 않는다. 아래 설치 절차는 개발·개인 운용 환경을 위한 것이다.

## 1. 필요한 프로그램

- Python 3.12 이상 (기존 Python 3.14 환경도 사용 가능)
- Git for Windows
- PostgreSQL 18
  - PostgreSQL Server
  - pgAdmin 4
  - Command Line Tools
- 선택 사항: VS Code와 Codex 확장

PostgreSQL의 Stack Builder는 이 프로젝트 실행에 필요하지 않다.

## 2. Python 설치 확인

Python 설치 화면에서 `Add python.exe to PATH`를 선택한다. 이미 설치했는데 `python` 명령을 찾지 못한다면 사용자 환경 변수의 `Path`에 다음 두 경로를 추가한다.

```text
C:\Users\<사용자 이름>\AppData\Local\Programs\Python\Python314\
C:\Users\<사용자 이름>\AppData\Local\Programs\Python\Python314\Scripts\
```

환경 변수를 바꾼 뒤에는 기존 PowerShell을 닫고 새 창을 연다.

```powershell
python --version
python -m pip --version
```

현재 서브 PC의 실행 환경은 Python 3.12.14이며 기존 Python 3.14 환경도 유지할 수 있다. 실행 파일은 3.12 미만일 때 안내하고 중단한다.

## 3. 프로젝트와 가상환경 준비

Git으로 프로젝트를 받은 뒤 프로젝트 폴더의 **FOLIO.cmd**를 실행하면 `.venv`와 기본 패키지를 자동 준비한다. 설치된 패키지 버전이 맞으면 다음 실행에서 다시 설치하지 않는다. `.env`가 없으면 예시를 복사하고 멈추므로 4·5번의 DB 생성과 접속 설정을 먼저 마친 뒤 다시 실행한다. 기존 `.env`, DB와 로컬 API 키를 덮어쓰지 않는다.

아래 명령은 수동 준비가 필요할 때 사용하는 방법이다.

```powershell
cd C:\경로\ai-VibeCoding-2026
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip check
```

PowerShell에서 스크립트 실행이 차단된 경우에만 다음 명령을 한 번 실행한 뒤 다시 활성화한다.

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\.venv\Scripts\Activate.ps1
```

정상 설치되면 `python -m pip check` 결과가 `No broken requirements found.`로 표시된다.

## 4. PostgreSQL 데이터베이스 만들기

PostgreSQL 설치 시 지정한 관리자 암호로 pgAdmin의 `PostgreSQL 18` 서버에 접속한다.

1. `Login/Group Roles`를 우클릭하고 `Create > Login/Group Role`을 선택한다.
2. `General` 탭의 이름에 `paper_local`을 입력한다.
3. `Definition` 탭에서 프로젝트용 DB 암호를 입력한다.
4. `Privileges` 탭에서 `Can login?`을 켜고 저장한다.
5. `Databases`를 우클릭하고 `Create > Database`를 선택한다.
6. 데이터베이스 이름은 `auto_trader`, Owner는 `paper_local`로 지정하고 저장한다.

기본 접속 정보는 다음과 같다.

```text
Host: 127.0.0.1
Port: 5432
Database: auto_trader
User: paper_local
Password: 위에서 직접 지정한 암호
```

테이블은 앱이 처음 실행될 때 `auto_trader/schema.sql`을 기준으로 자동 생성한다.

## 5. 환경 변수 설정

`.env`가 아직 없을 때만 예시 파일을 복사한다. 기존 `.env`를 덮어쓰면 안 된다.

```powershell
Copy-Item .env.example .env
```

`.env`에서 최소한 다음 값을 채운다.

```dotenv
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_DB=auto_trader
POSTGRES_USER=paper_local
POSTGRES_PASSWORD=직접_지정한_DB_암호

TOSS_CLIENT_ID=발급받은_Client_ID
TOSS_CLIENT_SECRET=발급받은_Client_Secret
TOSS_ACCOUNT=

# 선택 사항: 종목 상세 기업·재무·배당·공시
DART_API_KEY=발급받은_OpenDART_인증키
```

- `.env`에는 따옴표나 등호 주변의 불필요한 공백을 넣지 않는 것이 안전하다.
- `TOSS_ACCOUNT`는 API가 반환한 계좌가 하나이면 비워 둘 수 있다. 여러 계좌가 반환되면 사용할 `accountSeq`를 지정한다. 최초 환경 연결은 관리자에게 귀속한다. 로그인 후 설정 → 계정·연결에서 같은 실제 계좌의 API 키를 변경할 수 있지만 여러 실제 계좌 전환은 제공하지 않는다.
- `DART_API_KEY`는 무료 OpenDART 인증키이며, 비워 두면 기업·재무·배당·공시만 표시되지 않는다.
- 토스 허용 IP는 `.env`가 아니라 토스증권 Open API 관리 화면에 등록한다.
- `.env`는 Git에서 제외되며 절대 커밋하지 않는다.

## 6. 토스증권 Open API 준비

1. 토스증권 PC 웹사이트의 `설정 > Open API > Open API Key 설정`에서 키를 발급한다.
2. 현재 PC가 사용하는 공인 IP를 허용 IP로 등록한다.
3. 발급된 Client ID와 Client Secret을 `.env`에 입력한다.

첫 설치에서 증권 API 키를 `.env`에 넣지 않아도 DB와 관리자 계정을 준비하면 로그인할 수 있다. API 키가 없으면 로그인 후 설정 → 계정·연결로 이동한다. 연결 등록·변경 팝업에서 API 키와 Secret을 등록한다. 새로 입력한 키는 `.env`를 수정하지 않고 `.local-secrets/`의 Windows 암호화 파일로 저장하며 DB에는 저장하지 않는다. 다른 Windows 사용자·컴퓨터로 이전하거나 키 파일을 잃었다면 같은 화면에서 키를 재등록한다. 증권사 연결 장애는 로그인을 막지 않으며 DB 연결 실패는 계정 인증이 불가능하므로 복구해야 한다.

공인 IP가 바뀌면 토스증권 설정의 허용 IP도 다시 바꿔야 한다. 동일한 API 키로 여러 프로그램이 새 토큰을 계속 발급하면 기존 토큰이 무효화될 수 있으므로 개발 서버를 중복 실행하지 않는다.

## 7. 관리자와 LIVE PIN 설정

최초 한 번 관리자 계정을 생성한다.

```powershell
python -m auto_trader.create_admin
```

관리자 아이디·비밀번호로 로그인한다. 새 비밀번호는 8~20자다. 화면 잠금 해제와 실제 주문 인증에 사용할 숫자 6자리 PIN도 설정한다. 로그인 후 설정 → 계정·연결의 PIN 등록·변경 팝업에서도 설정할 수 있다.

```powershell
python -m auto_trader.set_live_pin
```

관리자 비밀번호와 PIN은 원문이 아닌 해시로 PostgreSQL에 저장된다.

## 8. 실행과 확인

평소에는 프로젝트 폴더의 **FOLIO.cmd**를 더블클릭한다. 루트 폴더가 아닌 다른 작업 위치에서도 실행할 수 있다. 실행 창이 준비 완료를 표시하면 기본 브라우저에서 사이트가 열린다.

1. DB 연결을 확인한다. 로컬 PostgreSQL이 꺼져 있으면 설치된 Windows 서비스를 시작한다. 서비스 시작에 관리자 권한이 필요한 경우 Windows 권한 확인 창이 뜬다. 전체 서버를 관리자 권한으로 실행하는 것은 아니다.
2. 테이블을 준비하고 관리자 계정이 없을 때만 아이디·비밀번호를 물어본다. 기존 계정과 자산은 유지한다.
3. 서버를 `127.0.0.1`에 한 개만 실행한다. API 연결 성공과 관계없이 로그인 화면에 접근할 수 있다.
4. 서버 준비가 끝나면 브라우저를 연다. 이미 같은 프로젝트 폴더의 서버가 실행 중이면 새 서버를 만들지 않고 기존 화면을 연다.

실행 창을 유지하고 서버를 종료하려면 **Ctrl+C**를 누른다. 브라우저 탭을 닫는 것은 서버 종료가 아니다. 서버 종료 시 자동매매와 수집도 멈추지만 공유 PostgreSQL 서비스는 끄지 않는다. 다시 실행해도 전략은 자동 시작하지 않는다.

오류 로그는 Git에서 제외된 `.runtime-logs/server.log`에 남는다. 다른 프로그램이 같은 포트를 쓰거나 다른 프로젝트 폴더의 FOLIO가 실행 중이면 임의로 종료하지 않고 안내한다. PostgreSQL을 여러 버전 설치했다면 `.env`에 `POSTGRES_SERVICE=postgresql-x64-18`처럼 사용할 서비스 이름을 지정한다. 서비스 이름은 Windows 서비스 목록에서 확인한다. 원격 DB 설정은 자동 서비스 시작 대상이 아니다.

직접 서버만 실행하는 기존 방식도 유지한다.

```powershell
.\.venv\Scripts\python.exe -m auto_trader
```

브라우저 없이 실행 흐름을 확인하려면 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/start.ps1 -NoBrowser`를 사용한다. `FOLIO.cmd`의 실행 정책 설정은 해당 실행에만 적용하며 Windows 사용자 설정을 바꾸지 않는다.

- 로그인: `http://127.0.0.1:8000/login`
- PAPER 모의매매: `http://127.0.0.1:8000/paper`
- LIVE 실제계좌 조회: `http://127.0.0.1:8000/live`
- API 문서: `http://127.0.0.1:8000/docs`

LIVE 화면에서 총자산, 보유종목, 원화·외화 매수 가능 금액이 나오면 계좌 연결이 완료된 것이다. 실제 수동 주문은 주문 PIN·서버 안전 잠금·입력 검증을 모두 통과해야 하며, 연결 성공만으로 주문이 허용되는 것은 아니다.

PAPER에서는 계좌 카드를 눌러 전환 → 전략 선택(즉시 적용) → 전략 시작 순서로 진행한다. 재시작 후 자산·선택 계좌·계좌별 마지막 전략은 복원되지만 자동매매는 정지 상태이며 전략 시작을 눌러야 한다. 세부 동작은 [PAPER_STRATEGY.md](PAPER_STRATEGY.md)를 참고한다.

실행은 단일 서버 프로세스로 유지한다. 컴퓨터나 서버를 종료하면 자동매매·정기 수집도 멈춘다. 외출 중 휴대폰 확인·정지는 계획만 기록했으며 현재 서버 포트 개방이나 외부 주소 접속을 설정하지 않는다.

## 9. 테스트

```powershell
python -m unittest discover -s tests -v
```

## 문제 해결

### `python` 명령을 찾지 못함

Python과 `Scripts` 경로를 사용자 `Path`에 추가하고 PowerShell을 새로 연다.

### PowerShell에서 `Activate.ps1` 실행이 차단됨

이 문서의 3번에 있는 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`를 현재 사용자 범위에서 한 번 실행한다.

### PostgreSQL 연결 또는 비밀번호 오류

PostgreSQL 18 서비스가 실행 중인지 확인하고 `.env`의 Host, Port, DB, User, Password가 pgAdmin에서 만든 값과 같은지 확인한다.

### 토스 API `401 invalid-token`

서버를 여러 개 실행하고 있지 않은지 확인한 뒤 한 개만 남기고 재시작한다. 앱은 만료되거나 무효화된 토큰에 대해 한 번 자동 재발급을 시도한다.

### 토스 API 접속 거부 또는 데이터가 나오지 않음

토스증권 Open API 설정에 현재 공인 IP가 허용되어 있는지 확인한다.

### 빠른 새로고침 후 `429`

짧은 시간에 요청이 너무 많이 발생한 경우다. 잠시 기다렸다가 다시 시도한다. 앱 내부 캐시가 반복 조회를 줄여 주지만 여러 탭이나 서버를 동시에 실행하면 제한에 걸릴 수 있다.

### VS Code의 `.env` 터미널 주입 경고

이 프로젝트는 실행 시 자체적으로 루트의 `.env`를 읽으므로 해당 경고만으로 앱 설정이 누락된 것은 아니다. 터미널에도 자동 주입하려면 VS Code 설정의 `python.terminal.useEnvFile`을 켤 수 있다.
