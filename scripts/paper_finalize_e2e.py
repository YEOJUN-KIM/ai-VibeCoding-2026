"""PAPER-FINALIZE-01 synthetic end-to-end through the REAL FOLIO app (auto_trader.main:app), isolated mode only.
Flow: login -> isolation proof -> /paper-mvp -> settings read/save -> account prepare -> start -> synthetic Scout -> virtual buy/sell ->
status/API consistency -> stop -> logout -> re-login. Never prints the password.
PR-FIX-01: fail closed. Loopback is not proof of isolation, so before any business-state write the script logs in and requires the
app's own isolation contract (status.data_source == "SYNTHETIC", see scripts/paper_e2e_guard.py). The first failed check stops the run
no later step can write after a failed prerequisite. Run ownership (scripts/paper_e2e_guard.py RunLedger): the app has no run id, so
the script starts only against an app the operator declares dedicated (FOLIO_E2E_EXCLUSIVE_APP=1) and stops a run only when it is provably
its own; on ANY exit path (check failure, exception, timeout) an owned run is stopped, an ambiguous one is reported (nonzero) and never stopped.
  python scripts/paper_finalize_e2e.py http://127.0.0.1:8766 <user> <password> [out.json] [timeout_seconds]
"""
import json
import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_e2e_guard import Client, RunLedger, Unsafe, exclusive_declared, prove_isolated  # noqa: E402

D = Decimal


class Abort(Exception):
    pass


def status_ok(s, st, *keys):
    """HTTP 200 and the isolation contract and a well-formed status object (run.running a real boolean, requested keys present)."""
    return (s == 200 and isinstance(st, dict) and st.get("data_source") == "SYNTHETIC" and isinstance(st.get("run"), dict)
            and isinstance(st["run"].get("running"), bool) and all(k in st for k in keys))


def main(argv, opener=None, opener2=None, exclusive=None):
    base, user, password = argv[1], argv[2], argv[3]
    exclusive = exclusive_declared() if exclusive is None else exclusive
    results = []
    ledger = None

    def check(name, ok, detail=""):
        results.append(bool(ok))
        print(("PASS " if ok else "FAIL ") + name + (" -- " + str(detail) if detail else ""), flush=True)
        if not ok:
            raise Abort(name)

    code = 1
    try:
        client = Client(base, opener)
        if not exclusive:
            raise Unsafe("set FOLIO_E2E_EXCLUSIVE_APP=1 only for an app/account dedicated to this test (this script starts and stops a run)")
        call = lambda c, method, path, body=None, csrf=None: c.call(method, path, body, csrf)[:2]   # noqa: E731
        csrf, st = prove_isolated(client, user, password)
        ledger = RunLedger(client, csrf, exclusive)
        print("PASS 0 target proven isolated synthetic app (login, CSRF, SYNTHETIC source, PAPER, idle)", flush=True)
        results.append(True)
        code = _flow(client, call, argv, user, password, csrf, check, results, ledger, opener2)
    except Unsafe as exc:
        print("FAIL 0 refusing before any write: %s" % exc, flush=True)
    except Abort:
        print("SUMMARY %d/%d (stopped at first failure)" % (sum(results), len(results) + 1), flush=True)
    finally:                                          # also runs while an unexpected exception propagates (it is not replaced)
        problem = ledger.cleanup() if ledger else None
        if problem:
            print("FAIL cleanup -- " + problem, flush=True)
            code = 1
    return code


def _flow(client, call, argv, user, password, csrf, check, results, ledger, opener2):
    op = client
    check("1 login + CSRF", bool(csrf))
    s, page = call(op, "GET", "/paper-mvp")
    check("2 /paper-mvp page (existing paper menu target)", s == 200 and "운용 홈" in page and 'href="/paper"' not in page and 'id="prepare"' in page, s)
    s, _ = call(op, "POST", "/paper/accounts/EXPERIMENT/select", None, csrf=csrf)
    check("3 experiment account prepared", s == 200, s)
    s, st = call(op, "GET", "/paper/mvp/status")
    check("4 status readable, SYNTHETIC badge source, stopped (running is a real False)", status_ok(s, st, "settings") and st["run"]["running"] is False
          and isinstance(st["settings"], dict), s)
    cfg = dict(st["settings"], budget=5000000)
    s, st = call(op, "PUT", "/paper/mvp/settings", cfg, csrf=csrf)
    check("5 settings saved", s == 200 and isinstance(st, dict) and isinstance(st.get("settings"), dict) and st["settings"].get("budget") == 5000000, s)
    s, _ = call(op, "PUT", "/paper/mvp/settings", dict(cfg, budget=1), None)
    check("6 settings without CSRF denied", s in (401, 403), s)
    start = time.time()
    s, st = ledger.start()[:2]                     # re-verifies idle/exclusive first; Unsafe means nothing was sent
    check("7 start", s == 200 and st["run"]["running"], (s, st if s != 200 else st["run"]["state"]))
    buy = sell = None
    deadline = time.time() + float(argv[5]) if len(argv) > 5 else time.time() + 240
    while time.time() < deadline:
        time.sleep(3)
        s, st = call(op, "GET", "/paper/mvp/status")
        check("8a status readable while waiting", s == 200 and isinstance(st, dict) and isinstance(st.get("trades"), list), s)
        filled = [t for t in st["trades"] if t["status"] == "FILLED"]
        buy = next((t for t in filled if t["side"] == "BUY"), None)
        sell = next((t for t in filled if t["side"] == "SELL"), None)
        if buy and sell:
            break
    check("8 synthetic Scout produced a virtual BUY", buy is not None, buy and (buy["symbol"], buy["quantity"]))
    check("9 virtual SELL (exit rule) happened", sell is not None, sell and (sell["symbol"], sell["quantity"], sell["realized"]))
    s, st = call(op, "GET", "/paper/mvp/status")
    check("9b final status readable and well-formed before any further processing or write", status_ok(s, st, "account", "trades", "risk")
          and isinstance(st["account"], dict) and isinstance(st["trades"], list), s)
    acc = st["account"]
    s1, api_acc = call(op, "GET", "/account")
    s2, api_orders = call(op, "GET", "/orders")
    check("10 status account == /account (cash, total asset)", s1 == 200 and isinstance(api_acc, dict) and D(acc["cash"]) == D(str(api_acc["cash"])) and D(acc["total_asset"]) == D(str(api_acc["total_asset"])), (acc["cash"], api_acc.get("cash") if isinstance(api_acc, dict) else api_acc))
    check("11 status trades == /orders (count of fills)", s2 == 200 and isinstance(api_orders, list) and len([o for o in api_orders if o["status"] == "FILLED"]) == len([t for t in st["trades"] if t["status"] == "FILLED"]))
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
    check("17 stopped state persisted, exit monitoring off", status_ok(s, st, "account", "trades", "risk") and st["run"]["running"] is False and not st["run"].get("exit_monitoring", True), st["run"]["label"] if isinstance(st, dict) and isinstance(st.get("run"), dict) else s)
    ledger.mark_stopped(st)
    final = st
    s, _ = call(op, "POST", "/auth/logout", None, csrf=csrf)
    check("18 logout", s in (200, 204), s)
    s, _ = call(op, "GET", "/paper/mvp/status")
    check("19 after logout status denied", s == 401, s)
    op2 = Client(client.base, opener2)
    s, me2 = call(op2, "POST", "/auth/login", {"username": user, "password": password})
    check("20 re-login", s == 200, s)
    s, st = call(op2, "GET", "/paper/mvp/status")
    check("21 state survives re-login (same trades/cash)", status_ok(s, st, "account", "trades") and st["run"]["running"] is False and len(st["trades"]) == len(final["trades"]) and st["account"]["cash"] == final["account"]["cash"])
    if len(argv) > 4 and argv[4]:
        json.dump({"elapsed_s": round(time.time() - start), "account": final["account"], "trades": final["trades"], "run": final["run"], "risk": final["risk"]}, open(argv[4], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("SUMMARY %d/%d" % (sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
