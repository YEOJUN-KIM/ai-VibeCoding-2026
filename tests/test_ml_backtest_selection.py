"""Event-date SQL selection remains separate from availability-time replay."""

import contextlib
import io
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from psycopg import sql

from auto_trader import database
from auto_trader.ml import backtest, data_pipeline
from auto_trader.ml.backtest import Config, replay, select_candles, KST
from auto_trader.ml.data_pipeline import RawCandle
from auto_trader.strategy import MovingAverageEngine


class BacktestSelectionTests(unittest.TestCase):
    def setUp(self):
        schema = "test_backtest_selection_" + uuid4().hex
        original = database.connect
        with original() as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        def cleanup():
            with original() as conn:
                conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        self.addCleanup(cleanup)
        def isolated():
            conn = original()
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            return conn
        self.connect = isolated
        for module in (database, data_pipeline, backtest):
            p = patch.object(module, "connect", isolated)
            p.start()
            self.addCleanup(p.stop)
        database.initialize()
        self.start = datetime(2026, 10, 28, 9, tzinfo=KST)

    def row(self, event, available):
        return RawCandle("TOSS", "005930", "1m", event, available, available,
                         Decimal(100), Decimal(101), Decimal(99), Decimal(100), Decimal(1))

    def selected(self):
        with self.connect() as conn:
            return select_candles(conn, symbols=["005930"], start="2026-10-28", end="2026-10-28")

    def test_prior_evening_received_today_is_excluded_and_late_today_is_kept(self):
        previous = self.start.replace(day=27, hour=19, minute=43)
        data_pipeline.save_raw_candles([
            self.row(previous, self.start + timedelta(minutes=1)),
            self.row(self.start + timedelta(minutes=5), self.start + timedelta(minutes=8)),
            self.row(self.start + timedelta(minutes=6), self.start + timedelta(days=1)),
        ])
        rows = self.selected()
        self.assertEqual([row["event_at"] for row in rows], [self.start + timedelta(minutes=5), self.start + timedelta(minutes=6)])
        self.assertEqual(rows[0]["available_at"], self.start + timedelta(minutes=8))
        self.assertEqual(rows[1]["available_at"], self.start + timedelta(days=1))

    def test_kst_date_boundaries_do_not_use_utc_calendar_dates(self):
        midnight = self.start.replace(hour=0)
        data_pipeline.save_raw_candles([
            self.row(midnight.astimezone(timezone.utc), midnight + timedelta(minutes=1)),
            self.row(midnight + timedelta(days=1) - timedelta(minutes=1), midnight + timedelta(days=1, minutes=1)),
            self.row(midnight - timedelta(minutes=1), midnight + timedelta(minutes=1)),
            self.row(midnight + timedelta(days=1), midnight + timedelta(days=1, minutes=1)),
        ])
        selected = self.selected()
        self.assertEqual(len(selected), 2)
        self.assertEqual({row["event_at"].astimezone(KST).date().isoformat() for row in selected}, {"2026-10-28"})

    def test_next_day_backfills_are_selected_but_do_not_trade_old_prices(self):
        received = self.start + timedelta(days=1)
        data_pipeline.save_raw_candles([self.row(self.start + timedelta(minutes=i), received) for i in range(9)])
        rows = [RawCandle(**row) for row in self.selected()]
        result = replay(rows, Config(short_period=2, long_period=3, execution="close"))
        self.assertEqual(result["summary"]["filled_orders"], 0)
        self.assertEqual(result["trades"], [])
        self.assertTrue(all(datetime.fromisoformat(point["at"]) >= received for point in result["equity_curve"]))

    def test_cli_observed_days_use_selected_event_dates(self):
        previous = self.start.replace(day=27, hour=19, minute=43)
        data_pipeline.save_raw_candles([self.row(previous, self.start + timedelta(minutes=1)),
                                       self.row(self.start, self.start + timedelta(minutes=1))])
        with tempfile.TemporaryDirectory() as output, patch("sys.argv", [
            "backtest", "--start", "2026-10-28", "--end", "2026-10-28", "--symbols", "005930", "--output", output
        ]), contextlib.redirect_stdout(io.StringIO()):
            backtest.main()
            report = json.loads((Path(output) / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["observed_days"], ["2026-10-28"])
        self.assertEqual(report["candles"], 1)
        self.assertEqual((report["selection_time"], report["replay_time"]), ("event_at", "available_at"))

    def test_replay_does_not_see_delayed_event_before_availability(self):
        prices = (10, 9, 8, 10, 12, 14, 13, 12, 10)
        rows = [replace(self.row(self.start + timedelta(minutes=i), self.start + timedelta(minutes=i + 1, seconds=2)),
                        open_price=Decimal(price), high_price=Decimal(price), low_price=Decimal(price), close_price=Decimal(price))
                for i, price in enumerate(prices)]
        event = self.start + timedelta(minutes=5)
        received = self.start + timedelta(minutes=8)
        rows[5] = replace(rows[5], available_at=received, collected_at=received)
        seen = []
        original = MovingAverageEngine.step
        def inspect(engine, *args, **kwargs):
            seen.append((engine._clock(), {bar.timestamp for bar in kwargs["candles"]["005930"]}))
            return original(engine, *args, **kwargs)
        with patch.object(MovingAverageEngine, "step", inspect):
            replay(rows, Config(short_period=2, long_period=10, execution="close"))
        self.assertTrue(any(at == received and event in history for at, history in seen))
        self.assertTrue(all(event not in history for at, history in seen if at < received))
