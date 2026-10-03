import unittest
import asyncio
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from uuid import uuid4

from psycopg import sql

from auto_trader import database, decision_history
from auto_trader.ml import decision_pipeline
from auto_trader import main
from tests.test_ml_decision_pipeline import decision


class DecisionHistoryTests(unittest.TestCase):
    def setUp(self):
        self.schema = 'test_decision_history_' + uuid4().hex
        self.original = database.connect
        with self.original() as conn:
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.addCleanup(self.cleanup)
        def isolated():
            conn = self.original()
            conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
            return conn
        for module in (decision_history, decision_pipeline):
            patcher = patch.object(module, 'connect', isolated)
            patcher.start()
            self.addCleanup(patcher.stop)
        with isolated() as conn:
            conn.execute("CREATE TABLE accounts(id BIGINT PRIMARY KEY,user_id BIGINT,mode TEXT,name TEXT)")
            conn.execute("CREATE TABLE stocks(symbol TEXT PRIMARY KEY,name TEXT)")
            ddl = Path('auto_trader/schema.sql').read_text(encoding='utf-8')
            ddl = ddl.split('CREATE TABLE IF NOT EXISTS ml_strategy_decisions (')[1].split('ALTER TABLE ml_strategy_decisions')[0]
            conn.execute('CREATE TABLE ml_strategy_decisions (' + ddl)
            conn.execute("INSERT INTO accounts VALUES (1,11,'PAPER','paper-default'),(2,11,'PAPER','paper-experiment'),(3,22,'PAPER','user-22-paper-experiment')")
            conn.execute("INSERT INTO stocks VALUES ('005930','삼성전자')")

    def cleanup(self):
        with self.original() as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def test_ownership_and_filters(self):
        decision_pipeline.save_strategy_decisions([
            decision(account_id=1), decision(account_id=2,action='BUY_BLOCKED'),
            decision(account_id=3,reason='private'), decision(account_id=None,reason='unowned'),
        ])
        result=decision_history.read_decision_history(11)
        self.assertEqual(result['total'],2)
        self.assertEqual({r['account_label'] for r in result['items']},{'기존 모의계좌','실험계좌'})
        self.assertEqual(len(result['accounts']),2)
        self.assertEqual(decision_history.read_decision_history(11,account_id=3)['total'],0)
        self.assertEqual(decision_history.read_decision_history(11,account_id=2,action='BUY_BLOCKED',symbol='삼성',strategy_id=2)['total'],1)
        self.assertEqual(decision_history.read_decision_history(11,symbol='%')['total'],0)

    def test_korean_dates_include_end_day_and_exclude_next_midnight(self):
        kst=timezone(timedelta(hours=9))
        times=[datetime(2026,10,2,0,0,tzinfo=kst),datetime(2026,10,2,23,59,59,tzinfo=kst),datetime(2026,10,3,0,0,tzinfo=kst)]
        decision_pipeline.save_strategy_decisions([decision(decision_at=t,available_at=t,candle_event_at=None) for t in times])
        result=decision_history.read_decision_history(11,start=date(2026,10,2),end=date(2026,10,2))
        self.assertEqual(result['total'],2)
        self.assertEqual(result['items'][0]['decision_at'],times[1])

    def test_pagination_does_not_shift_after_new_record(self):
        decision_pipeline.save_strategy_decisions([decision(reason=f'row-{i}') for i in range(21)])
        first=decision_history.read_decision_history(11)
        decision_pipeline.save_strategy_decisions([decision(reason='new')])
        last=decision_history.read_decision_history(11,page=2,before_id=first['before_id'])
        self.assertEqual(last['total'],21)
        self.assertEqual(len(last['items']),1)
        self.assertTrue(set(r['id'] for r in first['items']).isdisjoint(r['id'] for r in last['items']))
        self.assertEqual(decision_history.read_decision_history(11)['total'],22)
        self.assertEqual(decision_history.read_decision_history(11,page=99)['page'],2)

    def test_invalid_filters_and_empty_owner(self):
        for kwargs in ({'start':date(2026,10,3),'end':date(2026,10,2)},{'action':'oops'},{'page_size':30},{'end':date.max}):
            with self.assertRaises(ValueError):
                decision_history.read_decision_history(11,**kwargs)
        self.assertEqual(decision_history.read_decision_history(99)['items'],[])


class DecisionHistoryEndpointTests(unittest.TestCase):
    def request(self,path):
        async def run():
            messages=[]
            async def receive():
                await asyncio.Event().wait()
            async def send(message):
                messages.append(message)
            route,_,query=path.partition('?')
            scope={'type':'http','asgi':{'version':'3.0','spec_version':'2.4'},'http_version':'1.1',
                   'method':'GET','scheme':'http','path':route,'raw_path':route.encode(),
                   'query_string':query.encode(),'headers':[(b'host',b'localhost')],
                   'server':('localhost',80),'client':('127.0.0.1',1234),'root_path':''}
            await main.app(scope,receive,send)
            return next(m['status'] for m in messages if m['type']=='http.response.start')
        return asyncio.run(run())

    def test_login_required_and_invalid_inputs(self):
        self.assertEqual(self.request('/paper/decisions/history'),401)
        main.app.dependency_overrides[main.require_user]=lambda:SimpleNamespace(id=11)
        self.addCleanup(main.app.dependency_overrides.pop,main.require_user)
        for query in ('page=0','page_size=101','start=oops','account_id=-1','action=UNKNOWN','start=2026-10-03&end=2026-10-02'):
            self.assertEqual(self.request('/paper/decisions/history?'+query),422)

    def test_user_and_filters_are_forwarded(self):
        main.app.dependency_overrides[main.require_user]=lambda:SimpleNamespace(id=11)
        self.addCleanup(main.app.dependency_overrides.pop,main.require_user)
        with patch.object(main,'read_decision_history',return_value={'total':0,'items':[]}) as read:
            response=self.request('/paper/decisions/history?account_id=2&symbol=005930&start=2026-10-02&page_size=50&before_id=42')
            self.assertEqual(response,200)
            self.assertEqual(read.call_args.args,(11,))
            self.assertEqual(read.call_args.kwargs['account_id'],2)
            self.assertEqual(read.call_args.kwargs['start'],date(2026,10,2))
            self.assertEqual(read.call_args.kwargs['before_id'],42)


if __name__=='__main__':
    unittest.main()
