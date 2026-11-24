"""PAPER-SIMPLIFY-02 contract: retired screens/routes, shared account menu, settings reduced to the operating amount.
DB-free: routes are read from the app's route table, pages are read from disk, nothing is started."""
import asyncio
import re
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from auto_trader import main
from auto_trader.api import pages
from auto_trader.paper_mvp import DEFAULT_EXIT, MvpSettings

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "auto_trader" / "static"
RETIRED_FILES = ("index.html", "app.js", "settings.html", "settings.js", "settings-presets.js", "settings-summary.js")
SHELL_PAGES = ("live", "stocks", "stock-detail", "long-term", "news")


def routes():
    """(method, path) of every API route plus the hidden page routes (this FastAPI nests included routers)."""
    found = {(m.upper(), p) for p, item in main.app.openapi()["paths"].items() for m in item}
    for r in main.app.routes:
        for inner in getattr(getattr(r, "original_router", None), "routes", ()):
            found.update((m, inner.path) for m in getattr(inner, "methods", None) or ())
    return found


async def request(path, method="GET"):
    messages = []

    async def receive():
        await asyncio.Event().wait()

    async def send(message):
        messages.append(message)
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
             "headers": [(b"host", b"localhost")], "server": ("localhost", 80), "client": ("127.0.0.1", 1234)}
    await main.app(scope, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    return start["status"], dict((k.decode(), v.decode()) for k, v in start["headers"])


class RetiredRoutes(unittest.TestCase):
    def test_strategy_crud_presets_and_user_risk_settings_routes_are_gone(self):
        have = routes()
        for method, path in (("GET", "/settings/strategies"), ("POST", "/settings/strategies"), ("PUT", "/settings/strategies/{strategy_id}"),
                             ("DELETE", "/settings/strategies/{strategy_id}"), ("GET", "/settings/strategy-preset-status"),
                             ("GET", "/settings/strategy-presets/{kind}"), ("PUT", "/risk/settings"), ("GET", "/risk"),
                             ("PUT", "/strategy/settings"), ("POST", "/strategy/start"), ("POST", "/paper/strategies/{strategy_id}/select")):
            self.assertNotIn((method, path), have, (method, path))

    def test_account_connection_and_paper_mvp_routes_are_kept(self):
        have = routes()
        for method, path in (("GET", "/settings/account"), ("PUT", "/settings/account/{kind}"), ("GET", "/settings/connection"),
                             ("PUT", "/settings/connection"), ("GET", "/paper/mvp/status"), ("PUT", "/paper/mvp/settings"),
                             ("POST", "/paper/mvp/start"), ("POST", "/paper/mvp/stop"), ("POST", "/paper/mvp/emergency-stop"),
                             ("POST", "/paper/accounts/{mode}/select"), ("POST", "/strategy/stop"), ("POST", "/strategy/emergency-stop"),
                             ("POST", "/live/orders/real"), ("POST", "/live/orders/preview"), ("POST", "/auth/logout")):
            self.assertIn((method, path), have, (method, path))

    def test_page_urls_redirect_to_the_active_screens(self):
        with patch.object(pages, "session_from_request", return_value=object()):
            for path, target in (("/settings", "/live"), ("/paper", "/paper-mvp")):
                status, headers = asyncio.run(request(path))
                self.assertEqual((status, headers["location"]), (303, target), path)
        with patch.object(pages, "session_from_request", return_value=None):      # signed out: nothing is revealed
            for path in ("/settings", "/paper"):
                status, headers = asyncio.run(request(path))
                self.assertEqual((status, headers["location"]), (303, "/login"), path)

    def test_direct_static_urls_of_retired_screens_are_not_served(self):
        for name in RETIRED_FILES:
            self.assertFalse((STATIC / name).exists(), name)
            status, _ = asyncio.run(request("/static/" + name))
            self.assertEqual(status, 404, name)


class SharedAccountMenu(unittest.TestCase):
    def test_every_shell_page_loads_the_menu_first_and_has_no_settings_link_or_sidebar_account(self):
        for name in SHELL_PAGES:
            html = (STATIC / f"{name}.html").read_text(encoding="utf-8")
            self.assertIn('<head><script src="/static/account-menu.js" defer></script>', html, name)
            for gone in ('href="/settings"', "sidebar-account", 'id="logout-button"', 'id="current-user"', 'href="/paper"'):
                self.assertNotIn(gone, html, (name, gone))
            self.assertIn('class="header-actions"', html, name)              # the menu mounts in the shared header

    def test_paper_page_has_the_menu_and_no_retired_links(self):
        html = (STATIC / "paper-mvp.html").read_text(encoding="utf-8")
        self.assertIn('<script src="/static/account-menu.js"></script>', html)
        for gone in ('href="/settings"', 'href="/paper"', "legacy-link", "data-sector", 'id="stop-pct"', 'id="take-pct"', 'id="max-days"'):
            self.assertNotIn(gone, html, gone)
        for href in ("/live", "/stocks", "/long-term", "/news"):
            self.assertIn(f'href="{href}"', html)

    def test_menu_uses_only_existing_account_apis_and_exposes_every_account_function(self):
        js = (STATIC / "account-menu.js").read_text(encoding="utf-8")
        used = set(re.findall(r"['`\"](/(?:auth|settings)/[A-Za-z/_-]*)", js))
        self.assertEqual(used, {"/auth/me", "/auth/logout", "/settings/account", "/settings/account/", "/settings/connection"})
        for text in ("비밀번호 변경", "PIN 등록·변경", "연결 등록·변경", "연결 확인", "로그아웃", "X-CSRF-Token"):
            self.assertIn(text, js, text)
        self.assertNotIn("innerHTML = user", js)

    def test_first_login_without_connection_lands_on_the_account_menu(self):
        self.assertIn('destination = "/live#account"', (STATIC / "login.js").read_text(encoding="utf-8"))
        self.assertIn("location.hash === '#account'", (STATIC / "account-menu.js").read_text(encoding="utf-8"))

    def test_no_page_script_references_retired_files_or_routes(self):
        for path in list(STATIC.glob("*.html")) + list(STATIC.glob("*.js")):
            text = path.read_text(encoding="utf-8")
            for gone in ('"/static/app.js"', "settings-presets", "settings-summary", "settings.js", "/settings/strategies",
                         "/risk/settings", "/settings#", "api('/risk')", 'api("/risk")'):
                self.assertNotIn(gone, text, (path.name, gone))


class SettingsReducedToOperatingAmount(unittest.TestCase):
    def test_removed_inputs_are_rejected_by_the_api_model(self):
        self.assertEqual(MvpSettings(budget=3000000).budget, 3000000)
        for extra in ({"sectors": ["IT"]}, {"stop_pct": 1}, {"take_pct": 2}, {"max_days": 3}):
            with self.assertRaises(ValidationError, msg=extra):
                MvpSettings(budget=1, **extra)

    def test_exit_defaults_are_foliOs_own_constants(self):
        self.assertEqual((DEFAULT_EXIT["stop_pct"], DEFAULT_EXIT["take_pct"], DEFAULT_EXIT["max_days"]), (5, 10, 20))

    def test_settings_tab_sends_only_the_budget(self):
        js = (STATIC / "paper-mvp.js").read_text(encoding="utf-8")
        self.assertIn("const toServer = d => ({ budget: d.budget });", js)
        self.assertNotIn("data-sector", js)
        for gone in ("stop-pct", "take-pct", "max-days", "'sectors'"):
            self.assertNotIn(gone, js, gone)
        html = (STATIC / "paper-mvp.html").read_text(encoding="utf-8")
        self.assertRegex(html, r'<input id="budget" type="number" min="0" max="\d+" step="1"')
        self.assertIn('id="prepare"', html)          # the retired /paper screen was the only other way to prepare the account


class ManualOrderScreen(unittest.TestCase):
    def test_policy_warning_and_acceptance_flow_is_gone_from_the_order_screen(self):
        js = (STATIC / "stock-detail.js").read_text(encoding="utf-8")
        for gone in ("accept_financial_warnings", "한도 경고", "check.warning", "투자 한도를 초과해도"):
            self.assertNotIn(gone, js, gone)
        self.assertIn("impactList(preview.impact)", js)                  # neutral before/after view reuses the preview + confirm dialog
        self.assertIn("openDryRunConfirmModal()", js)
        html = (STATIC / "stock-detail.html").read_text(encoding="utf-8")
        self.assertIn('id="real-order-confirm-checkbox"', html)          # the ordinary final confirmation stays


if __name__ == "__main__":
    unittest.main()
