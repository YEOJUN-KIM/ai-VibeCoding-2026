import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from auto_trader.asset_history import record_and_read


class AssetHistoryTests(unittest.TestCase):
    def test_minute_bucket_account_scope_and_real_values(self):
        now = datetime(2026, 10, 3, 2, 12, 45, tzinfo=timezone.utc)
        portfolio = SimpleNamespace(account_label="account-A", market_value=Decimal("12.50"))
        cash = SimpleNamespace(account_label="account-A", krw_cash_buying_power=Decimal("7.25"))
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [{"observed_at": now, "total_assets": Decimal("19.75")}]
        with patch("auto_trader.asset_history.connect") as connect:
            connect.return_value.__enter__.return_value = conn
            points = record_and_read(42, portfolio, cash, now=now)
        insert, read = conn.execute.call_args_list
        self.assertEqual(insert.args[1], (42, "account-A", now.replace(second=0), now, Decimal("12.50"), Decimal("7.25"), Decimal("19.75")))
        self.assertIn("ON CONFLICT", insert.args[0])
        self.assertEqual(read.args[1][:2], (42, "account-A"))
        self.assertIn("Asia/Seoul", read.args[0])
        self.assertEqual(points[0]["total_assets"], Decimal("19.75"))

    def test_mixed_account_rejected_before_database(self):
        with patch("auto_trader.asset_history.connect") as connect:
            with self.assertRaises(ValueError):
                record_and_read(1, SimpleNamespace(account_label="A", market_value=Decimal(1)), SimpleNamespace(account_label="B", krw_cash_buying_power=Decimal(2)))
            connect.assert_not_called()

    def test_nonfinite_rejected_before_database(self):
        with patch("auto_trader.asset_history.connect") as connect:
            with self.assertRaises(ValueError):
                record_and_read(1, SimpleNamespace(account_label="A", market_value=Decimal("NaN")), SimpleNamespace(account_label="A", krw_cash_buying_power=Decimal(2)))
            connect.assert_not_called()
