"""PAPER-SIMPLIFY-01 static contract (no DB, no server): the PAPER settings tab exposes the operating amount as the only
limit, the legacy /paper page no longer shows saved limits, and the removed limit fields are not sent to the server."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "auto_trader" / "static"
REMOVED_IDS = ("per-stock", "max-holdings", "daily-loss", "daily-buys", "limit-unit", "custom-profile", "edit-exit", "exit-summary")


class SettingsTabContract(unittest.TestCase):
    def setUp(self):
        self.html = (STATIC / "paper-mvp.html").read_text(encoding="utf-8")
        self.js = (STATIC / "paper-mvp.js").read_text(encoding="utf-8")

    def test_operating_amount_is_a_free_won_input(self):
        self.assertRegex(self.html, r'<input id="budget" type="number" min="0" max="\d+" step="1"')
        self.assertIn("<span>원</span>", self.html)
        self.assertIn("자동투자 최대 사용 금액", self.html)

    def test_no_other_limit_inputs_or_profile_cards_remain(self):
        for name in REMOVED_IDS:
            self.assertNotIn(f'id="{name}"', self.html, name)
        self.assertNotIn("data-profile", self.html)
        for name in REMOVED_IDS:
            self.assertNotIn(f"'#{name}'", self.js, name)

    def test_only_budget_and_exit_rules_are_sent_to_the_server(self):
        to_server = re.search(r"const toServer = .*", self.js).group(0)
        for key in ("budget", "sectors", "stop_pct", "take_pct", "max_days"):
            self.assertIn(key, to_server)
        for gone in ("per_unit", "per_value", "max_holdings", "daily_loss_pct", "daily_buys", "profile"):
            self.assertNotIn(gone, to_server)

    def test_status_text_names_only_the_operating_amount(self):
        line = re.search(r"\$\('#account-safety'\)\.textContent = .*", self.js).group(0)
        self.assertIn("자동매매 운용 금액", line)
        for gone in ("종목당", "하루 손실", "최대 '"):
            self.assertNotIn(gone, line)


class LegacyPaperPage(unittest.TestCase):
    def test_saved_limit_tab_and_renderer_are_gone(self):
        index = (STATIC / "index.html").read_text(encoding="utf-8")
        app = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertNotIn('data-paper-tab="safety"', index)
        self.assertNotIn('data-paper-panel="safety"', index)
        self.assertNotIn("renderRisk", app)
        self.assertNotIn("api('/risk')", app)

    def test_settings_page_marks_the_limit_tab_as_live_only(self):
        settings = (STATIC / "settings.html").read_text(encoding="utf-8")
        self.assertIn("LIVE 주문 미리보기의 경고 기준", settings)
        self.assertIn("모의매매(PAPER)에는 적용되지 않으며", settings)


if __name__ == "__main__":
    unittest.main()
