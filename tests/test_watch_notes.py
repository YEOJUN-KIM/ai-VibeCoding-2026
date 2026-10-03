import unittest
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch
from psycopg import sql
from pydantic import ValidationError
from auto_trader import database, long_term_repository as repository
from auto_trader.models import LongTermWatchNoteWrite


class WatchNotesTests(unittest.TestCase):
    def setUp(self):
        self.schema = 'test_watch_notes_' + uuid4().hex
        self.original = database.connect
        with self.original() as conn:
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.addCleanup(self.cleanup)
        def isolated():
            conn = self.original()
            conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
            return conn
        self.isolated = isolated
        patcher = patch.object(repository, 'connect', isolated)
        patcher.start(); self.addCleanup(patcher.stop)
        with isolated() as conn:
            conn.execute('CREATE TABLE admin_users(id BIGINT PRIMARY KEY)')
            conn.execute('INSERT INTO admin_users VALUES (11),(22)')
            ddl = Path('auto_trader/schema.sql').read_text(encoding='utf-8')
            for table in ('long_term_watchlist', 'long_term_watch_notes', 'long_term_watch_exclusions'):
                definition = ddl.split(f'CREATE TABLE IF NOT EXISTS {table} (')[1].split(');')[0]
                conn.execute(f'CREATE TABLE {table} (' + definition + ');')
            conn.execute("CREATE TABLE favorite_stocks(user_id BIGINT,symbol TEXT,name TEXT,market TEXT,security_type TEXT,is_common_share BOOLEAN,created_at TIMESTAMPTZ DEFAULT now())")
            conn.execute('CREATE TABLE long_term_analyses(symbol TEXT,analysis JSONB,overall_score NUMERIC)')
        repository.add_long_term_watch(11,'005930','삼성전자','KOSPI')
        repository.add_long_term_watch(22,'005930','삼성전자','KOSPI')

    def cleanup(self):
        with self.original() as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def test_saved_notes_are_private_and_survive_refresh(self):
        self.assertTrue(repository.save_long_term_watch_note(11,'005930','다음 실적 확인\n<메모>'))
        repository.add_long_term_watch(11,'005930','삼성전자','KOSPI')
        mine=repository.list_long_term_watch(11)[0]
        self.assertEqual(mine.note,'다음 실적 확인\n<메모>')
        self.assertIsNotNone(mine.note_updated_at)
        self.assertEqual(repository.list_long_term_watch(22)[0].note,'')
        self.assertTrue(repository.save_long_term_watch_note(11,'005930',''))
        self.assertEqual(repository.list_long_term_watch(11)[0].note,'')

    def test_only_owned_common_stock_candidates_can_be_noted(self):
        self.assertFalse(repository.save_long_term_watch_note(22,'000660','not owned'))
        with self.isolated() as conn:
            conn.execute("INSERT INTO favorite_stocks(user_id,symbol,name,market,security_type,is_common_share) VALUES (11,'000660','SK하이닉스','KOSPI','STOCK',true),(11,'069500','ETF','KOSPI','ETF',false)")
        self.assertTrue(repository.save_long_term_watch_note(11,'000660','관심종목 메모'))
        self.assertFalse(repository.save_long_term_watch_note(11,'069500','ETF'))
        items=repository.list_long_term_watch(11)
        self.assertEqual(next(item.note for item in items if item.symbol=='000660'),'관심종목 메모')
        self.assertFalse(repository.save_long_term_watch_note(22,'000660','other user'))

    def test_note_length_is_validated(self):
        self.assertEqual(len(LongTermWatchNoteWrite(note='가'*2000).note),2000)
        with self.assertRaises(ValidationError): LongTermWatchNoteWrite(note='가'*2001)

    def test_unsaving_hides_favorites_without_deleting_them_or_notes(self):
        with self.isolated() as conn:
            conn.execute("INSERT INTO favorite_stocks(user_id,symbol,name,market,security_type,is_common_share) VALUES (11,'005930','삼성전자','KOSPI','STOCK',true)")
        repository.save_long_term_watch_note(11,'005930','유지할 메모')
        repository.remove_long_term_watch(11,'005930')
        self.assertEqual(repository.list_long_term_watch(11),[])
        self.assertFalse(repository.save_long_term_watch_note(11,'005930','hidden'))
        self.assertEqual(len(repository.list_long_term_watch(22)),1)
        with self.isolated() as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM favorite_stocks').fetchone()['n'],1)
        repository.add_long_term_watch(11,'005930','삼성전자','KOSPI')
        self.assertEqual(repository.list_long_term_watch(11)[0].note,'유지할 메모')


if __name__=='__main__': unittest.main()
