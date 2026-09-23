# Windows 개발 환경 설치 가이드

이 문서는 새 Windows PC에서 이 프로젝트를 실행하기 위한 설치 순서를 정리한 문서다. 실제 비밀번호, 토스증권 Client Secret 등 비밀값은 문서나 Git에 기록하지 않는다.

## 1. 필요한 프로그램

- Python 3.14.x
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

현재 개발 환경에서 확인한 버전은 Python 3.14.7이다.

## 3. 프로젝트와 가상환경 준비

Git으로 프로젝트를 받은 뒤 프로젝트 루트에서 실행한다.

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
TOSS_ALLOWED_IP=토스증권에_등록한_공인_IP
```

- `.env`에는 따옴표나 등호 주변의 불필요한 공백을 넣지 않는 것이 안전하다.
- `TOSS_ACCOUNT`는 계좌를 하나만 사용한다면 비워 둘 수 있다.
- `.env`는 Git에서 제외되며 절대 커밋하지 않는다.

## 6. 토스증권 Open API 준비

1. 토스증권 PC 웹사이트의 `설정 > Open API > Open API Key 설정`에서 키를 발급한다.
2. 현재 PC가 사용하는 공인 IP를 허용 IP로 등록한다.
3. 발급된 Client ID와 Client Secret을 `.env`에 입력한다.

공인 IP가 바뀌면 토스증권 설정의 허용 IP도 다시 바꿔야 한다. 동일한 API 키로 여러 프로그램이 새 토큰을 계속 발급하면 기존 토큰이 무효화될 수 있으므로 개발 서버를 중복 실행하지 않는다.

## 7. 관리자와 LIVE PIN 설정

최초 한 번 관리자 계정을 생성한다.

```powershell
python -m auto_trader.create_admin
```

LIVE 화면 잠금 해제에 사용할 숫자 6자리 PIN도 설정한다.

```powershell
python -m auto_trader.set_live_pin
```

관리자 비밀번호와 PIN은 원문이 아닌 해시로 PostgreSQL에 저장된다.

## 8. 실행과 확인

```powershell
python -m auto_trader
```

- 로그인: `http://127.0.0.1:8000/login`
- PAPER 모의매매: `http://127.0.0.1:8000/paper`
- LIVE 실제계좌 조회: `http://127.0.0.1:8000/live`
- API 문서: `http://127.0.0.1:8000/docs`

LIVE 화면에서 총자산, 보유종목, 원화·외화 매수 가능 금액이 나오면 계좌 연결이 완료된 것이다. 국내 종목 검색과 관심종목은 조회 전용이며, 현재 실제 주문 기능은 잠겨 있다.

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
