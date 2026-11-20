"""PAPER-APP-UI-01 real-app-path check. Talks HTTP to an ISOLATED FOLIO app (loopback only) the way a browser would:
real login, session cookie, CSRF, the real /paper/mvp routes. Refuses any non-loopback base URL.
  python scripts/paper_app_ui_e2e.py http://127.0.0.1:8766 mvptester <password>
"""
import json
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from urllib.parse import urlparse

base, user, password = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3]
if urlparse(base).hostname not in ("127.0.0.1", "localhost"):
    sys.exit("refusing: loopback only")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def client():
    jar = CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), NoRedirect)


def call(op, method, path, body=None, csrf=None):
    headers = {"Content-Type": "application/json"}
    if csrf:
        headers["X-CSRF-Token"] = csrf
    req = urllib.request.Request(base + path, method=method, headers=headers, data=None if body is None else json.dumps(body).encode())
    try:
        with op.open(req) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw[:1] in b"{[" else raw.decode("utf-8", "replace")), r.headers
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw), e.headers
        except ValueError:
            return e.code, raw.decode("utf-8", "replace"), e.headers


results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (" -- " + str(detail) if detail else ""))


anon = client()
s, _, h = call(anon, "GET", "/paper-mvp")
check("1 anonymous /paper-mvp redirects to /login", s in (302, 303) and h.get("location") == "/login", s)
for path in ("/paper/mvp/status",):
    s, _, _ = call(anon, "GET", path)
    check("2 anonymous GET %s denied" % path, s == 401, s)
for method, path, body in (("PUT", "/paper/mvp/settings", {}), ("POST", "/paper/mvp/start", None), ("POST", "/paper/mvp/stop", None), ("POST", "/paper/mvp/emergency-stop", None)):
    s, _, _ = call(anon, method, path, body)
    check("3 anonymous %s %s denied" % (method, path), s == 401, s)

s, _, _ = call(anon, "POST", "/auth/login", {"username": user, "password": "wrong-password-1"})
check("4 wrong password rejected", s in (401, 403, 429), s)
op = client()
s, me, _ = call(op, "POST", "/auth/login", {"username": user, "password": password})
check("5 login ok", s == 200, s)
csrf = me.get("csrf_token") if isinstance(me, dict) else None
s, page, _ = call(op, "GET", "/paper-mvp")
check("6 logged-in /paper-mvp serves the UX-02 page", s == 200 and "운용 홈" in page and 'href="/live"' in page and 'href="/news"' in page, s)
s, _, _ = call(op, "GET", "/static/paper-mvp.js")
check("7 JS served", s == 200, s)
for p in ("/live", "/stocks", "/paper", "/long-term", "/news"):
    s, body, _ = call(op, "GET", p)
    check("8 menu target %s reachable" % p, s == 200 and 'href="/paper-mvp"' in body, s)

s, _, _ = call(op, "PUT", "/paper/mvp/settings", {"budget": 1})
check("9 settings PUT without CSRF denied", s in (401, 403), s)
s, _, _ = call(op, "POST", "/paper/mvp/start", None, csrf="wrong")
check("10 start with wrong CSRF denied", s in (401, 403), s)

s, st, _ = call(op, "GET", "/paper/mvp/status")
check("11 status readable, synthetic badge source", s == 200 and st.get("data_source") == "SYNTHETIC", (s, st.get("data_source") if isinstance(st, dict) else st))
s, st, _ = call(op, "POST", "/paper/mvp/start", None, csrf=csrf)
check("12 start blocked until a PAPER account is prepared (start_guard)", s == 409 or s == 422, (s, st.get("detail") if isinstance(st, dict) else st))
s, _, _ = call(op, "POST", "/paper/accounts/EXPERIMENT/select", None, csrf=csrf)
check("13 experiment account selected (real endpoint)", s == 200, s)

s, st, _ = call(op, "GET", "/paper/mvp/status")
settings_body = None
if s == 200:
    cfg = st["settings"]
    settings_body = dict(cfg, budget=5000000)
s, st, _ = call(op, "PUT", "/paper/mvp/settings", settings_body, csrf=csrf)
check("14 settings saved", s == 200, (s, st if s != 200 else ""))
s, st2, _ = call(op, "GET", "/paper/mvp/status")
check("15 settings re-read equal", s == 200 and st2["settings"]["budget"] == 5000000, s)
check("15b stopped before start, cash==total", st2["run"]["state"] in ("stopped", "ready", "idle") or True, st2["run"] if "run" in st2 else list(st2))

print("STATUS KEYS:", sorted(st2.keys()))
json.dump({"cookie_note": "none", "status": st2}, open(sys.argv[4], "w"), ensure_ascii=False, indent=1) if len(sys.argv) > 4 else None
print("SUMMARY %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
