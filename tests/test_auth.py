import unittest
from uuid import uuid4
from unittest.mock import patch

from psycopg import sql
from starlette.requests import Request
from fastapi import HTTPException
from auto_trader import auth, database


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.schema = 'test_auth_' + uuid4().hex
        self.original_connect = database.connect
        with self.original_connect() as conn:
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.addCleanup(self.cleanup_schema)
        def isolated():
            conn = self.original_connect()
            conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
            return conn
        self.connect = isolated
        for module in (auth, database):
            patcher = patch.object(module, 'connect', isolated)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.initialize()
        self.password = 'test-only-password-2026'
        auth.create_admin('testadmin', self.password)

    def cleanup_schema(self):
        with self.original_connect() as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def request(self, token, csrf=None):
        headers = [(b'cookie', (auth.SESSION_COOKIE + '=' + token).encode())]
        if csrf:
            headers.append((b'x-csrf-token', csrf.encode()))
        return Request({'type': 'http', 'headers': headers})

    def test_single_admin_and_hash(self):
        with self.connect() as conn:
            row = conn.execute('SELECT password_hash FROM admin_users').fetchone()
        self.assertNotEqual(row['password_hash'], self.password)
        self.assertTrue(auth.verify_password(self.password, row['password_hash']))
        with self.assertRaises(ValueError):
            auth.create_admin('second', self.password)

    def test_lock_and_unlock(self):
        for _ in range(auth.settings.login_max_failures):
            self.assertIsNone(auth.authenticate('testadmin', 'wrong'))
        self.assertIsNone(auth.authenticate('testadmin', self.password))
        with self.connect() as conn:
            conn.execute("UPDATE admin_users SET locked_until=now()-interval '1 second'")
        self.assertIsNotNone(auth.authenticate('testadmin', self.password))

    def test_session_csrf_logout(self):
        token, user = auth.authenticate('testadmin', self.password)
        request = self.request(token)
        self.assertEqual(auth.require_user(request).username, 'testadmin')
        with self.assertRaises(HTTPException) as result:
            auth.require_csrf(request, user)
        self.assertEqual(result.exception.status_code, 403)
        self.assertEqual(auth.require_csrf(self.request(token, user.csrf_token), user), user)
        auth.delete_session(request)
        self.assertIsNone(auth.session_from_request(request))

    def test_expired_and_forged_session(self):
        token, _ = auth.authenticate('testadmin', self.password)
        with self.connect() as conn:
            conn.execute("UPDATE auth_sessions SET expires_at=now()-interval '1 second'")
        self.assertIsNone(auth.session_from_request(self.request(token)))
        self.assertIsNone(auth.session_from_request(self.request('forged')))

    def test_live_pin_hash_verify_and_session_expiry(self):
        auth.set_live_pin('123456')
        with self.connect() as conn:
            stored = conn.execute('SELECT live_pin_hash FROM admin_users').fetchone()['live_pin_hash']
        self.assertNotEqual(stored, '123456')
        token, user = auth.authenticate('testadmin', self.password)
        request = self.request(token, user.csrf_token)
        configured, authorized_until = auth.live_pin_status(request, user)
        self.assertTrue(configured)
        self.assertIsNone(authorized_until)
        with self.assertRaises(PermissionError):
            auth.verify_live_pin(request, user, '000000')
        auth.verify_live_pin(request, user, '123456')
        self.assertIsNotNone(auth.live_pin_status(request, user)[1])
        auth.set_live_pin('654321')
        self.assertIsNone(auth.live_pin_status(request, user)[1])

    def test_live_pin_validation(self):
        for invalid in ('12345', '1234567', 'abcdef'):
            with self.assertRaises(ValueError):
                auth.set_live_pin(invalid)
