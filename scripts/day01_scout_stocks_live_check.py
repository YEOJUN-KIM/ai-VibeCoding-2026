"""One-shot, READ-ONLY check of the real Toss `/api/v1/stocks` and `/api/v1/stocks/all` responses
against the DAY01 Scout instrument-facts adapter.

NOT a worker, scheduler or trading tool. Requests: one OAuth token request (existing client path),
`/api/v1/stocks/all` (KOSPI and KOSDAQ, 2 GETs via the client directory) and `/api/v1/stocks`
(1-2 GETs of at most 50 symbols). A hard fence (`guard_toss_client`) allows only those paths and at
most 6 requests in total, ranking GETs included in the cap but NOT allowed. Representative symbols
come from a previously saved ranking log, so no ranking request is made. Nothing from the
environment, headers or tokens is printed or stored.

    python -B scripts/day01_scout_stocks_live_check.py --approve-readonly-stocks-fetch \
        --env-file <abs path to existing .env> --ranking-log <abs saved ranking .jsonl> --out-dir <abs dir>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from auto_trader import day01_scout as sc  # noqa: E402
from auto_trader import day01_scout_link as link  # noqa: E402
from auto_trader.day01_scout_facts import (  # noqa: E402
    InstrumentFactsSource, toss_directory_source, toss_stocks_source)
from auto_trader.day01_scout_source import ResearchLog, guard_toss_client, redact  # noqa: E402
from auto_trader.ml.day01 import Config  # noqa: E402

APPROVED = [("/api/v1/stocks", "GET"), ("/api/v1/stocks/all", "GET")]


def now():
    return datetime.now(timezone.utc)


def type_name(value):
    return "absent" if value is _ABSENT else type(value).__name__


_ABSENT = object()


def field_pattern(rows, getter):
    """Counter of (type, value-if-small) for a field across rows; absence is its own bucket."""
    out = Counter()
    for row in rows:
        value = getter(row)
        if value is _ABSENT:
            out["<absent>"] += 1
        elif isinstance(value, (bool, int)) or value is None:
            out[repr(value)] += 1
        elif isinstance(value, str):
            out[f"str:{value}" if len(value) <= 24 else "str:<long>"] += 1
        else:
            out[f"<{type(value).__name__}>"] += 1
    return dict(out)


def get(row, *path):
    value = row
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return _ABSENT
        value = value[key]
    return value


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--approve-readonly-stocks-fetch", action="store_true", required=True)
    ap.add_argument("--env-file", required=True)
    ap.add_argument("--ranking-log", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    stamp = now().strftime("%Y%m%dT%H%M%SZ")
    log = ResearchLog(out_dir / f"toss-stocks-{stamp}.jsonl", clock=now)

    saved = next(json.loads(line) for line in Path(args.ranking_log).read_text(encoding="utf-8").splitlines()
                 if json.loads(line)["kind"] == "fetch_ok")
    ranking_rows = saved["rows"]
    top_symbols = [r["symbol"] for r in sorted(ranking_rows, key=lambda r: r["rank"])[:50]]

    from auto_trader import settings as settings_module
    from auto_trader.toss import TossClient
    settings_module._load_env_file(Path(args.env_file))              # existing loader; contents never shown
    client = TossClient(client_id=os.environ.get("TOSS_CLIENT_ID", ""),
                        client_secret=os.environ.get("TOSS_CLIENT_SECRET", ""),
                        live_trading_enabled=False, timeout=8)
    if not client.configured:
        print(json.dumps({"status": "BLOCKED", "reason": "credentials not available to the existing loader"}))
        return 2
    counts = guard_toss_client(client, max_requests=6, also_allow=APPROVED, rankings=False)
    summary: dict = {"status": "RUNNING", "requests": counts, "log": str(log.path)}

    def finish(code):
        print(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
        return code

    try:
        directory_rows, directory_received = toss_directory_source(client)()
    except Exception as exc:
        summary.update(status="DIRECTORY_FAILED", error_type=type(exc).__name__,
                       status_code=getattr(exc, "status_code", None), error_code=getattr(exc, "error_code", None))
        return finish(3)
    by_symbol = {}
    for row in directory_rows:
        by_symbol.setdefault(str(row.get("symbol", "")).strip().upper(), []).append(row)
    summary["directory"] = {
        "rows": len(directory_rows), "receipt_time_known": directory_received is not None,
        "distinct_symbols": len(by_symbol), "duplicate_symbols": sum(1 for v in by_symbol.values() if len(v) > 1),
        "key_set": sorted({k for r in directory_rows for k in r}),
        "market": field_pattern(directory_rows, lambda r: get(r, "market")),
        "securityType": field_pattern(directory_rows, lambda r: get(r, "securityType")),
        "isCommonShare": field_pattern(directory_rows, lambda r: get(r, "isCommonShare")),
        "status": field_pattern(directory_rows, lambda r: get(r, "status")),
        "security_x_common": dict(Counter(f"{get(r, 'securityType')}|{get(r, 'isCommonShare')}" for r in directory_rows)),
    }

    # Representatives: the top-50 ranking symbols plus (not already included) a few of each kind seen.
    reps: list = []
    def pick(predicate, n):
        for row in directory_rows:
            symbol = str(row.get("symbol", "")).strip()
            if len(reps) >= 60 or n <= 0:
                return
            if symbol and symbol not in top_symbols and symbol not in reps and predicate(row):
                reps.append(symbol)
                n -= 1
    pick(lambda r: r.get("securityType") == "ETN", 3)
    pick(lambda r: r.get("securityType") == "ETF", 2)
    pick(lambda r: r.get("securityType") == "STOCK" and r.get("isCommonShare") is False, 3)
    pick(lambda r: r.get("securityType") == "STOCK" and r.get("isCommonShare") is True, 2)
    symbols = top_symbols + reps

    fetched: list = []

    def recording_source(chunk):
        rows = toss_stocks_source(client)(chunk)
        fetched.extend(rows)
        return rows

    obs_clock = now
    facts_source = InstrumentFactsSource(recording_source, clock=obs_clock,
                                         directory_source=lambda: (directory_rows, directory_received), log=log,
                                         max_skew=timedelta(days=1))   # diagnostic: ranking rows are SAVED, see below
    ranking_stamp = now()                                              # DIAGNOSTIC re-stamp of saved ranking rows
    try:
        facts, notes = facts_source.collect(symbols, ranking_rows=ranking_rows, ranking_received_at=ranking_stamp)
    except Exception as exc:
        summary.update(status="STOCKS_FAILED", error_type=type(exc).__name__)
        return finish(3)
    stocks_by = {}
    for row in fetched:
        stocks_by.setdefault(str(row.get("symbol", "")).strip().upper(), []).append(row)
    summary["stocks"] = {
        "requested": len(symbols), "rows": len(fetched), "distinct_symbols": len(stocks_by),
        "missing_symbols": [s for s in symbols if s.strip().upper() not in stocks_by],
        "duplicate_symbols": sum(1 for v in stocks_by.values() if len(v) > 1),
        "key_set": sorted({k for r in fetched for k in r}),
        "securityType": field_pattern(fetched, lambda r: get(r, "securityType")),
        "isCommonShare": field_pattern(fetched, lambda r: get(r, "isCommonShare")),
        "status": field_pattern(fetched, lambda r: get(r, "status")),
        "koreanMarketDetail": field_pattern(fetched, lambda r: get(r, "koreanMarketDetail")),
        "koreanMarketDetail.key_set": sorted({k for r in fetched if isinstance(r.get("koreanMarketDetail"), dict)
                                              for k in r["koreanMarketDetail"]}),
        "krxTradingSuspended": field_pattern(fetched, lambda r: get(r, "koreanMarketDetail", "krxTradingSuspended")),
        "liquidationTrading": field_pattern(fetched, lambda r: get(r, "koreanMarketDetail", "liquidationTrading")),
        "nxtTradingSuspended": field_pattern(fetched, lambda r: get(r, "koreanMarketDetail", "nxtTradingSuspended")),
        "security_x_common": dict(Counter(f"{get(r, 'securityType')}|{get(r, 'isCommonShare')}" for r in fetched)),
    }
    # directory vs stocks agreement on the shared static fields
    mismatch = Counter()
    for symbol, rows in stocks_by.items():
        other = by_symbol.get(symbol)
        if not other:
            mismatch["not_in_directory"] += 1
            continue
        for field in ("securityType", "isCommonShare", "name"):
            if get(rows[0], field) != get(other[0], field):
                mismatch[field] += 1
    summary["directory_vs_stocks_mismatch"] = dict(mismatch)
    summary["adapter"] = {"facts": len(facts), "notes": dict(Counter(code for _, code in notes))}

    # Scout (DIAGNOSTIC): saved real ranking rows re-stamped to now; liquidity/readiness/portfolio SYNTHETIC.
    as_of = now()
    snap = sc.snapshot_from_rankings(as_of - timedelta(seconds=12), ranking_rows, received_at=as_of - timedelta(seconds=3))
    obs = as_of - timedelta(seconds=1)
    day01 = Config()
    names = sorted({r["symbol"] for r in ranking_rows if r["rank"] <= 50})
    cfg = link.scout_config_for(day01, target_count=10)
    result = sc.select_watchlist(
        as_of, [snap], instruments=facts, config=cfg, risk_config=day01,
        liquidity=[sc.LiquidityFacts(s, obs, turnover="20000000000", volume="100000", spread_pct="0.5") for s in names],
        dayone=[sc.DayOneFacts(s, obs, completed_1m_bars=20, opening_range_ready=True, vwap_ready=True) for s in names],
        portfolio=sc.Portfolio(obs, cash="1000000", total_asset="1000000", **link.portfolio_limits_for(day01)))
    gate = Counter(r for e in result.exclusions for r in e.reasons)
    types = {}
    for fact in facts:
        types[fact.symbol] = f"{fact.security_type}|{fact.is_common_share}"
    summary["scout_diagnostic"] = {
        "label": "DIAGNOSTIC: ranking rows are the SAVED real 10:11 snapshot re-stamped to now (not a real receipt); "
                 "liquidity/readiness/portfolio are SYNTHETIC; instrument facts are REAL (from this run)",
        "status": result.status, "candidates": [(c.symbol, types.get(c.symbol)) for c in result.candidates],
        "exclusion_reason_counts": dict(gate), "shortage": list(result.shortage_reasons),
        "top50_type_mix": dict(Counter(types.get(s, "no-fact") for s in names)),
    }
    log.append("scout_diagnostic", **summary["scout_diagnostic"])
    # Sanitized research file for the requested symbols only (public market metadata, no headers/tokens).
    research = out_dir / f"toss-stocks-sample-{stamp}.json"
    research.write_text(json.dumps(redact({
        "note": "sanitized sample of real /stocks rows for requested symbols; no credentials",
        "rows": [r for r in fetched],
        "directory_sample": [by_symbol[s][0] for s in symbols if s.strip().upper() in by_symbol]}),
        ensure_ascii=False, indent=1), encoding="utf-8")
    summary["research_file"] = str(research)
    summary["status"] = "OK"
    return finish(0)


if __name__ == "__main__":
    raise SystemExit(main())
