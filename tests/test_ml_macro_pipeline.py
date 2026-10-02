import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from psycopg import sql

from auto_trader import database
from auto_trader.ml import macro_pipeline
from auto_trader.ml.macro_pipeline import MacroObservation
from auto_trader.ml.macro_worker import next_macro_collection_at


class CsvResponse:
    def __init__(self, text):
        self.body = text.encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.body


class MacroPipelineRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.schema = "test_ml_macro_" + uuid4().hex
        self.original_connect = database.connect
        with self.original_connect() as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self._cleanup_schema)

        def isolated():
            conn = self.original_connect()
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
            return conn

        for module in (database, macro_pipeline):
            patcher = patch.object(module, "connect", isolated)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.initialize()

    def _cleanup_schema(self):
        with self.original_connect() as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def observation(self, value="7000", delay_minutes=5):
        event_at = datetime(2026, 10, 1, 20, 0, tzinfo=timezone.utc)
        available_at = event_at + timedelta(minutes=delay_minutes)
        return MacroObservation(
            source="FRED:SP500",
            indicator="US_SP500",
            event_at=event_at,
            available_at=available_at,
            collected_at=available_at,
            value=Decimal(value),
            unit="index",
            frequency="DAILY",
            raw_payload={"observation_date": "2026-10-01", "SP500": value},
        )

    def test_same_value_is_idempotent_but_revision_is_preserved(self):
        self.assertEqual(macro_pipeline.save_macro_observations([self.observation()]), (1, 0))
        self.assertEqual(macro_pipeline.save_macro_observations([self.observation()]), (0, 1))
        self.assertEqual(macro_pipeline.save_macro_observations([self.observation("7001")]), (1, 0))

        with macro_pipeline.connect() as conn:
            count = conn.execute("SELECT count(*) AS count FROM ml_macro_observations").fetchone()
        self.assertEqual(count["count"], 2)

    def test_as_of_excludes_observation_received_later(self):
        row = self.observation(delay_minutes=60)
        macro_pipeline.save_macro_observations([row])
        self.assertEqual(
            macro_pipeline.available_macro_observations(
                "US_SP500", as_of=row.event_at + timedelta(minutes=30)
            ),
            [],
        )
        self.assertEqual(
            len(macro_pipeline.available_macro_observations("US_SP500", as_of=row.available_at)),
            1,
        )


class FredClientTests(unittest.TestCase):
    def test_csv_parser_ignores_missing_values(self):
        csv_text = "observation_date,SP500\n2026-09-30,7651.54\n2026-10-01,.\n"
        with patch("auto_trader.ml.macro_pipeline.urlopen", return_value=CsvResponse(csv_text)):
            rows = macro_pipeline.fetch_fred_series(
                "SP500", start_date=date(2026, 9, 30), end_date=date(2026, 10, 1)
            )
        self.assertEqual(rows[0][1], Decimal("7651.54"))
        self.assertEqual(len(rows), 1)

    def test_batch_parser_reads_multiple_series_from_one_csv(self):
        csv_text = (
            "observation_date,SP500,DGS10\n"
            "2026-09-30,7651.54,5.29\n"
        )
        with patch("auto_trader.ml.macro_pipeline.urlopen", return_value=CsvResponse(csv_text)) as open_url:
            rows = macro_pipeline.fetch_fred_series_batch(
                ("SP500", "DGS10"),
                start_date=date(2026, 9, 30),
                end_date=date(2026, 10, 1),
            )
        self.assertEqual(rows["SP500"][0][1], Decimal("7651.54"))
        self.assertEqual(rows["DGS10"][0][1], Decimal("5.29"))
        open_url.assert_called_once()

    def test_batch_download_retries_transient_network_failure(self):
        csv_text = "observation_date,SP500\n2026-09-30,7651.54\n"
        with patch(
            "auto_trader.ml.macro_pipeline.urlopen",
            side_effect=[ConnectionResetError("reset"), CsvResponse(csv_text)],
        ) as open_url, patch("auto_trader.ml.macro_pipeline.sleep"):
            rows = macro_pipeline.fetch_fred_series_batch(
                ("SP500",),
                start_date=date(2026, 9, 30),
                end_date=date(2026, 10, 1),
            )
        self.assertEqual(rows["SP500"][0][1], Decimal("7651.54"))
        self.assertEqual(open_url.call_count, 2)


class MacroScheduleTests(unittest.TestCase):
    def test_schedule_uses_two_daily_runs_and_skips_weekend(self):
        morning = datetime.fromisoformat("2026-10-02T07:00:00+09:00")
        noon = datetime.fromisoformat("2026-10-02T12:00:00+09:00")
        friday_evening = datetime.fromisoformat("2026-10-02T18:00:00+09:00")
        self.assertEqual(next_macro_collection_at(morning).hour, 8)
        self.assertEqual(next_macro_collection_at(noon).hour, 13)
        self.assertEqual(
            next_macro_collection_at(friday_evening).isoformat(),
            "2026-10-05T08:10:00+09:00",
        )


if __name__ == "__main__":
    unittest.main()
