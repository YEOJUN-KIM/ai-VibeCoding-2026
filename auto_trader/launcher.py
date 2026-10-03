"""One local launch: database, account preparation, server, then browser.

Does not modify broker credentials, start strategies, or stop the shared DB service.
"""
import argparse
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.request import build_opener, ProxyHandler
import webbrowser

ROOT = Path(__file__).resolve().parent.parent


class LaunchError(Exception):
    pass


def workspace_id():
    return sha256(str(ROOT.resolve()).casefold().encode('utf-8')).hexdigest()[:24]


def server_status(port):
    try:
        # Local status must not pass through a configured HTTP proxy.
        with build_opener(ProxyHandler({})).open(f'http://127.0.0.1:{port}/startup/readiness', timeout=2) as response:
            result = json.load(response)
        if result.get('application') == 'FOLIO' and result.get('workspace_id') == workspace_id():
            return 'ready' if result.get('ready') else 'waiting'
    except Exception:
        pass
    return 'other' if port_open('127.0.0.1', port) else 'absent'


def port_open(host, port):
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


@contextmanager
def launch_lock():
    folder = ROOT / '.runtime-logs'
    folder.mkdir(exist_ok=True)
    with (folder / 'launcher.lock').open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise LaunchError('다른 실행 창에서 준비 중입니다. 잠시 뒤 다시 실행하세요.') from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def postgres_service(names, configured=None):
    if configured:
        if configured not in names:
            raise LaunchError('POSTGRES_SERVICE 이름이 설치된 PostgreSQL 서비스와 다릅니다.')
        return configured
    if len(names) == 1:
        return names[0]
    if not names:
        raise LaunchError('PostgreSQL 설치가 필요합니다. docs/INSTALL_WINDOWS.md의 4번을 확인하세요.')
    raise LaunchError('PostgreSQL 서비스가 여러 개입니다. .env의 POSTGRES_SERVICE에 사용할 이름을 지정하세요.')


def start_database_service():
    if os.name != 'nt':
        raise LaunchError('DB를 먼저 실행하세요. 자동 서비스 시작은 Windows에서 지원합니다.')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-Command',
        "@(Get-Service -Name 'postgresql*' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name) | ConvertTo-Json -Compress"],
        capture_output=True, text=True, check=False)
    if result.returncode:
        raise LaunchError('Windows PostgreSQL 서비스를 확인하지 못했습니다.')
    names = json.loads(result.stdout.strip() or '[]')
    names = [names] if isinstance(names, str) else names
    name = postgres_service(names, os.getenv('POSTGRES_SERVICE'))
    print('DB를 켭니다. Windows 권한 확인 창이 나오면 서비스 시작을 허용하세요.', flush=True)
    result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', str(ROOT / 'scripts' / 'start-postgres.ps1'), '-ServiceName', name], check=False)
    if result.returncode:
        raise LaunchError('DB를 켜지 못했습니다. PostgreSQL 서비스와 시작 권한을 확인하세요.')


def ensure_database(settings, connect):
    if not settings.postgres_ready:
        raise LaunchError('.env의 PostgreSQL DB 이름·사용자·비밀번호를 먼저 설정하세요.')
    if not port_open(settings.postgres_host, settings.postgres_port):
        if settings.postgres_host not in ('localhost', '127.0.0.1', '::1'):
            raise LaunchError('설정한 DB 서버에 연결하지 못했습니다. DB 주소와 실행 상태를 확인하세요.')
        start_database_service()
    for attempt in range(6):
        try:
            with connect() as conn:
                conn.execute('SELECT 1')
            return
        except Exception:
            if attempt < 5:
                time.sleep(1)
    # Do not print the exception: connection strings may contain a password.
    raise LaunchError('DB에 접속하지 못했습니다. .env의 DB 설정과 DB 생성 여부를 확인하세요. 기존 데이터는 변경하지 않았습니다.')


def prepare_account(connect, initialize):
    initialize()
    with connect() as conn:
        exists = conn.execute('SELECT EXISTS(SELECT 1 FROM admin_users) AS present').fetchone()['present']
    if not exists:
        print('첫 실행입니다. 로그인할 계정을 만들어 주세요.', flush=True)
        result = subprocess.run([sys.executable, '-m', 'auto_trader.create_admin'], cwd=ROOT, check=False)
        if result.returncode:
            raise LaunchError('계정 생성이 완료되지 않았습니다. 다시 실행해 주세요.')


def wait_server(child, port, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise LaunchError('서버가 시작되지 않았습니다. .runtime-logs/server.log를 확인하세요.')
        state = server_status(port)
        if state == 'ready':
            return
        time.sleep(0.5)
    raise LaunchError('서버 준비 시간이 초과됐습니다. .runtime-logs/server.log를 확인하세요.')


def stop_server(child):
    if child.poll() is not None:
        return
    if os.name == 'nt':
        child.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        child.terminate()
    try:
        child.wait(timeout=15)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


def run(open_browser=True):
    from .settings import settings
    from .database import connect, initialize
    port = settings.app_port
    url = f'http://127.0.0.1:{port}/live'
    state = server_status(port)
    if state == 'ready':
        print('이미 실행 중입니다. 기존 화면을 엽니다.', flush=True)
        if open_browser:
            webbrowser.open(url)
        return
    if state in ('other', 'waiting'):
        raise LaunchError('설정된 서버 포트를 이미 사용 중입니다. 기존 실행 창을 확인하세요. 다른 프로그램은 종료하지 않았습니다.')
    with launch_lock():
        # Recheck after acquiring the lock; another launcher may just have finished.
        if server_status(port) != 'absent':
            raise LaunchError('서버가 다른 실행 창에서 준비 중입니다. 잠시 뒤 다시 실행하세요.')
        print('DB 연결을 확인합니다.', flush=True)
        ensure_database(settings, connect)
        prepare_account(connect, initialize)
        print('FOLIO를 시작합니다.', flush=True)
        folder = ROOT / '.runtime-logs'
        with (folder / 'server.log').open('a', encoding='utf-8') as log:
            child = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'auto_trader.main:app',
                '--host', '127.0.0.1', '--port', str(port)], cwd=ROOT, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
            try:
                wait_server(child, port)
                print(f'실행 완료: {url}\n이 창을 유지하세요. 종료하려면 Ctrl+C를 누르세요.\n자동매매는 사이트에서 전략 시작을 눌러야 합니다.', flush=True)
                if open_browser:
                    webbrowser.open(url)
                # Windows' infinite process wait can delay Python's Ctrl+C handler.
                while child.poll() is None:
                    time.sleep(0.5)
                if child.returncode:
                    raise LaunchError('서버가 종료됐습니다. .runtime-logs/server.log를 확인하세요.')
            finally:
                stop_server(child)


def main():
    parser = argparse.ArgumentParser(description='FOLIO local launcher')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    try:
        run(open_browser=not args.no_browser)
    except KeyboardInterrupt:
        print('\nFOLIO를 종료했습니다. PostgreSQL 서비스는 유지합니다.')
    except LaunchError as exc:
        print(f'실행 안내: {exc}', file=sys.stderr)
        return 1
    except Exception:
        print('실행 준비에 실패했습니다. DB 설정과 docs/INSTALL_WINDOWS.md를 확인하세요.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
