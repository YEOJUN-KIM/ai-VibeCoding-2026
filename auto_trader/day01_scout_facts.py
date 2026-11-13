"""Instrument-facts adapter for DAY01 Scout: Toss `/api/v1/stocks` rows -> `InstrumentFacts`.

Pure apart from injected sources and an optional `ResearchLog`: no settings, database, order or
credential access, and no network of its own. Sources are plain callables so a test can feed
synthetic rows; `toss_stocks_source` / `toss_directory_source` wrap an existing Toss client.

Honesty rules
* A field the response does not carry stays None (UNKNOWN). A field that IS present but has an
  unexpected type (or null/empty) is MALFORMED: it becomes None and is never filled in from
  another source. Scout then excludes the symbol; nothing here relaxes Scout's policy.
* Symbols are compared as `strip().upper()` (the existing client's convention) across the request,
  the stocks rows, the directory rows and the ranking rows, so a case alias cannot hide a
  conflicting row.
* `observed_at` is OUR receipt time (availability), never a provider time (none is provided).
  It is the later of the stocks-batch receipt and the ranking receipt, and the two must be within
  `max_skew`; otherwise the symbol gets no fact.
* Time-sensitive fields (status, suspension, liquidation) come only from a fresh `/stocks` call.
  `/stocks` rows also carry `market` and `currency` (seen in the real response), which are used
  first. The cached directory (and the ranking row's `currency`) only gap-fill or cross-check them:
  a disagreement blocks the field. The directory is used only when its ORIGINAL receipt time is
  known, is not after the fact, and is on the same KST day.
* Facts are valid from `observed_at` on (Scout ignores later-observed facts), so today's facts
  can never be applied to an earlier `as_of`.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping

from .day01_scout import KST, InstrumentFacts
from .day01_scout_source import ClockRegression, ResearchLog, redact

__all__ = ["ReceiptLedger", "build_instrument_facts", "InstrumentFactsSource",
           "toss_stocks_source", "toss_directory_source", "STOCK_FIELDS_USED"]

BATCH_SIZE = 50          # the existing preset path requests 50 symbols per /stocks call (API limit unverified)
STOCK_FIELDS_USED = ("symbol", "name", "market", "currency", "securityType", "isCommonShare", "status",
                     "koreanMarketDetail.krxTradingSuspended", "koreanMarketDetail.liquidationTrading")
_CONFLICT = object()
_BAD = object()          # present but malformed
_STATIC = ("name", "market", "security_type", "is_common_share")


class ReceiptLedger:
    """First-receipt stamps per payload object (same rules as the ranking fetcher).

    `stamp` never remembers a new object; the caller calls `remember` only after the raw payload
    was recorded, so a failed log write cannot turn a retry into a row-less cache hit."""

    def __init__(self, *, clock: Callable[[], datetime], monotonic: Callable[[], float] = time.monotonic,
                 slack: timedelta = timedelta(seconds=1), remember: int = 256):
        self._clock, self._mono, self._slack, self._remember = clock, monotonic, slack, remember
        self._seen: OrderedDict = OrderedDict()
        self._last: tuple | None = None
        self._lock = threading.Lock()

    def stamp(self, obj: Any) -> tuple[datetime, bool]:
        """Return (first_receipt, was_cached). Raises ClockRegression instead of stamping badly."""
        with self._lock:
            known = self._seen.get(id(obj))
            if known is not None and known[0] is obj:
                return known[1], True
            received, mono = self._clock(), self._mono()
            if not isinstance(received, datetime) or received.utcoffset() is None:
                raise ClockRegression("clock must return an aware datetime")
            if self._last is not None:
                prev_utc, prev_mono = self._last
                if received < prev_utc or (received - prev_utc) - timedelta(seconds=mono - prev_mono) < -self._slack:
                    raise ClockRegression("wall clock moved backwards")
            self._last = (received, mono)
            return received, False

    def remember(self, obj: Any, received: datetime) -> None:
        with self._lock:
            self._seen[id(obj)] = (obj, received)
            while len(self._seen) > self._remember:
                self._seen.popitem(last=False)


def _text(mapping: Mapping, key: str):
    if key not in mapping:
        return None
    value = mapping[key]
    return value.strip() if isinstance(value, str) and value.strip() else _BAD


def _bool(mapping: Mapping, key: str):
    if key not in mapping:
        return None
    value = mapping[key]
    return value if type(value) is bool else _BAD


def _detail(row: Mapping):
    if "koreanMarketDetail" not in row:
        return {}, False
    detail = row["koreanMarketDetail"]
    return (detail, False) if isinstance(detail, Mapping) else ({}, True)


def _project_stock(row: Mapping) -> dict:
    detail, bad = _detail(row)
    return dict(name=_text(row, "name"), market=_text(row, "market"), currency=_text(row, "currency"),
                security_type=_text(row, "securityType"),
                is_common_share=_bool(row, "isCommonShare"), status=_text(row, "status"),
                krx_trading_suspended=_BAD if bad else _bool(detail, "krxTradingSuspended"),
                liquidation_trading=_BAD if bad else _bool(detail, "liquidationTrading"))


def _project_directory(row: Mapping) -> dict:
    return dict(name=_text(row, "name"), market=_text(row, "market"),
                security_type=_text(row, "securityType"), is_common_share=_bool(row, "isCommonShare"))


def _norm(value: Any):
    return value.strip().upper() if isinstance(value, str) and value.strip() else None


def _group(rows: Iterable, project: Callable[[Mapping], dict]) -> dict:
    """normalized symbol -> projection, or _CONFLICT if the same source repeats a symbol (in any
    spelling) with different data."""
    out: dict = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        key = _norm(row.get("symbol"))
        if key is None:
            continue
        item = project(row)
        if key in out and out[key] != item:
            out[key] = _CONFLICT
        else:
            out.setdefault(key, item)
    return out


def build_instrument_facts(*, stocks_batches, directory=None, ranking_rows=(), ranking_received_at,
                           max_skew: timedelta = timedelta(seconds=60)) -> tuple:
    """Pure conversion. `stocks_batches`: iterable of (requested_symbols, rows, received_at);
    `directory`: (rows, original_received_at) or None; `ranking_rows`: raw ranking rows (for
    `currency`). Returns (facts, notes) where notes is a tuple of (symbol, code)."""
    notes: list = []
    facts: list = []
    ranking = _group(ranking_rows, lambda r: dict(currency=_text(r, "currency")))
    dir_rows, dir_at = directory if directory is not None else ((), None)
    directory_by_symbol = _group(dir_rows, _project_directory)
    for requested, rows, batch_at in stocks_batches:
        by_symbol = _group(rows, _project_stock)
        for symbol in requested:
            key = _norm(symbol)
            if key is None:
                continue
            if key not in by_symbol:
                notes.append((symbol, "NO_STOCKS_ROW"))
                continue
            if abs(batch_at - ranking_received_at) > max_skew:
                notes.append((symbol, "SOURCE_SKEW"))
                continue
            observed = max(batch_at, ranking_received_at)
            stock = by_symbol[key]
            values = dict(name=None, market=None, currency=None, security_type=None, is_common_share=None,
                          status=None, krx_trading_suspended=None, liquidation_trading=None)
            if stock is _CONFLICT or ranking.get(key) is _CONFLICT:
                notes.append((symbol, "DUPLICATE_CONFLICT"))
            else:
                values.update(stock)
                if key in ranking:                                   # cross-check the stocks row's own currency
                    have, other = values["currency"], ranking[key]["currency"]
                    if have is _BAD or other is _BAD:
                        values["currency"] = _BAD
                    elif have is None:
                        values["currency"] = other
                    elif other is not None and other != have:
                        values["currency"] = None
                        notes.append((symbol, "CONFLICT:currency"))
                static = directory_by_symbol.get(key)
                if static is None:
                    notes.append((symbol, "NOT_IN_DIRECTORY"))
                elif dir_at is None or dir_at > observed or dir_at.astimezone(KST).date() != observed.astimezone(KST).date():
                    notes.append((symbol, "DIRECTORY_NOT_CURRENT"))
                elif static is _CONFLICT:
                    notes.append((symbol, "DUPLICATE_CONFLICT_DIRECTORY"))
                else:
                    for field in _STATIC:
                        have, other = values[field], static[field]
                        if have is _BAD or other is _BAD:
                            values[field] = _BAD                      # malformed never gets filled in
                        elif have is None:
                            values[field] = other
                        elif other is not None and other != have:
                            values[field] = None
                            notes.append((symbol, f"CONFLICT:{field}"))
                for field, value in list(values.items()):
                    if value is _BAD:
                        values[field] = None
                        notes.append((symbol, f"MALFORMED:{field}"))
            facts.append(InstrumentFacts(symbol.strip(), observed, **values))
    return tuple(facts), tuple(notes)


class InstrumentFactsSource:
    """Collects facts for ranked symbols. Every failure degrades to FEWER facts (fail closed).

    `directory_source()` must return `(rows, original_received_at_or_None)`; an unknown receipt
    time makes the directory unusable (a cache that may be from yesterday cannot be dated)."""

    def __init__(self, stocks_source: Callable[[tuple], list], *, clock: Callable[[], datetime],
                 directory_source: Callable[[], tuple] | None = None, log: ResearchLog | None = None,
                 batch_size: int = BATCH_SIZE, max_skew: timedelta = timedelta(seconds=60)):
        if type(batch_size) is not int or not 1 <= batch_size <= BATCH_SIZE:
            raise ValueError(f"batch_size must be 1..{BATCH_SIZE}")
        self._stocks, self._directory, self._log = stocks_source, directory_source, log
        self._ledger = ReceiptLedger(clock=clock)
        self._batch, self._skew = batch_size, max_skew

    def _emit(self, kind: str, **fields) -> None:
        if self._log is not None:
            self._log.append(kind, **fields)

    def _collect_directory(self, notes: list):
        if self._directory is None:
            return None
        try:
            returned = self._directory()
            if not (isinstance(returned, tuple) and len(returned) == 2 and isinstance(returned[0], list)):
                raise ValueError("directory source must return (rows, received_at)")
            rows, received = returned
            if received is None:
                self._emit("directory_fetch_ok", received_at=None, row_count=len(rows),
                           note="original receipt time unknown; directory not used")
                notes.append((None, "DIRECTORY_RECEIPT_UNKNOWN"))
                return None
            if not isinstance(received, datetime) or received.utcoffset() is None:
                raise ValueError("directory receipt time must be an aware datetime")
            digest = hashlib.sha256(json.dumps(redact(rows), sort_keys=True, default=str,
                                               ensure_ascii=False).encode("utf-8")).hexdigest()
            self._emit("directory_fetch_ok", received_at=received, row_count=len(rows), raw_digest=digest,
                       note="rows not stored (large); digest only")
            return rows, received
        except Exception as exc:
            try:
                self._emit("directory_fetch_failed", error_type=type(exc).__name__,
                           status_code=getattr(exc, "status_code", None), error_code=getattr(exc, "error_code", None))
            except Exception:
                pass
            notes.append((None, "DIRECTORY_FETCH_FAILED"))
            return None

    def collect(self, symbols: Iterable[str], *, ranking_rows: Iterable, ranking_received_at: datetime) -> tuple:
        wanted = tuple(dict.fromkeys(s.strip() for s in symbols if isinstance(s, str) and s.strip()))
        ranking_rows = tuple(ranking_rows)
        notes: list = []
        directory = self._collect_directory(notes)          # first: it must be available before the facts
        batches = []
        for start in range(0, len(wanted), self._batch):
            chunk = wanted[start:start + self._batch]
            try:
                rows = self._stocks(chunk)
                if not isinstance(rows, list):
                    raise ValueError("stocks source must return a list")
                received, cached = self._ledger.stamp(rows)
                self._emit("stocks_fetch_ok", symbols=chunk, received_at=received, cache_hit=cached,
                           row_count=len(rows), rows=None if cached else rows)
                if not cached:
                    self._ledger.remember(rows, received)   # only after the raw rows were recorded
            except Exception as exc:
                try:
                    self._emit("stocks_fetch_failed", symbols=chunk, error_type=type(exc).__name__,
                               status_code=getattr(exc, "status_code", None),
                               error_code=getattr(exc, "error_code", None))
                except Exception:
                    pass
                notes.extend((s, "STOCKS_FETCH_FAILED") for s in chunk)
                continue
            batches.append((chunk, rows, received))
        facts, build_notes = build_instrument_facts(stocks_batches=batches, directory=directory,
                                                    ranking_rows=ranking_rows,
                                                    ranking_received_at=ranking_received_at, max_skew=self._skew)
        notes.extend(build_notes)
        try:
            self._emit("instrument_facts", fact_provenance="REAL_FIELDS_ONLY_UNKNOWN_STAYS_NONE", count=len(facts),
                       facts=[(f.symbol, f.observed_at, f.market, f.currency, f.security_type, f.is_common_share,
                               f.status, f.krx_trading_suspended, f.liquidation_trading) for f in facts],
                       notes=notes)
        except Exception:
            pass                                             # the summary is derived; raw rows are already logged
        return facts, tuple(notes)


def toss_stocks_source(client, *, budget_seconds: float = 15.0) -> Callable[[tuple], list]:
    """Fresh `GET /api/v1/stocks?symbols=...` (bypasses the client's 12 h per-symbol cache)."""
    def source(symbols: tuple) -> list:
        from urllib.parse import urlencode
        from .brokers.toss.request_budget import RequestBudget
        url = f"{client.base_url}/api/v1/stocks?{urlencode({'symbols': ','.join(symbols)})}"
        with RequestBudget(budget_seconds, 1).activate():
            payload = client._authorized_json_request(url)
        result = payload.get("result") if isinstance(payload, Mapping) else None
        if not isinstance(result, list):
            raise ValueError("unexpected stocks response shape")
        return result
    return source


def toss_directory_source(client, *, budget_seconds: float = 30.0,
                          clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                          monotonic: Callable[[], float] = time.monotonic) -> Callable[[], tuple]:
    """Directory (`/api/v1/stocks/all`, KOSPI/KOSDAQ, `market` tagged by the client from the request
    parameter) as `(rows, original_receipt_time)`.

    The client keeps the directory up to 12 h but not its receipt time. It does keep the expiry
    (`_stock_universe_expires_at = monotonic() + 43200` set right after the fetch), from which the
    original receipt time is recovered as `now - (monotonic() - (expiry - 43200))`; that is the end
    of the fetch, i.e. never earlier than the real receipt. If the attribute is missing or implausible
    the receipt time is returned as None and the directory is not used."""
    def source() -> tuple:
        from .brokers.toss.request_budget import RequestBudget
        with RequestBudget(budget_seconds, 2).activate():
            rows = client._domestic_stock_universe()
        expiry = getattr(client, "_stock_universe_expires_at", None)
        now_mono, now = monotonic(), clock()
        if not isinstance(expiry, (int, float)) or isinstance(expiry, bool):
            return rows, None
        age = now_mono - (expiry - 43200)
        if not 0 <= age <= 43200 + 60:
            return rows, None
        return rows, now - timedelta(seconds=age)
    return source
