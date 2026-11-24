"""PAPER-APP-UI-01: isolation guard and menu wiring (no DB, no network)."""
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from auto_trader import paper_mvp_isolated as iso

STATIC = Path(__file__).resolve().parent.parent / "auto_trader" / "static"
SAFE = dict(postgres_db="folio_mvp_test", postgres_host="127.0.0.1", postgres_port=55432, toss_api_ready=False)


def with_settings(**over):
    return patch.object(iso, "settings", SimpleNamespace(**{**SAFE, **over}))


class IsolationGuard(unittest.TestCase):
    def test_off_by_default(self):
        with patch.dict(os.environ, {}), with_settings():
            os.environ.pop("FOLIO_MVP_ISOLATED", None)
            self.assertFalse(iso.active())
            iso.assert_safe()           # not requested: never raises

    def test_active_only_when_every_guard_holds(self):
        with patch.dict(os.environ, {"FOLIO_MVP_ISOLATED": "1"}), with_settings():
            self.assertTrue(iso.active())
        for bad in (dict(postgres_db="auto_trader"), dict(postgres_host="10.0.0.5"), dict(postgres_port=5432), dict(toss_api_ready=True)):
            with patch.dict(os.environ, {"FOLIO_MVP_ISOLATED": "1"}), with_settings(**bad):
                self.assertFalse(iso.active(), bad)
                with self.assertRaises(RuntimeError):
                    iso.assert_safe()   # requested but unsafe: the app refuses to start


class MenuWiring(unittest.TestCase):
    def test_every_page_menu_points_paper_to_the_new_screen(self):
        for name in ("live", "long-term", "news", "stock-detail", "stocks", "paper-mvp"):
            html = (STATIC / f"{name}.html").read_text(encoding="utf-8")
            self.assertIn('href="/paper-mvp"', html, name)
            self.assertNotIn('href="/paper"', html, name)
            self.assertNotIn('href="/settings"', html, name)       # PAPER-SIMPLIFY-02: account functions are in the account menu

    def test_new_screen_keeps_the_menus_without_the_retired_screens(self):
        html = (STATIC / "paper-mvp.html").read_text(encoding="utf-8")
        for href in ("/live", "/stocks", "/long-term", "/news"):
            self.assertIn('href="%s"' % href, html)


if __name__ == "__main__":
    unittest.main()
