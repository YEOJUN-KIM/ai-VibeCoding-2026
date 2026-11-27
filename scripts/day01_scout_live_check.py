"""One-shot, READ-ONLY check of the real Toss domestic trading-amount ranking against DAY01 Scout.

NOT a worker, scheduler or trading tool. It performs a OAuth token request (existing client
path) and INTENDS two ranking GETs (a re-read inside 30 s is a cache hit; the next real GET is
31 s later). The client may retry a 429/401 itself, so a hard fence (`guard_toss_client`) allows
only the token and ranking endpoints and at most 6 requests in total, then feeds the result to Scout together with CLEARLY LABELED SYNTHETIC instrument,
liquidity, readiness and portfolio facts. It never calls account, order, holdings or any
write endpoint, never opens a database, and never prints credentials.

Credentials come from the project's existing settings loader at runtime (the file is parsed by
the program, never displayed). Nothing from the environment is printed or logged.

    python -B scripts/day01_scout_live_check.py --approve-readonly-ranking-fetch \
        --env-file <absolute path to the existing .env> --out-dir <absolute isolated research dir>
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from auto_trader import day01_scout as sc  # noqa: E402
from auto_trader import day01_scout_link as link  # noqa: E402
from auto_trader.day01_scout_source import (  # noqa: E402
    RankingFetcher, ResearchLog, guard_toss_client, toss_ranking_source)
from auto_trader.ml.day01 import Config  # noqa: E402


def now():
    return datetime.now(timezone.utc)


def key_paths(value, prefix=""):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from key_paths(v, f"{prefix}{k}.")
    else:
        yield prefix.rstrip(".")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--approve-readonly-ranking-fetch", action="store_true", required=True)
    ap.add_argument("--env-file", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    stamp = now().strftime("%Y%m%dT%H%M%SZ")
    log = ResearchLog(out_dir / f"toss-ranking-{stamp}.jsonl", clock=now)

    from auto_trader import settings as settings_module
    from auto_trader.toss import TossApiError, TossClient
    settings_module._load_env_file(Path(args.env_file))          # existing loader; contents never shown
    import os
    client = TossClient(client_id=os.environ.get("TOSS_CLIENT_ID", ""),
                        client_secret=os.environ.get("TOSS_CLIENT_SECRET", ""),
                        live_trading_enabled=False, timeout=8)
    if not client.configured:
        print(json.dumps({"status": "BLOCKED", "reason": "credentials not available to the existing loader"}))
        return 2

    request_counts = guard_toss_client(client, max_requests=6)     # only /oauth2/token + /api/v1/rankings
    fetcher = RankingFetcher(toss_ranking_source(client, budget_seconds=15), clock=now, log=log)
    state = sc.ScoutState.empty()
    summary = {"log": str(log.path), "fetches": [], "requests": request_counts}
    for index in range(3):
        if index == 2:
            time.sleep(31)                                          # let the 30 s client cache expire
        try:
            state, outcome = sc.ingest_fetch(state, fetcher, clock=now)
        except TossApiError as exc:                                 # not expected: ingest_fetch catches
            print(json.dumps({"status": "FAILED", "type": type(exc).__name__}))
            return 3
        summary["fetches"].append({"status": outcome.status, "reason": outcome.reason})
        if outcome.status == "FETCH_FAILED":
            summary["status"] = "FETCH_FAILED"
            print(json.dumps(summary, ensure_ascii=False, indent=1))
            return 3
    snaps = sorted(state.snapshots, key=lambda s: s.received_at)
    first = snaps[0]
    raw = next(json.loads(line) for line in log.path.read_text(encoding="utf-8").splitlines()
               if json.loads(line)["kind"] == "fetch_ok")
    rows = raw["rows"]
    summary.update(
        status="OK", snapshots_in_state=len(snaps),
        provider_ranked_at_raw=raw["provider_ranked_at_raw"], received_at=raw["received_at"],
        raw_row_count=raw["raw_row_count"], parsed_rows=len(first.rows), defects=list(first.defects),
        row_key_paths=sorted(set(p for r in rows for p in key_paths(r))),
        rank_sequence_complete=sorted(r.rank for r in first.rows) == list(range(1, len(first.rows) + 1)),
        duplicate_symbols=[s for s, n in Counter(r.symbol for r in first.rows).items() if n > 1],
        cache_hit_logged=any(json.loads(line)["kind"] == "fetch_cache_hit"
                             for line in log.path.read_text(encoding="utf-8").splitlines()),
        provider_lag_seconds=(None if not first.provider_ranked_at
                              else (first.received_at - first.provider_ranked_at).total_seconds()),
        price_le_9990=sum(1 for r in first.rows if r.rank <= 50 and r.price is not None and r.price <= 9990),
        top50_price_missing=sum(1 for r in first.rows if r.rank <= 50 and r.price is None),
    )

    # Scout: ranking is REAL; every other fact is SYNTHETIC "eligible" (labeled in the log).
    as_of = now()
    obs = as_of
    symbols = sorted({r.symbol for s in snaps for r in s.rows})   # facts cover every snapshot's symbols
    for label, day01 in (("day01_default_qty10", Config()),
                         ("sensitivity_qty1", Config(order_quantity=1))):
        cfg = link.scout_config_for(day01, target_count=10)
        pf = sc.Portfolio(obs, cash="1000000", total_asset="1000000", **link.portfolio_limits_for(day01))
        result = sc.select_watchlist(
            as_of, state.snapshots, config=cfg, risk_config=day01, portfolio=pf,
            instruments=[sc.InstrumentFacts(s, obs, market="KOSPI", currency="KRW", security_type="STOCK",
                                            is_common_share=True, status="ACTIVE", krx_trading_suspended=False,
                                            liquidation_trading=False) for s in symbols],
            liquidity=[sc.LiquidityFacts(s, obs, turnover="20000000000", volume="100000", spread_pct="0.5")
                       for s in symbols],
            dayone=[sc.DayOneFacts(s, obs, completed_1m_bars=20, opening_range_ready=True, vwap_ready=True)
                    for s in symbols])
        log.record_scout_result(result, scenario=label,
                                fact_provenance="RANKING=REAL; instrument/liquidity/dayone/portfolio=SYNTHETIC")
        tally = Counter(reason for e in result.exclusions for reason in e.reasons)
        summary[label] = {"status": result.status, "candidates": len(result.candidates),
                          "candidate_symbols": [c.symbol for c in result.candidates],
                          "shortage": list(result.shortage_reasons), "exclusion_reasons": dict(tally),
                          "coverage": sc.to_dict(result.coverage), "facts": "SYNTHETIC (only ranking is real)"}
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
