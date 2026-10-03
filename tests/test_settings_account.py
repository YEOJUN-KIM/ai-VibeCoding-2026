import asyncio
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch, AsyncMock

from fastapi import HTTPException
from starlette.requests import Request
from auto_trader import main
from auto_trader.auth import AuthenticatedUser
from auto_trader.toss import TossApiError


class AccountSettingsRoutesTests(unittest.TestCase):
    def setUp(self):
        self.user = AuthenticatedUser(1, 'demo', 'csrf', datetime.now(timezone.utc))

    def request(self, payload):
        async def receive():
            return {'type': 'http.request', 'body': json.dumps(payload).encode(), 'more_body': False}
        return Request({'type': 'http', 'headers': []}, receive)

    def test_password_change_clears_cookie(self):
        with patch.object(main, 'change_credential') as change:
            response = asyncio.run(main.update_account_credential('password', self.request({'current_password': 'old', 'new_value': 'new-test-password'}), self.user))
        change.assert_called_once_with(self.user, 'old', 'new-test-password', 'password')
        self.assertIn('Max-Age=0', response.headers['set-cookie'])

    def test_pin_change_keeps_cookie(self):
        with patch.object(main, 'change_credential'):
            response = asyncio.run(main.update_account_credential('pin', self.request({'current_password': 'old', 'new_value': '123456'}), self.user))
        self.assertNotIn('set-cookie', response.headers)

    def test_bad_body_does_not_echo_password_or_change_credentials(self):
        with patch.object(main, 'change_credential') as change:
            with self.assertRaises(HTTPException) as result:
                asyncio.run(main.update_account_credential('pin', self.request({'current_password': 'secret', 'new_value': 123456}), self.user))
        self.assertEqual(result.exception.status_code, 400)
        self.assertNotIn('secret', result.exception.detail)
        change.assert_not_called()

    def test_connection_masks_numbers_and_omits_account_identifiers(self):
        client = Mock(configured=True)
        account = {'accountNo': '1234567890', 'accountSeq': 'private-seq'}
        client.accounts.return_value = [account]
        client.selected_account.return_value = account
        with patch.object(main, 'toss_client', client):
            result = main.settings_connection(self.user)
        self.assertEqual(result['accounts'], [{'label': '******7890', 'selected': True}])
        self.assertNotIn('private-seq', json.dumps(result))

    def test_connection_error_omits_raw_broker_response(self):
        client = Mock(configured=True)
        client.accounts.side_effect = TossApiError('private broker details')
        with patch.object(main, 'toss_client', client):
            result = main.settings_connection(self.user)
        self.assertFalse(result['connected'])
        self.assertNotIn('private broker details', json.dumps(result))

    def test_connection_change_rejects_invalid_keys_before_saving(self):
        with patch.object(main.connections, 'save') as save:
            with self.assertRaises(HTTPException) as error:
                asyncio.run(main.save_settings_connection({'client_id':12,'client_secret':'private'}, self.user))
        self.assertEqual(error.exception.status_code, 422)
        save.assert_not_called()

    def test_connection_change_requires_stopped_strategies(self):
        state = main.UserWorkspace(1, None, Mock(running=True), None, None)
        with patch.object(main, 'paper_state', return_value=state), patch.object(main.connections, 'save') as save:
            with self.assertRaises(HTTPException) as error:
                asyncio.run(main.save_settings_connection({'client_id':'fake','client_secret':'fake'}, self.user))
        self.assertEqual(error.exception.status_code, 409)
        save.assert_not_called()

    def test_connection_change_replaces_only_current_user_runtime(self):
        old_stream = Mock(close=AsyncMock())
        worker = Mock(running=False)
        state = main.UserWorkspace(1, None, worker, Mock(), old_stream)
        candidate = Mock()
        db = Mock()
        db.__enter__ = Mock(return_value=db)
        db.__exit__ = Mock(return_value=False)
        db.execute.return_value.fetchone.return_value = None
        with patch.object(main, 'paper_state', return_value=state), patch.object(main, 'connect', return_value=db), patch.object(main.connections, 'save', return_value=candidate), patch.object(main, 'QuoteStream'):
            result = asyncio.run(main.save_settings_connection({'client_id':'fake','client_secret':'fake'}, self.user))
        self.assertIs(state.client, candidate)
        old_stream.close.assert_awaited_once()
        self.assertIn('주문 인증', result['message'])
