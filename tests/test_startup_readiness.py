import auto_trader.api.pages as _api_pages
import auto_trader.runtime as _runtime
import auto_trader.services.readiness as _services_readiness
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from auto_trader.toss import TossApiError


class StartupReadinessTests(unittest.TestCase):
    def setUp(self):
        _services_readiness._readiness_cache = None

    @patch.object(_api_pages, "connect")
    @patch.object(_runtime, "toss_client")
    def test_login_ready_without_broker_keys_or_requests(self, toss_client, connect):
        toss_client.configured = False
        result = _api_pages.startup_readiness()
        self.assertTrue(result["ready"])
        self.assertNotIn("toss_api", result)
        toss_client.test_connection.assert_not_called()

    @patch.object(_api_pages, "connect")
    @patch.object(_runtime, "toss_client")
    def test_login_ready_even_when_broker_is_unavailable(self, toss_client, connect):
        toss_client.configured = True
        toss_client.test_connection.side_effect = TossApiError("Unavailable")
        self.assertTrue(_api_pages.startup_readiness()["ready"])
        toss_client.test_connection.assert_not_called()

    @patch.object(_api_pages, "connect")
    def test_login_waits_for_database_and_recovers(self, connect):
        connect.side_effect = OSError("database unavailable")
        self.assertFalse(_api_pages.startup_readiness()["ready"])
        connect.side_effect = None
        self.assertTrue(_api_pages.startup_readiness()["ready"])

    @patch.object(_services_readiness, "connect")
    @patch.object(_runtime, "toss_client")
    def test_ready_when_database_and_toss_account_connect(self, toss_client, connect):
        connect.return_value.__enter__.return_value.execute.return_value = None
        toss_client.configured = True
        toss_client.test_connection.return_value = SimpleNamespace(
            connected=True, message="토스 API 연결 완료",
        )

        result = _services_readiness._startup_readiness(force=True)

        self.assertTrue(result["ready"])
        self.assertTrue(result["database"]["connected"])
        self.assertTrue(result["toss_api"]["connected"])

    @patch.object(_services_readiness, "connect")
    @patch.object(_runtime, "toss_client")
    def test_waits_when_toss_connection_fails(self, toss_client, connect):
        connect.return_value.__enter__.return_value.execute.return_value = None
        toss_client.configured = True
        toss_client.test_connection.side_effect = TossApiError("토스 API 서버에 연결하지 못했습니다.")

        result = _services_readiness._startup_readiness(force=True)

        self.assertFalse(result["ready"])
        self.assertTrue(result["database"]["connected"])
        self.assertFalse(result["toss_api"]["connected"])
        self.assertIn("연결하지 못했습니다", result["toss_api"]["message"])


if __name__ == "__main__":
    unittest.main()
