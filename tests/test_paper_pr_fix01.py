"""PAPER PR-FIX-01 regression tests (three reviewed findings). Synthetic data only; no network, no real app.
 1. E2E scripts fail closed: no business-state write before login + CSRF + the app's own isolation contract are proven.
 2. A non-AUTO management scope is visible in the status and recoverable by the explicit existing management API.
 3. Valuation of held, unmanaged positions continues when there is no candidate and no managed lot (and orders nothing).
Classes 1 and 3 need no database. Class 2 uses the isolated test database like tests/test_paper_mvp.py."""
import asyncio
import contextlib
import io
import json
import os
import sys
import unittest
import urllib.error
import urllib.request
from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import paper_app_ui_e2e  # noqa: E402
import paper_e2e_guard as guard  # noqa: E402
import paper_finalize_e2e  # noqa: E402
from auto_trader.paper_mvp_synthetic import DAY, VirtualClock  # noqa: E402
from auto_trader.simulator import MarketSimulator  # noqa: E402
from auto_trader.strategy import MovingAverageEngine  # noqa: E402


# ---------------------------------------------------------------------------------------------- fake HTTP target
class FakeResponse:
    def __init__(self, status, body, headers=None):
        raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.status, self._raw, self.headers = status, raw, headers or {}

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    """Records every request; `routes` maps (METHOD, path) -> (status, body) or a callable returning it."""

    def __init__(self, routes):
        self.routes, self.requests = routes, []

    def open(self, req, timeout=None):
        self.last = req
        method, path = req.get_method(), req.full_url.split("://", 1)[1].split("/", 1)[1:]
        path = "/" + (path[0] if path else "")
        self.requests.append((method, path))
        route = self.routes.get((method, path))
        if route is None:
            route = (404, {"detail": "not found"})
        status, body = route() if callable(route) else route
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "err", {}, BytesIO(json.dumps(body).encode()))
        return FakeResponse(status, body, {"location": "/login"} if status in (302, 303) else {})

    def writes(self):
        """Business-state writes: any non-GET other than the login session creation."""
        return [(m, p) for m, p in self.requests if m not in ("GET", "HEAD") and p != "/auth/login"]


def status_doc(**over):
    doc = dict(mode="PAPER", live_blocked=True, data_source="SYNTHETIC", emergency=None,
               run=dict(running=False, state="idle"), settings={"budget": 1}, trades=[], account={})
    doc.update(over)
    return doc


def isolated_routes(**status_over):
    return {("POST", "/auth/login"): (200, {"csrf_token": "tok", "username": "u"}),
            ("GET", "/paper/mvp/status"): (200, status_doc(**status_over))}


class GuardUrl(unittest.TestCase):
    def test_only_plain_loopback_http_is_accepted(self):
        for ok in ("http://127.0.0.1:8766", "http://localhost:8766/", "http://[::1]:8766"):
            self.assertTrue(guard.safe_base(ok).startswith("http://"))
        for bad in ("http://10.0.0.5:8766", "http://example.com", "https://127.0.0.1:8766", "http://u:p@127.0.0.1:1",
                    "http://127.0.0.1:8766/app", "http://127.0.0.1.evil.com:80", "ftp://127.0.0.1", "http://0.0.0.0:80"):
            with self.assertRaises(guard.Unsafe, msg=bad):
                guard.safe_base(bad)

    def test_opener_ignores_proxy_environment_and_never_follows_redirects(self):
        with patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "HTTPS_PROXY": "http://127.0.0.1:1", "ALL_PROXY": "http://127.0.0.1:1"}):
            opener = guard.make_opener()
        # With the variables set, urllib's default opener would install an environment ProxyHandler (http_open -> proxy).
        self.assertEqual([h for h in opener.handlers if isinstance(h, urllib.request.ProxyHandler)], [])
        with patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1"}):
            self.assertTrue(any(isinstance(h, urllib.request.ProxyHandler) for h in urllib.request.build_opener().handlers))
        redirect = next(h for h in opener.handlers if isinstance(h, guard.NoRedirect))
        self.assertIsNone(redirect.redirect_request(None, None, 302, "x", {}, "http://evil.example/"))


class UnsafeTargetsDoNoWrites(unittest.TestCase):
    """Every script, every unsafe/failed-prerequisite target: zero business-state writes (login is only a session)."""

    SCRIPTS = (("finalize", paper_finalize_e2e), ("app_ui", paper_app_ui_e2e))

    def run_script(self, module, routes, base="http://127.0.0.1:1"):
        opener = FakeOpener(routes)
        code = module.main(["x", base, "u", "pw"], opener, FakeOpener(routes), exclusive=True)
        return code, opener

    def assert_refused(self, routes, **kw):
        for name, module in self.SCRIPTS:
            with self.subTest(script=name):
                code, opener = self.run_script(module, routes, **kw)
                self.assertEqual(code, 1)
                self.assertEqual(opener.writes(), [])

    def test_non_loopback_target_makes_no_request_at_all(self):
        for name, module in self.SCRIPTS:
            code, opener = self.run_script(module, isolated_routes(), base="http://203.0.113.9:8766")
            self.assertEqual((code, opener.requests), (1, []), name)

    def test_non_synthetic_loopback_server_is_refused(self):      # e.g. a developer's real FOLIO on loopback
        self.assert_refused(isolated_routes(data_source="NONE"))
        self.assert_refused(isolated_routes(data_source="TOSS"))
        self.assert_refused({("POST", "/auth/login"): (200, {"csrf_token": "t"}), ("GET", "/paper/mvp/status"): (200, {"data_source": "SYNTHETIC"})})

    def test_failed_login_csrf_or_status_is_refused(self):
        self.assert_refused({("POST", "/auth/login"): (401, {"detail": "no"})})
        self.assert_refused({("POST", "/auth/login"): (200, {"username": "u"})})                      # no CSRF token
        self.assert_refused({("POST", "/auth/login"): (200, {"csrf_token": "t"}), ("GET", "/paper/mvp/status"): (500, {})})
        self.assert_refused({("POST", "/auth/login"): (200, {"csrf_token": "t"}), ("GET", "/paper/mvp/status"): (401, {})})

    def test_live_not_blocked_or_not_paper_is_refused(self):
        self.assert_refused(isolated_routes(live_blocked=False))
        self.assert_refused(isolated_routes(mode="LIVE"))

    def test_finalize_refuses_an_active_run_or_emergency_latch_but_ui_check_only_needs_isolation(self):
        for over in (dict(run=dict(running=True, state="running")), dict(emergency=dict(at="x", unsaved=False))):
            code, opener = self.run_script(paper_finalize_e2e, isolated_routes(**over))
            self.assertEqual((code, opener.writes()), (1, []))

    def test_isolated_target_proceeds_to_the_first_write_only_after_the_proof(self):
        routes = isolated_routes()
        routes[("GET", "/paper-mvp")] = (200, "<html>운용 홈 id=\"prepare\"</html>")
        routes[("POST", "/paper/accounts/EXPERIMENT/select")] = (500, {})     # first write fails -> stop right there
        code, opener = self.run_script(paper_finalize_e2e, routes)
        self.assertEqual(code, 1)
        self.assertEqual(opener.requests[:3], [("POST", "/auth/login"), ("GET", "/paper/mvp/status"), ("GET", "/paper-mvp")])
        self.assertEqual(opener.writes(), [("POST", "/paper/accounts/EXPERIMENT/select")])      # nothing after the failed step

    def test_ui_check_never_writes_after_a_failed_precondition(self):
        routes = isolated_routes()                                                 # anonymous /paper-mvp is not a redirect -> check 1 fails
        routes[("GET", "/paper-mvp")] = (200, "page")
        code, opener = self.run_script(paper_app_ui_e2e, routes)
        self.assertEqual(code, 1)
        self.assertEqual(opener.writes(), [])


class SyntheticApp:
    """Stateful synthetic FOLIO behind the fake transport (no network, no real app); *_override callables inject failures."""

    def __init__(self, running=False, state="idle", emergency=None):
        self.run = dict(running=running, state=state)
        self.emergency, self.prepared, self.budget = emergency, False, 1
        self.status_override, self.start_override, self.stop_override = None, None, None
        self.opener = FakeOpener({})
        self.anon = FakeOpener({("GET", "/paper-mvp"): (302, ""), ("GET", "/paper/mvp/status"): (401, {}),
                                ("PUT", "/paper/mvp/settings"): (401, {}), ("POST", "/paper/mvp/start"): (401, {}),
                                ("POST", "/paper/mvp/stop"): (401, {}), ("POST", "/paper/mvp/emergency-stop"): (401, {}),
                                ("POST", "/auth/login"): (401, {})})
        page = '운용 홈 href="/live" href="/news" href="/paper-mvp" id="prepare"'
        self.opener.routes = {("POST", "/auth/login"): (200, {"csrf_token": "tok"}),
                              ("GET", "/paper/mvp/status"): self.status, ("POST", "/paper/mvp/start"): self.start,
                              ("POST", "/paper/mvp/stop"): self.stop, ("PUT", "/paper/mvp/settings"): self.settings,
                              ("POST", "/paper/accounts/EXPERIMENT/select"): self.select, ("GET", "/paper-mvp"): (200, page),
                              ("GET", "/static/paper-mvp.js"): (200, "js")}
        for p in ("/live", "/stocks", "/long-term", "/news"):
            self.opener.routes[("GET", p)] = (200, 'href="/paper-mvp"')

    def csrf_ok(self):
        return self.opener.last.get_header("X-csrf-token") == "tok"

    def doc(self):
        return status_doc(run=dict(self.run), emergency=self.emergency,
                          settings={"budget": self.budget}, account=dict(cash="100", total_asset="100"))

    def status(self):
        return self.status_override() if self.status_override else (200, self.doc())

    def settings(self):
        if not self.csrf_ok():
            return 403, {}
        self.budget = json.loads(self.opener.last.data)["budget"]
        return 200, self.doc()

    def select(self):
        self.prepared = True
        return 200, {}

    def start(self):
        if not self.csrf_ok():
            return 403, {}
        if self.start_override:
            return self.start_override()
        if not self.prepared:
            return 409, {"detail": "not prepared"}
        self.run = dict(running=True, state="running")
        return 200, self.doc()

    def stop(self):
        if self.stop_override:
            return self.stop_override()
        self.run = dict(running=False, state="stopped")
        return 200, self.doc()

    def count(self, method, path):
        return self.opener.requests.count((method, path))


class RunOwnershipCleanup(unittest.TestCase):
    """FOLIO-E2E-CLEANUP-01: a stop is sent only for a run this script provably started; every failure path is reported."""

    def ledger(self, app, exclusive=True):
        return guard.RunLedger(guard.Client("http://127.0.0.1:1", app.opener), "tok", exclusive)

    def started(self, app):
        led = self.ledger(app)
        self.assertEqual(led.start()[0], 200)
        return led

    # --- ledger: start prerequisites
    def test_start_refused_without_declaration_or_when_an_other_run_or_emergency_exists(self):
        cases = ((dict(), False), (dict(running=True, state="running"), True), (dict(running=True, state="other"), True),
                 (dict(emergency=dict(at="x", unsaved=False)), True))
        for kw, exclusive in cases:
            app = SyntheticApp(**kw)
            led = self.ledger(app, exclusive)
            with self.assertRaises(guard.Unsafe):
                led.start()
            self.assertIsNone(led.cleanup())
            self.assertEqual((app.count("POST", "/paper/mvp/start"), app.count("POST", "/paper/mvp/stop")), (0, 0))

    def test_start_refused_when_status_is_unreadable_malformed_or_not_synthetic(self):
        for override in (lambda: (500, {}), lambda: (200, "garbage"), lambda: (200, status_doc(data_source="NONE")),
                         lambda: (200, dict(status_doc(), run=None))):
            app = SyntheticApp()
            app.status_override = override
            with self.assertRaises(guard.Unsafe):
                self.ledger(app).start()
            self.assertEqual(app.count("POST", "/paper/mvp/start"), 0)

    # --- ledger: cleanup
    def test_owned_run_is_stopped_exactly_once_and_an_already_released_run_is_not_stopped_again(self):
        app = SyntheticApp()
        app.prepared = True
        led = self.started(app)
        self.assertIsNone(led.cleanup())
        self.assertIsNone(led.cleanup())
        self.assertEqual((app.run["running"], app.count("POST", "/paper/mvp/stop")), (False, 1))
        app = SyntheticApp()
        app.prepared = True
        led = self.started(app)
        app.run = dict(running=False, state="stopped")                  # the script's own stop step ran and was verified
        led.mark_stopped(app.doc())
        self.assertIsNone(led.cleanup())
        self.assertEqual(app.count("POST", "/paper/mvp/stop"), 0)

    def test_replaced_or_taken_over_run_is_ambiguous_and_never_stopped(self):
        for state in ("other", "emergency", "blocked"):
            app = SyntheticApp()
            app.prepared = True
            led = self.started(app)
            app.run = dict(running=True, state=state)
            self.assertIn("AMBIGUOUS", led.cleanup())
            self.assertEqual(app.count("POST", "/paper/mvp/stop"), 0)

    def test_emergency_latch_blocks_the_stop_and_is_never_touched(self):
        app = SyntheticApp()
        app.prepared = True
        led = self.started(app)
        app.emergency = dict(at="x", unsaved=False)
        self.assertIn("AMBIGUOUS", led.cleanup())
        self.assertTrue(all("emergency" not in p for _, p in app.opener.requests))
        self.assertEqual(app.count("POST", "/paper/mvp/stop"), 0)

    def test_rejected_start_is_not_ownership_so_a_later_run_is_not_stopped(self):
        app = SyntheticApp()                                             # account not prepared -> 409
        led = self.ledger(app)
        self.assertEqual(led.start()[0], 409)
        self.assertIsNone(led.cleanup())                                 # nothing running: nothing to do
        app.run = dict(running=True, state="running")                    # a concurrent start from elsewhere
        led.released = False
        self.assertIn("AMBIGUOUS", led.cleanup())
        self.assertEqual(app.count("POST", "/paper/mvp/stop"), 0)

    def test_timeout_during_start_is_an_unknown_outcome_and_the_owned_run_is_still_cleaned_up(self):
        app = SyntheticApp()
        app.prepared = True

        def start_then_timeout():
            app.run = dict(running=True, state="running")                # the server processed it, the client timed out
            raise TimeoutError("timed out")
        app.start_override = start_then_timeout
        led = self.ledger(app)
        with self.assertRaises(TimeoutError):
            led.start()
        self.assertIsNone(led.cleanup())
        self.assertEqual((app.run["running"], app.count("POST", "/paper/mvp/stop")), (False, 1))

    def test_unverifiable_state_at_cleanup_sends_no_stop_and_is_reported(self):
        for name, override in (("malformed", lambda: (200, "<html>")), ("http error", lambda: (500, {})),
                               ("no run", lambda: (200, dict(status_doc(), run=None))),
                               ("account changed", lambda: (200, status_doc(data_source="NONE", run=dict(running=True, state="running")))),
                               ("transport", lambda: (_ for _ in ()).throw(urllib.error.URLError("refused")))):
            with self.subTest(name):
                app = SyntheticApp()
                app.prepared = True
                led = self.started(app)
                app.status_override = override
                self.assertIn("CLEANUP", led.cleanup())
                self.assertEqual(app.count("POST", "/paper/mvp/stop"), 0)

    def test_failed_or_ineffective_stop_is_reported(self):
        for name, override in (("server error", lambda: (500, {})), ("session lost", lambda: (403, {})),
                               ("accepted but still running", lambda: (200, {}))):
            with self.subTest(name):
                app = SyntheticApp()
                app.prepared = True
                led = self.started(app)
                app.stop_override = override
                self.assertIn("CLEANUP FAILED", led.cleanup())
                self.assertEqual(app.count("POST", "/paper/mvp/stop"), 1)
                self.assertTrue(app.run["running"])

    # --- through the real script entry points
    def finalize(self, app, extra=()):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), patch.object(paper_finalize_e2e.time, "sleep", lambda s: None):
            try:
                code = paper_finalize_e2e.main(["x", "http://127.0.0.1:1", "u", "pw", "", "5", *extra][:6], app.opener, app.opener, exclusive=True)
            except Exception as exc:
                return exc, out.getvalue()
        return code, out.getvalue()

    def ui(self, app, exclusive=True):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = paper_app_ui_e2e.main(["x", "http://127.0.0.1:1", "u", "pw"], app.opener, app.anon, exclusive=exclusive)
        return code, out.getvalue()

    def test_scripts_without_the_exclusive_declaration_send_nothing(self):
        app = SyntheticApp()
        for fn in (lambda: self.ui(app, exclusive=False)[0], lambda: paper_finalize_e2e.main(["x", "http://127.0.0.1:1", "u", "pw"], app.opener, app.opener, exclusive=False)):
            self.assertEqual(fn(), 1)
        self.assertEqual((app.opener.requests, app.anon.requests), ([], []))

    def test_app_ui_normal_path_probe_is_rejected_and_nothing_is_left_running(self):
        app = SyntheticApp()
        code, out = self.ui(app)
        self.assertEqual((code, app.run["running"], app.count("POST", "/paper/mvp/stop")), (0, False, 0), out)

    def test_app_ui_unexpected_start_success_is_cleaned_up_and_reported_nonzero(self):
        app = SyntheticApp()
        app.prepared = True                                              # start is (wrongly) accepted -> check 12 fails
        code, out = self.ui(app)
        self.assertEqual(code, 1)
        self.assertFalse(app.run["running"])
        self.assertEqual(app.count("POST", "/paper/mvp/stop"), 1)
        self.assertEqual(app.count("PUT", "/paper/mvp/settings"), 1)     # only the CSRF-less denial probe (check 9), never a saved setting

    def test_app_ui_preexisting_run_means_no_start_probe_and_no_stop(self):
        app = SyntheticApp(running=True, state="running")
        code, out = self.ui(app)
        self.assertEqual(code, 1)
        self.assertTrue(app.run["running"])
        self.assertEqual((app.count("POST", "/paper/mvp/start"), app.count("POST", "/paper/mvp/stop"), app.count("POST", "/paper/accounts/EXPERIMENT/select")), (0, 0, 0))

    def test_app_ui_unexpected_start_success_with_failed_cleanup_is_reported_nonzero(self):
        app = SyntheticApp()
        app.prepared = True
        app.stop_override = lambda: (500, {})
        code, out = self.ui(app)
        self.assertEqual(code, 1)
        self.assertIn("FAIL cleanup", out)
        self.assertTrue(app.run["running"])

    def test_app_ui_failed_status_get_makes_no_settings_or_later_business_write(self):
        app = SyntheticApp()
        base = app.status

        def status():
            if app.prepared:                                             # the GET right after account select (before the settings PUT)
                return 500, {}
            return base()
        app.opener.routes[("GET", "/paper/mvp/status")] = status
        code, out = self.ui(app)
        self.assertEqual(code, 1)
        self.assertEqual(app.count("POST", "/paper/accounts/EXPERIMENT/select"), 1)
        self.assertNotIn(("PUT", "/paper/mvp/settings"), [r for r in app.opener.requests[app.opener.requests.index(("POST", "/paper/accounts/EXPERIMENT/select")):]])

    def test_finalize_exception_after_start_stops_the_owned_run_and_reraises_the_original(self):
        for make in (lambda: urllib.error.URLError("connection reset"), lambda: TimeoutError("read timed out")):
            app = SyntheticApp()
            exc = make()
            base = app.status

            fired = []

            def status(exc=exc, base=base, fired=fired, app=app):
                if app.run["running"] and not fired:
                    fired.append(1)
                    raise exc                                            # one network failure right after our start
                return base()
            app.opener.routes[("GET", "/paper/mvp/status")] = status
            result, out = self.finalize(app)
            self.assertIsInstance(result, type(exc))                    # not replaced by a cleanup message
            self.assertFalse(app.run["running"])
            self.assertEqual(app.count("POST", "/paper/mvp/stop"), 1)

    def test_finalize_malformed_status_after_start_stops_the_owned_run(self):
        app = SyntheticApp()
        base = app.status
        sent = {"n": 0}

        def status():
            if app.run["running"] and sent["n"] == 0:
                sent["n"] = 1
                return 200, "not json"                                   # parse failure: TypeError/KeyError inside the flow
            return base()
        app.opener.routes[("GET", "/paper/mvp/status")] = status
        result, out = self.finalize(app)
        self.assertNotEqual(result, 0)
        self.assertFalse(app.run["running"])

    def test_finalize_refuses_to_touch_a_preexisting_run_and_sends_no_stop(self):
        app = SyntheticApp(running=True, state="running")
        result, out = self.finalize(app)
        self.assertEqual((result, app.opener.writes(), app.run["running"]), (1, [], True))

    def test_finalize_failed_cleanup_after_a_failed_check_is_reported_nonzero(self):
        app = SyntheticApp()
        app.stop_override = lambda: (500, {})
        base = app.status

        def status():                                                    # fail check 8a while our run is active
            return (500, {}) if app.run["running"] else base()
        app.opener.routes[("GET", "/paper/mvp/status")] = status
        result, out = self.finalize(app)
        self.assertEqual(result, 1)
        self.assertIn("FAIL cleanup", out)
        self.assertTrue(app.run["running"])



class AmbiguousOutcomesFollowUp(unittest.TestCase):
    """FOLIO-E2E-CLEANUP-01-FIX: ambiguous start errors, strict running validation, GET validated before processing."""

    ARGV = ["x", "http://127.0.0.1:1", "u", "pw", "", "5"]

    def ledger(self, app):
        return guard.RunLedger(guard.Client("http://127.0.0.1:1", app.opener), "tok", True)

    def run_finalize(self, app):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), patch.object(paper_finalize_e2e.time, "sleep", lambda s: None):
            code = paper_finalize_e2e.main(self.ARGV, app.opener, app.opener, exclusive=True)
        return code, out.getvalue()

    @staticmethod
    def start_500_but_started(app):
        def start():
            app.run = dict(running=True, state="running")
            return 500, {}
        return start

    def test_start_500_after_the_server_really_started_is_unknown_and_the_owned_run_is_stopped(self):
        app = SyntheticApp()
        app.prepared = True
        app.start_override = self.start_500_but_started(app)
        led = self.ledger(app)
        self.assertEqual(led.start()[0], 500)
        self.assertTrue(led.owned)
        self.assertIsNone(led.cleanup())
        self.assertEqual((app.run["running"], app.count("POST", "/paper/mvp/stop")), (False, 1))

    def test_start_500_with_no_run_sends_no_stop_and_an_unrelated_run_is_never_stopped(self):
        app = SyntheticApp()
        app.prepared = True
        app.start_override = lambda: (500, {})
        led = self.ledger(app)
        self.assertEqual(led.start()[0], 500)
        self.assertIsNone(led.cleanup())
        self.assertEqual(app.count("POST", "/paper/mvp/stop"), 0)
        app.run = dict(running=True, state="other")                      # an unrelated run appears: identity not provable
        led.released = False
        self.assertIn("AMBIGUOUS", led.cleanup())
        self.assertEqual(app.count("POST", "/paper/mvp/stop"), 0)

    def test_only_4xx_is_a_definite_rejection(self):
        for code in (400, 401, 403, 409, 422, 429):
            self.assertTrue(guard.definitely_rejected(code), code)
        for code in (200, 202, 302, 408, 425, 500, 502, 503, 504, None):
            self.assertFalse(guard.definitely_rejected(code), code)

    def test_malformed_running_is_never_accepted_as_stopped_or_idle(self):
        bad_runs = (dict(state="idle"), dict(running=None, state="idle"), dict(running="false", state="idle"),
                    dict(running=0, state="idle"), dict(running=[], state="idle"))
        for run in bad_runs:
            with self.subTest(run=run):
                for require_idle in (True, False):                       # preflight
                    app = SyntheticApp()
                    app.status_override = lambda run=run: (200, status_doc(run=run))
                    with self.assertRaises(guard.Unsafe):
                        guard.prove_isolated(guard.Client("http://127.0.0.1:1", app.opener), "u", "pw", require_idle=require_idle)
                    self.assertEqual(app.opener.writes(), [])
                app = SyntheticApp()                                     # start prerequisite
                app.prepared = True
                app.status_override = lambda run=run: (200, status_doc(run=run))
                with self.assertRaises(guard.Unsafe):
                    self.ledger(app).start()
                self.assertEqual(app.count("POST", "/paper/mvp/start"), 0)
                app = SyntheticApp()                                     # cleanup: first read malformed -> reported, no stop
                app.prepared = True
                led = self.ledger(app)
                self.assertEqual(led.start()[0], 200)
                app.status_override = lambda run=run: (200, status_doc(run=run))
                self.assertIn("CLEANUP UNVERIFIED", led.cleanup())
                self.assertEqual(app.count("POST", "/paper/mvp/stop"), 0)
                app = SyntheticApp()                                     # cleanup: status after our stop malformed -> not "stopped"
                app.prepared = True
                led = self.ledger(app)
                self.assertEqual(led.start()[0], 200)
                base = app.status
                app.opener.routes[("GET", "/paper/mvp/status")] = lambda run=run, app=app, base=base: (
                    (200, status_doc(run=run)) if app.count("POST", "/paper/mvp/stop") else base())
                self.assertIn("CLEANUP FAILED", led.cleanup())
                self.assertFalse(led.released)

    def test_mark_stopped_requires_a_real_false(self):
        led = self.ledger(SyntheticApp())
        for run in (dict(), dict(running=None), dict(running=0), dict(running="false")):
            led.mark_stopped(dict(run=run))
            self.assertFalse(led.released)

    def test_malformed_running_in_cleanup_preserves_the_original_error_and_is_nonzero(self):
        app = SyntheticApp()
        base = app.status
        fired = []

        def status():
            if app.run["running"] and not fired:
                fired.append(1)
                raise urllib.error.URLError("connection reset")           # the original failure
            if app.run["running"]:
                return 200, status_doc(run=dict(running=None, state="running"))   # cleanup then sees garbage
            return base()
        app.opener.routes[("GET", "/paper/mvp/status")] = status
        out = io.StringIO()
        with contextlib.redirect_stdout(out), patch.object(paper_finalize_e2e.time, "sleep", lambda s: None):
            with self.assertRaises(urllib.error.URLError):
                paper_finalize_e2e.main(self.ARGV, app.opener, app.opener, exclusive=True)
        self.assertIn("FAIL cleanup", out.getvalue())
        self.assertTrue(app.run["running"])
        self.assertEqual(app.count("POST", "/paper/mvp/stop"), 0)

    def test_finalize_start_500_with_a_started_run_is_stopped_and_reported_nonzero(self):
        app = SyntheticApp()
        app.prepared = True
        app.start_override = self.start_500_but_started(app)
        code, out = self.run_finalize(app)
        self.assertEqual(code, 1)
        self.assertFalse(app.run["running"])
        self.assertEqual(app.count("POST", "/paper/mvp/stop"), 1)

    def test_finalize_start_500_with_no_run_sends_no_stop(self):
        app = SyntheticApp()
        app.prepared = True
        app.start_override = lambda: (500, {})
        code, out = self.run_finalize(app)
        self.assertEqual((code, app.count("POST", "/paper/mvp/stop"), app.run["running"]), (1, 0, False))

    def test_finalize_invalid_status_after_account_select_prevents_settings_put_and_start(self):
        base_doc = status_doc()
        bad = (dict(state="idle"), dict(running=None, state="idle"), dict(running=0, state="idle"), dict(running="false", state="idle"))
        for run in bad:
            with self.subTest(run=run):
                app = SyntheticApp()
                base = app.status
                app.opener.routes[("GET", "/paper/mvp/status")] = lambda app=app, base=base, run=run: (
                    (200, dict(base_doc, run=run)) if app.prepared else base())
                code, out = self.run_finalize(app)
                self.assertEqual(code, 1)
                self.assertIn("FAIL 4", out)
                self.assertEqual((app.count("PUT", "/paper/mvp/settings"), app.count("POST", "/paper/mvp/start"),
                                  app.count("POST", "/paper/mvp/stop")), (0, 0, 0))
        for bad_doc in ((200, "garbage"), (200, dict(base_doc, data_source="NONE")), (500, {})):   # same gate: unreadable / not SYNTHETIC
            with self.subTest(doc=bad_doc):
                app = SyntheticApp()
                base = app.status
                app.opener.routes[("GET", "/paper/mvp/status")] = lambda app=app, base=base, bad_doc=bad_doc: bad_doc if app.prepared else base()
                code, out = self.run_finalize(app)
                self.assertEqual(code, 1)
                self.assertEqual((app.count("PUT", "/paper/mvp/settings"), app.count("POST", "/paper/mvp/start")), (0, 0))

    def test_finalize_failed_final_get_with_a_plausible_body_makes_no_ordinary_write_and_leaves_evidence(self):
        trade = dict(status="FILLED", side="BUY", symbol="A", quantity=1, price="1", fee="0", tax="0", realized=None, source="STRATEGY")
        trades = [trade, dict(trade, side="SELL", realized="0")]
        for failing in (500, 503):
            app = SyntheticApp()
            base = app.status
            reads = []

            def status(app=app, base=base, failing=failing, reads=reads):
                if not app.run["running"]:
                    return base()
                reads.append(1)
                doc = status_doc(run=dict(running=True, state="running", exit_monitoring=True, label="x"),
                                 account=dict(cash="1", total_asset="1"), trades=trades, risk={})
                return (failing, doc) if len(reads) == 2 else (200, doc)   # loop read ok, the next GET (step 9b) fails once with a plausible body
            app.opener.routes[("GET", "/paper/mvp/status")] = status
            app.opener.routes[("GET", "/account")] = (200, {"cash": "1", "total_asset": "1"})
            app.opener.routes[("GET", "/orders")] = (200, [])
            code, out = self.run_finalize(app)
            self.assertEqual(code, 1)
            self.assertIn("FAIL 9b", out)
            self.assertIn("-- %d" % failing, out)
            self.assertNotIn(("GET", "/account"), app.opener.requests)    # nothing processed after the failed GET
            self.assertNotIn(("POST", "/auth/logout"), app.opener.requests)
            self.assertEqual(app.count("POST", "/paper/mvp/stop"), 1)     # only the ledger cleanup stop of our own run
            self.assertFalse(app.run["running"])


# ---------------------------------------------------------------------------------------------- engine valuation
class StubBroker:
    def __init__(self, held=(), managed=()):
        self.positions = [NS(symbol=s) for s in held]
        self.managed = list(managed)

    def account(self):
        return NS(positions=self.positions)

    def managed_lots(self):
        return self.managed


class RecordingFeed:
    def __init__(self, clock, prices=None, error=None, age=timedelta(0)):
        self.clock, self.prices, self.error, self.age = clock, prices or {}, error, age
        self.calls, self.prices_requested_at, self.unavailable = [], None, {}

    def read(self, symbols, valuation_symbols, period, now):
        self.calls.append((list(symbols), list(valuation_symbols)))
        if self.error:
            raise self.error
        self.prices_requested_at = self.clock() - self.age
        return dict(self.prices), {}


class UnmanagedValuation(unittest.TestCase):
    SYMBOL = "005930"

    def make(self, held=(SYMBOL,), **feed_kw):
        self.clock = VirtualClock(DAY + timedelta(minutes=25, seconds=5))
        self.market = MarketSimulator()
        self.broker = StubBroker(held)
        self.feed = RecordingFeed(self.clock, **feed_kw)
        self.engine = MovingAverageEngine(self.market, self.broker, interval_seconds=1, clock=self.clock, price_feed=self.feed)
        self.engine.candidate_provider = lambda now: []            # Scout found nothing
        self.engine.step = lambda **kw: self.fail("no candidate: nothing may be judged")
        self.engine.broker = self.broker
        self.before = self.market.quote(self.SYMBOL).price

    def poll(self):
        asyncio.run(self.engine.poll_market())

    def test_unmanaged_holding_is_revalued_with_zero_orders_and_no_fallback_to_all(self):
        self.make(prices={self.SYMBOL: Decimal("71500")})
        self.poll()
        self.assertEqual(self.market.quote(self.SYMBOL).price, Decimal("71500"))
        self.assertEqual(self.feed.calls, [([], [self.SYMBOL])])   # only the held symbol; never every stock
        self.assertEqual((self.engine.tick_count, self.engine._snapshots), (0, {}))
        self.assertFalse(hasattr(self.broker, "submit"))           # the stub has no order path: an order attempt would have raised

    def test_nothing_held_reads_nothing(self):
        self.make(held=())
        self.poll()
        self.assertEqual(self.feed.calls, [])

    def test_no_data_invalid_or_stale_prices_keep_the_old_valuation(self):
        for name, kw in (("no data", dict(prices={})), ("zero", dict(prices={self.SYMBOL: Decimal("0")})),
                         ("negative", dict(prices={self.SYMBOL: Decimal("-5")})), ("nan", dict(prices={self.SYMBOL: Decimal("NaN")})),
                         ("inf", dict(prices={self.SYMBOL: Decimal("Infinity")})),
                         ("stale", dict(prices={self.SYMBOL: Decimal("99999")}, age=timedelta(seconds=31))),
                         ("feed error", dict(error=RuntimeError("down")))):
            with self.subTest(name):
                self.make(**kw)
                self.poll()
                self.assertEqual(self.market.quote(self.SYMBOL).price, self.before)

    def test_only_held_symbols_are_updated_even_if_the_feed_returns_more(self):
        self.make(prices={self.SYMBOL: Decimal("71500"), "000660": Decimal("1")})
        other = self.market.quote("000660").price
        self.poll()
        self.assertEqual(self.market.quote("000660").price, other)

    def test_market_hours_gate_still_blocks_valuation(self):
        self.make(prices={self.SYMBOL: Decimal("71500")})
        self.clock.advance(hours=10)
        self.poll()
        self.assertEqual(self.feed.calls, [])
        self.assertEqual(self.market.quote(self.SYMBOL).price, self.before)


# ---------------------------------------------------------------------------------------------- management scope (DB)
try:
    from test_paper_mvp import MvpBase
    from auto_trader.models import OrderRequest
    from auto_trader.paper_mvp import MvpConflict
except Exception:                                                                  # pragma: no cover
    MvpBase = None

if MvpBase is not None:
    class ManagementScopeRecovery(MvpBase):
        def holding(self):
            self.broker.submit(OrderRequest(symbol="005930", side="BUY", quantity=3), source="MANUAL")

        def test_non_auto_scope_is_visible_refuses_start_and_recovers_explicitly_without_touching_the_account(self):
            self.holding()
            self.engine.set_management_scope("ALL")                                # the pre-existing / legacy state
            self.assertEqual(self.mvp.status()["management_scope"], "ALL")
            with self.assertRaises(MvpConflict):
                run_start(self.mvp)
            before = (self.broker.account().cash, [(p.symbol, p.quantity) for p in self.broker.account().positions], len(self.broker.orders()))
            self.engine.set_management_scope("AUTO")                               # what PUT /paper/management does
            after = (self.broker.account().cash, [(p.symbol, p.quantity) for p in self.broker.account().positions], len(self.broker.orders()))
            self.assertEqual(before, after)                                        # account, holdings and history preserved
            status = self.mvp.status()
            self.assertEqual(status["management_scope"], "AUTO")
            self.assertFalse(status["run"]["running"])                             # recovery never starts anything
            self.assertEqual(self.engine.tick_count, 0)

        def test_default_scope_is_auto(self):
            self.assertEqual(self.mvp.status()["management_scope"], "AUTO")

    def run_start(mvp):
        return asyncio.run(mvp.start())


if __name__ == "__main__":
    unittest.main()
