import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock, patch
from uuid import uuid4

from psycopg import sql

from auto_trader import database
from auto_trader.ml import decision_pipeline
from auto_trader.ml.decision_pipeline import StrategyDecisionRecorder
from auto_trader.models import LiveStockCandle
from auto_trader.paper import PaperBroker
from auto_trader.simulator import MarketSimulator
from auto_trader.strategy import MovingAverageEngine


def decision(**changes):
    now = datetime(2026, 10, 2, 3, 0, tzinfo=timezone.utc)
    values = {
        "run_id": "run-1",
        "account_id": 1,
        "account_name": "paper-default",
        "strategy_id": 2,
        "strategy_name": "test strategy",
        "data_source": "TOSS",
        "symbol": "005930",
        "candle_event_at": now - timedelta(minutes=1),
        "decision_at": now,
        "available_at": now,
        "action": "WAIT",
        "reason": "단기선 열세",
        "price": Decimal("70000"),
        "short_average": Decimal("69900"),
        "long_average": Decimal("70100"),
        "trend": "BELOW",
        "previous_trend": "ABOVE",
        "history_count": 20,
        "entry_armed": False,
        "confirmation_count": 0,
        "strategy_quantity": 0,
        "cash": Decimal("1000000"),
        "total_asset": Decimal("1000000"),
        "metadata": {"rising": False},
    }
    values.update(changes)
    return values


class DecisionRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.schema = "test_ml_decisions_" + uuid4().hex
        self.original_connect = database.connect
        with self.original_connect() as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self._cleanup_schema)

        def isolated():
            conn = self.original_connect()
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
            return conn

        for module in (database, decision_pipeline):
            patcher = patch.object(module, "connect", isolated)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.initialize()

    def _cleanup_schema(self):
        with self.original_connect() as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def test_decision_is_persisted_with_time_and_features(self):
        self.assertEqual(decision_pipeline.save_strategy_decisions([decision()]), 1)
        with decision_pipeline.connect() as conn:
            row = conn.execute("SELECT * FROM ml_strategy_decisions").fetchone()

        self.assertEqual(row["action"], "WAIT")
        self.assertEqual(row["symbol"], "005930")
        self.assertEqual(row["metadata"], {"rising": False})
        self.assertEqual(row["candle_event_at"].isoformat(), "2026-10-02T11:59:00+09:00")

    def test_retry_with_same_decision_key_is_idempotent(self):
        row = decision(decision_key="decision-1")
        self.assertEqual(decision_pipeline.save_strategy_decisions([row]), 1)
        self.assertEqual(decision_pipeline.save_strategy_decisions([row]), 0)

    def test_invalid_action_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "action"):
            decision_pipeline.save_strategy_decisions([decision(action="UNKNOWN")])


class DecisionRecorderTests(unittest.IsolatedAsyncioTestCase):
    async def test_flush_saves_queued_rows(self):
        recorder = StrategyDecisionRecorder()
        recorder.record(decision())
        with patch(
            "auto_trader.ml.decision_pipeline.save_strategy_decisions", return_value=1
        ) as save:
            self.assertEqual(await recorder.flush(), 1)

        save.assert_called_once()
        self.assertEqual(recorder.status()["queued"], 0)
        self.assertEqual(recorder.status()["saved"], 1)

    async def test_failure_keeps_batch_for_retry(self):
        recorder = StrategyDecisionRecorder()
        recorder.record(decision())
        with patch(
            "auto_trader.ml.decision_pipeline.save_strategy_decisions",
            side_effect=RuntimeError("database down"),
        ):
            self.assertEqual(await recorder.flush(), 0)

        self.assertEqual(recorder.status()["queued"], 1)
        self.assertEqual(recorder.status()["failures"], 1)


class StrategyDecisionHookTests(unittest.TestCase):
    def test_new_candle_records_final_wait_decision_once(self):
        now = datetime(2026, 10, 2, 10, 0, tzinfo=timezone(timedelta(hours=9)))
        market = MarketSimulator(symbols=("005930",))
        broker = PaperBroker(market)
        captured = []
        engine = MovingAverageEngine(
            market,
            broker,
            short_period=2,
            long_period=3,
            clock=lambda: now,
            decision_recorder=captured.append,
        )
        engine.configure(
            interval_seconds=10,
            short_period=2,
            long_period=3,
            order_quantity=1,
            target_symbol="005930",
        )
        bars = [
            LiveStockCandle(
                timestamp=now - timedelta(minutes=3 - index),
                open_price=Decimal(price),
                high_price=Decimal(price),
                low_price=Decimal(price),
                close_price=Decimal(price),
                volume=Decimal(1),
            )
            for index, price in enumerate(("70020", "70010", "70000"))
        ]

        engine.step(candles={"005930": bars})
        engine.step(candles={"005930": bars})

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["action"], "WAIT")
        self.assertEqual(captured[0]["trend"], "BELOW")
        self.assertEqual(captured[0]["candle_event_at"], bars[-1].timestamp)


if __name__ == "__main__":
    unittest.main()
