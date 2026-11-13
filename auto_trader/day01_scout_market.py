"""Pure market-data adapters for DAY01 Scout / DAY-01. No I/O, clock, settings, DB or network.

Existing data only:
* ranking snapshot rows (`RankingSnapshot`) -> `LiquidityFacts` (turnover, volume); the spread is NEVER
  estimated from them and stays None (UNKNOWN) until a real order book exists;
* stored one-minute rows with the `ml_raw_candles` / `available_candles()` column contract
  (source, symbol, interval, event_at, available_at, collected_at, OHLCV) -> `DayOneFacts` and DAY-01
  `Candle`s (through the existing `adapt_rows`).

Time rules: only rows whose availability (max of available_at and collected_at) is <= `as_of` are used;
later rows are ignored (a missing minute that exists only as a later row is reported as late, not used).
`event_at` is market time, `available_at`/`collected_at` are our receipt times and are never rewritten.

Readiness is data completeness UP TO `as_of - grace` (the collector stamps a bar a couple of seconds after
its minute ends). DAY-01 itself still decides at its next 5-minute boundary and stays fail-closed.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Iterable, Mapping

from .day01_scout import DayOneFacts, LiquidityFacts, RankingSnapshot
from .ml.day01 import KST, MINUTE, Candle, Config
from .ml.day01_research import adapt_rows

__all__ = ["SymbolReadiness", "MarketInputs", "liquidity_from_snapshot", "readiness_from_rows",
           "build_market_inputs", "SESSION_MINUTES", "OPENING_MINUTES"]

SESSION_MINUTES = 390          # [09:00, 15:30)
OPENING_MINUTES = 15           # DAY-01 opening range 09:00..09:14
DEFAULT_GRACE = timedelta(seconds=5)
STRICT_MODES = frozenset({"next_open", "legacy_close"})


@dataclass(frozen=True)
class SymbolReadiness:
    symbol: str                       # spelling used by the ranking snapshot
    ready: bool                       # opening range AND vwap ready (no defect inside the window)
    completed_1m_bars: int            # contiguous bars from 09:00 (a gap stops the count)
    required_bars: int                # minutes that must be known at as_of (minutes ended > grace ago)
    opening_range_ready: bool
    vwap_ready: bool
    vwap_basis: str                   # EXACT_TURNOVER | TYPICAL_APPROXIMATION | UNAVAILABLE
    observed_at: datetime | None      # newest availability among all usable bars seen (fact freshness)
    on_time_all: bool                 # every known bar available exactly at its minute end (informational)
    late_bar_count: int
    max_lag_seconds: float | None
    missing_minutes: tuple
    codes: tuple


@dataclass(frozen=True)
class MarketInputs:
    liquidity: tuple
    dayone: tuple
    candles: tuple                    # DAY-01 Candle objects (valid, deduplicated, available <= as_of)
    readiness: tuple
    notes: tuple                      # (symbol | None, code)
    ranking: dict
    provenance: tuple = ()            # original event_at / available_at / collected_at + effective_at per candle


def _norm(value):
    return value.strip().upper() if isinstance(value, str) and value.strip() else None


def _dt(value):
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(parsed, datetime) or parsed.utcoffset() is None:
        raise ValueError("aware datetime required")
    return parsed


def _aware(value, name):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{name} must be an aware datetime")
    return value


# --------------------------------------------------------------------------- ranking -> liquidity

def liquidity_from_snapshot(snapshot: RankingSnapshot, *, as_of: datetime | None = None) -> tuple:
    """(facts, notes). `observed_at` is OUR first receipt of the snapshot (never the provider time).
    Turnover and volume are the provider's day-cumulative values at `rankedAt`. The spread stays None."""
    notes = [(None, "SPREAD_UNKNOWN_NO_ORDERBOOK")]
    if as_of is not None and snapshot.received_at > _aware(as_of, "as_of"):
        return (), ((None, "RANKING_NOT_YET_RECEIVED"),) + tuple(notes)
    if snapshot.provider_ranked_at is None:
        notes.append((None, "RANKING_PROVIDER_TIME_UNKNOWN"))
    groups: dict = {}
    for row in snapshot.rows:
        groups.setdefault(row.symbol, []).append(row)
    facts = []
    for symbol, rows in sorted(groups.items()):
        distinct = {(r.turnover, r.volume) for r in rows}
        if len(distinct) > 1:
            notes.append((symbol, "DUPLICATE_RANKING_ROW_CONFLICT"))
            continue
        row = rows[0]
        facts.append(LiquidityFacts(symbol, snapshot.received_at, turnover=row.turnover, volume=row.volume,
                                    spread_pct=None))
    return tuple(facts), tuple(notes)


# --------------------------------------------------------------------------- candles -> readiness

def readiness_from_rows(rows: Iterable, *, symbols: Iterable[str], as_of: datetime,
                        config: Config | None = None, vwap_mode: str | None = None,
                        allow_approximate_vwap: bool = False, grace: timedelta = DEFAULT_GRACE,
                        max_lag: timedelta | None = None) -> tuple:
    """Returns (dayone_facts, readiness, candles, notes, provenance). Pure; rows are plain mappings.

    `candles` carry `available_at = max(available_at, collected_at)` (when we really had the bar);
    `provenance` keeps the three original times (event_at, available_at, collected_at) plus effective_at."""
    as_of = _aware(as_of, "as_of")
    mode = vwap_mode or (config.vwap_mode if config is not None else "exact")
    if mode not in {"exact", "typical_approximation"}:
        raise ValueError("explicit vwap_mode required")
    if config is not None and vwap_mode is not None and vwap_mode != config.vwap_mode:
        raise ValueError("vwap_mode disagrees with config.vwap_mode")
    strict = config is not None and config.execution in STRICT_MODES
    if max_lag is None:
        max_lag = (timedelta(minutes=float(config.research_max_observation_lag_minutes))
                   if config is not None else timedelta(minutes=2))
    wanted: dict = {}
    notes: list = []
    ambiguous = set()
    for requested in symbols:
        key = _norm(requested)
        if key is None:
            continue
        if key in wanted and wanted[key] != requested.strip():
            ambiguous.add(key)
            notes.append((requested.strip(), "SYMBOL_ALIAS_AMBIGUOUS"))
        wanted.setdefault(key, requested.strip())
    day = as_of.astimezone(KST).date()
    open_at = datetime.combine(day, time(9), KST)
    close_at = datetime.combine(day, time(15, 30), KST)
    known: dict = {key: {} for key in wanted}            # key -> {event_utc: (candle, moment)}
    conflicted: dict = {key: set() for key in wanted}
    future_events: dict = {key: set() for key in wanted}
    defects: dict = {key: Counter() for key in wanted}
    other_symbols, other_day = set(), Counter()
    entries = []                                          # (replay row with effective availability, provenance)
    for raw in rows:
        if not isinstance(raw, Mapping):
            notes.append((None, "ROW_NOT_MAPPING"))
            continue
        key = _norm(raw.get("symbol"))
        if key is None:
            notes.append((None, "ROW_BAD_SYMBOL"))
            continue
        if key not in wanted:
            other_symbols.add(key)
            continue
        try:
            event, avail = _dt(raw["event_at"]), _dt(raw["available_at"])
            collected = _dt(raw["collected_at"]) if raw.get("collected_at") is not None else None
        except (KeyError, ValueError, TypeError):
            defects[key]["INVALID_ROW"] += 1
            continue
        local = event.astimezone(KST)
        if local.date() != day:
            other_day[key] += 1
            continue
        if not open_at <= local < close_at:
            continue
        moment = max(avail, collected) if collected is not None else avail
        if moment > as_of:
            future_events[key].add(event.astimezone(KST))      # only the fact that a later row exists
            continue
        if event.second or event.microsecond:
            defects[key]["UNALIGNED_EVENT"] += 1
            continue
        if avail < event + MINUTE:
            defects[key]["INCOMPLETE_BAR"] += 1                # stamped before its minute ended
            continue
        if collected is not None and collected < avail:
            defects[key]["COLLECTED_BEFORE_AVAILABLE"] += 1
            continue
        if raw.get("interval") != "1m":
            defects[key]["NOT_1M"] += 1
            continue
        if not isinstance(raw.get("source"), str) or not raw["source"].strip():
            defects[key]["NO_SOURCE"] += 1
            continue
        # DAY-01 must see the bar only from the moment we really had it: max(available_at, collected_at).
        eff_row = dict(raw, symbol=key, available_at=moment)
        try:
            candle = adapt_rows([eff_row], universe=[key]).candles[0]
        except (ValueError, IndexError, KeyError, TypeError):
            defects[key]["INVALID_ROW"] += 1
            continue
        slot = candle.event_at.astimezone(KST)
        previous = known[key].get(slot)
        if previous is not None:
            if (previous[0], previous[1]) != (candle, moment):
                conflicted[key].add(slot)
                defects[key]["DUPLICATE_CONFLICT"] += 1
            continue
        known[key][slot] = (candle, moment)
        entries.append((eff_row, dict(symbol=key, source=raw["source"], event_at=event, available_at=avail,
                                      collected_at=collected, effective_at=moment)))
    elapsed = int((as_of.astimezone(KST) - grace - open_at) // MINUTE)
    required = max(0, min(SESSION_MINUTES, elapsed))
    facts, reports = [], []
    for key, requested in sorted(wanted.items()):
        if key in ambiguous:
            continue
        bars = known[key]
        blocked = conflicted[key]
        slots = [open_at + i * MINUTE for i in range(SESSION_MINUTES)]
        usable = {s for s in bars if s not in blocked}
        prefix = 0
        while prefix < SESSION_MINUTES and slots[prefix] in usable:
            prefix += 1
        missing = tuple(slots[i] for i in range(required) if slots[i] not in usable)
        codes = [code for code, count in defects[key].items() if count]
        if not bars:
            codes.append("NO_CANDLES")
        opening_missing = [m for m in missing if m < open_at + OPENING_MINUTES * MINUTE]
        session_missing = [m for m in missing if m >= open_at + OPENING_MINUTES * MINUTE]
        if opening_missing:
            codes.append("OPENING_MISSING")
        elif required < OPENING_MINUTES:
            codes.append("OPENING_NOT_DUE")
        if session_missing:
            codes.append("SESSION_GAP")
        if any(m in future_events[key] for m in missing):
            codes.append("LATE_NOT_YET_AVAILABLE")
        known_prefix = [bars[slots[i]][0] for i in range(prefix)]     # every contiguous bar we hold, not only up to `required`
        lags = [(bars[s][0].available_at - s - MINUTE).total_seconds() for s in sorted(usable)]
        on_time = bool(lags) and all(x == 0 for x in lags)
        # DAY-01's strict modes fire only when the NEWEST needed bar arrives exactly at its minute end.
        newest_slot = slots[prefix - 1] if prefix else None
        newest_on_time = newest_slot is not None and bars[newest_slot][0].available_at == newest_slot + MINUTE
        late = sum(1 for x in lags if x > max_lag.total_seconds())
        if late:
            codes.append("LATE_ARRIVAL")
        if strict and bars and not newest_on_time:
            codes.append("NOT_ON_TIME_FOR_STRICT_MODE")
        window_complete = required >= 1 and prefix >= required and not any(m in blocked for m in slots[:required])
        if mode == "exact":
            basis = "EXACT_TURNOVER" if window_complete and all(c.turnover is not None for c in known_prefix) else "UNAVAILABLE"
            if window_complete and basis == "UNAVAILABLE":
                codes.append("VWAP_NEEDS_TURNOVER")
        elif allow_approximate_vwap:
            basis = "TYPICAL_APPROXIMATION"
            codes.append("VWAP_APPROXIMATE")
        else:
            basis = "UNAVAILABLE"
            codes.append("VWAP_APPROXIMATION_NOT_ALLOWED")
        vwap_ready = window_complete and basis != "UNAVAILABLE"
        opening_ready = prefix >= OPENING_MINUTES and not any(m in blocked for m in slots[:OPENING_MINUTES])
        # Freshness of the FACT = newest availability among every usable bar we looked at. A gap therefore
        # keeps the fact fresh (Scout reports the real reason); a dead feed makes it old (Scout: STALE).
        observed = max((bars[s][1] for s in usable), default=None)
        strict_blocked = strict and bool(bars) and not newest_on_time
        ready = bool(opening_ready and vwap_ready and not strict_blocked)
        reports.append(SymbolReadiness(
            symbol=requested, ready=ready, completed_1m_bars=prefix, required_bars=required,
            opening_range_ready=opening_ready, vwap_ready=vwap_ready, vwap_basis=basis, observed_at=observed,
            on_time_all=on_time, late_bar_count=late, max_lag_seconds=max(lags) if lags else None,
            missing_minutes=missing, codes=tuple(dict.fromkeys(codes))))
        if observed is not None:
            facts.append(DayOneFacts(requested, observed, completed_1m_bars=prefix,
                                     opening_range_ready=opening_ready,
                                     vwap_ready=bool(vwap_ready and not strict_blocked)))
    if other_symbols:
        notes.append((None, "CANDLE_SYMBOLS_NOT_IN_RANKING:" + ",".join(sorted(other_symbols))))
    for key, count in other_day.items():
        notes.append((wanted[key], f"ROWS_FROM_OTHER_SESSION_IGNORED:{count}"))
    final = [(r, meta) for r, meta in entries if r["symbol"] not in ambiguous
             and meta["event_at"].astimezone(KST) not in conflicted[r["symbol"]]]
    try:
        candles = adapt_rows([r for r, _ in final], universe=sorted(wanted)).candles if final else ()
    except ValueError:
        candles, final = (), []
        notes.append((None, "ADAPT_ROWS_REJECTED"))
    provenance = tuple(sorted((meta for _, meta in final), key=lambda m: (m["symbol"], m["event_at"])))
    return tuple(facts), tuple(reports), tuple(candles), tuple(notes), provenance


# --------------------------------------------------------------------------- composition

def build_market_inputs(snapshot: RankingSnapshot, rows: Iterable, *, as_of: datetime,
                        symbols: Iterable[str] | None = None, config: Config | None = None,
                        vwap_mode: str | None = None, allow_approximate_vwap: bool = False,
                        grace: timedelta = DEFAULT_GRACE, max_ranking_candle_skew: timedelta = timedelta(seconds=180)
                        ) -> MarketInputs:
    """Liquidity facts from the ranking and readiness facts/candles from stored minute rows.

    `symbols` defaults to the snapshot's symbols. Nothing here relaxes Scout: the spread stays UNKNOWN,
    so Scout (require_liquidity=True) still excludes every symbol until a real order book is supplied."""
    as_of = _aware(as_of, "as_of")
    liquidity, notes = liquidity_from_snapshot(snapshot, as_of=as_of)
    names = sorted({r.symbol for r in snapshot.rows}) if symbols is None else list(symbols)
    dayone, readiness, candles, candle_notes, provenance = readiness_from_rows(
        rows, symbols=names, as_of=as_of, config=config, vwap_mode=vwap_mode,
        allow_approximate_vwap=allow_approximate_vwap, grace=grace)
    notes = list(notes) + list(candle_notes)
    skew = {}
    if snapshot.received_at <= as_of:
        for fact in dayone:
            seconds = abs((fact.observed_at - snapshot.received_at).total_seconds())
            skew[fact.symbol] = seconds
            if timedelta(seconds=seconds) > max_ranking_candle_skew:
                notes.append((fact.symbol, "RANKING_CANDLE_SKEW"))
    ranking = dict(snapshot_id=snapshot.snapshot_id, received_at=snapshot.received_at,
                   provider_ranked_at=snapshot.provider_ranked_at, skew_seconds=skew)
    return MarketInputs(liquidity, dayone, candles, readiness, tuple(notes), ranking, provenance)
