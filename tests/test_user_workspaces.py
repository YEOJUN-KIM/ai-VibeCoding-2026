import asyncio
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch, Mock
from psycopg import sql
from auto_trader import auth, database, paper, risk, main, user_connections, live_orders, connection_vault
from auto_trader.connection_vault import protect, unprotect
from auto_trader.toss import TossApiError
from auto_trader.user_workspace import current_workspace, UserWorkspace


class UserOwnershipTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        p = patch.object(connection_vault, 'SECRET_DIRECTORY', Path(directory.name))
        p.start()
        self.addCleanup(p.stop)
        self.schema = 'test_ownership_' + uuid4().hex
        self.original = database.connect
        with self.original() as conn:
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.addCleanup(self.cleanup_schema)
        def isolated():
            conn = self.original()
            conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
            return conn
        self.connect = isolated
        for module in (auth, database, paper, risk, main, user_connections, live_orders):
            p = patch.object(module, 'connect', isolated)
            p.start()
            self.addCleanup(p.stop)
        database.initialize()
        with self.connect() as conn:
            self.users = [conn.execute('INSERT INTO admin_users(username,password_hash) VALUES (%s,%s) RETURNING id', (name, auth.hash_password('password-2026'))).fetchone()['id'] for name in ('owner', 'second')]

    def cleanup_schema(self):
        with self.original() as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def test_existing_accounts_belong_only_to_first_user(self):
        original = paper.PaperBroker(main.market)
        original.initialize()
        owner = user_connections.initialize_ownership()
        self.assertEqual(owner, self.users[0])
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT user_id FROM accounts WHERE name='paper-default'").fetchone()['user_id'], owner)
        with self.assertRaises(ValueError):
            paper.PaperBroker(main.market, user_id=self.users[1]).initialize()
        self.assertEqual(user_connections.initialize_ownership(), owner)

    def test_unconnected_user_never_inherits_environment_credentials(self):
        user_connections.initialize_ownership()
        manager = user_connections.UserConnections()
        self.assertFalse(manager.client(self.users[1]).configured)
        self.assertEqual(manager.client(self.users[1]).account_ref, '')

    def test_two_users_restore_separate_assets_and_selected_accounts(self):
        owner = user_connections.initialize_ownership()
        with patch.object(main.legacy_workspace, 'user_id', owner):
            first = main._create_user_workspace(self.users[0])
            second = main._create_user_workspace(self.users[1])
            self.assertNotEqual(first.broker.account_id, second.broker.account_id)
            first.broker.load_snapshot(cash=Decimal('123456'), positions=[], metadata={'snapshot_at':'2026-10-03T09:00:00+09:00'})
            self.assertNotEqual(first.broker.account().cash, second.broker.account().cash)
            self.assertNotEqual(first.broker.risk_manager, second.broker.risk_manager)
            token = current_workspace.set(second)
            try:
                asyncio.run(main.select_paper_account('EXPERIMENT', None))
            finally:
                current_workspace.reset(token)
            restored = main._create_user_workspace(self.users[1])
            self.assertEqual(restored.mode, 'EXPERIMENT')
            self.assertEqual(restored.broker.account().cash, Decimal('10000000'))
            self.assertEqual(first.mode, 'LIVE_COPY')
            self.assertEqual(first.broker.account().cash, Decimal('123456'))

    def test_key_storage_is_encrypted_and_duplicate_account_rejected(self):
        manager = user_connections.UserConnections()
        fake = SimpleNamespace(base_url='https://example.test', selected_account=lambda:{'accountSeq':3,'accountNo':'12345678'})
        with patch.object(user_connections, 'TossClient', return_value=fake):
            manager.save(self.users[0], 'fake-client-key', 'fake-client-secret')
            with self.assertRaises(ValueError):
                manager.save(self.users[1], 'other-key', 'other-secret')
        with self.connect() as conn:
            row = dict(conn.execute('SELECT * FROM user_broker_connections').fetchone())
        self.assertNotIn('encrypted_credentials', row)
        self.assertNotIn('fake-client-secret', str(row))
        blob = connection_vault.read_encrypted(self.users[0])
        self.assertNotIn(b'fake-client-secret', blob)
        self.assertIn(b'fake-client-secret', unprotect(blob))
        self.assertIsNone(connection_vault.read_encrypted(self.users[1]))

    def test_old_database_keys_migrate_to_files_and_column_is_removed(self):
        blob = protect(b'{"client_id":"fake-key","client_secret":"fake-secret"}')
        with self.connect() as conn:
            conn.execute('ALTER TABLE user_broker_connections ADD COLUMN encrypted_credentials BYTEA')
            conn.execute("ALTER TABLE user_broker_connections ADD CONSTRAINT old_storage_check CHECK ((source='ENV' AND encrypted_credentials IS NULL) OR (source='LOCAL' AND encrypted_credentials IS NOT NULL))")
            conn.execute("INSERT INTO user_broker_connections(user_id,source,encrypted_credentials) VALUES (%s,'LOCAL',%s)", (self.users[0], blob))
        user_connections.migrate_local_credentials()
        self.assertEqual(connection_vault.read_encrypted(self.users[0]), blob)
        with self.connect() as conn:
            row = conn.execute('SELECT * FROM user_broker_connections').fetchone()
            self.assertNotIn('encrypted_credentials', row)
        user_connections.migrate_local_credentials()
        self.assertEqual(connection_vault.read_encrypted(self.users[0]), blob)

    def test_missing_file_allows_re_registration_without_environment_fallback(self):
        with self.connect() as conn:
            conn.execute("INSERT INTO user_broker_connections(user_id,source) VALUES (%s,'LOCAL')", (self.users[1],))
        self.assertFalse(user_connections.UserConnections().client(self.users[1]).configured)

    def recover_environment_keys(self, saved_ref, candidate_ref='3', with_order_record=False):
        with self.connect() as conn:
            conn.execute("INSERT INTO user_broker_connections(user_id,source,account_ref) VALUES (%s,'ENV',%s)", (self.users[0], saved_ref))
            if with_order_record:
                conn.execute("INSERT INTO broker_accounts(user_id,broker,external_account_ref,account_label) VALUES (%s,'TOSS','3','****5678')", (self.users[0],))
        manager = user_connections.UserConnections()
        missing = Mock()
        missing.selected_account.side_effect = TossApiError('Old credentials unavailable')
        candidate = SimpleNamespace(base_url='https://example.test', selected_account=lambda:{'accountSeq':candidate_ref,'accountNo':'12345678'})
        with patch.object(manager, 'client', return_value=missing), patch.object(user_connections, 'TossClient', return_value=candidate), patch.object(user_connections, 'protect', return_value=b'encrypted-test-fixture'):
            manager.save(self.users[0], 'replacement-id', 'replacement-secret')
        with self.connect() as conn:
            row = conn.execute('SELECT source,account_ref,identity_hash FROM user_broker_connections WHERE user_id=%s', (self.users[0],)).fetchone()
        self.assertEqual(row['source'], 'LOCAL')
        self.assertEqual(row['account_ref'], candidate_ref)
        self.assertTrue(row['identity_hash'])

    def test_removed_environment_keys_recover_with_saved_reference(self):
        self.recover_environment_keys('3')

    def test_removed_environment_keys_recover_with_order_account_record(self):
        self.recover_environment_keys('', with_order_record=True)

    def test_removed_environment_keys_cannot_switch_accounts(self):
        with self.assertRaisesRegex(ValueError, '같은 계좌'):
            self.recover_environment_keys('4')
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT source FROM user_broker_connections WHERE user_id=%s', (self.users[0],)).fetchone()['source'], 'ENV')
        self.assertIsNone(connection_vault.read_encrypted(self.users[0]))

    def test_removed_environment_keys_require_account_identity_evidence(self):
        with self.assertRaisesRegex(ValueError, '식별 기록'):
            self.recover_environment_keys('')

    def test_failed_file_write_rolls_back_connection_metadata(self):
        manager = user_connections.UserConnections()
        fake = SimpleNamespace(base_url='https://example.test', selected_account=lambda:{'accountSeq':3,'accountNo':'12345678'})
        def write(user_id, payload):
            if payload is not None:
                raise OSError('test disk error')
        with patch.object(user_connections, 'TossClient', return_value=fake), patch.object(user_connections, 'write_encrypted', side_effect=write):
            with self.assertRaises(OSError):
                manager.save(self.users[0], 'fake-key', 'fake-secret')
        with self.connect() as conn:
            self.assertIsNone(conn.execute('SELECT * FROM user_broker_connections WHERE user_id=%s', (self.users[0],)).fetchone())
        self.assertIsNone(connection_vault.read_encrypted(self.users[0]))

    def test_order_reconciliation_does_not_mix_equal_broker_references(self):
        from tests.test_live_orders import LiveOrderStoreTests
        for user in self.users:
            live_orders.prepare_real_order(user, {'accountSeq':3}, '****1234', LiveOrderStoreTests.real_payload(), LiveOrderStoreTests.real_preview())
        self.assertEqual(len(live_orders.orders_for_reconciliation('3', user_id=self.users[0])), 1)
        self.assertEqual(len(live_orders.orders_for_reconciliation('3', user_id=self.users[1])), 1)
        self.assertEqual({o.user_id for o in live_orders.orders_for_reconciliation('3', user_id=self.users[0])}, {self.users[0]})


class WorkspaceContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_middleware_routes_concurrent_requests_and_resets_scope(self):
        from starlette.requests import Request
        from starlette.responses import JSONResponse
        states = {i:UserWorkspace(i, SimpleNamespace(cash=i), None, SimpleNamespace(account_ref=str(i)), None) for i in (11,22)}
        def user(request):
            return SimpleNamespace(id=int(request.cookies[auth.SESSION_COOKIE]))
        async def next_request(request):
            await asyncio.sleep(.01)
            return JSONResponse({'cash':await asyncio.to_thread(lambda:main.broker.cash)})
        async def request(i):
            req = Request({'type':'http','path':'/account','headers':[(b'cookie',f'{auth.SESSION_COOKIE}={i}'.encode())]})
            response = await main.user_account_scope(req, next_request)
            self.assertIsNone(current_workspace.get())
            return response.body
        with patch.object(main, 'session_from_request', side_effect=user), patch.object(main.workspaces, 'get', side_effect=states.__getitem__):
            self.assertEqual(await asyncio.gather(request(11), request(22)), [b'{"cash":11}', b'{"cash":22}'])

    async def test_concurrent_tasks_and_thread_calls_keep_their_own_clients(self):
        async def read(user_id):
            state = UserWorkspace(user_id, SimpleNamespace(cash=user_id), None, SimpleNamespace(account_ref=str(user_id)), None)
            token = current_workspace.set(state)
            try:
                await asyncio.sleep(.01)
                return await asyncio.to_thread(lambda:(main.broker.cash, main.toss_client.account_ref))
            finally:
                current_workspace.reset(token)
        self.assertEqual(await asyncio.gather(read(11), read(22)), [(11,'11'),(22,'22')])


class ConnectionVaultTests(unittest.TestCase):
    def test_roundtrip_and_tamper_failure(self):
        encrypted = protect(b'example-secret')
        self.assertEqual(unprotect(encrypted), b'example-secret')
        with self.assertRaises(ValueError):
            unprotect(encrypted[:-10] + b'broken')
