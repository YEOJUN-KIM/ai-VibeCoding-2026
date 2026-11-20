"""PAPER-FINALIZE-01 synthetic end-to-end through the REAL FOLIO app (auto_trader.main:app), loopback + isolated mode only.
Flow: login -> /paper-mvp -> settings read/save -> account prepare -> start -> synthetic Scout -> virtual buy/sell ->
status/API consistency -> stop -> logout -> re-login. Never prints the password. Refuses non-loopback base URLs.
  python scripts/paper_finalize_e2e.py http://127.0.0.1:8766 <user> <password> [out.json]
"""
import json
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal
from http.cookiejar import CookieJar
from urllib.parse import urlparse

base, user, password = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3]
if urlparse(base).hostname not in ("127.0.0.1", "localhost"):
    sys.exit("refusing: loopback only")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def client():
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()), NoRedirect)


def call(op, method, path, body=None, csrf=None):
    headers = {"Content-Type": "application/json"}
    if csrf:
        headers["X-CSRF-Token"] = csrf
    req = urllib.request.Request(base + path, method=method, headers=headers, data=None if body is None else json.dumps(body).encode())
    try:
        with op.open(req) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw.decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw.decode("utf-8", "replace")


results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (" -- " + str(detail) if detail else ""), flush=True)


D = Decimal
op = client()
s, me = call(op, "POST", "/auth/login", {"username": user, "password": password})
check("1 login", s == 200, s)
csrf = me.get("csrf_token")
s, page = call(op, "GET", "/paper-mvp")
check("2 /paper-mvp page (existing paper menu target)", s == 200 and "운용 홈" in page and 'href="/paper"' in page, s)
s, _ = call(op, "POST", "/paper/accounts/EXPERIMENT/select", None, csrf=csrf)
check("3 experiment account prepared", s == 200, s)
s, st = call(op, "GET", "/paper/mvp/status")
check("4 status readable, SYNTHETIC badge source, stopped", s == 200 and st["data_source"] == "SYNTHETIC" and not st["run"]["running"], (s, st["run"]["state"] if s == 200 else st))
cfg = dict(st["settings"], budget=5000000)
s, st = call(op, "PUT", "/paper/mvp/settings", cfg, csrf=csrf)
check("5 settings saved", s == 200 and st["settings"]["budget"] == 5000000, s)
s, _ = call(op, "PUT", "/paper/mvp/settings", dict(cfg, budget=1), None)
check("6 settings without CSRF denied", s in (401, 403), s)
start = time.time()
s, st = call(op, "POST", "/paper/mvp/start", None, csrf=csrf)
check("7 start", s == 200 and st["run"]["running"], (s, st if s != 200 else st["run"]["state"]))
buy = sell = None
deadline = time.time() + float(sys.argv[5]) if len(sys.argv) > 5 else time.time() + 240
while time.time() < deadline:
    time.sleep(3)
    s, st = call(op, "GET", "/paper/mvp/status")
    filled = [t for t in st["trades"] if t["status"] == "FILLED"]
    buy = next((t for t in filled if t["side"] == "BUY"), None)
    sell = next((t for t in filled if t["side"] == "SELL"), None)
    if buy and sell:
        break
check("8 synthetic Scout produced a virtual BUY", buy is not None, buy and (buy["symbol"], buy["quantity"]))
check("9 virtual SELL (exit rule) happened", sell is not None, sell and (sell["symbol"], sell["quantity"], sell["realized"]))
s, st = call(op, "GET", "/paper/mvp/status")
acc = st["account"]
s1, api_acc = call(op, "GET", "/account")
s2, api_orders = call(op, "GET", "/orders")
check("10 status account == /account (cash, total asset)", s1 == 200 and D(acc["cash"]) == D(str(api_acc["cash"])) and D(acc["total_asset"]) == D(str(api_acc["total_asset"])), (acc["cash"], api_acc.get("cash")))
check("11 status trades == /orders (count of fills)", s2 == 200 and len([o for o in api_orders if o["status"] == "FILLED"]) == len([t for t in st["trades"] if t["status"] == "FILLED"]))
check("12 total = cash + holdings value; profit = total - initial", D(acc["total_asset"]) == D(acc["cash"]) + D(acc["holdings_value"]) and D(acc["total_profit"]) == D(acc["total_asset"]) - D(acc["initial_cash"]))
sells = sum((D(t["realized"]) for t in st["trades"] if t["status"] == "FILLED" and t["side"] == "SELL" and t["realized"] is not None), D(0))
check("13 sum of order realized == account realized", abs(sells - D(acc["realized_profit"])) < D("0.01"), (str(sells), acc["realized_profit"]))
fills = [t for t in st["trades"] if t["status"] == "FILLED"]
cash = D(acc["initial_cash"]) + sum((-(D(t["price"]) * t["quantity"]) - D(t["fee"]) if t["side"] == "BUY" else D(t["price"]) * t["quantity"] - D(t["fee"]) - D(t["tax"]) for t in fills), D(0))
check("14 cash == initial +/- every fill (history reproduces cash)", abs(cash - D(acc["cash"])) < D("0.01"), (str(cash), acc["cash"]))
check("15 all trades come from the automatic run (no manual/LIVE source)", all(t["source"] == "STRATEGY" for t in st["trades"]), {t["source"] for t in st["trades"]})
s, st = call(op, "POST", "/paper/mvp/stop", None, csrf=csrf)
check("16 stop", s == 200 and not st["run"]["running"], (s, st["run"]["state"] if s == 200 else st))
s, st = call(op, "GET", "/paper/mvp/status")
check("17 stopped state persisted, exit monitoring off", s == 200 and not st["run"]["running"] and not st["run"]["exit_monitoring"], st["run"]["label"])
final = st
s, _ = call(op, "POST", "/auth/logout", None, csrf=csrf)
check("18 logout", s in (200, 204), s)
s, _ = call(op, "GET", "/paper/mvp/status")
check("19 after logout status denied", s == 401, s)
op2 = client()
s, me2 = call(op2, "POST", "/auth/login", {"username": user, "password": password})
check("20 re-login", s == 200, s)
s, st = call(op2, "GET", "/paper/mvp/status")
check("21 state survives re-login (same trades/cash)", s == 200 and len(st["trades"]) == len(final["trades"]) and st["account"]["cash"] == final["account"]["cash"] and not st["run"]["running"])
if len(sys.argv) > 4 and sys.argv[4]:
    json.dump({"elapsed_s": round(time.time() - start), "account": final["account"], "trades": final["trades"], "run": final["run"], "risk": final["risk"]}, open(sys.argv[4], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("SUMMARY %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
