"""Official Toss order-book (`GET /api/v1/orderbook?symbol=`) -> best bid/ask -> `LiquidityFacts.spread_pct`.

Official contract (Toss Open API reference, MarketDataApi.getOrderbook, re-read 2026-11-13):
  request : GET, required query `symbol` (KRX 6-char code, digits/letters), rate-limit group MARKET_DATA
            (documented 15 req/s per client x group). One symbol per request; no batch form is documented.
  response: {"result": {"timestamp": Date|null ("데이터 시각. 데이터 미제공 시 null"), "currency": Currency,
            "asks": [{"price": BigDecimal, "volume": BigDecimal}, ...] ("매도호가 목록 (낮은 가격순)"),
            "bids": [... same ...] ("매수호가 목록 (높은 가격순)")}}
NOT documented (treated as UNKNOWN, never assumed): the timestamp text format, whether numbers are JSON
numbers or strings, the number of levels, the Currency values, real-time vs delayed, the error body for an
invalid symbol, and caching. So: a timestamp must be ISO-8601 with an explicit offset or the book is
UNKNOWN; numbers are accepted as int/str/Decimal/float (a float is read through its shortest repr, as a
JSON parser produced it); anything else is an invalid level.

Pure conversion (`parse_orderbook`, `with_spread`) has no I/O. `fetch_orderbook_results` is the only network
path and refuses an client that was not fenced by `guard_toss_orderbook_client`. Nothing here touches the
database, orders, settings, workers or Scout's thresholds: the spread only fills `LiquidityFacts.spread_pct`;
Scout's own gate (`max_spread_pct`, default 1 = percent) still decides. UNKNOWN stays None.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Context, Decimal, DecimalException, DivisionByZero, InvalidOperation, Overflow, localcontext
from fractions import Fraction
from typing import Any, Callable, Mapping
from urllib.parse import urlencode

from .day01_scout import LiquidityFacts
from .day01_scout_source import ORDERBOOK_PATH, fence_intact

DEFAULT_MAX_AGE_SECONDS = Decimal(10)      # research starting value, NOT a confirmed operating policy
ANOMALY_SPREAD_PCT = Decimal(50)           # strictly greater than this is an anomaly, not a usable spread
# A provider time later than our receipt time is never accepted (no tolerance): data stamped after we received
# it cannot be trusted as 'already known'. A tolerance for clock skew would need an explicit policy decision.
MAX_ABS_EXPONENT_HIGH, MAX_ABS_EXPONENT_LOW, MAX_DIGITS = 15, -9, 30   # accepted magnitude: 1e-9 .. <1e16
MAX_SYMBOLS = 4                            # matches the 4-request orderbook cap of the fence
_SYMBOL = re.compile(r"[0-9A-Z]{6}")
_NUMERIC = re.compile(r"[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?")   # ASCII only: Decimal() also takes "1_0" and Arabic digits



def _fixed_context() -> Context:
    """Every Decimal operation here runs in THIS context, never the caller's ambient one (a low precision or an
    Inexact trap elsewhere in the process must not change results or abort a run). 60 digits hold the exact sum
    or difference of any two accepted numbers (<= 30 digits, exponents within 1e-9..1e16)."""
    return Context(prec=60, traps=[InvalidOperation, DivisionByZero, Overflow])


OK, UNKNOWN, ANOMALY = "OK", "UNKNOWN", "ANOMALY"

__all__ = ["OrderbookResult", "parse_orderbook", "with_spread", "fetch_orderbook_results", "valid_symbol",
           "validate_symbols", "DEFAULT_MAX_AGE_SECONDS", "ANOMALY_SPREAD_PCT", "MAX_SYMBOLS", "OK", "UNKNOWN",
           "ANOMALY"]


@dataclass(frozen=True)
class OrderbookResult:
    symbol: str
    status: str                         # OK | UNKNOWN | ANOMALY
    spread_pct: Decimal | None          # only when status == OK; otherwise None (Scout sees UNKNOWN)
    reasons: tuple = ()                 # failure codes (why not OK)
    notes: tuple = ()                   # non-failing observations (e.g. skipped levels)
    best_bid: Decimal | None = None
    best_ask: Decimal | None = None
    bid_volume: Decimal | None = None
    ask_volume: Decimal | None = None
    provider_time: datetime | None = None    # the provider's `timestamp` (UTC)
    received_at: datetime | None = None      # OUR receipt stamp (UTC); never the provider's
    age_seconds: Decimal | None = None
    observed_spread_pct: Decimal | None = None   # kept for ANOMALY diagnosis only


def valid_symbol(value: Any) -> bool:
    return isinstance(value, str) and _SYMBOL.fullmatch(value) is not None


def validate_symbols(symbols: Any, *, max_symbols: int = MAX_SYMBOLS) -> tuple:
    """Domestic 6-char codes, unique, 1..max_symbols. Raises ValueError before any I/O."""
    if isinstance(symbols, (str, bytes)) or not isinstance(symbols, (list, tuple)):
        raise ValueError("symbols must be a list or tuple")
    if not 1 <= len(symbols) <= max_symbols:
        raise ValueError(f"1..{max_symbols} symbols required")
    if not all(valid_symbol(s) for s in symbols):
        raise ValueError("symbol must be a 6-character [0-9A-Z] code")
    if len(set(symbols)) != len(symbols):
        raise ValueError("duplicate symbols")
    return tuple(symbols)


def _number(value: Any) -> Decimal | None:
    """Finite Decimal of bounded size, or None. bool/unsupported types, NaN/Infinity, absurd magnitudes
    (adjusted exponent outside [-9, 15]) and more than 30 digits are invalid, so later arithmetic can
    neither overflow nor lose precision. float goes through its shortest repr."""
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, float):
            number = Decimal(repr(value))
        elif isinstance(value, Decimal):
            number = value
        elif isinstance(value, int):
            if value.bit_length() > 200:
                return None
            number = Decimal(value)
        elif isinstance(value, str) and len(value) <= 64 and _NUMERIC.fullmatch(value):
            number = Decimal(value)
        else:
            return None
        if not number.is_finite() or len(number.as_tuple().digits) > MAX_DIGITS:
            return None
        if number != 0 and not MAX_ABS_EXPONENT_LOW <= number.adjusted() <= MAX_ABS_EXPONENT_HIGH:
            return None
    except (InvalidOperation, ValueError, OverflowError):
        return None
    return number


def _best(levels: Any, *, pick_max: bool):
    """(price, volume, skipped) of the best VALID level, independent of the list order.
    A level is valid when price > 0 and volume > 0 (finite Decimals). Skipped levels can only make the
    spread wider (a skipped better price is replaced by a worse one), so skipping is conservative."""
    if not isinstance(levels, list):
        return None, None, None
    best = None
    skipped = 0
    for level in levels:
        price = _number(level.get("price")) if isinstance(level, Mapping) else None
        volume = _number(level.get("volume")) if isinstance(level, Mapping) else None
        if price is None or volume is None or price <= 0 or volume <= 0:
            skipped += 1
            continue
        if best is None or (price > best[0] if pick_max else price < best[0]):
            best = (price, volume)
        elif price == best[0]:
            best = (price, best[1] + volume)        # duplicate price level: quantities add up
    return (best[0], best[1], skipped) if best else (None, None, skipped)


def _max_age(value: Any) -> Decimal:
    """The ONE freshness-window check used by both `parse_orderbook` and `with_spread`: a finite number in
    (0, 1,000,000] seconds, never bool. Anything else is a ValueError (bad argument), so an out-of-range value
    can neither loosen nor silently change the freshness test. Call inside the fixed Decimal context."""
    if isinstance(value, bool):
        raise ValueError("max_age_seconds must be a number")
    try:
        age = value if isinstance(value, Decimal) else Decimal(str(value))
    except (DecimalException, ValueError):
        raise ValueError("max_age_seconds must be a number") from None
    if not age.is_finite() or not 0 < age <= 10**6:
        raise ValueError("max_age_seconds must be positive and at most 1,000,000")
    return age


def _provider_time(value: Any):
    if value is None:
        return None, "TIMESTAMP_MISSING"
    if not isinstance(value, str) or len(value) > 64:
        return None, "TIMESTAMP_INVALID"
    try:
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None or moment.utcoffset() is None:
            return None, "TIMESTAMP_NO_TIMEZONE"
        return moment.astimezone(timezone.utc), None
    except (ValueError, OverflowError):                  # includes year-1 / year-9999 offset overflow
        return None, "TIMESTAMP_INVALID"


def parse_orderbook(symbol: str, payload: Any, *, received_at: datetime,
                    max_age_seconds: Any = DEFAULT_MAX_AGE_SECONDS) -> OrderbookResult:
    """Convert one official order-book response into a spread. Never raises for a bad payload: every
    failure becomes UNKNOWN with reason codes (arithmetic failures become NUMERIC_ERROR). `received_at` is
    OUR receipt time (tz-aware). Raises ValueError only for bad arguments."""
    try:
        with localcontext(_fixed_context()):
            return _parse_orderbook(symbol, payload, received_at, max_age_seconds)
    except (DecimalException, ArithmeticError):
        received = received_at.astimezone(timezone.utc) if isinstance(received_at, datetime) and received_at.tzinfo else None
        return OrderbookResult(symbol, UNKNOWN, None, ("NUMERIC_ERROR",), received_at=received)


def _parse_orderbook(symbol: str, payload: Any, received_at: datetime, max_age_seconds: Any) -> OrderbookResult:
    if not valid_symbol(symbol):
        raise ValueError("symbol must be a 6-character [0-9A-Z] code")
    if not isinstance(received_at, datetime) or received_at.tzinfo is None or received_at.utcoffset() is None:
        raise ValueError("received_at must be timezone-aware")
    max_age = _max_age(max_age_seconds)
    received = received_at.astimezone(timezone.utc)

    result = payload.get("result") if isinstance(payload, Mapping) else None
    if not isinstance(result, Mapping):
        return OrderbookResult(symbol, UNKNOWN, None, ("RESPONSE_MALFORMED",), received_at=received)

    reasons, notes = [], []
    moment, why = _provider_time(result.get("timestamp"))
    age = None
    if why:
        reasons.append(why)
    else:
        delta = received - moment                          # exact timedelta; rounding below is for display only
        age = (Decimal(delta // timedelta(microseconds=1)) / Decimal(1_000_000)).quantize(Decimal("0.001"))
        if delta < timedelta(0):
            reasons.append("TIMESTAMP_IN_FUTURE")
        elif delta > timedelta(microseconds=int(max_age * 1_000_000)):
            reasons.append("STALE_ORDERBOOK")

    if result.get("currency") != "KRW":
        reasons.append("CURRENCY_MISSING" if result.get("currency") is None else "CURRENCY_NOT_KRW")

    ask, ask_vol, ask_skipped = _best(result.get("asks"), pick_max=False)
    bid, bid_vol, bid_skipped = _best(result.get("bids"), pick_max=True)
    if ask_skipped is None:
        reasons.append("ASKS_MALFORMED")
    elif ask is None:
        reasons.append("NO_VALID_ASK")
    if bid_skipped is None:
        reasons.append("BIDS_MALFORMED")
    elif bid is None:
        reasons.append("NO_VALID_BID")
    if ask_skipped:
        notes.append(f"ASK_LEVELS_SKIPPED={ask_skipped}")
    if bid_skipped:
        notes.append(f"BID_LEVELS_SKIPPED={bid_skipped}")

    spread = exact = None
    if ask is not None and bid is not None:
        if bid > ask:
            reasons.append("CROSSED_BOOK")
        elif bid == ask:
            reasons.append("LOCKED_BOOK")
        else:
            exact = (Fraction(ask) - Fraction(bid)) * 200 / (Fraction(ask) + Fraction(bid))   # exact percent
            try:
                with localcontext() as context:
                    context.prec = 40
                    spread = Decimal(exact.numerator) / Decimal(exact.denominator)
            except (DecimalException, ArithmeticError):
                reasons.append("NUMERIC_ERROR")

    common = dict(best_bid=bid, best_ask=ask, bid_volume=bid_vol, ask_volume=ask_vol, provider_time=moment,
                  received_at=received, age_seconds=age, notes=tuple(notes))
    if reasons:
        return OrderbookResult(symbol, UNKNOWN, None, tuple(reasons), **common)
    if exact > ANOMALY_SPREAD_PCT:                       # compared exactly, not after rounding
        return OrderbookResult(symbol, ANOMALY, None, ("ANOMALOUS_SPREAD",), observed_spread_pct=spread, **common)
    return OrderbookResult(symbol, OK, spread, (), **common)


def with_spread(liquidity: LiquidityFacts, result: OrderbookResult, *, as_of: datetime,
                max_age_seconds: Any = DEFAULT_MAX_AGE_SECONDS) -> LiquidityFacts:
    """Copy of a `LiquidityFacts` with the spread filled from an OK result, judged AT the decision time `as_of`.

    The spread stays None (UNKNOWN) when: the result is not OK; the book was received after `as_of` (it did not
    exist yet for that decision); or the book's provider time is older than `max_age_seconds` at `as_of`.
    `observed_at` becomes the EARLIER of the two receipt stamps, so Scout's own `max_fact_age` rule is applied
    to the oldest source and a fresh book cannot make an old ranking look fresh. Because both stamps are known
    to be <= `as_of`, taking the earlier one never claims availability before it happened. Turnover and volume
    are untouched; Scout's thresholds are not read or changed."""
    if liquidity.symbol != result.symbol:
        raise ValueError("symbol mismatch")
    if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    as_of = as_of.astimezone(timezone.utc)
    with localcontext(_fixed_context()):
        limit = timedelta(microseconds=int(_max_age(max_age_seconds) * 1_000_000))
    if (result.status != OK or result.received_at > as_of or liquidity.observed_at > as_of
            or as_of - result.provider_time > limit):
        return replace(liquidity, spread_pct=None)
    return replace(liquidity, spread_pct=result.spread_pct,
                   observed_at=min(liquidity.observed_at, result.received_at))


class _Stop(Exception):
    pass


def _failure(symbol: str, code: str, received_at, *notes) -> OrderbookResult:
    return OrderbookResult(symbol, UNKNOWN, None, (code,), notes=tuple(notes), received_at=received_at)


def fetch_orderbook_results(client, symbols, *, clock: Callable[[], datetime],
                            max_age_seconds: Any = DEFAULT_MAX_AGE_SECONDS, log=None,
                            budget_seconds: float = 15.0):
    """Read-only fetch of one order book per symbol through a client fenced by `guard_toss_orderbook_client`.

    Returns `(results, stopped)`: one `OrderbookResult` per requested symbol (in order) and the reason the
    run stopped early, or None. Symbols are validated before any I/O. The run stops (remaining symbols become
    `NOT_FETCHED`) on a fence refusal, 429, 401, a redirect/3xx, a timeout/network error, an unexpected error
    or a clock regression; only a 400/404 (bad symbol) is per-symbol. Failures keep only status and a slug
    code. Provider text, URLs and headers are never stored."""
    if not fence_intact(client):
        raise ValueError("client must be fenced with guard_toss_orderbook_client first (and the fence left intact)")
    wanted = validate_symbols(symbols)
    from .brokers.toss.request_budget import RequestBudget, RequestBudgetExceeded  # pure module

    results, stopped, last_stamp = [], None, None
    for symbol in wanted:
        raw = None
        if stopped:
            results.append(_failure(symbol, "NOT_FETCHED", None, f"stopped={stopped}"))
            continue
        if not fence_intact(client):                       # re-proved before every request, not only once
            stopped = "FENCE_REFUSED"
            results.append(_failure(symbol, "FENCE_REFUSED", None, "fence-replaced"))
            if log is not None:
                _log(log, results[-1], None)
            continue
        try:
            url = f"{client.base_url}{ORDERBOOK_PATH}?{urlencode({'symbol': symbol})}"
            try:
                with RequestBudget(budget_seconds, 1).activate():
                    payload = client._authorized_json_request(url)
            except PermissionError:
                stopped = "FENCE_REFUSED"
                raise _Stop(_failure(symbol, "FENCE_REFUSED", None))
            except RequestBudgetExceeded:
                stopped = "TIME_BUDGET"
                raise _Stop(_failure(symbol, "FETCH_FAILED", None, "code=time-budget"))
            except Exception as exc:
                status, code = getattr(exc, "status_code", None), getattr(exc, "error_code", None)
                if status not in (400, 404):
                    stopped = f"FETCH_ERROR_{status or type(exc).__name__}"
                raise _Stop(_failure(symbol, "FETCH_FAILED", None, f"status={status}", f"code={code}"))
            received = clock()
            if not isinstance(received, datetime) or received.tzinfo is None:
                stopped = "CLOCK_INVALID"
                raise _Stop(_failure(symbol, "CLOCK_INVALID", None))
            if last_stamp is not None and received < last_stamp:
                stopped = "CLOCK_REGRESSION"
                raise _Stop(_failure(symbol, "CLOCK_REGRESSION", received))
            last_stamp, raw = received, payload
            results.append(parse_orderbook(symbol, payload, received_at=received, max_age_seconds=max_age_seconds))
        except _Stop as stop:
            results.append(stop.args[0])
        if log is not None:
            _log(log, results[-1], raw)
    return results, stopped


_ISO_LIKE = re.compile(r"[0-9T:.+Zz-]{1,40}")
_CURRENCY = re.compile(r"[A-Z]{3}")
MAX_LOGGED_LEVELS = 50


def _safe_raw(payload: Any):
    with localcontext(_fixed_context()):
        return _safe_raw_inner(payload)


def _safe_raw_inner(payload: Any):
    """Diagnostic copy limited to the documented fields in a validated shape: timestamp text of date characters
    only, a 3-letter currency, and price/volume as parsed numbers. Any other key or free text in the response
    (messages, ids, echoed secrets or URLs) is dropped, because key-based redaction cannot see secrets in values."""
    result = payload.get("result") if isinstance(payload, Mapping) else None
    if not isinstance(result, Mapping):
        return None
    timestamp, currency = result.get("timestamp"), result.get("currency")

    def levels(name):
        rows = result.get(name)
        if not isinstance(rows, list):
            return None
        out = []
        for row in rows[:MAX_LOGGED_LEVELS]:
            row = row if isinstance(row, Mapping) else {}
            price, volume = _number(row.get("price")), _number(row.get("volume"))
            out.append({"price": None if price is None else str(price),
                        "volume": None if volume is None else str(volume)})
        return out

    return {"timestamp": timestamp if isinstance(timestamp, str) and _ISO_LIKE.fullmatch(timestamp) else
            (None if timestamp is None else "<invalid>"),
            "currency": currency if isinstance(currency, str) and _CURRENCY.fullmatch(currency) else
            (None if currency is None else "<invalid>"),
            "asks": levels("asks"), "bids": levels("bids"),
            "levels_truncated": any(isinstance(result.get(k), list) and len(result[k]) > MAX_LOGGED_LEVELS
                                    for k in ("asks", "bids"))}


def _log(log, result: OrderbookResult, raw) -> None:
    log.append("orderbook_result", symbol=result.symbol, status=result.status, reasons=list(result.reasons),
               notes=list(result.notes), spread_pct=str(result.spread_pct) if result.spread_pct is not None else None,
               observed_spread_pct=str(result.observed_spread_pct) if result.observed_spread_pct is not None else None,
               best_bid=str(result.best_bid) if result.best_bid is not None else None,
               best_ask=str(result.best_ask) if result.best_ask is not None else None,
               provider_time=result.provider_time, received_at=result.received_at,
               age_seconds=str(result.age_seconds) if result.age_seconds is not None else None,
               raw=_safe_raw(raw))
