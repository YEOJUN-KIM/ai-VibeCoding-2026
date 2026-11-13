"""DAY01 SCOUT: deterministic WATCH-candidate selection from a provided turnover ranking.

This module selects *watch* candidates only. It emits no buy signal, strategy, order or
return forecast, and never touches settings, databases, brokers, the network, files or the
clock: every instant is an explicit argument. Spec: docs/specs/DAY01_SCOUT.md.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any, Callable

from .risk_policy import RiskPolicy

SCHEMA_VERSION = "day01-scout-1"
# The provider query sets excludeInvestmentCaution=true: ranks are over that FILTERED list,
# so "top 50" never means the unfiltered market top 50.
SOURCE = "MARKET_TRADING_AMOUNT/KR/1d/excludeInvestmentCaution=true"
RANK_BASIS = "PROVIDER_FILTERED_EXCLUDE_INVESTMENT_CAUTION"
KST = timezone(timedelta(hours=9))
_MARKETS = frozenset({"KOSPI", "KOSDAQ"})

# Snapshot / result statuses.
OK = "OK"
NO_SNAPSHOT = "NO_SNAPSHOT"
STALE = "STALE"
DAY_ROLLOVER = "DAY_ROLLOVER"
UNKNOWN_PROVIDER_TIME = "UNKNOWN_PROVIDER_TIME"
INVALID_PROVIDER_TIME = "INVALID_PROVIDER_TIME"
INVALID_SNAPSHOT = "INVALID_SNAPSHOT"


# --------------------------------------------------------------------------- validation

class _Bad(ValueError):
    pass


def _dec(value: Any, *, nonneg: bool = True, optional: bool = False) -> Decimal | None:
    """Finite Decimal from int/Decimal/str only. float and bool are rejected."""
    if value is None:
        if optional:
            return None
        raise _Bad("missing number")
    if isinstance(value, bool) or isinstance(value, float):
        raise _Bad("bool/float not accepted")
    if not isinstance(value, (int, Decimal, str)):
        raise _Bad("unsupported number type")
    try:
        number = Decimal(value.strip() if isinstance(value, str) else value)
    except InvalidOperation as exc:
        raise _Bad("not a decimal") from exc
    if not number.is_finite():
        raise _Bad("not finite")
    if nonneg and number < 0:
        raise _Bad("negative")
    return number


def _utc(value: Any, *, optional: bool = False) -> datetime | None:
    """Aware datetime (or ISO-8601 string with offset) normalized to UTC; naive is rejected."""
    if value is None:
        if optional:
            return None
        raise _Bad("missing datetime")
    if isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            value = datetime.fromisoformat(text)
        except ValueError as exc:
            raise _Bad("bad datetime") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise _Bad("datetime must be timezone-aware")
    return value.astimezone(timezone.utc)


def _pos_int(value: Any, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise _Bad("bad integer")
    return value


def _opt_bool(value: Any) -> bool | None:
    if value is not None and type(value) is not bool:
        raise _Bad("bool expected")
    return value


def _sym(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _Bad("bad symbol")
    return value.strip()


def _kst_date(moment: datetime) -> date:
    return moment.astimezone(KST).date()


def _as_of(value: Any) -> datetime:
    try:
        return _utc(value)
    except _Bad as exc:
        raise ValueError(f"as_of: {exc}") from exc


def _wrap(name: str, fn: Callable, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except _Bad as exc:
        raise ValueError(f"{name}: {exc}") from exc


# --------------------------------------------------------------------------- config

@dataclass(frozen=True)
class ScoutConfig:
    provider: str = "toss"
    target_count: int = 12
    ranking_depth: int = 50
    quantity: int = 1
    # Placeholder cost model; callers pass the real configured rates.
    fee_rate: Decimal = Decimal("0.00015")
    buy_slippage_rate: Decimal = Decimal("0.001")
    max_snapshot_age: timedelta = timedelta(seconds=120)
    max_receive_lag: timedelta = timedelta(seconds=60)
    max_fact_age: timedelta = timedelta(seconds=300)
    max_portfolio_age: timedelta = timedelta(seconds=60)
    # Liquidity is required by default: known, fresh facts and all thresholds configured.
    require_liquidity: bool = True
    min_turnover: Decimal | None = Decimal("10000000000")
    min_volume: Decimal | None = Decimal("1")
    max_spread_pct: Decimal | None = Decimal("1")
    require_dayone_readiness: bool = True
    min_completed_1m_bars: int = 15
    require_capital_check: bool = True
    sort_mode: str = "SCORE"
    # (rank, completed bars, opening range, VWAP) points; readiness only, no return signal.
    score_weights: tuple = (Decimal(40), Decimal(20), Decimal(20), Decimal(20))

    def __post_init__(self):
        def set_(name, value):
            object.__setattr__(self, name, value)

        if not isinstance(self.provider, str) or not self.provider:
            raise ValueError("provider required")
        if type(self.target_count) is not int or not 10 <= self.target_count <= 15:
            raise ValueError("target_count must be an int in 10..15")
        if type(self.ranking_depth) is not int or not 1 <= self.ranking_depth <= 50:
            raise ValueError("ranking_depth must be an int in 1..50")
        if type(self.quantity) is not int or self.quantity < 1:
            raise ValueError("quantity must be an int >= 1")
        if type(self.min_completed_1m_bars) is not int or self.min_completed_1m_bars < 1:
            raise ValueError("min_completed_1m_bars must be an int >= 1")
        for name in ("require_liquidity", "require_dayone_readiness", "require_capital_check"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be bool")
        for name in ("fee_rate", "buy_slippage_rate"):
            set_(name, _wrap(name, _dec, getattr(self, name)))
        for name in ("min_turnover", "min_volume", "max_spread_pct"):
            set_(name, _wrap(name, _dec, getattr(self, name), optional=True))
        for name in ("max_snapshot_age", "max_receive_lag", "max_fact_age", "max_portfolio_age"):
            value = getattr(self, name)
            if not isinstance(value, timedelta) or value < timedelta(0):
                raise ValueError(f"{name} must be a non-negative timedelta")
        if self.require_liquidity and (
                None in (self.min_turnover, self.min_volume, self.max_spread_pct)
                or self.min_turnover <= 0 or self.min_volume <= 0):
            raise ValueError("require_liquidity needs positive min_turnover and min_volume and a max_spread_pct")
        if self.sort_mode not in {"SCORE", "RANK"}:
            raise ValueError("sort_mode must be SCORE or RANK")
        if not isinstance(self.score_weights, tuple) or len(self.score_weights) != 4:
            raise ValueError("score_weights must be a 4-tuple")
        set_("score_weights", tuple(_wrap("score_weights", _dec, w) for w in self.score_weights))


# --------------------------------------------------------------------------- snapshots

@dataclass(frozen=True)
class RankedRow:
    rank: int
    symbol: str
    turnover: Decimal | None = None
    volume: Decimal | None = None
    price: Decimal | None = None
    change_rate: Decimal | None = None


@dataclass(frozen=True)
class RankingSnapshot:
    snapshot_id: str
    provider: str
    source: str
    received_at: datetime  # when WE received it (UTC); never re-stamped on cache reuse
    provider_ranked_at: datetime | None  # provider's rankedAt; None means UNKNOWN
    requested_count: int
    rows: tuple
    defects: tuple
    content_digest: str  # independent of snapshot_id and received_at


@dataclass(frozen=True)
class FetchResult:
    """Fetch return carrying the ORIGINAL receipt time (required for cached payloads)."""
    payload: Any  # (ranked_at, rankings) tuple or {"result": {"rankedAt", "rankings"}}
    received_at: datetime


def _row_defect(index: int, why: str) -> str:
    return f"row[{index}]:{why}"


def _digest(provider: str, source: str, ranked_at, requested_count: int, rows: tuple) -> str:
    body = {
        "provider": provider, "source": source,
        "ranked_at": ranked_at.isoformat() if ranked_at else None,
        "requested_count": requested_count,
        "rows": [[r.rank, r.symbol, *(None if v is None else str(v) for v in
                                      (r.turnover, r.volume, r.price, r.change_rate))] for r in rows],
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def snapshot_from_rankings(ranked_at: Any, rankings: Any, *, received_at: Any,
                           snapshot_id: str | None = None, provider: str = "toss",
                           requested_count: int = 100) -> RankingSnapshot:
    """Build a snapshot from the existing ranking contract (rankedAt + rankings rows).

    `received_at` must be the instant the payload was ORIGINALLY received.
    Missing/invalid provider time becomes None (UNKNOWN); it is never fabricated.
    Invalid rows are dropped with a defect; duplicate ranks/symbols are kept so selection
    can fail closed on them.
    """
    received = _wrap("received_at", _utc, received_at)
    if not isinstance(provider, str) or not provider:
        raise ValueError("provider required")
    if type(requested_count) is not int or requested_count < 1:
        raise ValueError("requested_count must be an int >= 1")
    if not isinstance(rankings, (list, tuple)):
        raise ValueError("rankings must be a list")
    defects: list[str] = []
    try:
        ranked = _utc(ranked_at, optional=True)
    except _Bad:
        ranked = None
        defects.append("BAD_RANKED_AT")
    rows = []
    for index, raw in enumerate(rankings):
        if not isinstance(raw, Mapping):
            defects.append(_row_defect(index, "NOT_A_MAPPING"))
            continue
        rank = raw.get("rank")
        if type(rank) is not int or rank < 1:
            defects.append(_row_defect(index, "BAD_RANK"))
            continue
        try:
            symbol = _sym(raw.get("symbol"))
        except _Bad:
            defects.append(_row_defect(index, "BAD_SYMBOL"))
            continue
        price_block = raw.get("price")
        price_block = price_block if isinstance(price_block, Mapping) else {}
        values = {}
        for name, source in (("turnover", raw.get("tradingAmount")), ("volume", raw.get("tradingVolume")),
                             ("price", price_block.get("lastPrice")),
                             ("change_rate", price_block.get("changeRate"))):
            try:
                values[name] = _dec(source, nonneg=name != "change_rate", optional=True)
            except _Bad:
                values[name] = None
                defects.append(_row_defect(index, f"BAD_{name.upper()}"))
        rows.append(RankedRow(rank=rank, symbol=symbol, **values))
    # Canonical order (by rank, symbol, values) so content identity ignores provider row order.
    rows_t = tuple(sorted(rows, key=lambda r: (r.rank, r.symbol, str(r.price), str(r.turnover),
                                               str(r.volume), str(r.change_rate))))
    digest = _digest(provider, SOURCE, ranked, requested_count, rows_t)
    return RankingSnapshot(
        snapshot_id=snapshot_id or f"snap-{digest[:16]}", provider=provider, source=SOURCE,
        received_at=received, provider_ranked_at=ranked, requested_count=requested_count,
        rows=rows_t, defects=tuple(defects), content_digest=digest)


def snapshot_from_payload(payload: Any, *, received_at: Any, **kwargs) -> RankingSnapshot:
    """Accept the raw API payload `{"result": {"rankedAt": ..., "rankings": [...]}}` or the
    `(rankedAt, rankings)` tuple returned by `_domestic_trading_amount_rankings`."""
    if isinstance(payload, tuple) and len(payload) == 2:
        ranked_at, rankings = payload
    elif isinstance(payload, Mapping) and isinstance(payload.get("result"), Mapping):
        ranked_at, rankings = payload["result"].get("rankedAt"), payload["result"].get("rankings")
    else:
        raise ValueError("unrecognized ranking payload")
    return snapshot_from_rankings(ranked_at, rankings, received_at=received_at, **kwargs)


# --------------------------------------------------------------------------- facts

@dataclass(frozen=True)
class InstrumentFacts:
    symbol: str
    observed_at: datetime
    name: str | None = None
    market: str | None = None
    currency: str | None = None
    security_type: str | None = None
    is_common_share: bool | None = None
    status: str | None = None
    krx_trading_suspended: bool | None = None
    liquidation_trading: bool | None = None

    def __post_init__(self):
        object.__setattr__(self, "symbol", _wrap("symbol", _sym, self.symbol))
        object.__setattr__(self, "observed_at", _wrap("observed_at", _utc, self.observed_at))
        for name in ("is_common_share", "krx_trading_suspended", "liquidation_trading"):
            _wrap(name, _opt_bool, getattr(self, name))


@dataclass(frozen=True)
class LiquidityFacts:
    symbol: str
    observed_at: datetime
    turnover: Decimal | None = None
    volume: Decimal | None = None
    spread_pct: Decimal | None = None

    def __post_init__(self):
        object.__setattr__(self, "symbol", _wrap("symbol", _sym, self.symbol))
        object.__setattr__(self, "observed_at", _wrap("observed_at", _utc, self.observed_at))
        for name in ("turnover", "volume", "spread_pct"):
            object.__setattr__(self, name, _wrap(name, _dec, getattr(self, name), optional=True))


@dataclass(frozen=True)
class DayOneFacts:
    """Watch-readiness inputs for the existing DAY01 flow; not a signal or return estimate."""
    symbol: str
    observed_at: datetime
    completed_1m_bars: int | None = None
    opening_range_ready: bool | None = None
    vwap_ready: bool | None = None

    def __post_init__(self):
        object.__setattr__(self, "symbol", _wrap("symbol", _sym, self.symbol))
        object.__setattr__(self, "observed_at", _wrap("observed_at", _utc, self.observed_at))
        if self.completed_1m_bars is not None:
            _wrap("completed_1m_bars", _pos_int, self.completed_1m_bars)
        _wrap("opening_range_ready", _opt_bool, self.opening_range_ready)
        _wrap("vwap_ready", _opt_bool, self.vwap_ready)


@dataclass(frozen=True)
class Position:
    """Held position. Only symbol/quantity are mandatory: invalid or missing price and value
    fields become None plus a `defects` entry, so the position is still monitored and an
    unknown value is never treated as zero."""
    symbol: str
    quantity: int
    market_value: Decimal | None = None
    market_value_observed_at: datetime | None = None  # when the value became available to us
    price: Decimal | None = None
    price_observed_at: datetime | None = None
    defects: tuple = ()

    def __post_init__(self):
        object.__setattr__(self, "symbol", _wrap("symbol", _sym, self.symbol))
        _wrap("quantity", _pos_int, self.quantity)
        defects = list(self.defects)
        for name, parse in (("market_value", _dec), ("price", _dec),
                            ("market_value_observed_at", _utc), ("price_observed_at", _utc)):
            try:
                value = parse(getattr(self, name), optional=True)
            except _Bad:
                value = None
                defects.append(f"{name.upper()}_INVALID")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "defects", tuple(defects))


@dataclass(frozen=True)
class Portfolio:
    """Account snapshot. `observed_at` is when this data became available to us (not a provider
    event time). Pending orders must be given as explicit reserved exposure/slots; reserved
    cash alone does not cover per-symbol, total-investment or position-slot limits."""
    observed_at: datetime
    cash: Decimal
    reserved_cash: Decimal = Decimal(0)
    positions: tuple = ()
    daily_loss: Decimal = Decimal(0)
    max_positions: int | None = None
    min_cash: Decimal = Decimal(0)
    min_cash_ratio_pct: Decimal | None = None  # needs total_asset
    total_asset: Decimal | None = None
    daily_loss_limit: Decimal | None = None
    reserved_symbol_exposure: tuple = ()  # ((symbol, Decimal), ...)
    reserved_total_exposure: Decimal = Decimal(0)
    reserved_slots: int = 0
    pending_orders_known: bool = True

    def __post_init__(self):
        object.__setattr__(self, "observed_at", _wrap("observed_at", _utc, self.observed_at))
        for name in ("cash", "reserved_cash", "daily_loss", "min_cash", "reserved_total_exposure"):
            object.__setattr__(self, name, _wrap(name, _dec, getattr(self, name)))
        for name in ("daily_loss_limit", "min_cash_ratio_pct", "total_asset"):
            object.__setattr__(self, name, _wrap(name, _dec, getattr(self, name), optional=True))
        if self.max_positions is not None:
            _wrap("max_positions", _pos_int, self.max_positions)
        _wrap("reserved_slots", _pos_int, self.reserved_slots)
        if type(self.pending_orders_known) is not bool:
            raise ValueError("pending_orders_known must be bool")
        positions = tuple(self.positions)
        if not all(isinstance(p, Position) for p in positions):
            raise ValueError("positions must be Position objects")
        if len({p.symbol for p in positions}) != len(positions):
            raise ValueError("duplicate position symbols")
        object.__setattr__(self, "positions", positions)
        reserved = []
        for item in self.reserved_symbol_exposure:
            try:
                symbol, amount = item
            except (TypeError, ValueError):
                raise ValueError("reserved_symbol_exposure items are (symbol, amount)") from None
            reserved.append((_wrap("symbol", _sym, symbol), _wrap("exposure", _dec, amount)))
        if len({sym for sym, _ in reserved}) != len(reserved):
            raise ValueError("duplicate reserved symbols")
        object.__setattr__(self, "reserved_symbol_exposure", tuple(sorted(reserved)))


# --------------------------------------------------------------------------- results

@dataclass(frozen=True)
class SnapshotRef:
    snapshot_id: str
    provider: str
    source: str
    received_at: datetime
    provider_ranked_at: datetime | None
    content_digest: str


@dataclass(frozen=True)
class Coverage:
    expected: int
    present: int
    missing_ranks: tuple
    excluded_conflict_rows: int
    complete: bool  # all provided ranks 1..expected present and usable
    marketwide_complete: bool = False  # never claimed
    basis: str = RANK_BASIS


@dataclass(frozen=True)
class Evidence:
    kind: str
    status: str  # FRESH / STALE / MISSING
    observed_at: datetime | None
    age_seconds: int | None


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    required: Decimal | None = None
    limit: Decimal | None = None
    detail: str = ""


@dataclass(frozen=True)
class Candidate:
    symbol: str
    name: str | None
    rank: int
    turnover: Decimal | None
    volume: Decimal | None
    price: Decimal | None
    change_rate: Decimal | None
    provenance: SnapshotRef
    as_of: datetime
    evidence: tuple
    score: Decimal
    score_components: tuple
    capital_checks: tuple
    fully_eligible: bool
    verification_gaps: tuple
    entry_expires_at: datetime
    purpose: str = "WATCH_ONLY"
    joint_orders_guaranteed: bool = False
    liquidity_note: str = "Ranking turnover is not order-book depth; actual liquidity is not guaranteed."


@dataclass(frozen=True)
class Exclusion:
    rank: int | None
    symbol: str | None
    reasons: tuple


@dataclass(frozen=True)
class HeldMonitor:
    symbol: str
    quantity: int
    rank: int | None
    in_ranking_depth: bool
    price: Decimal | None
    price_observed_at: datetime | None
    price_status: str  # FRESH / STALE / UNKNOWN / FUTURE
    ranking_price: Decimal | None
    snapshot_status: str
    flags: tuple
    monitor_only: bool = True


@dataclass(frozen=True)
class WatchEntry:
    symbol: str
    sources: tuple  # ("CANDIDATE",) or ("HELD",) or both


@dataclass(frozen=True)
class ScoutResult:
    schema_version: str
    as_of: datetime
    status: str
    snapshot: SnapshotRef | None
    coverage: Coverage | None
    candidates: tuple
    held: tuple
    watch_union: tuple
    exclusions: tuple
    shortage_reasons: tuple
    snapshot_rejections: tuple
    portfolio_status: str
    target_count: int


# --------------------------------------------------------------------------- selection

def _ref(snap: RankingSnapshot) -> SnapshotRef:
    return SnapshotRef(snap.snapshot_id, snap.provider, snap.source, snap.received_at,
                       snap.provider_ranked_at, snap.content_digest)


_CONFLICT = object()


def _latest_fact(facts, symbol: str, as_of: datetime):
    """Latest fact for symbol available at or before as_of; later facts are ignored entirely.
    Two different facts at the same latest timestamp are a conflict (order-independent,
    fail-closed): returns _CONFLICT."""
    at_latest: list = []
    for fact in facts:
        if fact.symbol != symbol or fact.observed_at > as_of:
            continue
        if not at_latest or fact.observed_at > at_latest[0].observed_at:
            at_latest = [fact]
        elif fact.observed_at == at_latest[0].observed_at and fact not in at_latest:
            at_latest.append(fact)
    if not at_latest:
        return None
    return at_latest[0] if len(at_latest) == 1 else _CONFLICT


def _evidence(kind: str, fact, as_of: datetime, max_age: timedelta) -> Evidence:
    if fact is _CONFLICT:
        return Evidence(kind, "CONFLICT", None, None)
    if fact is None:
        return Evidence(kind, "MISSING", None, None)
    age = as_of - fact.observed_at
    # Exclusive boundary: a fact exactly max_age old is already stale.
    return Evidence(kind, "STALE" if age >= max_age else "FRESH", fact.observed_at,
                    int(age.total_seconds()))


def _select_snapshot(as_of: datetime, snapshots, config: ScoutConfig):
    """Return (status, snapshot|None, rejections). Snapshots received after as_of are ignored
    entirely, so appending future snapshots never changes the result (prefix invariance)."""
    rejections = []
    usable = []
    for snap in snapshots:
        if snap.received_at > as_of:
            continue
        if snap.provider != config.provider:
            rejections.append((snap.snapshot_id, "PROVIDER_MISMATCH"))
            continue
        usable.append(snap)
    if not usable:
        return NO_SNAPSHOT, None, tuple(rejections)
    # Latest by actual receipt, then provider time (None sorts first), then digest/id.
    # An invalid latest blocks; it is never prefiltered so that an older snapshot takes over.
    latest = max(usable, key=lambda s: (s.received_at, s.provider_ranked_at or datetime.min.replace(tzinfo=timezone.utc),
                                        s.content_digest, s.snapshot_id))
    if any(s.snapshot_id == latest.snapshot_id and s.content_digest != latest.content_digest
           for s in usable):
        return INVALID_SNAPSHOT, latest, tuple(rejections + [(latest.snapshot_id, "SNAPSHOT_ID_CONFLICT")])
    ranked = latest.provider_ranked_at
    if ranked is None:
        return UNKNOWN_PROVIDER_TIME, latest, tuple(rejections)
    if ranked > latest.received_at or ranked > as_of:
        return INVALID_PROVIDER_TIME, latest, tuple(rejections)
    if _kst_date(ranked) != _kst_date(as_of):
        return DAY_ROLLOVER, latest, tuple(rejections)
    if latest.received_at - ranked >= config.max_receive_lag:
        # A fresh receipt cannot revive an old provider basis.
        return STALE, latest, tuple(rejections + [(latest.snapshot_id, "PROVIDER_BASIS_STALE")])
    if as_of - latest.received_at >= config.max_snapshot_age:
        return STALE, latest, tuple(rejections)
    return OK, latest, tuple(rejections)


def _usable_rows(snap: RankingSnapshot, depth: int):
    """Rows with unique rank and unique symbol; every involved row of a conflict is excluded."""
    by_rank: dict[int, list] = {}
    by_symbol: dict[str, list] = {}
    for row in snap.rows:
        by_rank.setdefault(row.rank, []).append(row)
        by_symbol.setdefault(row.symbol, []).append(row)
    good, conflicts = [], []
    for row in sorted(snap.rows, key=lambda r: (r.rank, r.symbol)):
        if len(by_rank[row.rank]) > 1:
            conflicts.append(Exclusion(row.rank, row.symbol, ("CONFLICT_RANK",)))
        elif len(by_symbol[row.symbol]) > 1:
            conflicts.append(Exclusion(row.rank, row.symbol, ("CONFLICT_SYMBOL",)))
        else:
            good.append(row)
    return good, conflicts


def _resolve_portfolio(as_of: datetime, portfolio, config: ScoutConfig):
    """Pick the effective portfolio. Returns (portfolio | None, state).

    state: FRESH / STALE / MISSING / CONFLICT. Only FRESH may clear capital gates.
    Portfolios available after as_of are ignored ENTIRELY for every output (prefix
    invariance): if nothing causally available exists the result equals "no portfolio".
    Different portfolios at the same latest timestamp are CONFLICT; monitored positions are
    then merged conservatively (largest quantity per symbol).
    """
    if portfolio is None:
        return None, "MISSING"
    items = [portfolio] if isinstance(portfolio, Portfolio) else list(portfolio)
    if not items or not all(isinstance(p, Portfolio) for p in items):
        raise ValueError("portfolio must be a Portfolio or an iterable of Portfolio")
    known = [p for p in items if p.observed_at <= as_of]
    if not known:
        return None, "MISSING"
    latest = max(p.observed_at for p in known)
    top = []
    for p in known:
        if p.observed_at == latest and p not in top:
            top.append(p)
    if len(top) > 1:
        merged: dict[str, Position] = {}
        for p in top:
            for pos in p.positions:
                if pos.symbol not in merged or pos.quantity > merged[pos.symbol].quantity:
                    merged[pos.symbol] = pos
        monitor_only = Portfolio(observed_at=latest, cash=top[0].cash,
                                 positions=tuple(merged[k] for k in sorted(merged)))
        return monitor_only, "CONFLICT"
    p = top[0]
    return p, ("STALE" if as_of - p.observed_at >= config.max_portfolio_age else "FRESH")


def _value_status(pos: Position, as_of: datetime, config: ScoutConfig) -> str:
    """Whether a position's market value may be used. Unusable values are never treated as 0."""
    if pos.market_value is None or pos.market_value_observed_at is None:
        return "UNKNOWN"
    if pos.market_value_observed_at > as_of:
        return "FUTURE"
    if as_of - pos.market_value_observed_at >= config.max_fact_age:
        return "STALE"
    return "FRESH"


def _monitor(as_of: datetime, portfolio, port_state: str, snapshot, snapshot_status: str,
             config: ScoutConfig) -> tuple:
    if portfolio is None:
        return ()
    rank_by_symbol = {}
    if snapshot is not None and snapshot_status == OK:
        good, _ = _usable_rows(snapshot, config.ranking_depth)
        rank_by_symbol = {r.symbol: r for r in good}
    monitors = []
    for pos in sorted(portfolio.positions, key=lambda p: p.symbol):
        flags = list(pos.defects)
        if port_state != "FRESH":
            flags.append(f"PORTFOLIO_{port_state}")
        if pos.price_observed_at is None or pos.price is None:
            price_status, price, observed = "UNKNOWN", None, pos.price_observed_at
            flags.append("PRICE_UNKNOWN")
        elif pos.price_observed_at > as_of:
            price_status, price, observed = "FUTURE", None, None  # future price is never used
            flags.append("PRICE_FUTURE_IGNORED")
        elif as_of - pos.price_observed_at >= config.max_fact_age:
            price_status, price, observed = "STALE", pos.price, pos.price_observed_at
            flags.append("PRICE_STALE")
        else:
            price_status, price, observed = "FRESH", pos.price, pos.price_observed_at
        row = rank_by_symbol.get(pos.symbol)
        if snapshot_status != OK:
            flags.append(f"SNAPSHOT_{snapshot_status}")
        elif row is None:
            flags.append("RANK_UNKNOWN_OR_OUTSIDE_PROVIDED_RANKING")
        value_state = _value_status(pos, as_of, config)
        if value_state != "FRESH":
            flags.append(f"MARKET_VALUE_{value_state}")
        monitors.append(HeldMonitor(
            symbol=pos.symbol, quantity=pos.quantity,
            rank=row.rank if row else None,
            in_ranking_depth=bool(row and row.rank <= config.ranking_depth),
            price=price, price_observed_at=observed, price_status=price_status,
            ranking_price=row.price if row else None, snapshot_status=snapshot_status,
            flags=tuple(dict.fromkeys(flags))))
    return tuple(monitors)


def monitor_positions(as_of: Any, portfolio, snapshot: RankingSnapshot | None,
                      snapshot_status: str, config: ScoutConfig) -> tuple:
    """Independent held-position monitor. Never drops a position and never emits an exit.
    Positions stay monitored whatever the ranking, instrument type, budget or portfolio age."""
    as_of = _as_of(as_of)
    effective, state = _resolve_portfolio(as_of, portfolio, config)
    return _monitor(as_of, effective, state, snapshot, snapshot_status, config)


def _limit(risk_config, name: str):
    """Validated non-negative Decimal limit from a duck-typed config, else None (reject)."""
    try:
        return _dec(getattr(risk_config, name))
    except (_Bad, AttributeError):
        return None


def _capital_checks(row: RankedRow, portfolio: Portfolio, risk_config, as_of: datetime,
                    config: ScoutConfig) -> tuple:
    """Per-symbol affordability only; passing does NOT promise that all candidates can be
    ordered together. Boundaries follow RiskPolicy: exposure == limit is allowed, while
    daily loss == limit blocks."""
    if row.price is None or row.price <= 0:
        return (Check("PRICE", False, detail="PRICE_UNKNOWN"),)
    notional = row.price * (1 + config.buy_slippage_rate) * config.quantity
    fee = notional * config.fee_rate
    total = notional + fee
    checks = []
    values = {pos.symbol: _value_status(pos, as_of, config) for pos in portfolio.positions}
    invested_known = all(v == "FRESH" for v in values.values())
    invested = sum((pos.market_value for pos in portfolio.positions if values[pos.symbol] == "FRESH"),
                   Decimal(0))
    held_unknown = values.get(row.symbol, "FRESH") != "FRESH"
    held_value = next((p.market_value for p in portfolio.positions
                       if p.symbol == row.symbol and not held_unknown), Decimal(0))
    reserved_symbol = dict(portfolio.reserved_symbol_exposure).get(row.symbol, Decimal(0))

    limits = {n: _limit(risk_config, n) for n in
              ("max_order_amount", "max_symbol_amount", "max_total_investment")}
    safe = SimpleNamespace(**{n: v for n, v in limits.items() if v is not None})

    def policy(name, limit_name, call, *, unknown=None):
        limit = limits[limit_name]
        if limit is None:
            checks.append(Check(name, False, total, None, "LIMIT_INVALID"))
        elif unknown:
            checks.append(Check(name, False, total, limit, unknown))
        else:
            checks.append(Check(name, bool(call()), total, limit))

    policy("ORDER_LIMIT", "max_order_amount",
           lambda: RiskPolicy.order_amount_allowed(safe, notional, fee))
    policy("SYMBOL_EXPOSURE", "max_symbol_amount",
           lambda: RiskPolicy.symbol_amount_allowed(safe, held_value + reserved_symbol, total),
           unknown="HELD_VALUE_UNKNOWN" if held_unknown else None)
    policy("TOTAL_INVESTMENT", "max_total_investment",
           lambda: RiskPolicy.total_investment_allowed(safe, invested + portfolio.reserved_total_exposure, total),
           unknown=None if invested_known else "INVESTED_UNKNOWN")
    checks.append(Check("PENDING_ORDERS", portfolio.pending_orders_known,
                        detail="" if portfolio.pending_orders_known else "PENDING_UNKNOWN"))
    available = portfolio.cash - portfolio.reserved_cash
    after = available - total
    checks.append(Check("CASH", after >= portfolio.min_cash, total, available - portfolio.min_cash))
    if portfolio.min_cash_ratio_pct is not None:
        if not portfolio.total_asset:
            checks.append(Check("CASH_RATIO", False, detail="TOTAL_ASSET_UNKNOWN"))
        else:
            ratio = RiskPolicy.cash_ratio(after, portfolio.total_asset)
            checks.append(Check("CASH_RATIO", ratio >= portfolio.min_cash_ratio_pct,
                                ratio, portfolio.min_cash_ratio_pct))
    if portfolio.max_positions is not None:
        held = any(p.symbol == row.symbol for p in portfolio.positions)
        slots = len(portfolio.positions) + portfolio.reserved_slots + (0 if held else 1)
        checks.append(Check("MAX_POSITIONS", slots <= portfolio.max_positions,
                            Decimal(slots), Decimal(portfolio.max_positions)))
    if portfolio.daily_loss_limit is not None:
        checks.append(Check("DAILY_LOSS", portfolio.daily_loss < portfolio.daily_loss_limit,
                            portfolio.daily_loss, portfolio.daily_loss_limit))
    return tuple(checks)


def _next_kst_midnight(as_of: datetime) -> datetime:
    local = as_of.astimezone(KST)
    return datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), KST).astimezone(timezone.utc)


def select_watchlist(as_of: Any, snapshots, *, instruments=(), liquidity=(), dayone=(),
                     portfolio: Portfolio | None = None, config: ScoutConfig | None = None,
                     risk_config=None) -> ScoutResult:
    """Pure, deterministic selection as of `as_of`. Output is 0..target_count WATCH candidates
    (shortages are reported, never padded) plus an independent held-position monitor."""
    config = config or ScoutConfig()
    as_of = _as_of(as_of)
    status, snap, rejections = _select_snapshot(as_of, tuple(snapshots), config)
    portfolio, port_state = _resolve_portfolio(as_of, portfolio, config)
    held = _monitor(as_of, portfolio, port_state, snap, status, config)
    held_symbols = {h.symbol for h in held}
    candidates: list[Candidate] = []
    exclusions: list[Exclusion] = []
    coverage = None
    shortage: list[str] = []

    if snap is not None and status == OK:
        good, conflicts = _usable_rows(snap, config.ranking_depth)
        exclusions.extend(c for c in conflicts if c.rank <= config.ranking_depth)
        in_depth = [r for r in good if r.rank <= config.ranking_depth]
        present = {r.rank for r in in_depth}
        missing = tuple(r for r in range(1, config.ranking_depth + 1) if r not in present)
        coverage = Coverage(config.ranking_depth, len(present), missing,
                            sum(1 for c in conflicts if c.rank <= config.ranking_depth),
                            not missing and snap.requested_count >= config.ranking_depth)
        ref = _ref(snap)
        for row in in_depth:  # provided order; rank 51+ is never promoted
            reasons: list[str] = []
            gaps: list[str] = []
            evidence = [Evidence("RANKING", "FRESH", snap.received_at, int((as_of - snap.received_at).total_seconds()))]
            # Earliest bound of every input; a candidate is valid while as_of < entry_expires_at.
            expiry = [snap.received_at + config.max_snapshot_age,
                      snap.provider_ranked_at + config.max_receive_lag + config.max_snapshot_age,
                      _next_kst_midnight(as_of)]
            if row.symbol in held_symbols:
                exclusions.append(Exclusion(row.rank, row.symbol, ("HELD_MONITOR_ONLY",)))
                continue

            def fact_of(kind, facts, missing_reason):
                fact = _latest_fact(facts, row.symbol, as_of)
                ev = _evidence(kind, fact, as_of, config.max_fact_age)
                evidence.append(ev)
                if ev.status != "FRESH":
                    return None, ev.status
                expiry.append(fact.observed_at + config.max_fact_age)
                return fact, "FRESH"

            inst, state = fact_of("INSTRUMENT", instruments, "")
            if inst is None:
                reasons.append(f"INSTRUMENT_{state}")
            else:
                for ok, why in ((inst.market in _MARKETS, "NOT_KOSPI_KOSDAQ"),
                                (inst.currency == "KRW", "NOT_KRW"),
                                (inst.security_type == "STOCK", "NOT_STOCK"),
                                (inst.is_common_share is True, "NOT_COMMON_SHARE"),
                                (inst.status == "ACTIVE", "NOT_ACTIVE"),
                                (inst.krx_trading_suspended is False, "SUSPENDED_OR_UNKNOWN"),
                                (inst.liquidation_trading is False, "LIQUIDATION_OR_UNKNOWN")):
                    if not ok:
                        reasons.append(why)

            liq, state = fact_of("LIQUIDITY", liquidity, "")
            if liq is None:
                if config.require_liquidity:
                    reasons.append(f"LIQUIDITY_{state}")
                else:
                    gaps.append("LIQUIDITY_UNVERIFIED")
            else:
                limits = ((liq.turnover, config.min_turnover, "TURNOVER"),
                          (liq.volume, config.min_volume, "VOLUME"))
                for value, floor, label in limits:
                    if floor is None:
                        continue
                    if value is None:
                        reasons.append(f"LIQUIDITY_{label}_UNKNOWN")
                    elif value < floor:
                        reasons.append(f"LIQUIDITY_{label}_BELOW_MIN")
                if config.max_spread_pct is not None:
                    if liq.spread_pct is None:
                        reasons.append("LIQUIDITY_SPREAD_UNKNOWN")
                    elif liq.spread_pct > config.max_spread_pct:
                        reasons.append("LIQUIDITY_SPREAD_TOO_WIDE")

            day, state = fact_of("DAY01_READINESS", dayone, "")
            bars_pts = or_pts = vwap_pts = Decimal(0)
            if day is None:
                if config.require_dayone_readiness:
                    reasons.append(f"DAY01_READINESS_{state}")
                else:
                    gaps.append("DAY01_READINESS_UNVERIFIED")
            else:
                bars = day.completed_1m_bars
                if bars is None or day.opening_range_ready is None or day.vwap_ready is None:
                    if config.require_dayone_readiness:
                        reasons.append("DAY01_READINESS_UNKNOWN")
                    else:
                        gaps.append("DAY01_READINESS_UNVERIFIED")
                else:
                    if config.require_dayone_readiness:
                        if bars < config.min_completed_1m_bars:
                            reasons.append("DAY01_BARS_INSUFFICIENT")
                        if not day.opening_range_ready:
                            reasons.append("DAY01_OPENING_RANGE_NOT_READY")
                        if not day.vwap_ready:
                            reasons.append("DAY01_VWAP_NOT_READY")
                    wb, wo, wv = config.score_weights[1:]
                    bars_pts = wb * min(Decimal(bars) / config.min_completed_1m_bars, Decimal(1))
                    or_pts = wo if day.opening_range_ready else Decimal(0)
                    vwap_pts = wv if day.vwap_ready else Decimal(0)

            checks: tuple = ()
            if port_state == "FRESH":
                expiry.append(portfolio.observed_at + config.max_portfolio_age)
                checks = _capital_checks(row, portfolio, risk_config, as_of, config)
                reasons.extend(f"CAPITAL_{c.name}_FAILED" + (f":{c.detail}" if c.detail else "")
                               for c in checks if not c.passed)
            elif config.require_capital_check:
                reasons.append(f"PORTFOLIO_{port_state}")
            else:
                gaps.append("CAPITAL_UNVERIFIED")
            evidence.append(Evidence("PORTFOLIO", "FRESH" if port_state == "FRESH" else port_state,
                                     portfolio.observed_at if portfolio else None,
                                     int((as_of - portfolio.observed_at).total_seconds())
                                     if port_state in ("FRESH", "STALE") else None))

            if reasons:
                exclusions.append(Exclusion(row.rank, row.symbol, tuple(reasons)))
                continue
            rank_pts = config.score_weights[0] * Decimal(config.ranking_depth + 1 - row.rank) / config.ranking_depth
            components = (("RANK", rank_pts), ("COMPLETED_1M_BARS", bars_pts),
                          ("OPENING_RANGE", or_pts), ("VWAP", vwap_pts))
            candidates.append(Candidate(
                symbol=row.symbol, name=inst.name if inst else None, rank=row.rank,
                turnover=row.turnover, volume=row.volume, price=row.price, change_rate=row.change_rate,
                provenance=ref, as_of=as_of, evidence=tuple(evidence),
                score=sum((v for _, v in components), Decimal(0)), score_components=components,
                capital_checks=checks, fully_eligible=not gaps, verification_gaps=tuple(gaps),
                entry_expires_at=min(expiry)))
        if config.sort_mode == "SCORE":
            candidates.sort(key=lambda c: (-c.score, c.rank, c.symbol))
        else:
            candidates.sort(key=lambda c: (c.rank, c.symbol))
        if len(candidates) > config.target_count:
            cut = candidates[config.target_count:]
            candidates = candidates[:config.target_count]
            exclusions.extend(Exclusion(c.rank, c.symbol, ("BELOW_TARGET_COUNT",)) for c in cut)
        if len(candidates) < config.target_count:
            counts: dict[str, int] = {}
            for ex in exclusions:
                for reason in ex.reasons:
                    key = reason.split(":")[0]
                    counts[key] = counts.get(key, 0) + 1
            shortage.append(f"SHORTAGE:{len(candidates)}/{config.target_count}")
            shortage.extend(f"{k}={counts[k]}" for k in sorted(counts))
        if coverage and not coverage.complete:
            shortage.append(f"PARTIAL_RANK_COVERAGE:{coverage.present}/{coverage.expected}")
    else:
        shortage.append(f"NO_NEW_CANDIDATES:{status}")

    cand_symbols = {c.symbol for c in candidates}
    union = [WatchEntry(c.symbol, ("CANDIDATE",)) for c in candidates]
    union.extend(WatchEntry(h.symbol, ("HELD",)) for h in held if h.symbol not in cand_symbols)
    return ScoutResult(
        schema_version=SCHEMA_VERSION, as_of=as_of, status=status,
        snapshot=_ref(snap) if snap is not None else None, coverage=coverage,
        candidates=tuple(candidates), held=held, watch_union=tuple(union),
        exclusions=tuple(sorted(exclusions, key=lambda e: (e.rank or 0, e.symbol or ""))),
        shortage_reasons=tuple(shortage), snapshot_rejections=rejections,
        portfolio_status=port_state, target_count=config.target_count)


# --------------------------------------------------------------------------- refresh state

@dataclass(frozen=True)
class ScoutState:
    last_as_of: datetime | None = None
    snapshots: tuple = ()
    last_candidates: tuple = ()
    last_snapshot_id: str | None = None
    max_history: int = 8

    @staticmethod
    def empty(max_history: int = 8) -> "ScoutState":
        if type(max_history) is not int or max_history < 1:
            raise ValueError("max_history must be an int >= 1")
        return ScoutState(max_history=max_history)


@dataclass(frozen=True)
class RefreshReport:
    status: str  # REFRESHED / AS_OF_REGRESSION
    added: tuple
    removed: tuple
    unchanged: tuple
    snapshot_changed: bool
    ingested: tuple
    rejected: tuple  # (snapshot_id, reason)
    result: ScoutResult | None


@dataclass(frozen=True)
class FetchOutcome:
    status: str  # INGESTED / DUPLICATE / REJECTED / FETCH_FAILED
    snapshot_id: str | None
    reason: str | None = None


def _merge(snapshots: tuple, incoming, max_history: int):
    stored = list(snapshots)
    ingested, rejected = [], []
    for snap in incoming:
        if not isinstance(snap, RankingSnapshot):
            rejected.append((None, "NOT_A_SNAPSHOT"))
            continue
        same_id = next((s for s in stored if s.snapshot_id == snap.snapshot_id), None)
        if same_id is not None:
            if same_id.content_digest != snap.content_digest:
                rejected.append((snap.snapshot_id, "SNAPSHOT_ID_CONFLICT"))
            continue  # idempotent
        twin = next((s for s in stored if s.provider == snap.provider
                     and s.content_digest == snap.content_digest), None)
        if twin is not None:  # same provider content again: keep the ORIGINAL (earliest) receipt
            if snap.received_at < twin.received_at:
                stored[stored.index(twin)] = snap
                ingested.append(snap.snapshot_id)
            continue
        if snap.provider_ranked_at is not None and any(
                s.provider == snap.provider and s.provider_ranked_at == snap.provider_ranked_at
                for s in stored):
            rejected.append((snap.snapshot_id, "SAME_RANKED_AT_DIFFERENT_CONTENT"))
            continue
        stored.append(snap)
        ingested.append(snap.snapshot_id)
    stored.sort(key=lambda s: (s.received_at, s.snapshot_id))
    return tuple(stored[-max_history:]), tuple(ingested), tuple(rejected)


def refresh(state: ScoutState, as_of: Any, *, new_snapshots=(), instruments=(), liquidity=(),
            dayone=(), portfolio: Portfolio | None = None, config: ScoutConfig | None = None,
            risk_config=None) -> tuple[ScoutState, RefreshReport]:
    """Explicit incremental refresh: returns a NEW state; the input state is never mutated."""
    config = config or ScoutConfig()
    as_of = _as_of(as_of)
    if state.last_as_of is not None and as_of < state.last_as_of:
        return state, RefreshReport("AS_OF_REGRESSION", (), (), (), False, (), (), None)
    merged, ingested, rejected = _merge(state.snapshots, tuple(new_snapshots), state.max_history)
    today = _kst_date(as_of)
    # Day rollover: drop snapshots received on an earlier KST day (never future-received ones).
    merged = tuple(s for s in merged if s.received_at > as_of or _kst_date(s.received_at) >= today)
    result = select_watchlist(as_of, merged, instruments=instruments, liquidity=liquidity,
                              dayone=dayone, portfolio=portfolio, config=config, risk_config=risk_config)
    now_symbols = tuple(c.symbol for c in result.candidates)
    previous = state.last_candidates
    new_state = dataclasses.replace(
        state, last_as_of=as_of, snapshots=merged, last_candidates=now_symbols,
        last_snapshot_id=result.snapshot.snapshot_id if result.snapshot and result.status == OK else None)
    report = RefreshReport(
        status="REFRESHED",
        added=tuple(s for s in now_symbols if s not in previous),
        removed=tuple(s for s in previous if s not in now_symbols),
        unchanged=tuple(s for s in now_symbols if s in previous),
        snapshot_changed=new_state.last_snapshot_id != state.last_snapshot_id,
        ingested=ingested, rejected=rejected + result.snapshot_rejections, result=result)
    return new_state, report


def ingest_fetch(state: ScoutState, fetch: Callable[[], Any], *, clock: Callable[[], datetime],
                 provider: str = "toss", requested_count: int = 100) -> tuple[ScoutState, FetchOutcome]:
    """Run an injected, non-blocking, bounded `fetch` and merge its snapshot into state.

    The fetch owns its timeout; production must pass a bounded call. Any exception (including
    TimeoutError) leaves state unchanged and reports FETCH_FAILED, so held monitoring is
    unaffected and no old data is re-stamped fresh. `fetch` must return a `FetchResult` that
    carries the ORIGINAL receipt time; anything else (e.g. a bare tuple) is rejected, because
    stamping a cached payload with now() would renew its age.
    """
    try:
        returned = fetch()
        if not isinstance(returned, FetchResult):
            return state, FetchOutcome("REJECTED", None, "FETCH_RESULT_REQUIRED")
        received = _utc(returned.received_at)
        if received > _utc(clock()):
            return state, FetchOutcome("REJECTED", None, "RECEIPT_IN_FUTURE")
        snap = snapshot_from_payload(returned.payload, received_at=received, provider=provider,
                                     requested_count=requested_count)
    except Exception as exc:  # fail closed; type name only, messages may carry sensitive text
        return state, FetchOutcome("FETCH_FAILED", None, type(exc).__name__)
    merged, ingested, rejected = _merge(state.snapshots, (snap,), state.max_history)
    if rejected:
        return state, FetchOutcome("REJECTED", snap.snapshot_id, rejected[0][1])
    status = "INGESTED" if ingested else "DUPLICATE"
    return dataclasses.replace(state, snapshots=merged), FetchOutcome(status, snap.snapshot_id)


# --------------------------------------------------------------------------- serialization

def to_dict(obj: Any) -> Any:
    """JSON-serializable form (Decimal -> str, datetime -> ISO UTC). Performs no I/O."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.astimezone(timezone.utc).isoformat()
    if isinstance(obj, timedelta):
        return obj.total_seconds()
    if isinstance(obj, (tuple, list)):
        return [to_dict(v) for v in obj]
    if isinstance(obj, Mapping):
        return {str(k): to_dict(v) for k, v in obj.items()}
    return obj
