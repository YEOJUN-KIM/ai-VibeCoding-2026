"""Regression tests for the INTEGRATION-VERIFY P2 findings (isolated test DB schema, synthetic market, no network)."""
import asyncio
import os
import unittest
from decimal import Decimal

from auto_trader import paper
from auto_trader.paper_mvp import _honest_message
from auto_trader.settings import settings as app_settings
from tests.test_paper_mvp import MvpBase


class TestDatabaseIsolation(unittest.TestCase):
    def test_test_database_is_loopback_non_default_port(self):
        self.assertNotEqual(app_settings.postgres_port, 5432)
        self.assertIn(app_settings.postgres_host, ("127.0.0.1", "localhost", "::1"))
        self.assertTrue(app_settings.postgres_db.startswith("folio_mvp_test"))
        self.assertEqual(str(app_settings.postgres_port), os.environ.get("POSTGRES_PORT", str(app_settings.postgres_port)))


class SyntheticBadgeStaysVisible(unittest.TestCase):
    def test_no_css_rule_hides_the_source_badge_at_any_width(self):
        from pathlib import Path
        html = (Path(__file__).parent.parent / "auto_trader" / "static" / "paper-mvp.html").read_text(encoding="utf-8")
        self.assertIn('id="source-badge"', html)
        self.assertNotIn(".topbar .right span:first-child{display:none}", html)   # it hid the only child: the badge, below 1200px


class SyntheticCopy(MvpBase):
    def test_synthetic_run_message_never_claims_real_prices(self):
        self.mvp.apply(self.balanced())
        self.engine.running = True
        self.poll(5)
        st = self.mvp.status()
        self.assertEqual(st["data_source"], "SYNTHETIC")
        self.assertNotIn("실제", st["run"]["message"])
        self.assertIn("합성", st["run"]["message"])

    def test_relabel_only_for_synthetic(self):
        live = "실제 현재가 · 완료된 1분봉 · 모의 체결"
        self.assertEqual(_honest_message(live, False), live)               # real-price flow keeps its (true) wording
        self.assertEqual(_honest_message(live, True), "합성(가상) 현재가 · 완료된 1분봉 · 모의 체결")
        self.assertNotIn("실제", _honest_message("운영 시간 외 대기 · 실제 시세 기반 모의매매", True))


class ResetKeepsSettingsConsistent(MvpBase):
    """Reset clears results but keeps the saved MVP settings; persisted and in-memory views agree, also after a restart."""

    def test_reset_then_status_and_restart_agree(self):
        self.mvp.apply(self.balanced(budget=3000000))
        self.engine.running = True
        self.poll(40)
        self.engine.running = False
        self.engine._release_run()
        self.broker.reset_practice()
        self.assertIsNone(self.broker.risk_manager.auto_limits)             # run limits do not outlive the run
        st = self.mvp.status()
        self.assertEqual(st["settings"]["budget"], 3000000)
        self.assertEqual(st["trades"], [])
        self.assertEqual(Decimal(st["account"]["cash"]), self.CASH)
        restarted = paper.PaperBroker(self.market, self.CASH, account_name="mvp-test")
        restarted.initialize()
        self.assertEqual(restarted.mvp_state().get("settings"), self.broker.mvp_state().get("settings"))
        self.assertEqual(restarted.mvp_state().get("decisions", []), [])

    def test_two_sessions_see_the_same_state_after_reset(self):
        self.mvp.apply(self.balanced(budget=3000000))
        other = paper.PaperBroker(self.market, self.CASH, account_name="mvp-test")      # a second session on the same account
        other.initialize()
        self.broker.reset_practice()
        other2 = paper.PaperBroker(self.market, self.CASH, account_name="mvp-test")
        other2.initialize()
        self.assertEqual(other2.mvp_state().get("settings"), self.broker.mvp_state().get("settings"))


if __name__ == "__main__":
    unittest.main()
