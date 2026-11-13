"""Read-only ranking source adapter and append-only research log for DAY01 Scout.

Wraps an injected source returning the EXISTING `(rankedAt, rankings)` tuple (what
`TossMarketData._domestic_trading_amount_rankings()` returns) into the `FetchResult` that
`day01_scout.ingest_fetch` requires. It never imports settings, the database, orders or
credentials: the caller injects the source. Nothing here starts a worker, writes to PostgreSQL
or places an order.

Receipt time rule: the existing client keeps a 30 s cache without a receipt time. We stamp the
receipt time ONCE, right after the source returns a payload object we have not seen before.
That moment is never earlier than the real receipt (so no future-data leak) and can be later by
at most the time the payload sat in the client cache before our first read (a conservative
bias). A cache hit returns the SAME object and reuses the stored stamp, never `clock()`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from .day01_scout import FetchResult, RankingSnapshot, ScoutResult, snapshot_from_payload, to_dict

LOG_SCHEMA = "day01-scout-research-log-1"
_PROTECTED = {".env", ".backups", ".paper-history", ".local-secrets", ".git"}
_SECRET_KEY = re.compile(r"token|secret|authorization|password|passwd|api[-_]?key|cookie|bearer|credential",
                         re.IGNORECASE)

__all__ = ["RankingFetcher", "ResearchLog", "toss_ranking_source", "redact"]


def redact(value: Any) -> Any:
    """Copy of `value` with any secret-looking key's value replaced (defense in depth)."""
    if isinstance(value, Mapping):
        return {str(k): "[REDACTED]" if _SECRET_KEY.search(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def _raw_digest(rows: Any) -> str:
    return hashlib.sha256(json.dumps(redact(rows), sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


class ResearchLog:
    """Append-only JSON Lines file. One record per line, flushed and fsynced; never rewritten.

    The path must be absolute, end in `.jsonl`, and stay outside protected locations. There is
    no default location: the caller chooses an isolated research directory.
    """

    def __init__(self, path, *, clock: Callable[[], datetime]):
        p = Path(path)
        if not p.is_absolute() or p.suffix != ".jsonl":
            raise ValueError("absolute .jsonl path required")
        if any(part in _PROTECTED or part.startswith(".env") for part in p.parts):
            raise ValueError("protected location")
        if p.is_symlink() or p.is_dir():
            raise ValueError("path must be a regular file")
        self.path, self._clock = p, clock

    def append(self, kind: str, **fields) -> None:
        record = {"schema": LOG_SCHEMA, "kind": kind, "written_at": to_dict(self._clock())}
        record.update(redact(to_dict(fields)))
        line = json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8", newline="\n") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())

    def record_scout_result(self, result: ScoutResult, **extra) -> None:
        self.append(
            "scout_result", as_of=result.as_of, status=result.status, snapshot=result.snapshot,
            coverage=result.coverage, candidates=[c.symbol for c in result.candidates],
            candidate_gaps={c.symbol: c.verification_gaps for c in result.candidates},
            exclusions=[(e.rank, e.symbol, e.reasons) for e in result.exclusions],
            shortage=result.shortage_reasons, snapshot_rejections=result.snapshot_rejections,
            held=[h.symbol for h in result.held], portfolio_status=result.portfolio_status, **extra)


class RankingFetcher:
    """Callable for `ingest_fetch(state, fetcher, clock=clock)`; returns `FetchResult`.

    * Preserves the first receipt time of a cached payload object.
    * Logs success (raw rows once per new payload), cache hits, failures (error type/status/
      code only: provider messages are never written) and same-`rankedAt` content conflicts.
    * A missing provider time stays missing (Scout then blocks entries as UNKNOWN_PROVIDER_TIME).
    * Exceptions are logged and re-raised; `ingest_fetch` turns them into FETCH_FAILED.
    """

    def __init__(self, source: Callable[[], tuple], *, clock: Callable[[], datetime],
                 log: ResearchLog | None = None, requested_count: int = 100):
        self._source, self._clock, self._log = source, clock, log
        self._requested_count = requested_count
        self._last_payload: Any = None
        self._last_received: datetime | None = None
        self._ranked_digest: dict = {}
        self.fetches = 0

    def _emit(self, kind: str, **fields) -> None:
        if self._log is not None:
            self._log.append(kind, **fields)

    def __call__(self) -> FetchResult:
        self.fetches += 1
        fetch_no = self.fetches
        try:
            payload = self._source()
        except Exception as exc:
            self._emit("fetch_failed", fetch_no=fetch_no, error_type=type(exc).__name__,
                       status_code=getattr(exc, "status_code", None), error_code=getattr(exc, "error_code", None))
            raise
        if not (isinstance(payload, tuple) and len(payload) == 2):
            self._emit("fetch_failed", fetch_no=fetch_no, error_type="BadSourcePayload")
            raise ValueError("source must return (rankedAt, rankings)")
        if payload is self._last_payload and self._last_received is not None:
            self._emit("fetch_cache_hit", fetch_no=fetch_no, received_at=self._last_received,
                       note="same payload object; original receipt time reused")
            return FetchResult(payload, self._last_received)
        received = self._clock()                     # first observation: upper bound of receipt
        self._last_payload, self._last_received = payload, received
        ranked_raw, rows = payload
        digest = _raw_digest(rows)
        snap: RankingSnapshot | None
        try:
            snap = snapshot_from_payload(payload, received_at=received, requested_count=self._requested_count)
            quality = dict(provider_time="PRESENT" if snap.provider_ranked_at else "MISSING_OR_INVALID",
                           parsed_rows=len(snap.rows), defects=snap.defects, snapshot_id=snap.snapshot_id,
                           content_digest=snap.content_digest)
        except ValueError as exc:
            snap, quality = None, dict(provider_time="UNKNOWN", defects=(type(exc).__name__,))
        self._emit("fetch_ok", fetch_no=fetch_no, received_at=received, provider_ranked_at_raw=ranked_raw,
                   requested_count=self._requested_count, raw_row_count=len(rows) if isinstance(rows, list) else None,
                   raw_digest=digest, quality=quality, rows=rows,
                   receipt_note="stamped after source returned; upper bound of true receipt")
        key = str(ranked_raw)
        previous = self._ranked_digest.setdefault(key, digest)
        if previous != digest:
            self._emit("conflict", fetch_no=fetch_no, reason="SAME_RANKED_AT_DIFFERENT_CONTENT",
                       provider_ranked_at_raw=ranked_raw, first_raw_digest=previous, new_raw_digest=digest)
        return FetchResult(payload, received)


def toss_ranking_source(client, *, budget_seconds: float = 15.0) -> Callable[[], tuple]:
    """Source over an existing Toss client's ranking GET, bounded by the client's own
    `RequestBudget` (deadline for waits, locks and the request). Only the ranking read is
    called; no account, order or token-management method is touched by this adapter."""
    def source():
        from .brokers.toss.request_budget import RequestBudget  # pure module, no settings import
        with RequestBudget(budget_seconds, 1).activate():
            return client._domestic_trading_amount_rankings()
    return source
