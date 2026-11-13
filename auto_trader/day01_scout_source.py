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
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

from .day01_scout import FetchResult, RankingSnapshot, ScoutResult, snapshot_from_payload, to_dict

LOG_SCHEMA = "day01-scout-research-log-1"
_PROTECTED = {".env", ".backups", ".paper-history", ".local-secrets", ".git"}
_SECRET_KEY = re.compile(r"token|secret|authorization|password|passwd|api[-_]?key|cookie|bearer|credential",
                         re.IGNORECASE)

__all__ = ["RankingFetcher", "ResearchLog", "ClockRegression", "toss_ranking_source",
           "guard_toss_client", "guard_toss_orderbook_client", "OFFICIAL_TOSS_ORIGIN",
           "ORDERBOOK_PATH", "redact", "open_without_redirect"]


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
        if not p.is_absolute() or p.suffix.lower() != ".jsonl":
            raise ValueError("absolute .jsonl path required")
        self.path, self._clock = p, clock
        self._validate()

    def _validate(self) -> None:
        """Reject protected names (case-insensitive, also after resolving links) and any
        symlink/junction ancestor. Re-run before every write."""
        p = self.path
        resolved = p.resolve()
        for candidate in (p, resolved):
            if any(part.casefold() in _PROTECTED or part.casefold().startswith(".env")
                   for part in candidate.parts):
                raise ValueError("protected location")
        for ancestor in (p, *p.parents):
            if ancestor.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(ancestor)):
                raise ValueError("links are not allowed in the log path")
        if p.is_dir():
            raise ValueError("path must be a regular file")

    def append(self, kind: str, **fields) -> None:
        self._validate()
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


class ClockRegression(RuntimeError):
    """Wall clock moved backwards relative to a prior stamp or to the monotonic clock."""


class RankingFetcher:
    """Callable for `ingest_fetch(state, fetcher, clock=clock)`; returns `FetchResult`.

    * Preserves the FIRST receipt time of every payload object it has seen (bounded memory of the
      last `remember` objects, references held so identities cannot be reused), so A,B,A never
      re-stamps A. Calls are serialized per instance.
    * Rejects a wall clock that is naive, earlier than a prior stamp, or lags the monotonic clock
      by more than `clock_slack` since the previous stamp (`ClockRegression`, fail closed).
    * Logs success (raw rows once per new payload), cache hits, failures (error type/status/code
      only) and same-`rankedAt` content conflicts. Raw rows are logged BEFORE the payload is
      remembered: if logging fails the call raises and the same object is treated as new next time.
    * A missing provider time stays missing (Scout then blocks entries as UNKNOWN_PROVIDER_TIME).
    """

    def __init__(self, source: Callable[[], tuple], *, clock: Callable[[], datetime],
                 log: ResearchLog | None = None, requested_count: int = 100,
                 monotonic: Callable[[], float] = time.monotonic,
                 clock_slack: timedelta = timedelta(seconds=1), remember: int = 64):
        self._source, self._clock, self._log, self._mono = source, clock, log, monotonic
        self._requested_count, self._slack, self._remember = requested_count, clock_slack, remember
        self._seen: OrderedDict = OrderedDict()           # id(payload) -> (payload, received_at)
        self._ranked_digest: dict = {}
        self._last_stamp: tuple | None = None            # (utc, monotonic)
        self._lock = threading.Lock()
        self.fetches = 0

    def _emit(self, kind: str, **fields) -> None:
        if self._log is not None:
            self._log.append(kind, **fields)

    def _stamp(self) -> datetime:
        received, mono = self._clock(), self._mono()
        if not isinstance(received, datetime) or received.utcoffset() is None:
            raise ClockRegression("clock must return an aware datetime")
        if self._last_stamp is not None:
            prev_utc, prev_mono = self._last_stamp
            if received < prev_utc or (received - prev_utc) - timedelta(seconds=mono - prev_mono) < -self._slack:
                raise ClockRegression("wall clock moved backwards")
        self._last_stamp = (received, mono)
        return received

    def __call__(self) -> FetchResult:
        with self._lock:
            return self._fetch()

    def _fetch(self) -> FetchResult:
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
        known = self._seen.get(id(payload))
        if known is not None and known[0] is payload:
            self._emit("fetch_cache_hit", fetch_no=fetch_no, received_at=known[1],
                       note="same payload object; original receipt time reused")
            return FetchResult(payload, known[1])
        try:
            received = self._stamp()                 # first observation: upper bound of receipt
        except ClockRegression as exc:
            self._emit("fetch_failed", fetch_no=fetch_no, error_type=type(exc).__name__)
            raise
        ranked_raw, rows = payload
        digest = _raw_digest(rows)
        try:
            snap = snapshot_from_payload(payload, received_at=received, requested_count=self._requested_count)
            quality = dict(provider_time="PRESENT" if snap.provider_ranked_at else "MISSING_OR_INVALID",
                           parsed_rows=len(snap.rows), defects=snap.defects, snapshot_id=snap.snapshot_id,
                           content_digest=snap.content_digest)
        except ValueError as exc:
            quality = dict(provider_time="UNKNOWN", defects=(type(exc).__name__,))
        self._emit("fetch_ok", fetch_no=fetch_no, received_at=received, provider_ranked_at_raw=ranked_raw,
                   requested_count=self._requested_count, raw_row_count=len(rows) if isinstance(rows, list) else None,
                   raw_digest=digest, quality=quality, rows=rows,
                   receipt_note="stamped after source returned; upper bound of true receipt")
        key = str(ranked_raw)
        previous = self._ranked_digest.get(key, digest)
        if previous != digest:
            self._emit("conflict", fetch_no=fetch_no, reason="SAME_RANKED_AT_DIFFERENT_CONTENT",
                       provider_ranked_at_raw=ranked_raw, first_raw_digest=previous, new_raw_digest=digest)
        self._ranked_digest.setdefault(key, digest)
        self._seen[id(payload)] = (payload, received)       # remembered only after logging succeeded
        while len(self._seen) > self._remember:
            self._seen.popitem(last=False)
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


_FENCE_LOCK = threading.RLock()   # shared by every guarded client: serializes counters and the urlopen swap


def open_without_redirect(request, timeout=None, *, _handlers=()):
    """`urlopen` replacement that never follows a redirect (a 3xx surfaces as HTTPError), so an
    allowed URL cannot send the Authorization header to a different path or host."""
    import urllib.request

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(_NoRedirect(), *_handlers)
    return opener.open(request, timeout=timeout) if timeout is not None else opener.open(request)


OFFICIAL_TOSS_ORIGIN = "https://openapi.tossinvest.com"
TOKEN_PATH = "/oauth2/token"
ORDERBOOK_PATH = "/api/v1/orderbook"
_ORDERBOOK_QUERY = re.compile(r"symbol=[0-9A-Z]{6}")
_SAFE_ERROR_CODE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def _normalize_origins(origins) -> frozenset:
    """`https://host[:port]` only. Plain http is accepted solely for loopback (local fake servers)."""
    from urllib.parse import urlsplit
    result = set()
    for origin in origins:
        parts = urlsplit(origin)
        if (parts.path, parts.query, parts.fragment, parts.username, parts.password) != ("", "", "", None, None) \
                or not parts.hostname:
            raise ValueError("origin must be scheme://host[:port] only")
        if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in _LOOPBACK_HOSTS):
            raise ValueError("origin must be https (http only for loopback)")
        result.add(f"{parts.scheme}://{parts.netloc}")
    if not result:
        raise ValueError("at least one origin is required")
    return frozenset(result)


def _origin_of(parts) -> str:
    return f"{parts.scheme}://{parts.netloc}"


def _strict_denial(origins, url, method, has_body, fragment, has_headers=False) -> str | None:
    """Reason a request is outside the strict read-only orderbook policy, else None (no I/O)."""
    from urllib.parse import urlsplit
    if not isinstance(url, str) or any(ord(c) <= 0x20 or ord(c) == 0x7F for c in url):
        return "control characters or spaces in url"      # urlsplit would silently strip some of them
    try:
        parts = urlsplit(url)
    except ValueError:
        return "unparsable url"
    if _origin_of(parts) not in origins:
        return "origin not allowed"
    if parts.fragment or fragment or ";" in parts.path:
        return "fragment/params not allowed"
    if (parts.path, method) == (TOKEN_PATH, "POST"):
        return "token url must not carry a query" if parts.query else None
    if (parts.path, method) == (ORDERBOOK_PATH, "GET"):
        if has_body or has_headers:
            return "orderbook request must not carry a body or extra headers"
        if not _ORDERBOOK_QUERY.fullmatch(parts.query):
            return "orderbook query must be exactly one symbol=<6 chars [0-9A-Z]>"
        return None
    return "endpoint not allowed"


def guard_toss_client(client, *, max_requests: int = 6, also_allow=(), rankings: bool = True,
                      origins=None, path_caps=None, strict_requests: bool = False,
                      preflight: bool = False, sanitize_errors: bool = False) -> dict:
    """Hard read-only fence around ONE existing Toss client instance (it does not cover any other
    `TossClient`, other transports, or other code that opens its own connections).

    Wraps its single-request transport so that only the token endpoint (POST) and the ranking
    GET can ever be sent, and at most `max_requests` requests in total (the client itself may
    retry a 429 or re-authorize a 401, so this cap, not the fetch count, is the real upper
    bound). Anything else raises before any network I/O. Returns the live counter (paths only).
    While a fenced request runs, the transport module's `urlopen` is swapped for
    `open_without_redirect` (restored afterwards), so redirects cannot bypass the allowlist or cap.
    All guarded clients in the process share one lock, so their requests run one at a time and the
    swap/restore cannot interleave; any other client of the same transport module that sends a
    request during a guarded one also gets the no-redirect opener (stricter, never looser).
    `also_allow` adds explicit (path, GET) pairs, e.g. the stocks endpoints; the default adds none.
    `rankings=False` removes the ranking GET from the allowlist (token POST and `also_allow` only).

    Optional hardening (all off by default, so existing callers are unchanged):
    `origins` pins scheme://host[:port] for every request; `path_caps` is a {path: max} per-path cap
    (retries and re-authorizations count; a request that is reserved counts even if it then fails);
    `strict_requests` applies `_strict_denial` (exact token POST, exact `GET /api/v1/orderbook?symbol=XXXXXX`);
    `preflight` also validates BEFORE `_authorized_json_request` asks for a token, so a request that
    would be refused never costs an authentication; `sanitize_errors` re-raises `TossApiError` with only
    status and a slug-shaped code (provider text, URLs and headers are dropped, cause not chained).
    """
    from urllib.parse import urlparse, urlsplit
    original = client._json_request_once
    allowed = {(TOKEN_PATH, "POST")}
    if rankings:
        allowed.add(("/api/v1/rankings", "GET"))
    for path, method in also_allow:
        if method != "GET":
            raise ValueError("only GET endpoints can be added to the allowlist")
        allowed.add((path, method))
    origin_set = _normalize_origins(origins) if origins is not None else None
    if strict_requests and origin_set is None:
        raise ValueError("strict_requests needs explicit origins")
    caps = dict(path_caps or {})
    counts = {"total": 0, "paths": {}}

    def policy_denial(url, method, has_body, fragment, has_headers=False):
        if strict_requests:
            return _strict_denial(origin_set, url, method, has_body, fragment, has_headers)
        if origin_set is not None and _origin_of(urlsplit(url)) not in origin_set:
            return "origin not allowed"
        return None

    def room(path) -> str | None:
        """Cap check; call with `_FENCE_LOCK` held."""
        if counts["total"] >= max_requests:
            return "request cap reached"
        if path in caps and counts["paths"].get(path, 0) >= caps[path]:
            return "path request cap reached"
        return None

    def fenced(request):
        path, method = urlparse(request.full_url).path, request.get_method()
        if (path, method) not in allowed:
            raise PermissionError("request outside the read-only ranking allowlist")
        why = policy_denial(request.full_url, method, getattr(request, "data", None) is not None,
                            getattr(request, "fragment", None))
        if why:
            raise PermissionError(why)
        with _FENCE_LOCK:
            why = room(path)
            if why:
                raise PermissionError(why)
            counts["total"] += 1
            counts["paths"][path] = counts["paths"].get(path, 0) + 1
            namespace = getattr(type(client)._json_request_once, "__globals__", None)
            if namespace is None or "urlopen" not in namespace:
                return original(request)
            saved = namespace["urlopen"]
            namespace["urlopen"] = open_without_redirect
            try:
                return original(request)
            finally:
                namespace["urlopen"] = saved

    client._json_request_once = fenced

    if preflight or sanitize_errors:
        original_authorized = client._authorized_json_request

        def checked(url, *, headers=None, method="GET", body=None):
            if preflight:
                why = policy_denial(url, method, body is not None, None, bool(headers))
                if not why and (urlparse(url).path, method) not in allowed:
                    why = "request outside the allowlist"
                if not why:
                    path = urlparse(url).path
                    with _FENCE_LOCK:
                        why = room(path)
                        if not why and getattr(client, "_access_token", None) is None:
                            # a token request would be needed first: both it and the data request must fit
                            why = room(TOKEN_PATH) or (
                                "request cap would be reached before the data request"
                                if counts["total"] + 2 > max_requests else None)
                if why:
                    raise PermissionError(why)
            try:
                return original_authorized(url, headers=headers, method=method, body=body)
            except Exception as exc:
                if not sanitize_errors or not (hasattr(exc, "status_code") and hasattr(exc, "error_code")):
                    raise                       # fence refusals (PermissionError) and unknown errors pass through
                error_class, status = type(exc), exc.status_code
                code = exc.error_code if isinstance(exc.error_code, str) and _SAFE_ERROR_CODE.fullmatch(exc.error_code) else None
            # Raised outside the except block on purpose: no __cause__ and no __context__, so the provider text,
            # URL and headers of the original exception are not reachable from the one we raise.
            raise error_class(f"Toss request failed (status={status}, code={code})", status_code=status, error_code=code)

        client._authorized_json_request = checked
    return counts


def guard_toss_orderbook_client(client, *, origins=(OFFICIAL_TOSS_ORIGIN,), max_requests: int = 6,
                                max_auth: int = 2, max_orderbook: int = 4) -> dict:
    """Strict read-only fence for the ORDERBOOK research check on one client instance.

    Only `POST /oauth2/token` and `GET /api/v1/orderbook?symbol=<6 chars [0-9A-Z]>` on the pinned
    HTTPS origin can leave the process; at most `max_requests` total, `max_auth` token requests and
    `max_orderbook` orderbook requests (retries and re-authorizations included); invalid requests
    are refused before any token is requested; redirects are never followed; errors are sanitized.
    Scope: this one client instance only."""
    if max_auth > max_requests or max_orderbook > max_requests:
        raise ValueError("per-path caps cannot exceed the total cap")
    counts = guard_toss_client(client, max_requests=max_requests, rankings=False,
                               also_allow=((ORDERBOOK_PATH, "GET"),), origins=origins,
                               path_caps={TOKEN_PATH: max_auth, ORDERBOOK_PATH: max_orderbook},
                               strict_requests=True, preflight=True, sanitize_errors=True)
    client._day01_orderbook_fence = counts        # marker: fetch_orderbook_results refuses unfenced clients
    return counts
