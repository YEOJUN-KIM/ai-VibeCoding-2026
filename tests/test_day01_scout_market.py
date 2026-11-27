"""Synthetic tests for the pure market-data adapters (ranking -> liquidity, stored 1m rows -> readiness).
No network, DB, files, clock or broker. Rows follow the ml_raw_candles / available_candles() columns."""
import ast
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

from auto_trader import day01_scout as sc
from auto_trader import day01_scout_link as link
from auto_trader import day01_scout_market as market
from auto_trader.day01_scout_market import (
    build_market_inputs, liquidity_from_snapshot, readiness_from_rows)
from auto_trader.ml.day01 import Config, KST, evaluate_entry

RESEARCH = Config(execution='research_estimated_open', vwap_mode='typical_approximation')
RESEARCH_EXACT = Config(execution='research_estimated_open', vwap_mode='exact')
LEGACY = Config(execution='legacy_close', vwap_mode='typical_approximation')
AS_OF = datetime(2026, 11, 13, 9, 20, 10, tzinfo=KST)
# Scout skips the spread check only if BOTH are set (an existing liquidity fact keeps its thresholds otherwise).
OPT_OUT = dict(require_liquidity=False, max_spread_pct=None)


def at(h, m, s=0, day=13):
    return datetime(2026, 11, day, h, m, s, tzinfo=KST)


def row(symbol, i, price='100', *, delay=2, turnover=None, day=13, **changes):
    event = at(9, 0, day=day) + timedelta(minutes=i)
    available = event + timedelta(minutes=1, seconds=delay)
    base = dict(source='TOSS', symbol=symbol, interval='1m', event_at=event, available_at=available,
                collected_at=available, open_price=D(price), high_price=D(price), low_price=D(price),
                close_price=D(price), volume=D(100))
    if turnover is not None:
        base['turnover'] = D(price) * D(100)
    base.update(changes)
    return base


def bars(symbol, count=20, **kw):
    return [row(symbol, i, **kw) for i in range(count)]


def snapshot(symbols=('AAA',), *, received=at(9, 19, 40), ranked=at(9, 19, 30), **kw):
    rows = [{"rank": i, "symbol": s, "tradingAmount": "20000000000", "tradingVolume": "100000",
             "price": {"lastPrice": "5000", "changeRate": "1"}} for i, s in enumerate(symbols, 1)]
    return sc.snapshot_from_rankings(ranked, rows, received_at=received, **kw)


def ready(symbol='AAA', **kw):
    facts, reports, candles, notes, _prov = readiness_from_rows(kw.pop('rows'), symbols=[symbol], as_of=kw.pop('as_of', AS_OF), **kw)
    return facts, reports[0] if reports else None, candles, notes


class LiquidityTests(unittest.TestCase):
    def test_turnover_and_volume_come_from_the_ranking_and_spread_stays_unknown(self):
        snap = snapshot(('AAA', 'BBB'))
        facts, notes = liquidity_from_snapshot(snap, as_of=AS_OF)
        self.assertEqual([f.symbol for f in facts], ['AAA', 'BBB'])
        for f in facts:
            self.assertEqual((f.turnover, f.volume, f.spread_pct), (D('20000000000'), D('100000'), None))
            self.assertEqual(f.observed_at, snap.received_at)                      # our receipt, not rankedAt
            self.assertNotEqual(f.observed_at, snap.provider_ranked_at)
        self.assertIn((None, 'SPREAD_UNKNOWN_NO_ORDERBOOK'), notes)

    def test_a_ranking_received_after_as_of_yields_nothing_and_missing_provider_time_is_noted(self):
        late = snapshot(received=at(9, 21, 0))
        facts, notes = liquidity_from_snapshot(late, as_of=AS_OF)
        self.assertEqual(facts, ())
        self.assertIn((None, 'RANKING_NOT_YET_RECEIVED'), notes)
        unknown = sc.snapshot_from_rankings(None, [{"rank": 1, "symbol": "AAA", "tradingAmount": "1", "tradingVolume": "1",
                                                     "price": {"lastPrice": "5000"}}], received_at=at(9, 19, 40))
        self.assertIn((None, 'RANKING_PROVIDER_TIME_UNKNOWN'), liquidity_from_snapshot(unknown, as_of=AS_OF)[1])

    def test_conflicting_duplicate_symbol_rows_produce_no_fact(self):
        rows = [{"rank": 1, "symbol": "AAA", "tradingAmount": "20000000000", "tradingVolume": "100000", "price": {"lastPrice": "5000"}},
                {"rank": 2, "symbol": "AAA", "tradingAmount": "30000000000", "tradingVolume": "100000", "price": {"lastPrice": "5000"}},
                {"rank": 3, "symbol": "BBB", "tradingAmount": "20000000000", "tradingVolume": "100000", "price": {"lastPrice": "5000"}}]
        snap = sc.snapshot_from_rankings(at(9, 19, 30), rows, received_at=at(9, 19, 40))
        facts, notes = liquidity_from_snapshot(snap)
        self.assertEqual([f.symbol for f in facts], ['BBB'])
        self.assertIn(('AAA', 'DUPLICATE_RANKING_ROW_CONFLICT'), notes)


class ReadinessTests(unittest.TestCase):
    def test_complete_opening_and_vwap_modes_are_distinguished(self):
        data = bars('AAA')
        _, exact, candles, _ = ready(rows=data, config=RESEARCH_EXACT)
        self.assertEqual((exact.completed_1m_bars, exact.required_bars, exact.opening_range_ready), (20, 20, True))
        self.assertFalse(exact.vwap_ready)
        self.assertEqual((exact.vwap_basis, exact.ready), ('UNAVAILABLE', False))
        self.assertIn('VWAP_NEEDS_TURNOVER', exact.codes)                        # Toss 1m rows carry no turnover
        facts, typical, _, _ = ready(rows=data, config=RESEARCH, allow_approximate_vwap=True)
        self.assertEqual((typical.vwap_ready, typical.vwap_basis, typical.ready), (True, 'TYPICAL_APPROXIMATION', True))
        self.assertIn('VWAP_APPROXIMATE', typical.codes)
        self.assertEqual((facts[0].completed_1m_bars, facts[0].opening_range_ready, facts[0].vwap_ready), (20, True, True))
        _, refused, _, _ = ready(rows=data, config=RESEARCH)                      # approximation never implicit
        self.assertEqual((refused.vwap_ready, refused.ready), (False, False))
        self.assertIn('VWAP_APPROXIMATION_NOT_ALLOWED', refused.codes)
        with_turnover = [row('AAA', i, turnover=True) for i in range(20)]
        _, exact_ok, _, _ = ready(rows=with_turnover, config=RESEARCH_EXACT)
        self.assertEqual((exact_ok.vwap_ready, exact_ok.vwap_basis), (True, 'EXACT_TURNOVER'))
        self.assertEqual(len(candles), 20)

    def test_vwap_mode_must_match_the_config(self):
        with self.assertRaises(ValueError):
            readiness_from_rows([], symbols=['AAA'], as_of=AS_OF, config=RESEARCH, vwap_mode='exact')
        with self.assertRaises(ValueError):
            readiness_from_rows([], symbols=['AAA'], as_of=AS_OF, vwap_mode='bogus')
        with self.assertRaises(ValueError):
            readiness_from_rows([], symbols=['AAA'], as_of=datetime(2026, 11, 13, 9, 20))

    def test_missing_opening_bar_session_gap_and_no_candles_are_separate(self):
        data = [r for r in bars('AAA') if r['event_at'] != at(9, 5)]
        _, opening, _, _ = ready(rows=data, config=RESEARCH, allow_approximate_vwap=True)
        self.assertEqual((opening.completed_1m_bars, opening.opening_range_ready, opening.vwap_ready), (5, False, False))
        self.assertIn('OPENING_MISSING', opening.codes)
        self.assertNotIn('SESSION_GAP', opening.codes)
        self.assertEqual(opening.missing_minutes, (at(9, 5),))
        data = [r for r in bars('AAA') if r['event_at'] != at(9, 17)]
        _, gap, _, _ = ready(rows=data, config=RESEARCH, allow_approximate_vwap=True)
        self.assertEqual((gap.completed_1m_bars, gap.opening_range_ready, gap.vwap_ready), (17, True, False))
        self.assertIn('SESSION_GAP', gap.codes)
        self.assertNotIn('OPENING_MISSING', gap.codes)
        facts, none, candles, _ = ready(rows=[], config=RESEARCH, allow_approximate_vwap=True)
        self.assertEqual((facts, candles), ((), ()))
        self.assertIn('NO_CANDLES', none.codes)
        self.assertFalse(none.ready)

    def test_before_the_opening_range_is_due_it_is_not_reported_as_missing(self):
        early = at(9, 10, 10)
        _, rep, _, _ = ready(rows=bars('AAA', 10), as_of=early, config=RESEARCH, allow_approximate_vwap=True)
        self.assertIn('OPENING_NOT_DUE', rep.codes)
        self.assertNotIn('OPENING_MISSING', rep.codes)
        self.assertEqual((rep.opening_range_ready, rep.ready), (False, False))

    def test_late_bars_are_distinguished_from_missing_ones_and_never_used_early(self):
        data = bars('AAA')
        data[18] = row('AAA', 18, delay=190)                                     # available 09:22:10, 190 s after its minute end
        late_row = data[18]
        _, early, candles, _ = ready(rows=data, config=RESEARCH, allow_approximate_vwap=True)    # as_of 09:20:10
        self.assertEqual(early.missing_minutes, (at(9, 18),))
        self.assertIn('LATE_NOT_YET_AVAILABLE', early.codes)                    # exists, but only later: late, not "never"
        self.assertIn('SESSION_GAP', early.codes)
        self.assertFalse(early.vwap_ready)
        self.assertNotIn(late_row['event_at'], [c.event_at for c in candles])
        _, later, candles2, _ = ready(rows=data, as_of=at(9, 23), config=RESEARCH, allow_approximate_vwap=True)
        self.assertIn(at(9, 18), [c.event_at for c in candles2])
        self.assertIn('LATE_ARRIVAL', later.codes)                              # 190 s > the 2 minute default max lag
        self.assertEqual(later.late_bar_count, 1)
        veryl = bars('AAA')
        veryl[10] = row('AAA', 10, delay=200)                                    # > 2 min lag: usable but flagged
        _, flagged, _, _ = ready(rows=veryl, as_of=at(9, 25), config=RESEARCH, allow_approximate_vwap=True)
        self.assertIn('LATE_ARRIVAL', flagged.codes)
        self.assertEqual(flagged.late_bar_count, 1)
        self.assertTrue(flagged.opening_range_ready)

    def test_rows_available_after_as_of_cannot_change_anything(self):
        base = bars('AAA')
        extra = base + [row('AAA', 20, price='999', delay=2), row('AAA', 21, price='1', delay=2)]
        extra += [row('AAA', 5, price='777', delay=1000)]                        # conflicting re-delivery far in the future
        a = ready(rows=base, config=RESEARCH, allow_approximate_vwap=True)
        b = ready(rows=extra, config=RESEARCH, allow_approximate_vwap=True)
        self.assertEqual(a[0], b[0])
        self.assertEqual(a[2], b[2])
        self.assertEqual(replace(a[1], codes=()), replace(b[1], codes=()))

    def test_availability_uses_the_later_of_available_at_and_collected_at(self):
        data = bars('AAA')
        data[19] = row('AAA', 19, collected_at=at(9, 20, 30))                    # collected after as_of
        _, rep, candles, _ = ready(rows=data, config=RESEARCH, allow_approximate_vwap=True)
        self.assertEqual(rep.completed_1m_bars, 19)
        self.assertEqual(len(candles), 19)

    def test_incomplete_unaligned_invalid_and_foreign_rows_are_defects_not_data(self):
        data = bars('AAA')
        data[19] = row('AAA', 19, available_at=at(9, 19, 40))                    # stamped before its minute ended
        _, rep, _, _ = ready(rows=data, config=RESEARCH, allow_approximate_vwap=True)
        self.assertIn('INCOMPLETE_BAR', rep.codes)
        self.assertEqual(rep.completed_1m_bars, 19)
        for changes, code in (({'event_at': at(9, 19, 30)}, 'UNALIGNED_EVENT'),
                              ({'interval': '5m'}, 'NOT_1M'), ({'source': ' '}, 'NO_SOURCE'),
                              ({'collected_at': at(9, 19, 30)}, 'COLLECTED_BEFORE_AVAILABLE'),
                              ({'high_price': D(1)}, 'INVALID_ROW'), ({'close_price': 'x'}, 'INVALID_ROW')):
            data = bars('AAA')
            data[19] = dict(row('AAA', 19), **changes)
            _, rep, _, _ = ready(rows=data, config=RESEARCH, allow_approximate_vwap=True)
            self.assertIn(code, rep.codes, changes)
            self.assertEqual(rep.completed_1m_bars, 19, changes)
        for bad in ('not a row', {'no': 'symbol'}):
            _, _, _, notes = ready(rows=bars('AAA') + [bad], config=RESEARCH, allow_approximate_vwap=True)
            self.assertTrue(any(code in ('ROW_NOT_MAPPING', 'ROW_BAD_SYMBOL') for _, code in notes))

    def test_duplicate_minutes_identical_collapse_conflicting_block(self):
        data = bars('AAA')
        _, same, candles, _ = ready(rows=data + [dict(data[7])], config=RESEARCH, allow_approximate_vwap=True)
        self.assertTrue(same.vwap_ready)
        self.assertEqual(len(candles), 20)
        other_source = dict(data[7], source='OTHER')
        _, tolerated, _, _ = ready(rows=data + [other_source], config=RESEARCH, allow_approximate_vwap=True)
        self.assertTrue(tolerated.vwap_ready)                                     # same values from another source
        conflict = dict(data[7], close_price=D('101'), high_price=D('101'))
        _, bad, candles, _ = ready(rows=data + [conflict], config=RESEARCH, allow_approximate_vwap=True)
        self.assertIn('DUPLICATE_CONFLICT', bad.codes)
        self.assertEqual((bad.vwap_ready, bad.ready), (False, False))
        self.assertEqual(bad.completed_1m_bars, 7)
        self.assertNotIn(data[7]['event_at'], [c.event_at for c in candles])      # neither version is used

    def test_symbol_normalization_unmatched_and_ambiguous_symbols(self):
        data = bars(' aaa ') + bars('BBB') + bars('ZZZ')
        facts, reports, candles, notes, _prov = readiness_from_rows(data, symbols=['AAA', 'CCC'], as_of=AS_OF,
                                                             config=RESEARCH, allow_approximate_vwap=True)
        self.assertEqual([f.symbol for f in facts], ['AAA'])
        self.assertEqual({r.symbol: 'NO_CANDLES' in r.codes for r in reports}, {'AAA': False, 'CCC': True})
        self.assertTrue(all(c.symbol == 'AAA' for c in candles))
        self.assertIn((None, 'CANDLE_SYMBOLS_NOT_IN_RANKING:BBB,ZZZ'), notes)
        facts, reports, _, notes, _prov = readiness_from_rows(bars('AAA'), symbols=['AAA', 'aaa'], as_of=AS_OF,
                                                       config=RESEARCH, allow_approximate_vwap=True)
        self.assertEqual((facts, reports), ((), ()))
        self.assertIn(('aaa', 'SYMBOL_ALIAS_AMBIGUOUS'), notes)

    def test_other_session_and_outside_session_rows_are_ignored(self):
        data = bars('AAA') + bars('AAA', 20, day=12)
        data.append(row('AAA', 391))                                              # 15:31 is outside [09:00,15:30)
        _, rep, candles, notes = ready(rows=data, config=RESEARCH, allow_approximate_vwap=True)
        self.assertEqual((rep.completed_1m_bars, len(candles)), (20, 20))
        self.assertIn(('AAA', 'ROWS_FROM_OTHER_SESSION_IGNORED:20'), notes)

    def test_strict_modes_need_an_on_time_newest_bar_research_mode_does_not(self):
        late = bars('AAA')                                                         # real collector: ~2 s after the minute
        _, strict, _, _ = ready(rows=late, config=LEGACY, allow_approximate_vwap=True)
        self.assertFalse(strict.ready)
        self.assertIn('NOT_ON_TIME_FOR_STRICT_MODE', strict.codes)
        facts, _, _, _ = ready(rows=late, config=LEGACY, allow_approximate_vwap=True)
        self.assertEqual((facts[0].opening_range_ready, facts[0].vwap_ready), (True, False))
        on_time = bars('AAA', delay=0)
        _, ok, _, _ = ready(rows=on_time, config=LEGACY, allow_approximate_vwap=True)
        self.assertTrue(ok.ready)
        self.assertTrue(ok.on_time_all)
        _, research, _, _ = ready(rows=late, config=RESEARCH, allow_approximate_vwap=True)
        self.assertTrue(research.ready)
        self.assertFalse(research.on_time_all)


def one(rows, **kw):
    """readiness_from_rows for a single symbol: (facts, report, candles, notes, provenance)."""
    facts, reports, candles, notes, prov = readiness_from_rows(rows, **kw)
    return facts, (reports[0] if reports else None), candles, notes, prov


class BoundaryAndReviewRegressionTests(unittest.TestCase):
    def test_collected_after_available_is_the_effective_availability_for_candles_lag_and_strict_gate(self):
        data = bars('AAA', delay=0)
        data[19] = row('AAA', 19, delay=0, collected_at=at(9, 20, 30))        # known to us only at 09:20:30
        asof = at(9, 20, 40)
        facts, rep, candles, _, prov = one(data, symbols=['AAA'], as_of=asof, config=LEGACY,
                                           allow_approximate_vwap=True)
        last = next(c for c in candles if c.event_at == at(9, 19))
        self.assertEqual(last.available_at, at(9, 20, 30))                     # DAY-01 cannot use it earlier
        meta = next(m for m in prov if m['event_at'] == at(9, 19))
        self.assertEqual((meta['available_at'], meta['collected_at'], meta['effective_at']),
                         (at(9, 20, 0), at(9, 20, 30), at(9, 20, 30)))        # originals preserved
        self.assertEqual(facts[0].observed_at, at(9, 20, 30))
        self.assertEqual(rep.max_lag_seconds, 30.0)
        self.assertFalse(rep.on_time_all)
        self.assertIn('NOT_ON_TIME_FOR_STRICT_MODE', rep.codes)            # strict gate sees the collected delay
        self.assertFalse(rep.ready)
        # DAY-01 at 09:20:00 must not have the 09:19 bar yet
        early = [c for c in candles if c.available_at <= at(9, 20, 0)]
        self.assertNotIn(at(9, 19), [c.event_at for c in early])

    def test_grace_cannot_hide_a_missing_turnover_on_the_newest_known_bar(self):
        data = [row('AAA', i, delay=0, turnover=True) for i in range(19)] + [row('AAA', 19, delay=0)]   # bar 19: no turnover
        asof = at(9, 20, 0)
        _, rep, candles, _, _ = one(data, symbols=['AAA'], as_of=asof, config=RESEARCH_EXACT)
        self.assertEqual(rep.required_bars, 19)                                # grace still lets bar 19 be "not yet due"
        self.assertFalse(rep.vwap_ready)
        self.assertIn('VWAP_NEEDS_TURNOVER', rep.codes)
        self.assertEqual(evaluate_entry(list(candles), asof, RESEARCH_EXACT).reason, 'MISSING_EXACT_TURNOVER')
        full = [row('AAA', i, delay=0, turnover=True) for i in range(20)]
        _, ok, candles, _, _ = one(full, symbols=['AAA'], as_of=asof, config=RESEARCH_EXACT)
        self.assertTrue(ok.vwap_ready)
        self.assertNotEqual(evaluate_entry(list(candles), asof, RESEARCH_EXACT).reason, 'MISSING_EXACT_TURNOVER')

    def test_strict_gate_checks_the_newest_known_bar_not_the_one_inside_the_grace_window(self):
        data = bars('AAA', 19, delay=0) + [row('AAA', 19, delay=2)]            # newest bar arrived 2 s late
        _, rep, _, _, _ = one(data, symbols=['AAA'], as_of=at(9, 20, 3), config=LEGACY,
                                              allow_approximate_vwap=True)
        self.assertEqual(rep.required_bars, 19)                                # grace excludes bar 19 from "required"
        self.assertEqual(rep.completed_1m_bars, 20)
        self.assertIn('NOT_ON_TIME_FOR_STRICT_MODE', rep.codes)
        self.assertFalse(rep.ready)
        # at the 09:20 boundary the 09:19 bar has not arrived yet: DAY-01 has nothing to decide on, so not ready
        _, before, _, _, _ = one(bars('AAA', 19, delay=0), symbols=['AAA'], as_of=at(9, 20, 0),
                                 config=LEGACY, allow_approximate_vwap=True)
        self.assertFalse(before.ready)
        self.assertIn('STRICT_DECISION_BAR_MISSING_OR_LATE', before.codes)
        _, arrived, _, _, _ = one(bars('AAA', 20, delay=0), symbols=['AAA'], as_of=at(9, 20, 0),
                                  config=LEGACY, allow_approximate_vwap=True)
        self.assertTrue(arrived.ready)                                          # bar 19 on time at 09:20:00
        self.assertNotIn('STRICT_DECISION_BAR_MISSING_OR_LATE', arrived.codes)

    def test_strict_needs_the_last_minute_before_the_latest_boundary_even_if_older_bars_are_on_time(self):
        data = bars('AAA', 24, delay=0)                                          # 09:00..09:23, boundary 09:25 needs 09:24
        _, rep, candles, _, _ = one(data, symbols=['AAA'], as_of=at(9, 25, 0), config=LEGACY, allow_approximate_vwap=True)
        self.assertEqual((rep.required_bars, rep.completed_1m_bars), (24, 24))     # grace-limited report is unchanged
        self.assertFalse(rep.ready)
        self.assertIn('STRICT_DECISION_BAR_MISSING_OR_LATE', rep.codes)
        self.assertEqual(evaluate_entry(list(candles), at(9, 25, 0), Config(execution='legacy_close',
                         vwap_mode='typical_approximation')).reason, 'NO_FRESH_FIVE_MINUTE_BAR')   # DAY-01 agrees
        _, ok, candles, _, _ = one(bars('AAA', 25, delay=0), symbols=['AAA'], as_of=at(9, 25, 0), config=LEGACY,
                                   allow_approximate_vwap=True)
        self.assertTrue(ok.ready)
        # research mode tolerates the grace window (it can decide on late bars)
        _, research, _, _, _ = one(data, symbols=['AAA'], as_of=at(9, 25, 0), config=RESEARCH, allow_approximate_vwap=True)
        self.assertTrue(research.ready)
        # mid-window as_of: the last boundary is 09:20 and its 09:19 bar was on time
        _, mid, _, _, _ = one(bars('AAA', 23, delay=0), symbols=['AAA'], as_of=at(9, 23, 30), config=LEGACY,
                              allow_approximate_vwap=True)
        self.assertTrue(mid.ready)

    def test_duplicates_with_the_same_effective_time_but_different_original_times_conflict_in_any_order(self):
        base = bars('AAA')
        one_row = dict(base[19], available_at=at(9, 20, 0), collected_at=at(9, 20, 30))
        other = dict(base[19], available_at=at(9, 20, 10), collected_at=at(9, 20, 30))
        for order in ([one_row, other], [other, one_row]):
            data = base[:19] + order
            _, rep, candles, _, prov = one(data, symbols=['AAA'], as_of=at(9, 20, 40), config=RESEARCH,
                                           allow_approximate_vwap=True)
            self.assertIn('DUPLICATE_CONFLICT', rep.codes)
            self.assertEqual((rep.completed_1m_bars, rep.ready), (19, False))
            self.assertNotIn(at(9, 19), [c.event_at for c in candles])
            self.assertNotIn(at(9, 19), [m['event_at'] for m in prov])
        same = dict(one_row)                                                     # a true duplicate is still fine
        _, ok, _, _, _ = one(base[:19] + [one_row, same], symbols=['AAA'], as_of=at(9, 20, 40), config=RESEARCH,
                             allow_approximate_vwap=True)
        self.assertTrue(ok.ready)

    def test_grace_boundary_is_exact(self):
        data = bars('AAA', 19, delay=0)                                          # bar 19 never arrives
        for second, expect_ready in ((4, True), (5, False)):
            _, rep, _, _, _ = one(data, symbols=['AAA'], as_of=at(9, 20, second) , config=RESEARCH,
                                                  allow_approximate_vwap=True)
            self.assertEqual((rep.required_bars, rep.vwap_ready), (19 if second == 4 else 20, expect_ready), second)
        just_before = at(9, 20, 5) - timedelta(microseconds=1)
        _, rep, _, _, _ = one(data, symbols=['AAA'], as_of=just_before, config=RESEARCH,
                                              allow_approximate_vwap=True)
        self.assertEqual(rep.required_bars, 19)

    def test_before_the_open_and_after_the_close(self):
        data = bars('AAA', 20)
        facts, rep, candles, _, _ = one(data, symbols=['AAA'], as_of=at(8, 59), config=RESEARCH,
                                        allow_approximate_vwap=True)
        self.assertEqual((facts, candles, rep.required_bars), ((), (), 0))
        self.assertIn('NO_CANDLES', rep.codes)
        full = [row('AAA', i, delay=0) for i in range(391)]                     # 391st row is 15:30: outside the session
        _, rep, candles, _, _ = one(full, symbols=['AAA'], as_of=at(16, 0), config=RESEARCH,
                                                    allow_approximate_vwap=True)
        self.assertEqual((rep.required_bars, rep.completed_1m_bars, len(candles), rep.ready), (390, 390, 390, True))

    def test_utc_inputs_are_equivalent_to_kst_inputs(self):
        data = bars('AAA')
        utc_data = [dict(r, event_at=r['event_at'].astimezone(timezone.utc), available_at=r['available_at'].astimezone(timezone.utc),
                         collected_at=r['collected_at'].astimezone(timezone.utc)) for r in data]
        a = readiness_from_rows(data, symbols=['AAA'], as_of=AS_OF, config=RESEARCH, allow_approximate_vwap=True)
        b = readiness_from_rows(utc_data, symbols=['AAA'], as_of=AS_OF.astimezone(timezone.utc), config=RESEARCH,
                                allow_approximate_vwap=True)
        self.assertEqual(a[0], b[0])
        self.assertEqual(a[1], b[1])
        self.assertEqual([(c.symbol, c.event_at, c.available_at) for c in a[2]], [(c.symbol, c.event_at, c.available_at) for c in b[2]])


class ConsistencyWithDay01Tests(unittest.TestCase):
    """The adapter's readiness must not contradict what evaluate_entry actually requires."""
    SIGNAL_AT = at(9, 20, 2)                                                      # last bar of the 09:15-09:19 window arrives

    def decide(self, data, config):
        _, rep, candles, _ = ready(rows=data, config=config, allow_approximate_vwap=True, as_of=AS_OF)
        decision = evaluate_entry(list(candles), self.SIGNAL_AT, config)
        return rep, decision

    def test_ready_readiness_means_day01_finds_the_history_complete(self):
        rep, decision = self.decide(bars('AAA'), RESEARCH)
        self.assertTrue(rep.ready)
        self.assertNotIn(decision.reason, {'INCOMPLETE_OPENING_RANGE', 'INCOMPLETE_SESSION_VWAP', 'MISSING_EXACT_TURNOVER',
                                           'INCOMPLETE_PREVIOUS_FIVE_MINUTE_BAR', 'NO_FRESH_FIVE_MINUTE_BAR'})

    def test_opening_gap_agrees(self):
        rep, decision = self.decide([r for r in bars('AAA') if r['event_at'] != at(9, 10)], RESEARCH)
        self.assertFalse(rep.opening_range_ready)
        self.assertEqual(decision.reason, 'INCOMPLETE_OPENING_RANGE')

    def test_session_gap_agrees(self):
        # a gap inside the CURRENT 5-minute window: DAY-01 sees no usable bar and does not enter
        rep, decision = self.decide([r for r in bars('AAA') if r['event_at'] != at(9, 17)], RESEARCH)
        self.assertTrue(rep.opening_range_ready)
        self.assertFalse(rep.vwap_ready)
        self.assertEqual(decision.reason, 'NO_FRESH_FIVE_MINUTE_BAR')
        # a gap older than the last two 5-minute bars: exactly INCOMPLETE_SESSION_VWAP at the 09:30 boundary
        data = [r for r in bars('AAA', 30) if r['event_at'] != at(9, 17)]
        asof = at(9, 30, 10)
        _, reps, candles, _, _prov = readiness_from_rows(data, symbols=['AAA'], as_of=asof, config=RESEARCH,
                                                  allow_approximate_vwap=True)
        self.assertEqual((reps[0].completed_1m_bars, reps[0].opening_range_ready, reps[0].vwap_ready), (17, True, False))
        self.assertEqual(evaluate_entry(list(candles), at(9, 30, 2), RESEARCH).reason, 'INCOMPLETE_SESSION_VWAP')
        # the same minute present again: both agree it is complete
        full = bars('AAA', 30)
        _, ok, candles, _, _prov = readiness_from_rows(full, symbols=['AAA'], as_of=asof, config=RESEARCH, allow_approximate_vwap=True)
        self.assertTrue(ok[0].vwap_ready)
        self.assertNotIn(evaluate_entry(list(candles), at(9, 30, 2), RESEARCH).reason,
                         {'INCOMPLETE_SESSION_VWAP', 'INCOMPLETE_OPENING_RANGE', 'INCOMPLETE_PREVIOUS_FIVE_MINUTE_BAR'})

    def test_exact_vwap_without_turnover_agrees(self):
        _, rep, candles, _ = ready(rows=bars('AAA'), config=RESEARCH_EXACT, as_of=AS_OF)
        decision = evaluate_entry(list(candles), self.SIGNAL_AT, RESEARCH_EXACT)
        self.assertFalse(rep.vwap_ready)
        self.assertEqual(decision.reason, 'MISSING_EXACT_TURNOVER')
        with_turnover = [row('AAA', i, turnover=True) for i in range(20)]
        _, rep2, candles2, _ = ready(rows=with_turnover, config=RESEARCH_EXACT, as_of=AS_OF)
        self.assertTrue(rep2.vwap_ready)
        self.assertNotEqual(evaluate_entry(list(candles2), self.SIGNAL_AT, RESEARCH_EXACT).reason, 'MISSING_EXACT_TURNOVER')


class ScoutEndToEndTests(unittest.TestCase):
    def scout(self, inputs, snap, *, cfg=RESEARCH, **scout_kw):
        obs = AS_OF - timedelta(seconds=3)
        symbols = [f.symbol for f in inputs.liquidity] or ['AAA']
        instruments = [sc.InstrumentFacts(s, obs, market='KOSPI', currency='KRW', security_type='STOCK',
                                          is_common_share=True, status='ACTIVE', krx_trading_suspended=False,
                                          liquidation_trading=False) for s in symbols]       # SYNTHETIC
        pf = sc.Portfolio(obs, cash='1000000', total_asset='1000000', **link.portfolio_limits_for(cfg))   # SYNTHETIC
        scout_cfg = link.scout_config_for(cfg, target_count=10, **scout_kw)
        return sc.select_watchlist(AS_OF, [snap], instruments=instruments, liquidity=inputs.liquidity,
                                   dayone=inputs.dayone, portfolio=pf, config=scout_cfg, risk_config=cfg)

    def inputs(self, rows, snap=None, **kw):
        snap = snap or snapshot()
        return snap, build_market_inputs(snap, rows, as_of=AS_OF, config=RESEARCH, allow_approximate_vwap=True, **kw)

    def test_without_an_order_book_scout_still_excludes_everyone_and_nothing_is_fully_eligible(self):
        snap, inputs = self.inputs(bars('AAA'))
        result = self.scout(inputs, snap)
        self.assertEqual(result.candidates, ())
        self.assertIn('LIQUIDITY_SPREAD_UNKNOWN', next(e.reasons for e in result.exclusions if e.symbol == 'AAA'))
        # require_liquidity=False alone does NOT bypass: an existing fact with an unknown spread is still excluded
        self.assertEqual(self.scout(inputs, snap, require_liquidity=False).candidates, ())
        # full explicit opt-out (no spread threshold): candidate allowed, never fully eligible, gap named
        opted = self.scout(inputs, snap, **OPT_OUT)
        self.assertEqual([c.symbol for c in opted.candidates], ['AAA'])
        self.assertFalse(opted.candidates[0].fully_eligible)
        self.assertIn('LIQUIDITY_UNVERIFIED', opted.candidates[0].verification_gaps)

    def test_readiness_flows_into_scout_reasons(self):
        snap, ok = self.inputs(bars('AAA'))
        self.assertEqual(self.scout(ok, snap, **OPT_OUT).candidates[0].symbol, 'AAA')
        snap, stale = self.inputs(bars('AAA', 10))                                  # feed stopped at 09:10: stale, not "ready"
        self.assertEqual(next(e.reasons for e in self.scout(stale, snap, **OPT_OUT).exclusions), ('DAY01_READINESS_STALE',))
        snap, gappy = self.inputs([r for r in bars('AAA') if r['event_at'] != at(9, 14)])   # fresh feed, opening gap
        reasons = next(e.reasons for e in self.scout(gappy, snap, **OPT_OUT).exclusions)
        self.assertIn('DAY01_BARS_INSUFFICIENT', reasons)
        self.assertIn('DAY01_OPENING_RANGE_NOT_READY', reasons)
        self.assertIn('DAY01_VWAP_NOT_READY', reasons)
        snap = snapshot()
        exact = build_market_inputs(snap, bars('AAA'), as_of=AS_OF, config=RESEARCH_EXACT)
        reasons = next(e.reasons for e in self.scout(exact, snap, cfg=RESEARCH_EXACT, **OPT_OUT).exclusions)
        self.assertIn('DAY01_VWAP_NOT_READY', reasons)                              # exact VWAP is impossible with Toss rows
        snap, none = self.inputs([])
        reasons = next(e.reasons for e in self.scout(none, snap, **OPT_OUT).exclusions)
        self.assertIn('DAY01_READINESS_MISSING', reasons)

    def test_ranking_and_candle_time_alignment_is_reported_not_hidden(self):
        snap = snapshot(received=at(9, 5, 0), ranked=at(9, 4, 50))
        _, inputs = self.inputs(bars('AAA'), snap)
        self.assertIn(('AAA', 'RANKING_CANDLE_SKEW'), inputs.notes)
        self.assertGreater(inputs.ranking['skew_seconds']['AAA'], 180)
        self.assertEqual(inputs.ranking['received_at'], at(9, 5, 0))
        self.assertEqual(self.scout(inputs, snap, **OPT_OUT).candidates, ())      # Scout: ranking STALE
        future = snapshot(received=at(9, 21, 0))
        inputs = build_market_inputs(future, bars('AAA'), as_of=AS_OF, config=RESEARCH, allow_approximate_vwap=True)
        self.assertEqual(inputs.liquidity, ())
        self.assertIn((None, 'RANKING_NOT_YET_RECEIVED'), inputs.notes)

    def test_dayone_observed_at_is_the_newest_needed_bar_availability_so_stale_feeds_are_stale(self):
        snap, inputs = self.inputs(bars('AAA'))
        self.assertEqual(inputs.dayone[0].observed_at, at(9, 20, 2))                # bar 09:19 available 09:20:02
        old = bars('AAA')
        stale_asof = datetime(2026, 11, 13, 9, 26, 0, tzinfo=KST)                   # no new bars for 6 minutes
        facts, reports, _, _, _prov = readiness_from_rows(old, symbols=['AAA'], as_of=stale_asof, config=RESEARCH,
                                                   allow_approximate_vwap=True)
        self.assertFalse(reports[0].vwap_ready)                                      # minutes 20..24 are missing now
        self.assertIn('SESSION_GAP', reports[0].codes)


class CandleQualityTests(unittest.TestCase):
    """CANDLE-01: gaps found in the existing readiness checks (all synthetic rows, no clock/DB/network)."""

    def rep(self, rows, **kw):
        _, report, candles, _ = ready(rows=rows, config=RESEARCH, allow_approximate_vwap=True, **kw)
        return report, candles

    def test_baseline_is_ready_and_unchanged(self):
        report, candles = self.rep(bars('AAA'))
        self.assertEqual((report.ready, report.completed_1m_bars, len(candles)), (True, 20, 20))
        self.assertNotIn('ZERO_VOLUME_BARS', report.codes)

    def test_zero_traded_volume_is_never_a_ready_vwap(self):
        for label, volume_of in (('all zero', lambda i: D(0)), ('only the first 15 zero', lambda i: D(0) if i < 15 else D(50))):
            with self.subTest(label):
                report, _ = self.rep([row('AAA', i, volume=volume_of(i)) for i in range(20)])
                self.assertFalse(report.vwap_ready)
                self.assertFalse(report.ready)
                self.assertEqual(report.vwap_basis, 'UNAVAILABLE')
                self.assertIn('VWAP_ZERO_VOLUME', report.codes)
        facts, report, _, _ = ready(rows=[row('AAA', i, volume=D(0)) for i in range(20)], config=RESEARCH, allow_approximate_vwap=True)
        self.assertFalse(facts[0].vwap_ready)                                  # the fact Scout sees agrees

    def test_a_single_zero_volume_bar_is_reported_but_does_not_block(self):
        report, _ = self.rep([row('AAA', i, volume=D(0) if i == 7 else D(100)) for i in range(20)])
        self.assertEqual((report.ready, 'ZERO_VOLUME_BARS' in report.codes, 'VWAP_ZERO_VOLUME' in report.codes), (True, True, False))

    def test_implausible_prices_block_the_bar_and_never_reach_day01(self):
        spike = bars('AAA')
        spike[10] = row('AAA', 10, price='1000')                              # 10x the neighbours: OHLC-consistent but impossible
        report, candles = self.rep(spike)
        self.assertEqual((report.ready, report.completed_1m_bars), (False, 10))
        self.assertIn('IMPLAUSIBLE_PRICE', report.codes)
        self.assertTrue(all(c.high_price < 500 for c in candles))
        wide = bars('AAA')
        wide[3] = row('AAA', 3, high_price=D(140), low_price=D(100), open_price=D(100), close_price=D(100))   # 40 % inside one bar
        self.assertIn('IMPLAUSIBLE_PRICE', self.rep(wide)[0].codes)
        gap = [row('AAA', i, price='100') for i in range(10)] + [row('AAA', i, price='140') for i in range(10, 20)]
        self.assertIn('IMPLAUSIBLE_PRICE', self.rep(gap)[0].codes)            # +40 % between adjacent minutes
        edge = [row('AAA', i, price='100') for i in range(10)] + [row('AAA', i, price='130') for i in range(10, 20)]
        report, _ = self.rep(edge)                                            # exactly +30 % is allowed
        self.assertEqual((report.ready, 'IMPLAUSIBLE_PRICE' in report.codes), (True, False))

    def test_clock_uncertainty_distrusts_bars_stamped_too_close_to_their_minute_end(self):
        close = [row('AAA', i, delay=0) for i in range(20)]                   # stamped exactly at the minute end
        self.assertTrue(self.rep(close)[0].ready)                             # default: unchanged
        report, candles = self.rep(close, clock_uncertainty=timedelta(seconds=2))
        self.assertFalse(report.ready)
        self.assertIn('CLOCK_UNCERTAIN_BAR', report.codes)
        self.assertIn('CLOCK_UNCERTAINTY_APPLIED', report.codes)
        self.assertEqual(candles, ())
        normal = bars('AAA', delay=2)                                         # 2 s lag is exactly the bound: trusted
        self.assertTrue(self.rep(normal, clock_uncertainty=timedelta(seconds=2))[0].ready)
        near = [row('AAA', i, delay=1) for i in range(20)]
        self.assertFalse(self.rep(near, clock_uncertainty=timedelta(seconds=2))[0].ready)
        for bad in (timedelta(seconds=-1), timedelta(hours=2), 2, None):
            with self.assertRaises(ValueError):
                self.rep(bars('AAA'), clock_uncertainty=bad)

    def test_clock_uncertainty_flows_through_build_market_inputs_and_scout_stays_conservative(self):
        data = [row('AAA', i, delay=1) for i in range(20)]
        inputs = build_market_inputs(snapshot(), data, as_of=AS_OF, config=RESEARCH, allow_approximate_vwap=True,
                                     clock_uncertainty=timedelta(seconds=5))
        self.assertFalse(inputs.readiness[0].ready)
        self.assertEqual(inputs.candles, ())
        plain = build_market_inputs(snapshot(), data, as_of=AS_OF, config=RESEARCH, allow_approximate_vwap=True)
        self.assertTrue(plain.readiness[0].ready)


class PurityTests(unittest.TestCase):
    def test_module_imports_only_pure_dependencies(self):
        tree = ast.parse(Path(market.__file__).read_text(encoding='utf-8'))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split('.')[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add(('.' * node.level) + (node.module or ''))
        allowed = {'__future__', 'collections', 'dataclasses', 'datetime', 'decimal', 'typing',
                   '.day01_scout', '.ml.day01', '.ml.day01_research'}
        self.assertEqual(names - allowed, set())


if __name__ == '__main__':
    unittest.main()
