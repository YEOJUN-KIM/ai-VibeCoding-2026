"""Each site user has an independent broker credential and token/cache scope."""
import hashlib
import json
from threading import RLock

from psycopg.errors import UniqueViolation
from psycopg import sql
from .connection_vault import protect, unprotect, read_encrypted, write_encrypted
from .database import connect
from .settings import settings
from .toss import TossClient, TossApiError


def migrate_local_credentials() -> None:
    """Move the earlier encrypted column to local files before removing that column."""
    with connect() as conn:
        exists = conn.execute("SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='user_broker_connections' AND column_name='encrypted_credentials'").fetchone()
        if not exists:
            return
        rows = conn.execute('SELECT user_id,encrypted_credentials FROM user_broker_connections WHERE encrypted_credentials IS NOT NULL').fetchall()
        for row in rows:
            write_encrypted(row['user_id'], bytes(row['encrypted_credentials']))
        constraints = conn.execute("SELECT conname FROM pg_constraint WHERE conrelid='user_broker_connections'::regclass AND pg_get_constraintdef(oid) LIKE '%encrypted_credentials%'").fetchall()
        for constraint in constraints:
            conn.execute(sql.SQL('ALTER TABLE user_broker_connections DROP CONSTRAINT {}').format(sql.Identifier(constraint['conname'])))
        conn.execute('ALTER TABLE user_broker_connections DROP COLUMN encrypted_credentials')


def initialize_ownership() -> int | None:
    migrate_local_credentials()
    with connect() as conn:
        conn.execute('''INSERT INTO application_owner(singleton,user_id)
            SELECT TRUE,id FROM admin_users ORDER BY id LIMIT 1 ON CONFLICT DO NOTHING''')
        row = conn.execute('SELECT user_id FROM application_owner').fetchone()
        if not row:
            return None
        owner = row['user_id']
        conn.execute("UPDATE accounts SET user_id=%s WHERE user_id IS NULL AND name IN ('paper-default','paper-experiment')", (owner,))
        if settings.toss_api_ready:
            conn.execute('''INSERT INTO user_broker_connections(user_id,source,account_ref)
                VALUES (%s,'ENV',%s) ON CONFLICT DO NOTHING''', (owner, settings.toss_account))
        return owner


def connection_info(user_id: int) -> dict:
    with connect() as conn:
        row = conn.execute('SELECT source,account_label,updated_at FROM user_broker_connections WHERE user_id=%s', (user_id,)).fetchone()
    return dict(row) if row else {}


class UserConnections:
    def __init__(self):
        self._clients = {}
        self._lock = RLock()

    def client(self, user_id: int) -> TossClient:
        with self._lock:
            with connect() as conn:
                row = conn.execute('SELECT * FROM user_broker_connections WHERE user_id=%s', (user_id,)).fetchone()
            revision = row['updated_at'] if row else None
            cached = self._clients.get(user_id)
            if cached and cached[0] == revision:
                return cached[1]
            if row and row['source'] == 'ENV':
                client = TossClient(account_ref=row['account_ref'])
            elif row:
                try:
                    encrypted = read_encrypted(user_id)
                    values = json.loads(unprotect(encrypted)) if encrypted else {}
                    client = TossClient(client_id=values['client_id'], client_secret=values['client_secret'], account_ref=row['account_ref'])
                except (ValueError, KeyError, OSError):
                    # A copied DB without its local credentials must still allow sign-in and re-registration.
                    return TossClient(client_id='', client_secret='', account_ref='')
            else:
                # Do not inherit the server owner's credentials for an unconnected user.
                client = TossClient(client_id='', client_secret='', account_ref='')
            self._clients[user_id] = (revision, client)
            return client

    def save(self, user_id: int, client_id: str, client_secret: str) -> TossClient:
        candidate = TossClient(client_id=client_id, client_secret=client_secret, account_ref='')
        account = candidate.selected_account()
        ref = str(account['accountSeq'])
        number = str(account.get('accountNo', ''))
        if not number:
            raise ValueError('증권 계좌 번호를 확인하지 못했습니다.')
        label = '*' * max(0, len(number)-4) + number[-4:] or '증권 계좌'
        identity = hashlib.sha256((candidate.base_url + ':' + number).encode()).hexdigest() if number else None
        encrypted = protect(json.dumps({'client_id': client_id, 'client_secret': client_secret}).encode())
        with connect() as conn:
            previous = conn.execute('SELECT identity_hash,source,account_ref FROM user_broker_connections WHERE user_id=%s', (user_id,)).fetchone()
            environment_rows = conn.execute("SELECT user_id,identity_hash,account_ref FROM user_broker_connections WHERE source='ENV'").fetchall()
        previous_identity = previous['identity_hash'] if previous else None
        for row in environment_rows:
            if row['identity_hash']:
                if row['user_id'] != user_id and row['identity_hash'] == identity:
                    raise ValueError('이 증권 계좌는 이미 다른 사이트 계정에 연결되어 있습니다.')
                continue
            existing = self.client(row['user_id'])
            try:
                existing_number = str(existing.selected_account().get('accountNo', ''))
            except TossApiError:
                if row['user_id'] != user_id:
                    raise ValueError('다른 사용자의 기존 연결 계좌를 확인하지 못했습니다. 기존 연결을 복구한 뒤 다시 시도하세요.') from None
                # Verify replacement credentials against saved references when ENV keys are lost.
                saved_ref = row['account_ref']
                if not saved_ref:
                    with connect() as conn:
                        saved_accounts = conn.execute("SELECT DISTINCT external_account_ref FROM broker_accounts WHERE user_id=%s AND broker='TOSS'", (user_id,)).fetchall()
                    saved_ref = saved_accounts[0]['external_account_ref'] if len(saved_accounts) == 1 else None
                if not saved_ref:
                    raise ValueError('기존 계좌의 식별 기록이 없어 같은 계좌인지 확인할 수 없습니다. 기존 환경 연결을 복구한 뒤 다시 등록하세요.') from None
                if str(saved_ref) != ref:
                    raise ValueError('이미 연결한 증권 계좌와 같은 계좌의 API 키를 입력하세요.') from None
                previous_identity = identity
                continue
            existing_identity = hashlib.sha256((existing.base_url + ':' + existing_number).encode()).hexdigest()
            if row['user_id'] != user_id and existing_identity == identity:
                raise ValueError('이 증권 계좌는 이미 다른 사이트 계정에 연결되어 있습니다.')
            if row['user_id'] == user_id:
                previous_identity = existing_identity
        if previous_identity and previous_identity != identity:
            raise ValueError('이미 연결한 증권 계좌와 같은 계좌의 API 키를 입력하세요.')
        previous_file = read_encrypted(user_id)
        try:
            with connect() as conn:
                conn.execute('''INSERT INTO user_broker_connections(user_id,source,account_ref,account_label,identity_hash)
                    VALUES (%s,'LOCAL',%s,%s,%s) ON CONFLICT(user_id) DO UPDATE SET
                    source='LOCAL',account_ref=EXCLUDED.account_ref,
                    account_label=EXCLUDED.account_label,identity_hash=EXCLUDED.identity_hash,updated_at=now()''',
                    (user_id, ref, label, identity))
                conn.execute('UPDATE auth_sessions SET live_authorized_until=NULL WHERE user_id=%s', (user_id,))
                write_encrypted(user_id, encrypted)
        except UniqueViolation:
            write_encrypted(user_id, previous_file)
            raise ValueError('이 증권 계좌는 이미 다른 사이트 계정에 연결되어 있습니다.') from None
        except Exception:
            write_encrypted(user_id, previous_file)
            raise
        candidate.account_ref = ref
        with self._lock:
            self._clients.pop(user_id, None)
        return candidate
