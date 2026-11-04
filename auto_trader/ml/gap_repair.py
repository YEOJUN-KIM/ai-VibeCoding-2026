"""Inspect session gaps and append only missing candles actually returned by Toss.

The timestamp grid is a diagnostic expectation, never synthetic OHLCV data.
Provider absence is evidence, not a claim of no trading or a revised calendar.
"""

import json
from datetime import date, datetime, time, timedelta, timezone
from threading import Lock
from zoneinfo import ZoneInfo
from contextlib import nullcontext
from time import monotonic

from ..database import connect
from ..brokers.toss.request_budget import RequestBudget, RequestBudgetExceeded
from .collection_errors import error_detail
from .data_pipeline import (RawCandle, RawMarketIndicator, finish_collection_run,
                            save_raw_candles, save_market_indicator_candles,
                            start_collection_run)

KST = ZoneInfo("Asia/Seoul")
MARKET_OPEN = time(9, 0)
LAST_BAR = time(15, 30)
FULL_SESSION_BARS = 391
_repair_lock = Lock()


def session_bounds(day: date):
    return datetime.combine(day, MARKET_OPEN, KST), datetime.combine(day, LAST_BAR, KST) + timedelta(minutes=1)


def expected_timestamps(day: date, as_of: datetime) -> list[datetime]:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of에는 시간대 정보가 필요합니다.")
    start, end = session_bounds(day)
    completed_end = min(end, as_of.astimezone(KST).replace(second=0, microsecond=0))
    count = max(0, int((completed_end - start).total_seconds() // 60))
    return [start + timedelta(minutes=index) for index in range(count)]


def missing_timestamps(existing, expected):
    return sorted(set(expected) - set(existing))


def opening_observation(candles, received_at, requested_count):
    start, _ = session_bounds(received_at.astimezone(KST).date())
    oldest = min((c.timestamp for c in candles), default=None)
    newest = max((c.timestamp for c in candles), default=None)
    return {"received_at": received_at.isoformat(),
            "first_returned_event_at": oldest.isoformat() if oldest else None,
            "last_returned_event_at": newest.isoformat() if newest else None,
            "opening_bar_present": any(c.timestamp == start for c in candles),
            "window_covers_opening": bool(candles) and (oldest <= start or len(candles) < requested_count)}


def existing_timestamps(kind: str, item: str, start: datetime, end: datetime, *, as_of: datetime):
    table, key, source = {
        "stock": ("ml_raw_candles", "symbol", "TOSS"),
        "indicator": ("ml_market_indicator_candles", "indicator", "TOSS_MARKET_INDICATOR"),
    }[kind]
    with connect() as conn:
        rows = conn.execute(
            f"SELECT event_at FROM {table} WHERE source=%s AND {key}=%s AND interval='1m' "
            "AND event_at>=%s AND event_at<%s AND available_at<=%s",
            (source, item, start, end, as_of),
        ).fetchall()
    return {row["event_at"] for row in rows}


def final_reconciliation_exists(day, targets) -> bool:
    with connect() as conn:
        return conn.execute(
            "SELECT 1 FROM ml_collection_runs WHERE source='CANDLE_RECONCILIATION' "
            "AND status='COMPLETED' AND errors->'_diagnostics'->>'kind'='final' "
            "AND errors->'_diagnostics'->>'expected_bars'=%s "
            "AND errors->'_diagnostics'->>'report_date'=%s "
            "AND started_at>=%s "
            "AND requested_symbols @> %s::jsonb LIMIT 1",
            (str(FULL_SESSION_BARS), day.isoformat(), datetime.combine(day, time(15, 35), KST), json.dumps(targets)),
        ).fetchone() is not None


def pending_opening_reconciliations(day):
    """Recover the latest committed cursor per opening scan after a restart."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT ON (errors->'_diagnostics'->>'kind') "
            "errors->'_diagnostics' AS diagnostics FROM ml_collection_runs "
            "WHERE source='CANDLE_RECONCILIATION' "
            "AND errors->'_diagnostics'->>'report_date'=%s "
            "AND errors->'_diagnostics'->>'kind' IN ('opening_0905','opening_0910') "
            "ORDER BY errors->'_diagnostics'->>'kind',id DESC", (day.isoformat(),)
        ).fetchall()
    return {row['diagnostics']['kind']: row['diagnostics']['resume'] for row in rows
            if row['diagnostics'].get('resume')}


def reconcile_session(client, day: date, *, symbols, indicators=("KOSPI", "KOSDAQ"),
                      kind="manual", clock=None, skip_completed=False,
                      budget_seconds=None, max_pages=2, resume=None) -> dict:
    """Serialize maintenance scans; regular collection remains independent.

    `clock` is an injectable receive-time clock for deterministic tests, not an
    event-time override. Production always samples it after each API response.
    """
    clock = clock or (lambda: datetime.now(timezone.utc))
    targets = [("stock", item.strip().upper()) for item in dict.fromkeys(symbols)]
    targets += [("indicator", item.strip().upper()) for item in dict.fromkeys(indicators)]
    targets = [(family, item) for family, item in targets if item]
    bounded = kind.startswith('opening') and budget_seconds is not None
    if resume and not bounded:
        raise ValueError('Resume is only supported for bounded opening scans')
    budget = RequestBudget(budget_seconds, max_pages) if bounded else None
    if resume:
        if resume['report_date'] != day.isoformat() or resume['kind'] != kind:
            raise ValueError('Resume date/kind mismatch')
        allowed = set(targets)
        targets = [tuple(target) for target in resume['targets']]
        if not set(targets) <= allowed:
            raise ValueError('Unexpected resume targets')
    # Do not queue behind another maintenance scan during market hours.
    if bounded and not _repair_lock.acquire(blocking=False):
        return {'status': 'DEFERRED', 'inserted_rows': 0, 'kind': kind,
                'resume': resume or {'report_date': day.isoformat(), 'kind': kind,
                                    'as_of': clock().isoformat(), 'targets': targets}}
    lock_context = nullcontext() if bounded else _repair_lock
    try:
        with lock_context:
            return _reconcile_locked(client, day, targets=targets, kind=kind, clock=clock,
                                     skip_completed=skip_completed, budget=budget, resume=resume)
    finally:
        if bounded:
            _repair_lock.release()


def _reconcile_locked(client, day, *, targets, kind, clock, skip_completed, budget, resume):
    # Full final/historical scans deliberately keep their original evidence path.
    with budget.activate() if budget else nullcontext():
        if skip_completed and final_reconciliation_exists(day, [f"{k}:{v}" for k, v in targets]):
            return {"status": "SKIPPED", "reason": "already_reconciled", "inserted_rows": 0}
        started = clock()
        as_of = datetime.fromisoformat(resume['as_of']) if resume else started
        expected = expected_timestamps(day, as_of)
        if not targets or not expected:
            return {"status": "SKIPPED", "reason": "no_completed_bars", "inserted_rows": 0}
        start, end = session_bounds(day)
        historical = day < started.astimezone(KST).date()
        # Historical scans start at the requested session end, using the same
        # existing pagination. Today's V1 overlap/reconciliation is unchanged.
        count = 600 if historical else min(10000, max(600, int((started - start).total_seconds() // 60) + 60))
        if budget:
            count = min(200, len(expected))
        run_id = start_collection_run("CANDLE_RECONCILIATION", "1m", [f"{k}:{v}" for k, v in targets], count, started)
        errors, details, series = {}, {}, {}
        inserted = duplicates = 0
        pending = []
        windows = dict((resume or {}).get('windows', {}))
        deferred_reason = None
        for index, (family, item) in enumerate(targets):
            key = f"{family}:{item}"
            info = {"provider_checked": False}
            series[key] = info
            try:
                if budget:
                    budget.remaining()
                before = existing_timestamps(family, item, start, end, as_of=clock())
                missing = missing_timestamps(before, expected)
                info["missing_before"] = [stamp.isoformat() for stamp in missing]
                if not missing:
                    info.update(recovered_rows=0, missing_after=[], provider_absent_timestamps=[])
                    continue
                fetch = client._domestic_candles if family == "stock" else client._market_indicator_candles
                options = {"cache_seconds": 0}
                selected = missing
                fetch_count = count
                if budget:
                    # A single page around the earliest missing window. The
                    # provider cursor excludes its upper bound; allow one bar
                    # of overlap for inclusive provider cursor implementations.
                    lower = datetime.fromisoformat(windows[key]) if key in windows else start
                    unscanned = [stamp for stamp in missing if stamp >= lower]
                    if not unscanned:
                        continue
                    upper = min(unscanned[-1], unscanned[0] + timedelta(minutes=198))
                    selected = [stamp for stamp in unscanned if stamp <= upper]
                    fetch_count = int((upper - unscanned[0]).total_seconds() // 60) + 2
                    options['before'] = (upper + timedelta(minutes=1)).isoformat()
                    budget.page()
                if historical and not budget:
                    options["before"] = end.isoformat()
                info['requested_count'] = fetch_count
                received = fetch(item, "1m", fetch_count, **options)
                received_at = clock()
                provider = {bar.timestamp: bar for bar in received
                            if start <= bar.timestamp < end and bar.timestamp in expected
                            and bar.timestamp + timedelta(minutes=1) <= received_at}
                oldest = min((bar.timestamp for bar in received), default=None)
                window_complete = not budget and bool(received) and (oldest <= start or len(received) < count)
                final_check = not budget and len(expected) == FULL_SESSION_BARS and received_at >= end and not kind.startswith("opening")
                candidates = sorted(set(missing) & provider.keys())
                factory = RawCandle if family == "stock" else RawMarketIndicator
                save = save_raw_candles if family == "stock" else save_market_indicator_candles
                observations = [factory.from_live_candle(item, provider[stamp], collected_at=received_at) for stamp in candidates]
                added, duplicate = save(observations, run_id=run_id)
                inserted += added
                duplicates += duplicate
                after = existing_timestamps(family, item, start, end, as_of=clock())
                remaining = missing_timestamps(after, expected)
                info.update(provider_checked=True, received_at=received_at.isoformat(),
                            provider_window_complete=window_complete,
                            final_provider_check=final_check,
                            oldest_received=oldest.isoformat() if oldest else None,
                            opening_bar_present=start in provider,
                            provider_timestamps=[t.astimezone(KST).isoformat() for t in sorted(provider)],
                            recovered_rows=added,
                            missing_after=[t.isoformat() for t in remaining],
                            raw_missing_timestamps=[t.isoformat() for t in remaining],
                            provider_absent_timestamps=[t.isoformat() for t in remaining if t not in provider] if window_complete and final_check else [],
                            collection_gap_timestamps=[t.isoformat() for t in remaining if t in provider],
                            unverified_timestamps=[t.isoformat() for t in remaining if t not in provider] if not (window_complete and final_check) else [])
                if budget and any(stamp > upper for stamp in remaining):
                    windows[key] = (upper + timedelta(minutes=1)).isoformat()
                    pending.append((family, item))
                if remaining and not window_complete and not budget:
                    errors[key] = "원천 응답에서 정규장 전체 범위를 확인하지 못했습니다. 다음 재검사에서 확인합니다."
                    details[key] = {"category": "OTHER", "message": errors[key], "error_code": "incomplete-window"}
            except RequestBudgetExceeded as exc:
                if budget.last_error is not None:
                    errors[key] = str(budget.last_error)
                    details[key] = error_detail(budget.last_error)
                    info['error'] = details[key]
                pending.extend(targets[index:])
                deferred_reason = str(exc)
                break
            except Exception as exc:
                errors[key] = str(exc)
                details[key] = error_detail(exc)
                info["error"] = details[key]
                if budget:
                    pending.append((family, item))
        diagnostics = {"kind": kind, "report_date": day.isoformat(), "expected_bars": len(expected),
                       "series": series, "error_details": details}
        if budget:
            diagnostics['budget'] = {'pages': budget.pages, 'max_pages': budget.max_pages,
                                     'max_seconds': budget.seconds,
                                     'socket_requests': budget.requests, 'wait_seconds': budget.wait_seconds,
                                     'elapsed_seconds': monotonic() - budget.started,
                                     'reason': deferred_reason, 'pending_targets': len(pending)}
            diagnostics['resume'] = ({'report_date': day.isoformat(), 'kind': kind,
                                      'as_of': as_of.isoformat(), 'targets': pending,
                                      **({'windows': windows} if windows else {})} if pending else None)
        status = finish_collection_run(run_id, inserted_rows=inserted, duplicate_rows=duplicates,
                                       errors=errors, finished_at=clock(), diagnostics=diagnostics)
        return {"run_id": run_id, "status": status, "inserted_rows": inserted,
                "duplicate_rows": duplicates, "errors": errors, "error_details": details, **diagnostics}
