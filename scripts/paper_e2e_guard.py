"""Shared fail-closed guard for the PAPER end-to-end scripts (PR-FIX-01).

A loopback URL alone does not prove the target is the isolated synthetic app (a developer's real server is loopback too).
Before ANY business-state write the scripts therefore (1) accept only an http loopback base URL, (2) talk to it without
proxies and without following redirects, (3) log in, and (4) read the app's own isolation contract: the authenticated
GET /paper/mvp/status reports data_source == "SYNTHETIC" only when the server runs the isolated mode
(paper_mvp_isolated.active(): throwaway folio_mvp_test* database, loopback DB host, non-default port, no Toss credentials).
Any failure raises Unsafe and the scripts stop; nothing here prints the password or any server secret.
"""
import ipaddress
import json
import os
import socket
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from urllib.parse import urlparse

READ_ONLY = ("GET", "HEAD")
EXCLUSIVE_ENV = "FOLIO_E2E_EXCLUSIVE_APP"
TIMEOUT = 15.0


class Unsafe(Exception):
    """The target is not proven to be the isolated synthetic app, or a prerequisite failed: stop before writing."""


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    if host != "localhost":
        return False
    try:
        return all(ipaddress.ip_address(info[4][0]).is_loopback for info in socket.getaddrinfo(host, None))
    except (OSError, ValueError):
        return False


def safe_base(url: str) -> str:
    """Return a normalised http://host[:port] for a loopback target, otherwise raise Unsafe."""
    parts = urlparse(url)
    try:
        port = parts.port
    except ValueError:
        raise Unsafe("invalid port in base URL")
    if parts.scheme != "http" or not parts.hostname or parts.username or parts.password:
        raise Unsafe("base URL must be plain http://<loopback host>[:port] without credentials")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise Unsafe("base URL must not carry a path, query or fragment")
    if not _is_loopback(parts.hostname):
        raise Unsafe("refusing: loopback only")
    host = "[%s]" % parts.hostname if ":" in parts.hostname else parts.hostname
    return "http://%s%s" % (host, ":%d" % port if port else "")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def make_opener():
    """Cookie session, empty proxy map (HTTP(S)/ALL_PROXY must never carry loopback test traffic), no redirects."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(CookieJar()), NoRedirect)


class Client:
    def __init__(self, base: str, opener=None, timeout: float = TIMEOUT):
        self.base = safe_base(base)
        self.opener = opener or make_opener()
        self.timeout = timeout

    def call(self, method, path, body=None, csrf=None):
        headers = {"Content-Type": "application/json"}
        if csrf:
            headers["X-CSRF-Token"] = csrf
        req = urllib.request.Request(self.base + path, method=method, headers=headers,
                                     data=None if body is None else json.dumps(body).encode())
        try:
            with self.opener.open(req, timeout=self.timeout) as r:
                status, raw, response_headers = r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            status, raw, response_headers = e.code, e.read(), e.headers
        try:
            parsed = json.loads(raw) if raw[:1] in (b"{", b"[") else raw.decode("utf-8", "replace")
        except ValueError:
            parsed = raw.decode("utf-8", "replace")
        return status, parsed, response_headers


def prove_isolated(client: Client, user: str, password: str, *, require_idle: bool = True):
    """Log in and prove isolation. Only login (a session, no business state) and GETs happen here.
    Returns (csrf_token, status_json). Raises Unsafe on any failed prerequisite."""
    status, me, _ = client.call("POST", "/auth/login", {"username": user, "password": password})
    if status != 200 or not isinstance(me, dict) or not me.get("csrf_token"):
        raise Unsafe("login failed or returned no CSRF token (status %s)" % status)
    csrf = me["csrf_token"]
    status, st, _ = client.call("GET", "/paper/mvp/status")
    if status != 200 or not isinstance(st, dict):
        raise Unsafe("status not readable (status %s)" % status)
    if st.get("data_source") != "SYNTHETIC":
        raise Unsafe("target is not the isolated synthetic app (data_source != SYNTHETIC)")
    if st.get("mode") != "PAPER" or st.get("live_blocked") is not True:
        raise Unsafe("target does not report PAPER with LIVE blocked")
    run = st.get("run")
    if not isinstance(run, dict) or not isinstance(run.get("running"), bool):
        raise Unsafe("status malformed: run.running is missing or not a boolean")
    if require_idle:
        if run["running"]:
            raise Unsafe("precondition failed: a run is already active")
        if st.get("emergency"):
            raise Unsafe("precondition failed: emergency latch is set")
    return csrf, st


def exclusive_declared(env=None) -> bool:
    """The operator's explicit statement that the target app/account/process is dedicated to this test run (nobody else can start,
    stop or replace its automatic run). The app exposes no run id, so this declaration is the ownership root; see RunLedger."""
    return (os.environ if env is None else env).get(EXCLUSIVE_ENV) == "1"


def definitely_rejected(status) -> bool:
    """Only a 4xx answer (except 408 Request Timeout / 425 Too Early) proves the server refused the start before acting on it.
    5xx, 2xx other than the expected one, 3xx and anything unusual leave the outcome unknown: the server may have started the run."""
    return isinstance(status, int) and 400 <= status < 500 and status not in (408, 425)


def _verified_status(client: Client):
    """GET the status and re-check the isolation contract. Returns (status_json, None) or (None, reason)."""
    status, st, _ = client.call("GET", "/paper/mvp/status")
    if status != 200 or not isinstance(st, dict) or not isinstance(st.get("run"), dict) or not isinstance(st["run"].get("running"), bool):
        return None, "status not readable or malformed (status %s; run.running must be a boolean)" % status
    if st.get("data_source") != "SYNTHETIC" or st.get("mode") != "PAPER" or st.get("live_blocked") is not True:
        return None, "target no longer reports the isolated synthetic PAPER contract"
    return st, None


class RunLedger:
    """Fail-closed ownership of the ONE automatic run this script may start, and its cleanup.

    The existing API has no run id: HTTP 200 on start is also returned for a duplicate/idempotent start, `running` is also true for a
    run somebody else started, and an idle observation before start does not exclude a concurrent start. Ownership therefore cannot be
    proven from responses alone. It is claimed only when ALL hold: the operator declared an exclusive dedicated app (EXCLUSIVE_ENV=1),
    a fresh status immediately before our start was idle without an emergency latch, and our start was not definitively rejected
    (HTTP 200, or an unknown outcome such as a timeout, where the server may still have processed it). Otherwise no stop is ever sent
    and an active run is reported as ambiguous. A replacement run cannot be told apart from ours without a run id: that is why the
    exclusive declaration is mandatory. Cleanup never clears the emergency latch and never stops a run in state "other"."""

    def __init__(self, client: Client, csrf: str, exclusive: bool):
        self.client, self.csrf, self.exclusive = client, csrf, bool(exclusive)
        self.attempted = False      # a start request may have reached the server
        self.owned = False
        self.released = False       # our stop was sent and a following status confirmed the run is not active

    def start(self, csrf=None):
        """Verify every start prerequisite, then send ONE start (csrf overrides the token, e.g. a deliberately wrong one). Raises Unsafe (nothing sent) when a prerequisite fails."""
        if not self.exclusive:
            raise Unsafe("start refused: set %s=1 only for an app/account dedicated to this test (run ownership cannot be proven otherwise)" % EXCLUSIVE_ENV)
        st, reason = _verified_status(self.client)
        if st is None:
            raise Unsafe("start refused: " + reason)
        if st["run"].get("running") or st["run"].get("state") == "other":
            raise Unsafe("start refused: a run is already active (not ours)")
        if st.get("emergency"):
            raise Unsafe("start refused: emergency latch is set")
        self.attempted = True
        self.owned = True                    # unknown outcome (timeout/network error) stays "owned": the server may have started it
        status, body, _ = self.client.call("POST", "/paper/mvp/start", None, self.csrf if csrf is None else csrf)
        if definitely_rejected(status):
            self.owned = False               # definitively rejected (4xx): nothing of ours is running
        # HTTP 200 -> started; 5xx / other non-4xx -> UNKNOWN outcome, ownership is retained so cleanup can stop a run the server did start
        return status, body

    def mark_stopped(self, st):
        """Call after a status that follows our stop request and shows the run is not active."""
        if isinstance(st, dict) and isinstance(st.get("run"), dict) and st["run"].get("running") is False:
            self.released = True

    def cleanup(self):
        """Stop our own run if (and only if) it is provably ours. Returns None when nothing is left behind, else a problem text
        (the caller reports it and exits nonzero). Never raises."""
        if not self.attempted or self.released:
            return None
        # Ownership here rests only on the operator's exclusive-app declaration + the idle, emergency-free status verified right before
        # our start request; the app has no run id, so it is re-checked below (state, emergency, isolation contract) and never assumed.
        try:
            st, reason = _verified_status(self.client)
            if st is None:
                return "CLEANUP UNVERIFIED: %s; no stop sent, a run started by this script may still be active" % reason
            run = st["run"]
            if run["running"] is False:
                return None                  # nothing active (an emergency latch, if any, is left untouched)
            if not self.owned:
                return "CLEANUP AMBIGUOUS: a run is active but this script's start was rejected; not ours, no stop sent"
            if st.get("emergency") or run.get("state") != "running":
                return "CLEANUP AMBIGUOUS: active run is in state %r / emergency=%s, not provably ours; no stop sent" % (run.get("state"), bool(st.get("emergency")))
            status, _, _ = self.client.call("POST", "/paper/mvp/stop", None, self.csrf)
            after, reason = _verified_status(self.client)
            if after is None or after["run"].get("running"):
                return "CLEANUP FAILED: stop returned %s and the run is %s" % (status, "unverifiable" if after is None else "still active")
            self.released = True
            return None
        except Exception as exc:
            return "CLEANUP FAILED: %s: %s" % (type(exc).__name__, exc)
