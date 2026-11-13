"""Pure caller-input adapters and DAY01 coverage diagnostics. No I/O or calendars."""
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Iterable, Mapping

from .day01 import Candle, Config, KST, MINUTE, _aware, replay, select_universe


@dataclass(frozen=True)
class ResearchInput:
    candles: tuple[Candle, ...]
    provenance: tuple[dict, ...]
    excluded_rows: tuple[dict, ...]


def _timestamp(value):
    parsed = datetime.fromisoformat(value) if isinstance(value,str) else value
    _aware(parsed)
    return parsed  # preserve caller's original offset; core replay normalizes KST


def _decimal(value):
    if isinstance(value,bool) or not isinstance(value,(Decimal,str,int)):
        raise ValueError('numeric inputs must be Decimal, decimal text or integer')
    try:
        return Decimal(value)
    except Exception as exc:
        raise ValueError('invalid decimal input') from exc


def adapt_rows(rows: Iterable[Mapping], *, universe: Iterable[str]) -> ResearchInput:
    """Explicit RAW mapping contract; no invented availability, collection or source."""
    symbols = select_universe(universe)
    accepted, provenance, excluded, keys = [], [], [], set()
    for raw in rows:
        if not isinstance(raw,Mapping) or 'symbol' not in raw:
            raise ValueError('each input row must be a mapping with explicit symbol')
        if raw.get('symbol') not in symbols:
            continue
        if not isinstance(raw.get('source'),str) or not raw['source'].strip():
            raise ValueError('explicit source required')
        if raw.get('interval') != '1m':
            raise ValueError('explicit 1m interval required')
        try:
            candle = Candle(raw['symbol'],_timestamp(raw['event_at']),_timestamp(raw['available_at']),
                *(_decimal(raw[k]) for k in ('open_price','high_price','low_price','close_price','volume')),
                turnover=_decimal(raw['turnover']) if raw.get('turnover') is not None else None)
        except KeyError as exc:
            raise ValueError(f'missing required input field: {exc.args[0]}') from exc
        candle.validate(require_regular_session=False)
        key = candle.symbol,_aware(candle.event_at)
        if key in keys:
            raise ValueError('duplicate symbol/event; caller must explicitly select one source')
        keys.add(key)
        meta = dict(source=raw['source'],interval=raw['interval'],symbol=candle.symbol,
                    event_at=candle.event_at,available_at=candle.available_at)
        if raw.get('collected_at') is not None:
            collected = _timestamp(raw['collected_at'])
            if collected < candle.available_at:
                raise ValueError('collected_at precedes available_at')
            meta['collected_at'] = collected
        if 'collection_run_id' in raw:
            run_id = raw['collection_run_id']
            if run_id is not None and (type(run_id) is not int or run_id<1):
                raise ValueError('collection_run_id must be positive integer or None')
            meta['collection_run_id'] = run_id
        clock = _aware(candle.event_at).time()
        if not time(9) <= clock < time(15,30):
            excluded.append(dict(candle=candle,provenance=meta,
                                 reason='CLOSING_1530' if clock==time(15,30) else 'OUTSIDE_REPLAY_SESSION'))
        else:
            accepted.append(candle)
            provenance.append(meta)
    ordered = sorted(zip(accepted,provenance),key=lambda pair:(pair[0].symbol,pair[0].event_at))
    return ResearchInput(tuple(c for c,_ in ordered),tuple(p for _,p in ordered),
                         tuple(sorted(excluded,key=lambda r:(r['candle'].symbol,r['candle'].event_at))))


def coverage(data: ResearchInput, *, universe: Iterable[str], expected_session_dates: Iterable[date]) -> dict:
    """390-minute grid per EXPLICIT caller date. Does not infer holidays or DB range."""
    symbols = select_universe(universe)
    days = []
    for day in expected_session_dates:
        if isinstance(day,str):
            day = date.fromisoformat(day)
        if type(day) is not date:
            raise ValueError('expected_session_dates must contain dates, not datetimes')
        days.append(day)
    days = sorted(set(days))
    if not days:
        raise ValueError('explicit nonempty expected_session_dates required')
    rows = [c for c in data.candles if c.symbol in symbols]
    excluded = [r for r in data.excluded_rows if r['candle'].symbol in symbols]
    groups = {}
    keys = set()
    for row in rows:
        row.validate()
        at = _aware(row.event_at)
        if (row.symbol,at) in keys:
            raise ValueError('duplicate coverage candle')
        keys.add((row.symbol,at))
        groups.setdefault((at.date(),row.symbol),set()).add(at)
    cells = []
    for day in days:
        start = datetime.combine(day,time(9),KST)
        expected = {start+i*MINUTE for i in range(390)}
        for symbol in symbols:
            actual = groups.get((day,symbol),set())
            missing = sorted(expected-actual)
            cells.append(dict(day=day,symbol=symbol,count=len(actual),expected_minutes=390,
                              missing_minutes=missing,missing_count=len(missing),
                              status='NO_ROWS' if not actual else 'MISSING' if missing else 'COMPLETE'))
    events = [_aware(r.event_at) for r in rows]
    avail = [_aware(r.available_at) for r in rows]
    lag = [(r.available_at-r.event_at-MINUTE).total_seconds() for r in rows]
    observed_days = sorted({at.date() for at in events})
    missing_days = [day for day in days if not any(d==day for d,_ in groups)]
    missing_symbols = [s for s in symbols if not any(row.symbol==s for row in rows)]
    return dict(scope='CALLER_PROVIDED_ROWS_ONLY',actual_stored_date_range='UNKNOWN',
        status='NO_ROWS' if not rows else 'MISSING' if any(c['missing_count'] for c in cells) else 'COMPLETE',
        universe=symbols,expected_session_dates=tuple(days),input_count=len(rows)+len(excluded),
        replay_count=len(rows),excluded_count=len(excluded),closing_1530_count=sum(r['reason']=='CLOSING_1530' for r in excluded),
        outside_session_count=sum(r['reason']=='OUTSIDE_REPLAY_SESSION' for r in excluded),
        event_min=min(events) if events else None,event_max=max(events) if events else None,
        available_min=min(avail) if avail else None,available_max=max(avail) if avail else None,
        observed_days=observed_days,missing_days=missing_days,unexpected_days=sorted(set(observed_days)-set(days)),
        missing_symbols=missing_symbols,missing_minute_count=sum(c['missing_count'] for c in cells),cells=cells,
        availability=dict(on_time_count=sum(x==0 for x in lag),late_count=sum(x>0 for x in lag),
            max_lag_seconds=max(lag) if lag else None,
            historical_backfill_count=sum(_aware(r.available_at).date()>_aware(r.event_at).date() for r in rows)),
        zero_volume_count=sum(r.volume==0 for r in rows),missing_turnover_count=sum(r.turnover is None for r in rows),
        exact_vwap_turnover_ready=bool(rows) and all(r.turnover is not None for r in rows),
        strict_open_quote_evidence='NOT_INFERABLE_FROM_CANDLE_INPUT')


def run_input(data: ResearchInput, config: Config, *, universe: Iterable[str],
              expected_session_dates: Iterable[date], open_quotes=()) -> dict:
    """No implicit date/universe selection; coverage expectations do not filter replay."""
    symbols = select_universe(universe)
    config.validate()
    report = coverage(data,universe=symbols,expected_session_dates=expected_session_dates)
    selected = [c for c in data.candles if c.symbol in symbols]
    result = replay(selected,config,universe=symbols,open_quotes=open_quotes) if selected else None
    run_status = 'NO_REPLAY_ROWS'
    if result is not None:
        assumptions = result['research_assumptions']
        # RAN means the normal simulation path completed, never that invalid
        # counterfactual research was successful. Forward nonvalid research states.
        run_status = ('RAN' if not assumptions['active'] or assumptions['run_status']=='ESTIMATED_ONLY'
                      else assumptions['run_status'] or 'INCONCLUSIVE_RESEARCH_STATUS')
    return dict(run_status=run_status,coverage=report,result=result,
                mode=config.execution,uncertain=True,physical_fill_claim=False,
                costs=dict(fee_rate=config.fee_rate,sell_tax_rate=config.sell_tax_rate,slippage_rate=config.slippage_rate),
                unfilled_entry_quantity=result['summary']['unfilled_entry_quantity'] if result else 0,
                unfilled_exit_quantity=result['summary']['unfilled_exit_quantity'] if result else 0,
                provenance=tuple(p for p in data.provenance if p['symbol'] in symbols),
                excluded_rows=tuple(r for r in data.excluded_rows if r['candle'].symbol in symbols))
