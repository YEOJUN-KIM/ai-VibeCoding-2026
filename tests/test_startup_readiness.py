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
