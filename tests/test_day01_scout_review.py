"""Independent synthetic adversarial review. No producer fixtures, settings or I/O hooks."""
import ast
import dataclasses as dc
import importlib.util
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

from auto_trader import day01_scout as s

T = datetime(2026, 11, 13, 1, 0, 20, tzinfo=timezone.utc)
SEC = timedelta(seconds=1)
ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT.parent / 'day01-claude-review-20261112'


def rows(n=2, price='100'):
    return [dict(rank=i, symbol=f'X{i:03}', tradingAmount='30000000000',
                 tradingVolume='30000', price=dict(lastPrice=price, changeRate='-1'))
            for i in range(1, n + 1)]


def snap(data=None, **kw):
    args = dict(received_at=T-SEC, snapshot_id='base')
    args.update(kw)
    return s.snapshot_from_rankings(T-2*SEC, rows() if data is None else data, **args)


def fixture(n=2):
    instruments = tuple(s.InstrumentFacts(f'X{i:03}', T, market='KOSDAQ', currency='KRW',
                        security_type='STOCK', is_common_share=True, status='ACTIVE',
                        krx_trading_suspended=False, liquidation_trading=False) for i in range(1,n+1))
    liquidity = tuple(s.LiquidityFacts(f'X{i:03}', T, '30000000000', '30000', '0.2') for i in range(1,n+1))
    dayone = tuple(s.DayOneFacts(f'X{i:03}', T, 15, True, True) for i in range(1,n+1))
    return dict(instruments=instruments, liquidity=liquidity, dayone=dayone,
                portfolio=s.Portfolio(T, '10000'),
                config=s.ScoutConfig(fee_rate=D(0), buy_slippage_rate=D(0)),
                risk_config=SimpleNamespace(max_order_amount=D(10000), max_symbol_amount=D(10000),
                                            max_total_investment=D(10000)))


def result(snapshots=None, as_of=T, **changes):
    args=fixture()
    args.update(changes)
    return s.select_watchlist(as_of, (snap(),) if snapshots is None else snapshots, **args)


def symbols(out):
    return tuple(c.symbol for c in out.candidates)


class CausalReview(unittest.TestCase):
    def test_future_only_portfolio_is_identical_to_no_portfolio(self):
        future=s.Portfolio(T+SEC,'10000',positions=(s.Position('FUTURE_SECRET',7),))
        self.assertEqual(result(portfolio=None), result(portfolio=future))

    def test_stale_known_holdings_survive_future_portfolio(self):
        known=s.Portfolio(T-100*SEC, '0', positions=(s.Position('KNOWN',1),))
        future=s.Portfolio(T+SEC,'10000',positions=(s.Position('FUTURE',9),))
        self.assertEqual(result(portfolio=known), result(portfolio=(future,known)))
        self.assertEqual(result(portfolio=known).held[0].symbol,'KNOWN')

    def test_future_fact_prefix_for_each_fact_kind(self):
        for kind in ('instruments','liquidity','dayone'):
            with self.subTest(kind=kind):
                facts=fixture()[kind]
                future=dc.replace(facts[0],observed_at=T+SEC)
                self.assertEqual(result(),result(**{kind:(future,)+facts}))

    def test_future_ranking_ignored_even_with_same_id_conflict(self):
        future=snap(rows(price='999'), received_at=T+SEC)
        self.assertEqual(result(),result((future,snap())))

    def test_latest_unknown_basis_blocks_older_valid(self):
        bad=s.snapshot_from_rankings(None,rows(),received_at=T,snapshot_id='bad')
        out=result((snap(),bad))
        self.assertEqual(out.status,s.UNKNOWN_PROVIDER_TIME)
        self.assertFalse(out.candidates)

    def test_latest_invalid_basis_blocks_older_valid(self):
        bad=s.snapshot_from_rankings(T+SEC,rows(),received_at=T,snapshot_id='bad')
        self.assertEqual(result((snap(),bad)).status,s.INVALID_PROVIDER_TIME)

    def test_each_fact_conflict_is_order_independent(self):
        fields={'instruments':dict(status='SUSPENDED'), 'liquidity':dict(volume=D(0)),
                'dayone':dict(vwap_ready=False)}
        for kind,change in fields.items():
            facts=fixture()[kind]; conflict=dc.replace(facts[0],**change)
            a=result(**{kind:facts+(conflict,)})
            self.assertEqual(a,result(**{kind:(conflict,)+facts}))
            self.assertNotIn('X001',symbols(a))

    def test_conflicting_portfolio_held_prices_are_order_independent(self):
        a=s.Portfolio(T,'10000',positions=(s.Position('OLD',1,price='100',price_observed_at=T),))
        b=s.Portfolio(T,'10000',positions=(s.Position('OLD',1,price='200',price_observed_at=T),))
        self.assertEqual(result(portfolio=(a,b)),result(portfolio=(b,a)))

    def test_age_boundaries_and_candidate_expiry(self):
        args=fixture()
        args['portfolio']=s.Portfolio(T-59*SEC,'10000')
        out=result(**args)
        self.assertEqual(out.candidates[0].entry_expires_at,T+SEC)
        self.assertFalse(result(as_of=T+SEC,**args).candidates)

    def test_no_day_rollover_revival(self):
        tomorrow=T+timedelta(days=1)
        self.assertEqual(result(as_of=tomorrow).status,s.DAY_ROLLOVER)


class RankAndQualityReview(unittest.TestCase):
    def test_original_rank_50_no_51_replacement(self):
        data=rows(51); args=fixture(51)
        args['instruments']=tuple(dc.replace(f,status='STOP') if f.symbol!='X050' else f for f in args['instruments'])
        out=result((snap(data),),**args)
        self.assertEqual(symbols(out),('X050',))
        self.assertEqual(out.candidates[0].rank,50)
        self.assertFalse(out.coverage.marketwide_complete)

    def test_partial_rank_is_reported_not_invented(self):
        data=rows(); data[1]['rank']=50
        out=result((snap(data),))
        self.assertEqual(out.coverage.present,2)
        self.assertIn(2,out.coverage.missing_ranks)
        self.assertEqual(out.candidates[1].rank,50)

    def test_conflicting_rank_or_symbol_excludes_every_involved_row(self):
        for field,value in [('rank',1),('symbol','X001')]:
            data=rows(); data[1][field]=value
            self.assertFalse(result((snap(data),)).candidates)

    def test_unknown_security_fields_each_fail_closed(self):
        for field in ('market','currency','security_type','is_common_share','status',
                      'krx_trading_suspended','liquidation_trading'):
            facts=fixture()['instruments']
            out=result(instruments=(dc.replace(facts[0],**{field:None}),facts[1]))
            self.assertNotIn('X001',symbols(out),field)

    def test_liquidity_requires_spread_volume_and_turnover_not_ranking_claim(self):
        for field in ('spread_pct','volume','turnover'):
            facts=fixture()['liquidity']
            out=result(liquidity=(dc.replace(facts[0],**{field:None}),facts[1]))
            self.assertNotIn('X001',symbols(out),field)

    def test_target_is_10_to_15_but_shortage_zero_valid(self):
        for bad in (9,16,True,D(12)):
            with self.assertRaises(ValueError): s.ScoutConfig(target_count=bad)
        self.assertEqual(len(result().candidates),2)
        self.assertEqual(result(instruments=()).candidates,())
        with self.assertRaises(ValueError): s.ScoutConfig(ranking_depth=51)

    def test_bad_price_never_affordable(self):
        for bad in (True,False,float('nan'),'NaN','Infinity','-1','0',None):
            with self.subTest(bad=bad):
                self.assertFalse(result((snap(rows(1,price=bad)),)).candidates)

    def test_bad_dates_do_not_fabricate_provider_time(self):
        for bad in ('nonsense','2026-11-13T01:00:00',None):
            out=result((s.snapshot_from_rankings(bad,rows(),received_at=T),))
            self.assertFalse(out.candidates)
            self.assertIsNone(out.snapshot.provider_ranked_at)

    def test_public_rankedrow_bool_rank_cannot_bypass_validation(self):
        try:
            bad=dc.replace(snap(),rows=(s.RankedRow(True,'X001',price=D(100)),))
            out=result((bad,))
        except ValueError:
            return
        self.assertFalse(out.candidates)

    def test_direct_snapshot_owns_immutable_rows(self):
        mutable=[s.RankedRow(1,'X001',price=D(100))]
        original=snap()
        try:
            direct=s.RankingSnapshot(original.snapshot_id,original.provider,original.source,
                                     original.received_at,original.provider_ranked_at,100,
                                     mutable,(),original.content_digest)
        except ValueError:
            return
        before=tuple(direct.rows)
        mutable.clear()
        self.assertEqual(tuple(direct.rows),before)

    def test_opt_out_never_claims_fully_eligible_even_with_present_facts(self):
        for flag in ('require_liquidity','require_dayone_readiness','require_capital_check'):
            cfg=dc.replace(fixture()['config'],**{flag:False})
            out=result(config=cfg)
            self.assertTrue(out.candidates)
            self.assertTrue(all(not c.fully_eligible for c in out.candidates),flag)


class CapitalAndMonitorReview(unittest.TestCase):
    def test_fee_slippage_full_cash_boundary(self):
        cfg=s.ScoutConfig(fee_rate=D('.01'),buy_slippage_rate=D('.01'))
        cost=D('102.01')
        self.assertEqual(len(result(config=cfg,portfolio=s.Portfolio(T,cost)).candidates),2)
        self.assertFalse(result(config=cfg,portfolio=s.Portfolio(T,cost-D('.01'))).candidates)

    def test_each_reserved_limit_is_enforced(self):
        changes=(dict(reserved_cash='9901'),dict(reserved_total_exposure='9901'),
                 dict(reserved_slots=1,max_positions=1),dict(daily_loss='10',daily_loss_limit='10'),
                 dict(pending_orders_known=False),dict(min_cash='9901'),
                 dict(min_cash_ratio_pct='100',total_asset='10000'))
        for change in changes:
            with self.subTest(change=change):
                self.assertFalse(result(portfolio=s.Portfolio(T,'10000',**change)).candidates)

    def test_per_symbol_reservation_only_blocks_that_symbol(self):
        out=result(portfolio=s.Portfolio(T,'10000',reserved_symbol_exposure=(('X001','9901'),),
                                        reserved_total_exposure='9901'),
                   risk_config=SimpleNamespace(max_order_amount=D(10000),max_symbol_amount=D(10000),
                                                max_total_investment=D(100000)))
        self.assertEqual(symbols(out),('X002',))

    def test_unknown_held_value_blocks_new_entries_but_keeps_monitor(self):
        for value in (None,'NaN','-1'):
            p=s.Position('OLD',1,market_value=value)
            out=result(portfolio=s.Portfolio(T,'10000',positions=(p,)))
            self.assertFalse(out.candidates)
            self.assertEqual(out.held[0].symbol,'OLD')
            self.assertTrue(out.held[0].monitor_only)

    def test_invalid_risk_limit_values_fail_closed(self):
        for field in ('max_order_amount','max_symbol_amount','max_total_investment'):
            for bad in (True,1.0,'NaN','Infinity','-1',None):
                limits=vars(fixture()['risk_config']).copy(); limits[field]=bad
                with self.subTest(field=field,bad=bad):
                    self.assertFalse(result(risk_config=SimpleNamespace(**limits)).candidates)

    def test_individual_affordability_does_not_imply_joint_orders(self):
        out=result(portfolio=s.Portfolio(T,'100'))
        self.assertEqual(len(out.candidates),2)
        self.assertTrue(all(not c.joint_orders_guaranteed and c.purpose=='WATCH_ONLY' for c in out.candidates))

    def test_held_monitor_survives_no_rank_no_budget_unknown_price(self):
        positions=tuple(s.Position(f'H{i:03}',1) for i in range(60))
        out=result((),portfolio=s.Portfolio(T-1000*SEC,'0',positions=positions))
        self.assertEqual(len(out.held),60)
        self.assertEqual(len(out.watch_union),60)
        self.assertFalse(out.candidates)


class IncrementalReview(unittest.TestCase):
    def test_raw_inputs_and_output_are_immutable_and_json_safe(self):
        data=rows(); snapshot=snap(data)
        data[0]['price']['lastPrice']='999'
        self.assertEqual(snapshot.rows[0].price,D(100))
        with self.assertRaises(dc.FrozenInstanceError): snapshot.provider='new'
        json.dumps(s.to_dict(result((snapshot,))))

    def test_shuffled_rows_and_fact_collections_identical(self):
        args=fixture()
        for k in ('instruments','liquidity','dayone'): args[k]=tuple(reversed(args[k]))
        self.assertEqual(result(),result((snap(list(reversed(rows()))),),**args))

    def test_repeat_refresh_idempotent_and_asof_regression(self):
        state,report=s.refresh(s.ScoutState.empty(),T,new_snapshots=(snap(),),**fixture())
        twice,again=s.refresh(state,T,new_snapshots=(snap(),),**fixture())
        self.assertEqual(state,twice)
        self.assertFalse(again.added)
        self.assertEqual(again.unchanged,report.added)
        unchanged,back=s.refresh(state,T-SEC,**fixture())
        self.assertIs(unchanged,state)
        self.assertEqual(back.status,'AS_OF_REGRESSION')

    def test_timeout_leaves_state_and_hides_exception_message(self):
        state=s.ScoutState.empty()
        def fail(): raise TimeoutError('PRIVATE_MESSAGE')
        after,out=s.ingest_fetch(state,fail,clock=lambda:T)
        self.assertIs(after,state)
        self.assertEqual(out.reason,'TimeoutError')
        self.assertNotIn('PRIVATE_MESSAGE',repr(out))

    def test_bare_fetch_payload_is_not_given_new_receipt(self):
        state=s.ScoutState.empty()
        after,out=s.ingest_fetch(state,lambda:(T,rows()),clock=lambda:T)
        self.assertIs(after,state)
        self.assertEqual(out.status,'REJECTED')

    def test_same_content_earlier_receipt_must_survive_reverse_arrival(self):
        late=snap(received_at=T,snapshot_id='same')
        early=snap(received_at=T-SEC,snapshot_id='same')
        state,_=s.refresh(s.ScoutState.empty(),T,new_snapshots=(late,early),**fixture())
        self.assertEqual(state.snapshots[0].received_at,T-SEC)

    def test_direct_selector_cached_content_preserves_earliest_receipt(self):
        late=snap(received_at=T,snapshot_id='cached')
        out=result((snap(),late))
        self.assertEqual(out.snapshot.received_at,T-SEC)

    def test_future_snapshot_must_not_evict_known_history(self):
        future=tuple(s.snapshot_from_rankings(T+i*SEC,rows(price=str(101+i)),
                     received_at=T+(i+1)*SEC,snapshot_id=f'future{i}') for i in range(8))
        _,baseline=s.refresh(s.ScoutState.empty(max_history=2),T,new_snapshots=(snap(),),**fixture())
        _,appended=s.refresh(s.ScoutState.empty(max_history=2),T,new_snapshots=(snap(),)+future,**fixture())
        self.assertEqual(baseline.result,appended.result)

    def test_refresh_conflicting_snapshot_batch_does_not_choose_first(self):
        a=snap(snapshot_id='collision'); b=snap(rows(price='999'),snapshot_id='collision')
        _,first=s.refresh(s.ScoutState.empty(),T,new_snapshots=(a,b),**fixture())
        _,second=s.refresh(s.ScoutState.empty(),T,new_snapshots=(b,a),**fixture())
        self.assertEqual(first.result,second.result)
        self.assertFalse(first.result.candidates)

    def test_different_ids_conflicting_same_provider_basis_fail_closed(self):
        a=snap(snapshot_id='first'); b=snap(rows(price='999'),snapshot_id='second')
        _,out=s.refresh(s.ScoutState.empty(),T,new_snapshots=(a,b),**fixture())
        self.assertFalse(out.result.candidates)


class CompatibilityReview(unittest.TestCase):
    def test_no_app_settings_db_broker_imports(self):
        tree=ast.parse((ROOT/'auto_trader/day01_scout.py').read_text(encoding='utf-8'))
        local=[n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom) and n.level]
        self.assertEqual(local,['risk_policy'])
        self.assertFalse(set(sys.modules)&{'auto_trader.database','auto_trader.settings','auto_trader.brokers'})

    @unittest.skipUnless((OLD/'auto_trader/ml/day01.py').is_file(),'preserved DAY01 worktree unavailable')
    def test_synthetic_watch_symbols_are_accepted_by_preserved_universe_contract(self):
        # Load only the audited pure source. Package imports use current inert package init.
        name='auto_trader.ml._scout_review_day01'
        spec=importlib.util.spec_from_file_location(name,OLD/'auto_trader/ml/day01.py')
        module=importlib.util.module_from_spec(spec)
        sys.modules[name]=module
        try:
            spec.loader.exec_module(module)
            exported=symbols(result())
            self.assertEqual(module.select_universe(exported),tuple(sorted(exported)))
            self.assertFalse(hasattr(result().candidates[0],'buy_signal'))
        finally:
            sys.modules.pop(name,None)


if __name__=='__main__': unittest.main()
