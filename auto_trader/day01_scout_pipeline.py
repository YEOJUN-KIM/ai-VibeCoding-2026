"""Pure DAY-01 research pipeline: ranking + candle readiness (CANDLE-01) + order-book spread (ORDERBOOK-01)
-> Scout `select_watchlist` -> per-symbol PASS / EXCLUDED report and a PROCEED / HOLD decision.

Glue only. No I/O, clock, settings, DB, broker or network; no new strategy and no new thresholds. Every gate is
the existing one: the ranking comes from `RankingSnapshot`, candle readiness from `build_market_inputs`, the
spread from `with_spread`, selection from `select_watchlist`, and the amount/quantity/loss limits are the DAY-01
`Config` numbers (checked with `limit_mismatches`; a mismatch is an error, never a silent difference).

Fail-closed choices made here (each one is tested):
* `clock_uncertainty=None` (not declared) means HOLD. The candle checks depend on how far our clock may be
  ahead of the provider's; if the caller does not state it, no candidate may pass. `timedelta(0)` is an explicit
  attestation that the clock is synchronised and is recorded as such.
* Only `target_count` / `ranking_depth` may be overridden for Scout; gates and limits cannot be loosened here.
* A missing, failed, stale-at-`as_of` or not-yet-received order book leaves the spread UNKNOWN, so Scout excludes
  the symbol. Ranking turnover is never used as order-book depth.
* PROCEED means at least one candidate and a declared clock. Anything else is HOLD with reasons.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .day01_scout import OK, ScoutResult, select_watchlist
from .day01_scout_link import limit_mismatches, scout_config_for
from .day01_scout_market import MarketInputs, build_market_inputs
from .day01_scout_orderbook import DEFAULT_MAX_AGE_SECONDS, OrderbookResult, with_spread
from .day01_scout_orderbook import OK as BOOK_OK

__all__ = ["SymbolDecision", "PipelineResult", "run_scout_pipeline", "PROCEED", "HOLD", "PASS", "EXCLUDED"]

PROCEED, HOLD = "PROCEED", "HOLD"
PASS, EXCLUDED = "PASS", "EXCLUDED"
_OVERRIDABLE = frozenset({"target_count", "ranking_depth"})


@dataclass(frozen=True)
class SymbolDecision:
    symbol: str | None            # None only for ranking rows whose symbol itself was unusable
    rank: int | None
    verdict: str                  # PASS | EXCLUDED
    reasons: tuple                # Scout exclusion reasons (empty for PASS)
    vwap_basis: str | None        # EXACT_TURNOVER | TYPICAL_APPROXIMATION | UNAVAILABLE | None (no candle report)
    candle_codes: tuple           # CANDLE-01 readiness codes (defects, gaps, late bars, ...)
    completed_1m_bars: int | None
    orderbook: str                # OK_USED | NONE | <status>:<reason,..> | NOT_YET_RECEIVED | STALE_AT_AS_OF
    spread_pct: Any               # Decimal when the book was used, else None
    notes: tuple                  # e.g. NOT_IN_COLLECTION_UNIVERSE, APPROXIMATE_VWAP
    fully_eligible: bool = False
    verification_gaps: tuple = ()


@dataclass(frozen=True)
class PipelineResult:
    as_of: datetime
    decision: str                 # PROCEED | HOLD
    hold_reasons: tuple
    clock_declared: bool
    scout: ScoutResult            # the unmodified Scout result (feed it to `WatchTimeline`)
    symbols: tuple                # SymbolDecision per ranking row inside the depth
    passed: tuple                 # symbols with verdict PASS
    candles: tuple                # DAY-01 Candle objects available <= as_of
    notes: tuple                  # market/orderbook notes (symbol | None, code)
    exact_vwap_symbols: tuple     # passed symbols whose VWAP is exact turnover based
    approximate_vwap_symbols: tuple


def _aware(value, name) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _apply_orderbooks(liquidity, results, as_of, max_age):
    """Fill spreads from order books judged AT as_of. Returns (liquidity, status_by_symbol, notes)."""
    notes, by_symbol, ambiguous = [], {}, set()
    for result in results:
        if not isinstance(result, OrderbookResult):
            raise ValueError("OrderbookResult objects required")
        if result.symbol in by_symbol:
            ambiguous.add(result.symbol)
            notes.append((result.symbol, "ORDERBOOK_DUPLICATE_RESULT"))
        by_symbol[result.symbol] = result
    known = {f.symbol for f in liquidity}
    for symbol in sorted(set(by_symbol) - known):
        notes.append((symbol, "ORDERBOOK_SYMBOL_NOT_IN_RANKING"))
    out, status = [], {}
    for fact in liquidity:
        result = by_symbol.get(fact.symbol)
        if result is None:
            out.append(fact)
            status[fact.symbol] = "NONE"
        elif fact.symbol in ambiguous:
            out.append(fact)                       # conflicting results: use neither
            status[fact.symbol] = "AMBIGUOUS"
        else:
            filled = with_spread(fact, result, as_of=as_of, max_age_seconds=max_age)
            out.append(filled)
            if filled.spread_pct is not None:
                status[fact.symbol] = "OK_USED"
            elif result.status != BOOK_OK:
                status[fact.symbol] = f"{result.status}:{','.join(result.reasons)}"
            elif result.received_at > as_of:
                status[fact.symbol] = "NOT_YET_RECEIVED"
            else:
                status[fact.symbol] = "STALE_AT_AS_OF"
    return tuple(out), status, notes


def run_scout_pipeline(*, as_of: datetime, snapshot, candle_rows: Iterable, orderbook_results: Iterable = (),
                       instruments=(), portfolio, day01_config, clock_uncertainty: timedelta | None = None,
                       collection_universe: Iterable[str] | None = None, allow_approximate_vwap: bool = False,
                       orderbook_max_age_seconds: Any = DEFAULT_MAX_AGE_SECONDS,
                       scout_overrides: dict | None = None) -> PipelineResult:
    """One decision at `as_of` from data that exists at `as_of` (later rows/books/facts are ignored by the
    underlying adapters and by Scout). Raises ValueError for bad arguments or when the portfolio/Scout limits
    differ from the DAY-01 `Config`; bad DATA never raises, it excludes or holds."""
    moment = _aware(as_of, "as_of")
    overrides = dict(scout_overrides or {})
    if set(overrides) - _OVERRIDABLE:
        raise ValueError("only target_count and ranking_depth may be overridden: " + ",".join(sorted(set(overrides) - _OVERRIDABLE)))
    scout_cfg = scout_config_for(day01_config, **overrides)          # validates the DAY-01 config
    mismatch = limit_mismatches(day01_config, scout_cfg, portfolio, day01_config)
    if mismatch:
        raise ValueError("portfolio/Scout limits differ from the DAY-01 config: " + ",".join(mismatch))

    declared = clock_uncertainty is not None
    uncertainty = clock_uncertainty if declared else timedelta(0)
    names = sorted({r.symbol for r in snapshot.rows} | {p.symbol for p in portfolio.positions})
    inputs: MarketInputs = build_market_inputs(
        snapshot, list(candle_rows), as_of=as_of, symbols=names, config=day01_config,
        allow_approximate_vwap=allow_approximate_vwap, clock_uncertainty=uncertainty)
    liquidity, book_status, book_notes = _apply_orderbooks(
        inputs.liquidity, tuple(orderbook_results), moment, orderbook_max_age_seconds)

    # No declared clock bound -> Scout gets no readiness facts, so nothing can pass (fail-closed).
    scout = select_watchlist(as_of, [snapshot], instruments=instruments, liquidity=liquidity,
                             dayone=inputs.dayone if declared else (), portfolio=portfolio,
                             config=scout_cfg, risk_config=day01_config)

    ready = {r.symbol: r for r in inputs.readiness}
    passed_by = {c.symbol: c for c in scout.candidates}
    excluded_by: dict = {}
    anonymous = []
    for item in scout.exclusions:
        if item.symbol is None:
            anonymous.append(item)
        else:
            excluded_by.setdefault(item.symbol, []).extend(item.reasons)
    universe = None if collection_universe is None else {s.strip().upper() for s in collection_universe}

    rows = []
    if snapshot.received_at.astimezone(timezone.utc) <= moment:          # a ranking received later does not exist yet
        seen = set()
        for row in snapshot.rows:
            if row.rank > scout_cfg.ranking_depth or row.symbol in seen:
                continue
            seen.add(row.symbol)
            rows.append(row)
    decisions = []
    for row in rows:
        symbol, report, cand = row.symbol, ready.get(row.symbol), passed_by.get(row.symbol)
        notes = []
        if universe is not None and symbol not in universe:
            notes.append("NOT_IN_COLLECTION_UNIVERSE")
        if report is not None and report.vwap_basis == "TYPICAL_APPROXIMATION":
            notes.append("APPROXIMATE_VWAP")
        if not declared:
            notes.append("CLOCK_UNCERTAINTY_UNDECLARED")
        if cand is not None:
            verdict, reasons = PASS, ()
        else:
            verdict = EXCLUDED
            reasons = tuple(dict.fromkeys(excluded_by.get(symbol, ())
                                          or (("SCOUT_" + scout.status,) if scout.status != OK else ("NOT_SELECTED",))))
        spread = next((f.spread_pct for f in liquidity if f.symbol == symbol), None)
        decisions.append(SymbolDecision(
            symbol, row.rank, verdict, reasons, None if report is None else report.vwap_basis,
            () if report is None else report.codes, None if report is None else report.completed_1m_bars,
            book_status.get(symbol, "NONE"), spread, tuple(notes),
            bool(cand and cand.fully_eligible), () if cand is None else tuple(cand.verification_gaps)))
    for item in anonymous:
        decisions.append(SymbolDecision(None, item.rank, EXCLUDED, tuple(item.reasons), None, (), None, "NONE", None, ()))

    passed = tuple(d.symbol for d in decisions if d.verdict == PASS)
    hold = []
    if not declared:
        hold.append("CLOCK_UNCERTAINTY_UNDECLARED")
    if scout.status != OK:
        hold.append("SCOUT_" + scout.status)
    if scout.status == OK and not passed:
        hold.append("NO_CANDIDATES")
    if not passed:
        if not inputs.candles:
            hold.append("NO_CANDLE_DATA")
        if not any(f.spread_pct is not None for f in liquidity):
            hold.append("NO_USABLE_ORDERBOOK")
        if not liquidity:
            hold.append("NO_RANKING_DATA")
    decision = PROCEED if passed and declared else HOLD
    exact = tuple(d.symbol for d in decisions if d.verdict == PASS and d.vwap_basis == "EXACT_TURNOVER")
    approx = tuple(d.symbol for d in decisions if d.verdict == PASS and d.vwap_basis == "TYPICAL_APPROXIMATION")
    return PipelineResult(as_of, decision, tuple(hold) if decision == HOLD else (), declared, scout,
                          tuple(decisions), passed, inputs.candles, tuple(inputs.notes) + tuple(book_notes),
                          exact, approx)
