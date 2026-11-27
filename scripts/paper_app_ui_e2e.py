"""PAPER-APP-UI-01 real-app-path check. Talks HTTP to an ISOLATED FOLIO app the way a browser would:
real login, session cookie, CSRF, the real /paper/mvp routes.
PR-FIX-01: fail closed. A loopback URL is not proof of isolation, so the script first logs in and requires the app's own isolation
contract (status.data_source == "SYNTHETIC", scripts/paper_e2e_guard.py) before ANY other request; no proxy is used and redirects are not
followed. The first failed check stops the run, so no business-state write (account select, settings) can follow a failed prerequisite.
The start probe (check 12) needs a dedicated app (FOLIO_E2E_EXCLUSIVE_APP=1, see RunLedger): it is sent only when the app is idle, and a run
that an unexpected start success leaves behind is stopped on every exit path; an ambiguous run is reported (nonzero) and never stopped.
  python scripts/paper_app_ui_e2e.py http://127.0.0.1:8766 mvptester <password> [out.json]
"""
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_e2e_guard import Client, RunLedger, Unsafe, exclusive_declared, prove_isolated  # noqa: E402


class Abort(Exception):
    pass


def main(argv, opener=None, opener2=None, exclusive=None):
    base, user, password = argv[1], argv[2], argv[3]
    exclusive = exclusive_declared() if exclusive is None else exclusive
    results = []
    ledger = None

    def check(name, ok, detail=""):
        results.append(bool(ok))
        print(("PASS " if ok else "FAIL ") + name + (" -- " + str(detail) if detail else ""))
        if not ok:
            raise Abort(name)

    code = 1
    try:
        op = Client(base, opener)
        if not exclusive:
            raise Unsafe("set FOLIO_E2E_EXCLUSIVE_APP=1 only for an app/account dedicated to this test (check 12 sends a start request)")
        csrf, _ = prove_isolated(op, user, password, require_idle=False)
        ledger = RunLedger(op, csrf, exclusive)
        print("PASS 0 target proven isolated synthetic app (login, CSRF, SYNTHETIC source, PAPER)")
        results.append(True)
        code = _flow(op, Client(op.base, opener2), argv, user, password, csrf, check, results, ledger)
    except Unsafe as exc:
        print("FAIL 0 refusing before any write: %s" % exc)
    except Abort:
        print("SUMMARY %d/%d (stopped at first failure)" % (sum(results), len(results) + 1))
    finally:                                          # also runs while an unexpected exception propagates (it is not replaced)
        problem = ledger.cleanup() if ledger else None
        if problem:
            print("FAIL cleanup -- " + problem)
            code = 1
    return code


def _flow(op, anon, argv, user, password, csrf, check, results, ledger):
    call = lambda c, *a, **k: c.call(*a, **k)   # noqa: E731
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
    check("5 login ok + CSRF (proved before any other request)", bool(csrf))
    s, page, _ = call(op, "GET", "/paper-mvp")
    check("6 logged-in /paper-mvp serves the UX-02 page", s == 200 and "운용 홈" in page and 'href="/live"' in page and 'href="/news"' in page, s)
    s, _, _ = call(op, "GET", "/static/paper-mvp.js")
    check("7 JS served", s == 200, s)
    for p in ("/live", "/stocks", "/long-term", "/news"):
        s, body, _ = call(op, "GET", p)
        check("8 menu target %s reachable" % p, s == 200 and 'href="/paper-mvp"' in body, s)

    s, _, _ = call(op, "PUT", "/paper/mvp/settings", {"budget": 1})
    check("9 settings PUT without CSRF denied", s in (401, 403), s)
    s = ledger.start("wrong")[0]                   # a start must go through the ledger: an unexpected success is then cleaned up
    check("10 start with wrong CSRF denied", s in (401, 403), s)

    s, st, _ = call(op, "GET", "/paper/mvp/status")
    check("11 status readable, synthetic badge source", s == 200 and st.get("data_source") == "SYNTHETIC", (s, st.get("data_source") if isinstance(st, dict) else st))
    s, st, _ = (*ledger.start(), None)             # idle + exclusive re-verified first; an active run means Unsafe, nothing sent
    check("12 start blocked until a PAPER account is prepared (start_guard)", s == 409 or s == 422, (s, st.get("detail") if isinstance(st, dict) else st))
    s, _, _ = call(op, "POST", "/paper/accounts/EXPERIMENT/select", None, csrf=csrf)
    check("13 experiment account selected (real endpoint)", s == 200, s)

    s, st, _ = call(op, "GET", "/paper/mvp/status")
    before_settings = st
    check("13b status readable before the settings write", s == 200 and isinstance(st, dict)
          and isinstance(st.get("settings"), dict) and isinstance(st.get("run"), dict) and isinstance(st.get("account"), dict), s)
    settings_body = dict(st["settings"], budget=5000000)
    s, st, _ = call(op, "PUT", "/paper/mvp/settings", settings_body, csrf=csrf)
    check("14 settings saved", s == 200, (s, st if s != 200 else ""))
    s, st2, _ = call(op, "GET", "/paper/mvp/status")
    check("15 settings re-read equal", s == 200 and st2["settings"]["budget"] == 5000000, s)
    check("15b stopped before start, cash==total and unchanged by settings",
          s == 200
          and before_settings["run"]["state"] in ("stopped", "ready", "idle")
          and before_settings["run"]["running"] is False
          and st2["run"]["state"] == before_settings["run"]["state"]
          and st2["run"]["running"] is False
          and Decimal(before_settings["account"]["cash"]) == Decimal(before_settings["account"]["total_asset"])
          == Decimal(st2["account"]["cash"]) == Decimal(st2["account"]["total_asset"]),
          {"before": {k: before_settings[k] for k in ("run", "account")},
           "after": {k: st2[k] for k in ("run", "account")}})
    print("STATUS KEYS:", sorted(st2.keys()))
    json.dump({"cookie_note": "none", "status": st2}, open(argv[4], "w"), ensure_ascii=False, indent=1) if len(argv) > 4 else None
    print("SUMMARY %d/%d" % (sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
