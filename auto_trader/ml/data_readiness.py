"""Read-only RAW audit and reproducible, point-in-time Curated Dataset v1.

Features require knowledge at prediction time. Verified later receipts may supply
market outcomes, never historical features. No training or trading imports.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess

from ..database import connect
from ..settings import settings
from .gap_repair import KST, expected_timestamps, session_bounds
from .quality_report import (_classify_gaps, _final_provider_evidence,
                             _prefer_provider_evidence, CORE_MACRO_INDICATORS)

MINUTE = timedelta(minutes=1)
HORIZONS = (5, 15, 20, 30, 60)
TABLES = ('ml_raw_candles', 'ml_market_indicator_candles',
          'ml_macro_observations', 'ml_strategy_decisions', 'ml_collection_runs')
MACRO_NAMES = dict(zip(CORE_MACRO_INDICATORS, (
    'usd_krw', 'us_sp500_proxy', 'us_nasdaq100_proxy', 'us_djia_proxy',
    'us_volatility_proxy', 'us_treasury_proxy')))


@dataclass(frozen=True)
class Config:
    lookback: int = 40
    max_delay_minutes: int = 5
    stock_source: str = 'TOSS'
    index_source: str = 'TOSS_MARKET_INDICATOR'
    fee_rate: str = '0.00015'
    sell_tax_rate: str = '0.002'
    slippage_rate: str = '0.0005'

    def __post_init__(self):
        if self.lookback < 40 or self.max_delay_minutes < 1:
            raise ValueError('lookback >= 40 and max_delay_minutes >= 1 required')
        fee, tax, slip = map(Decimal, (self.fee_rate, self.sell_tax_rate, self.slippage_rate))
        if not all(x.is_finite() and 0 <= x < 1 for x in (fee, tax, slip)) or fee + tax > 1:
            raise ValueError('invalid cost rates')


def encode(value):
    if isinstance(value, (datetime, Decimal)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    raise TypeError(type(value).__name__)


def canonical(value):
    return json.dumps(value, default=encode, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def time_valid(row):
    values = [row.get(k) for k in ('event_at', 'available_at', 'collected_at')]
    return all(isinstance(v, datetime) and v.utcoffset() is not None for v in values) and values[0] <= values[1] <= values[2]


def price_valid(row):
    if row is None:
        return False
    try:
        o, h, l, c, v = [Decimal(row[k]) for k in
                         ('open_price', 'high_price', 'low_price', 'close_price', 'volume')]
        return all(x.is_finite() and x > 0 for x in (o, h, l, c)) and v.is_finite() and v >= 0 and l <= min(o, c) <= max(o, c) <= h
    except (ValueError, TypeError, KeyError):
        return False


def regular(at):
    start, end = session_bounds(at.astimezone(KST).date())
    return start <= at < end and at.second == at.microsecond == 0


def load_snapshot():
    """One MVCC snapshot, enforced READ ONLY by PostgreSQL, no initialize()."""
    with connect() as conn:
        conn.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        meta = conn.execute('SELECT transaction_timestamp() AS as_of, '
                            'pg_current_snapshot()::text AS snapshot_id').fetchone()
        orders = ('source,symbol,interval,event_at', 'source,indicator,interval,event_at',
                  'source,indicator,event_at,value', 'id', 'id')
        data = {table: conn.execute('SELECT * FROM ' + table + ' ORDER BY ' + order).fetchall()
                for table, order in zip(TABLES, orders)}
        meta['transaction_read_only'] = conn.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']
        # A running clock must not change the dataset for unchanged inputs.
        # Audit the current partial session up to the latest persisted receipt.
        meta['transaction_at'] = meta['as_of']
        receipts = [r[k] for rows in data.values() for r in rows
                    for k in ('collected_at', 'finished_at', 'started_at')
                    if isinstance(r.get(k), datetime) and r[k] <= meta['transaction_at']]
        meta['as_of'] = max(receipts) if receipts else datetime(1970, 1, 1, tzinfo=timezone.utc)
    return data, meta


def make_series(rows, key, source):
    series = defaultdict(dict)
    for row in sorted(rows, key=lambda r: (r['event_at'], r['available_at'], canonical(r))):
        if row['source'] == source and row['interval'] == '1m':
            series[row[key]].setdefault(row['event_at'], row)
    return series


def macro_feature_rows(rows, runs):
    """Legacy macro timestamps describe cycle start, not each HTTP receipt.

    A completed collection run provides a conservative known-by boundary. RAW
    remains untouched; missing completion evidence means optional macro absent.
    """
    completed = {r['id']: r['finished_at'] for r in runs
                 if r.get('source') == 'MACRO_CONTEXT' and r.get('finished_at')}
    result = []
    for row in rows:
        finished = completed.get(row.get('collection_run_id'))
        if finished is not None and time_valid(row):
            result.append({**row, 'raw_available_at': row['available_at'], 'raw_collected_at': row['collected_at'],
                           'available_at': max(row['available_at'], row['collected_at'], finished),
                           'collected_at': max(row['collected_at'], finished)})
    return result


def audit_series(rows, key, names, dates, runs, as_of):
    evidence = {}
    for run in runs:
        if run['source'] != 'CANDLE_RECONCILIATION' or not run.get('finished_at') or run['finished_at'] > as_of:
            continue
        diag = (run.get('errors') or {}).get('_diagnostics', {})
        day = diag.get('report_date')
        if day not in dates:
            continue
        end = session_bounds(datetime.fromisoformat(day).date())[1]
        for item, info in diag.get('series', {}).items():
            candidate = (info, _final_provider_evidence(diag, info, end, as_of))
            evidence[day, item] = _prefer_provider_evidence(evidence.get((day, item)), candidate, as_of=as_of)
    grouped = defaultdict(list)
    for r in rows:
        grouped[r[key], r['event_at'].astimezone(KST).date().isoformat()].append(r)
    result = []
    family = 'stock' if key == 'symbol' else 'indicator'
    for day in dates:
        expected = expected_timestamps(datetime.fromisoformat(day).date(), as_of)
        for name in names:
            group = grouped[name, day]
            reg = sorted((r for r in group if regular(r['event_at']) and r['interval'] == '1m'), key=lambda r: r['event_at'])
            present = {r['event_at'] for r in reg}
            counts = Counter((r['source'], r[key], r['interval'], r['event_at']) for r in group)
            info = dict(family=family, item=name, market_date=day, raw_rows=len(group),
                        row_count=len(reg), regular_session_rows=len(reg), outside_session_rows=len(group)-len(reg),
                        expected_bars=len(expected), raw_missing_timestamps=[t.isoformat() for t in expected if t not in present],
                        duplicate_rows=sum(n-1 for n in counts.values()),
                        time_order_violations=sum(not time_valid(r) for r in group),
                        invalid_ohlc=sum(not price_valid(r) for r in group),
                        delayed_over_5m=sum(r['available_at']-r['event_at'] > 5*MINUTE for r in group),
                        historical_backfill_rows=sum(r['available_at'].astimezone(KST).date() > r['event_at'].astimezone(KST).date() for r in group),
                        zero_volume=sum(r['volume'] == 0 for r in group),
                        first_candle=reg[0]['event_at'] if reg else None,
                        last_candle=reg[-1]['event_at'] if reg else None,
                        suspicious_timestamps=[b['event_at'] for a, b in zip(reg, reg[1:])
                            if price_valid(a) and price_valid(b) and abs(float(b['close_price']/a['close_price'])-1) > .1])
            ev = evidence.get((day, f'{family}:{name}')) or ({}, False)
            _classify_gaps(info, *ev)
            result.append(info)
    return result


def net_return(entry, exit_price, config):
    """One-share PAPER cost convention, including KRW fee/tax round-down."""
    fee, tax, slip = map(Decimal, (config.fee_rate, config.sell_tax_rate, config.slippage_rate))
    buy = entry * (1+slip)
    sell = exit_price * (1-slip)
    cost = buy + (buy*fee).quantize(Decimal('1'), rounding=ROUND_DOWN)
    proceeds = sell - (sell*fee).quantize(Decimal('1'), rounding=ROUND_DOWN) - (sell*tax).quantize(Decimal('1'), rounding=ROUND_DOWN)
    return float(proceeds/cost-1)


def price_features(window):
    close = [float(r['close_price']) for r in window]
    volumes = [float(r['volume']) for r in window]
    f = {f'return_{n}m': close[-1]/close[-1-n]-1 for n in (1, 3, 5, 10, 20)}
    f.update(ma_10=statistics.mean(close[-10:]), ma_40=statistics.mean(close[-40:]), volume=volumes[-1])
    f['ma_spread'] = f['ma_10']-f['ma_40']
    f['ma_spread_percent'] = f['ma_spread']/f['ma_40']*100
    for n in (5, 20):
        returns = [close[i]/close[i-1]-1 for i in range(len(close)-n, len(close))]
        f[f'volatility_{n}m'] = statistics.pstdev(returns)
        f[f'range_{n}m'] = (max(float(r['high_price']) for r in window[-n:]) - min(float(r['low_price']) for r in window[-n:]))/close[-1]
        avg = statistics.mean(volumes[-n:])
        f[f'volume_avg_{n}m'] = avg
        f[f'relative_volume_{n}m'] = volumes[-1]/avg if avg else None
    return f


def build_samples(stocks, indices, macros, decisions, symbols, dates, as_of, config, audits):
    stock_map = make_series(stocks, 'symbol', config.stock_source)
    index_map = make_series(indices, 'indicator', config.index_source)
    gap_map = {(a['item'], a['market_date']): a for a in audits if a['family'] == 'stock'}
    macro_map = defaultdict(list)
    for row in macros:
        if time_valid(row) and Decimal(row['value']).is_finite():
            macro_map[row['indicator']].append(row)
    # Sort by knowledge time. Select latest event among known observations,
    # so a newly received older revision does not displace a newer event.
    macro_latest = {}
    for key, rows in macro_map.items():
        best = None
        choices = []
        for row in sorted(rows, key=lambda r: (r['available_at'], r['event_at'], canonical(r))):
            if best is None or (row['event_at'], row['available_at']) >= (best['event_at'], best['available_at']):
                best = row
            choices.append(best)
        macro_map[key] = sorted(r['available_at'] for r in rows)
        macro_latest[key] = choices
    decision_map = defaultdict(list)
    for row in decisions:
        if row.get('data_source') == config.stock_source and row.get('candle_event_at'):
            decision_map[row['symbol'], row['candle_event_at']].append(row)
    samples = []
    for day in dates:
        day_date = datetime.fromisoformat(day).date()
        start, end = session_bounds(day_date)
        for symbol in symbols:
            series = stock_map[symbol]
            grid = set(expected_timestamps(day_date, as_of))
            grid.update(t for t in series if t.astimezone(KST).date() == day_date and not regular(t))
            for at in sorted(grid):
                anchor = series.get(at)
                prediction = max(at+MINUTE, anchor['available_at']) if anchor else at+MINUTE
                reasons, flags = [], []
                s = dict(market_date=day, symbol=symbol, prediction_event_at=at, prediction_at=prediction,
                         historical_backfill_feature_rows=0,
                         feature_available_at_max=None, usable=False, exclusion_reasons=[], flags=[], features={}, labels={}, provenance={})
                if not regular(at):
                    reasons.append('OUTSIDE_SESSION')
                if anchor is None:
                    reasons.append('MISSING_STOCK_DATA')
                # Never turn a historical backfill into a prediction days later.
                if prediction > at + config.max_delay_minutes*MINUTE or prediction >= end or prediction > as_of:
                    reasons.append('LATE_LOOKBACK_DATA')
                needed = [at-i*MINUTE for i in reversed(range(config.lookback))]
                window = [series.get(t) for t in needed]
                missing = [t for t, r in zip(needed, window) if r is None or t < start]
                if missing:
                    reasons.append('MISSING_LOOKBACK')
                    gap = gap_map.get((symbol, day), {})
                    if set(t.isoformat() for t in missing) & set(gap.get('collection_gap_timestamps', [])):
                        reasons.append('COLLECTION_GAP')
                    if set(t.isoformat() for t in missing) & set(gap.get('unverified_timestamps', [])):
                        reasons.append('UNVERIFIED_GAP')
                used = [r for r in window if r is not None]
                if any(not time_valid(r) for r in used):
                    reasons.append('POINT_IN_TIME_VIOLATION')
                if any(not price_valid(r) for r in used):
                    reasons.append('INVALID_OHLC')
                if any(r['available_at'] > prediction or r['event_at']+MINUTE > prediction for r in used):
                    reasons.append('LATE_LOOKBACK_DATA')
                domestic = {}
                for name in ('KOSPI', 'KOSDAQ'):
                    bars = [index_map[name].get(at-i*MINUTE) for i in range(21)]
                    if any(r is None or not time_valid(r) or not price_valid(r) or r['available_at'] > prediction for r in bars):
                        reasons.append('MISSING_'+name)
                    else:
                        domestic[name] = bars
                if not reasons:
                    f = price_features(window)
                    f['minutes_since_open'] = int((at-start).total_seconds()/60)
                    opening = series.get(start)
                    if opening and price_valid(opening) and time_valid(opening) and opening['available_at'] <= prediction:
                        used.append(opening)
                        f['price_vs_session_open'] = float(anchor['close_price']/opening['open_price']-1)
                    else:
                        f['price_vs_session_open'] = None
                    f['has_session_open'] = f['price_vs_session_open'] is not None
                    for name, bars in domestic.items():
                        used.extend(bars)
                        for n in (1, 5, 20):
                            f[f'{name.lower()}_return_{n}m'] = float(bars[0]['close_price']/bars[n]['close_price']-1)
                    for indicator in sorted(set(MACRO_NAMES) | set(macro_map)):
                        key = MACRO_NAMES.get(indicator, indicator.lower())
                        pos = bisect_right(macro_map.get(indicator, []), prediction)-1
                        row = macro_latest[indicator][pos] if pos >= 0 else None
                        f[key] = float(row['value']) if row else None
                        f['has_'+key] = row is not None
                        if row:
                            used.append(row)
                            s['provenance'][key] = {k: row[k] for k in ('source', 'event_at', 'available_at', 'collected_at')}
                            if 'raw_available_at' in row:
                                s['provenance'][key]['raw_available_at'] = row['raw_available_at']
                                s['provenance'][key]['raw_collected_at'] = row['raw_collected_at']
                                s['provenance'][key]['availability_policy'] = 'max(RAW times, collection run finished_at)'
                    contexts = [r for r in decision_map.get((symbol, at), [])
                                if r['available_at'] <= prediction and r['candle_event_at'] <= r['decision_at'] <= r['available_at'] <= r['collected_at']]
                    # Keep strategy identities; never guess BUY candidate from action.
                    s['baseline_context'] = [{k: r.get(k) for k in ('strategy_id', 'strategy_name', 'available_at', 'short_average',
                        'long_average', 'trend', 'previous_trend', 'history_count', 'entry_armed', 'confirmation_count')}
                        for r in sorted(contexts, key=canonical)]
                    f['baseline_buy_candidate'] = None
                    used.extend(contexts)
                    s['feature_available_at_max'] = max(r['available_at'] for r in used)
                    s['historical_backfill_feature_rows'] = sum(
                        r['available_at'].astimezone(KST).date() > r['event_at'].astimezone(KST).date()
                        for r in used if 'close_price' in r)
                    s['provenance']['core'] = dict(stock_source=config.stock_source, index_source=config.index_source,
                        lookback_start=needed[0], lookback_end=at,
                        stock_available_at_max=max(r['available_at'] for r in window),
                        **{name.lower()+'_available_at_max': max(r['available_at'] for r in bars) for name, bars in domestic.items()})
                    if s['feature_available_at_max'] > prediction:
                        reasons.append('POINT_IN_TIME_VIOLATION')
                    else:
                        s['features'] = f
                # Labels are outcome-only. A later actual RAW receipt is allowed.
                # Exact horizons and complete paths: no stitching through missing bars.
                entry_at = prediction.replace(second=0, microsecond=0)+MINUTE
                entry = series.get(entry_at)
                executable = entry is not None and entry_at < end and regular(entry_at) and price_valid(entry) and time_valid(entry)
                s['entry_event_at'] = entry_at if executable else None
                s['entry_open'] = entry['open_price'] if executable else None
                if not executable:
                    flags.append('NO_EXECUTABLE_NEXT_BAR')
                for n in HORIZONS:
                    path = [series.get(at+i*MINUTE) for i in range(1, n+1)]
                    valid = anchor is not None and price_valid(anchor) and time_valid(anchor) and at+n*MINUTE < end and all(r and price_valid(r) and time_valid(r) for r in path)
                    s['labels'][f'future_return_{n}m'] = float(path[-1]['close_price']/anchor['close_price']-1) if valid else None
                    s['labels'][f'future_net_return_{n}m'] = net_return(anchor['close_price'], path[-1]['close_price'], config) if valid else None
                    exec_path = [series.get(entry_at+i*MINUTE) for i in range(n)] if executable else []
                    exec_valid = executable and entry_at+(n-1)*MINUTE < end and all(r and price_valid(r) and time_valid(r) for r in exec_path)
                    s['labels'][f'executable_net_return_{n}m'] = net_return(entry['open_price'], exec_path[-1]['close_price'], config) if exec_valid else None
                    s['labels'][f'max_favorable_excursion_{n}m'] = float(max(r['high_price'] for r in exec_path)/entry['open_price']-1) if exec_valid else None
                    s['labels'][f'max_adverse_excursion_{n}m'] = float(min(r['low_price'] for r in exec_path)/entry['open_price']-1) if exec_valid else None
                if all(s['labels'][f'future_return_{n}m'] is None for n in HORIZONS):
                    flags.append('MISSING_FUTURE_LABEL')
                s['exclusion_reasons'] = sorted(set(reasons))
                s['flags'] = flags
                s['usable'] = not reasons
                samples.append(s)
    return samples


def audit_decisions(rows, stocks):
    times = {(r['symbol'], r['event_at']) for r in stocks}
    fields = ('strategy_id strategy_name symbol candle_event_at decision_at available_at action reason price '
              'short_average long_average trend previous_trend history_count entry_armed confirmation_count strategy_quantity cash total_asset').split()
    return dict(rows=len(rows), null_counts={k: sum(r.get(k) is None for r in rows) for k in fields},
                actions=dict(Counter(r['action'] for r in rows)),
                time_order_violations=sum(not (r.get('candle_event_at') and r['candle_event_at'] <= r['decision_at'] <= r['available_at'] <= r['collected_at']) for r in rows),
                missing_candle_links=sum((r['symbol'], r.get('candle_event_at')) not in times for r in rows),
                baseline_buy_candidate='UNAVAILABLE: action does not safely reconstruct pre-risk BUY candidate; no behavior labels')


def summarize(samples, audits, key):
    result = []
    names = sorted({s[key] for s in samples})
    for name in names:
        subset = [s for s in samples if s[key] == name]
        usable = sum(s['usable'] for s in subset)
        raw = sum(a['raw_rows'] for a in audits if a['family'] == 'stock' and a['market_date' if key == 'market_date' else 'item'] == name)
        result.append({key: name, 'raw_rows': raw, 'candidate_samples': len(subset), 'usable_samples': usable,
                       'excluded_samples': len(subset)-usable, 'usable_ratio': usable/len(subset) if subset else 0,
                       'exclusion_reason_counts': dict(Counter(r for s in subset for r in s['exclusion_reasons']))})
    return result


def write_csv(path, rows, fields=None):
    fields = fields or sorted({k for r in rows for k in r})
    with path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: canonical(v) if isinstance(v, (dict, list)) else encode(v) if isinstance(v, (datetime, Decimal)) else v for k, v in row.items()})


def generate(data, meta, config, symbols, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    stocks, indices, macros, decisions, runs = [data[t] for t in TABLES]
    dates = sorted({r['event_at'].astimezone(KST).date().isoformat() for r in stocks+indices})
    audits = audit_series(stocks, 'symbol', symbols, dates, runs, meta['as_of'])
    audits += audit_series(indices, 'indicator', ('KOSPI', 'KOSDAQ'), dates, runs, meta['as_of'])
    effective_macros = macro_feature_rows(macros, runs)
    samples = build_samples(stocks, indices, effective_macros, decisions, symbols, dates, meta['as_of'], config, audits)
    good = [s for s in samples if s['usable']]
    excluded = [s for s in samples if not s['usable']]
    by_date = summarize(samples, audits, 'market_date')
    by_symbol = summarize(samples, audits, 'symbol')
    usable_dates = [r['market_date'] for r in by_date if r['usable_samples']]
    reason_counts = dict(Counter(r for s in excluded for r in s['exclusion_reasons']))
    label_counts = {k: sum(s['labels'][k] is not None for s in good) for k in samples[0]['labels']} if samples else {}
    feature_list = sorted({k for s in good for k in s['features']})
    flatten = lambda s: {**{k: v for k, v in s.items() if k not in ('features', 'labels')}, **s['features'], **s['labels']}
    fields = sorted({k for s in samples for k in flatten(s)})
    write_csv(output/'samples.csv', [flatten(s) for s in good], fields)
    write_csv(output/'excluded-samples.csv', [flatten(s) for s in excluded], fields)
    write_csv(output/'readiness-by-date.csv', by_date)
    write_csv(output/'readiness-by-symbol.csv', by_symbol)
    write_csv(output/'candle-audit.csv', audits)
    input_hashes = {t: digest(data[t]) for t in TABLES}
    dataset_hash = digest(dict(config=asdict(config), symbols=symbols, samples=samples))
    macro_audit = {name: dict(rows=sum(r['indicator'] == name for r in macros),
        time_order_violations=sum(not time_valid(r) for r in macros if r['indicator'] == name),
        usable_sample_available=sum(s['features'].get('has_'+MACRO_NAMES.get(name, name.lower()), False) for s in good))
        for name in sorted(set(MACRO_NAMES) | {r['indicator'] for r in macros})}
    for name, info in macro_audit.items():
        rows = [r for r in macros if r['indicator'] == name]
        info['observations'] = [{k: r[k] for k in ('source', 'event_at', 'available_at', 'collected_at', 'value', 'frequency')} for r in rows]
        info['with_completed_run_evidence'] = sum(r['indicator'] == name for r in effective_macros)
        info['feature_availability_policy'] = 'Legacy RAW macro times are cycle-start. Use completed MACRO_CONTEXT run finished_at as conservative available_at; missing run evidence => optional null.'
    report = dict(readiness='PARTIALLY_READY' if good else 'NOT_READY', readiness_scope='DATA_PIPELINE',
        first_ml_experiment='READY FOR FIRST ML EXPERIMENT' if good and len(usable_dates) >= 5 else 'NOT READY FOR FIRST ML EXPERIMENT',
        profitability_generalization_confidence='NOT_READY', raw_candles=len(stocks), raw_index_candles=len(indices),
        candidate_samples=len(samples), usable_samples=len(good), excluded_samples=len(excluded),
        usable_ratio=len(good)/len(samples) if samples else 0, market_dates=dates, usable_market_dates=usable_dates,
        symbols=symbols, symbols_with_usable_samples=[r['symbol'] for r in by_symbol if r['usable_samples']],
        exclusion_reason_counts=reason_counts, label_counts=label_counts,
        outcome_flag_counts=dict(Counter(f for s in good for f in s['flags'])),
        feature_point_in_time_violations=sum(s['feature_available_at_max'] > s['prediction_at'] for s in good),
        raw_time_order_violations=sum(a['time_order_violations'] for a in audits),
        historical_backfill_feature_rows=sum(s['historical_backfill_feature_rows'] for s in good),
        index_alignment={name: dict(missing_samples=sum('MISSING_'+name in s['exclusion_reasons'] for s in samples),
                                    aligned_usable_samples=len(good)) for name in ('KOSPI', 'KOSDAQ')},
        macro=macro_audit, decisions=audit_decisions(decisions, stocks),
        by_date=by_date, by_symbol=by_symbol,
        warnings=['No profitability validation or model training.',
                  'Observed event dates only; no exchange holiday calendar inferred.',
                  'Short date span: stable final test/generalization confidence not established.',
                  'Missing optional macro and session-open context do not exclude core samples.',
                  'Flags and missing horizons are task-specific filters, not whole-sample exclusions.'])
    report['source_inventory'] = {table: dict(Counter(
        '/'.join(str(r.get(k, '')) for k in ('source', 'interval')) for r in data[table]))
        for table in TABLES[:3]}
    if good and len(usable_dates) >= 20 and len(report['symbols_with_usable_samples']) == len(symbols) and report['usable_ratio'] >= .8:
        report['readiness'] = 'ML_READY'
    try:
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = 'unknown'
    manifest = dict(dataset_version='v1', created_at=datetime.now(timezone.utc), git_revision=revision,
        builder_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), source_tables=list(TABLES),
        input_hashes=input_hashes, data_hash=digest(input_hashes), dataset_hash=dataset_hash,
        snapshot=meta, config=asdict(config), date_range=[dates[0], dates[-1]] if dates else [], symbols=symbols,
        feature_list=feature_list, label_list=sorted(label_counts), lookback=config.lookback,
        session_policy='Existing diagnostic grid 09:00..15:30 KST inclusive, completed 1m bars, same day only; not exchange semantics.',
        prediction_policy=f'max(anchor event+1m, anchor available_at), at most event+{config.max_delay_minutes}m; before session end. Never wait for future lookback/index.',
        label_policy='Decimal returns as fractions. Outcome close at event+horizon, entire path required. executable: first minute open strictly after prediction_at; exit close of horizon-th entry bar. Verified real historical receipts allowed only for outcomes.',
        cost_assumptions=dict(fee_rate=config.fee_rate, sell_tax_rate=config.sell_tax_rate, slippage_rate=config.slippage_rate,
                              quantity=1, rounding='PAPER convention: fee/tax each ROUND_DOWN to KRW 1; per-share experiment, not a portfolio backtest'),
        macro_availability_policy='Use max(RAW available_at, RAW collected_at, collection run finished_at). Require completed MACRO_CONTEXT run. RAW event/receipt times remain unchanged.',
        usable_samples=len(good), excluded_samples=len(excluded), exclusion_reason_counts=reason_counts,
        chronological_dates=usable_dates, split_policy='No random split. No fixed final test until longer coverage. Use past train, later validation, latest test with horizon purge at boundaries.',
        readiness_criteria='NOT_READY if no core samples; ML_READY >=20 usable dates, all symbols usable, >=80% usable; otherwise PARTIALLY_READY. First exploratory experiment >=5 usable dates and >0 samples.',
        artifacts={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.glob('*.csv'))})
    (output/'readiness-report.json').write_text(json.dumps(report, default=encode, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    (output/'manifest.json').write_text(json.dumps(manifest, default=encode, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return report, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='artifacts/ml/datasets/v1')
    parser.add_argument('--version', choices=['v1'], default='v1')
    parser.add_argument('--lookback', type=int, default=40)
    parser.add_argument('--verify-determinism', action='store_true', help='Rebuild from the same in-memory read-only snapshot and compare content hash')
    args = parser.parse_args()
    config = Config(lookback=args.lookback, fee_rate=str(settings.paper_fee_rate),
                    sell_tax_rate=str(settings.paper_sell_tax_rate), slippage_rate=str(settings.paper_slippage_rate))
    data, meta = load_snapshot()
    symbols = sorted(set(settings.watch_symbols) | {r['symbol'] for r in data[TABLES[0]]})
    report, manifest = generate(data, meta, config, symbols, args.output)
    if args.verify_determinism:
        stocks, indices, macros, decisions, runs = [data[t] for t in TABLES]
        dates = report['market_dates']
        audits = audit_series(stocks, 'symbol', symbols, dates, runs, meta['as_of'])
        audits += audit_series(indices, 'indicator', ('KOSPI', 'KOSDAQ'), dates, runs, meta['as_of'])
        rebuilt = build_samples(stocks, indices, macro_feature_rows(macros, runs), decisions, symbols, dates, meta['as_of'], config, audits)
        verification = dict(dataset_hash=digest(dict(config=asdict(config), symbols=symbols, samples=rebuilt)),
                            usable_samples=sum(s['usable'] for s in rebuilt), candidate_samples=len(rebuilt))
        verification['matches'] = (verification['dataset_hash'] == manifest['dataset_hash']
                                   and verification['usable_samples'] == report['usable_samples'])
        (Path(args.output)/'determinism-check.json').write_text(json.dumps(verification, indent=2)+'\n', encoding='utf-8')
        if not verification['matches']:
            raise RuntimeError('Same-snapshot determinism verification failed')
    print(json.dumps({k: report[k] for k in ('readiness', 'first_ml_experiment', 'raw_candles', 'candidate_samples',
        'usable_samples', 'excluded_samples', 'usable_ratio', 'exclusion_reason_counts', 'label_counts')}, indent=2))
    print('dataset_hash:', manifest['dataset_hash'])


if __name__ == '__main__':
    main()
