import unittest
from types import SimpleNamespace
from unittest.mock import patch

from auto_trader import main
from auto_trader.toss import TossApiError


class StartupReadinessTests(unittest.TestCase):
    def setUp(self):
        main._readiness_cache = None

    @patch.object(main, "connect")
    @patch.object(main, "toss_client")
    def test_login_ready_without_broker_keys_or_requests(self, toss_client, connect):
        toss_client.configured = False
        result = main.startup_readiness()
        self.assertTrue(result["ready"])
        self.assertNotIn("toss_api", result)
        toss_client.test_connection.assert_not_called()

    @patch.object(main, "connect")
    @patch.object(main, "toss_client")
    def test_login_ready_even_when_broker_is_unavailable(self, toss_client, connect):
        toss_client.configured = True
        toss_client.test_connection.side_effect = TossApiError("Unavailable")
        self.assertTrue(main.startup_readiness()["ready"])
        toss_client.test_connection.assert_not_called()

    @patch.object(main, "connect")
    def test_login_waits_for_database_and_recovers(self, connect):
        connect.side_effect = OSError("database unavailable")
        self.assertFalse(main.startup_readiness()["ready"])
        connect.side_effect = None
        self.assertTrue(main.startup_readiness()["ready"])

    @patch.object(main, "connect")
    @patch.object(main, "toss_client")
    def test_ready_when_database_and_toss_account_connect(self, toss_client, connect):
        connect.return_value.__enter__.return_value.execute.return_value = None
        toss_client.configured = True
        toss_client.test_connection.return_value = SimpleNamespace(
            connected=True, message="토스 API 연결 완료",
        )

        result = main._startup_readiness(force=True)

        self.assertTrue(result["ready"])
        self.assertTrue(result["database"]["connected"])
        self.assertTrue(result["toss_api"]["connected"])

    @patch.object(main, "connect")
    @patch.object(main, "toss_client")
    def test_waits_when_toss_connection_fails(self, toss_client, connect):
        connect.return_value.__enter__.return_value.execute.return_value = None
        toss_client.configured = True
        toss_client.test_connection.side_effect = TossApiError("토스 API 서버에 연결하지 못했습니다.")

        result = main._startup_readiness(force=True)

        self.assertFalse(result["ready"])
        self.assertTrue(result["database"]["connected"])
        self.assertFalse(result["toss_api"]["connected"])
        self.assertIn("연결하지 못했습니다", result["toss_api"]["message"])


if __name__ == "__main__":
    unittest.main()
