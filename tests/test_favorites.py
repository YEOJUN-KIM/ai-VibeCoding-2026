import unittest
from unittest.mock import patch
from uuid import uuid4

from psycopg import sql

from auto_trader import auth, database, favorites


class FavoriteTests(unittest.TestCase):
    def setUp(self):
        self.schema = "test_favorites_" + uuid4().hex
        self.original_connect = database.connect
        with self.original_connect() as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.cleanup_schema)

        def isolated():
            conn = self.original_connect()
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
            return conn

        self.connect = isolated
        for module in (auth, database, favorites):
            patcher = patch.object(module, "connect", isolated)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.initialize()
        auth.create_admin("favoriteadmin", "test-only-password-2026")
        with self.connect() as conn:
            self.user_id = conn.execute("SELECT id FROM admin_users").fetchone()["id"]

    def cleanup_schema(self):
        with self.original_connect() as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    @staticmethod
    def stock(symbol="005930", name="삼성전자"):
        return {
            "symbol": symbol,
            "name": name,
            "market": "KOSPI",
            "securityType": "STOCK",
            "isCommonShare": True,
        }

    def test_add_duplicate_list_and_remove(self):
        first = favorites.add_favorite(self.user_id, self.stock())
        duplicate = favorites.add_favorite(self.user_id, self.stock())
        self.assertEqual(first["symbol"], "005930")
        self.assertEqual(duplicate["symbol"], "005930")
        self.assertEqual(favorites.favorite_symbols(self.user_id), {"005930"})
        self.assertEqual(len(favorites.list_favorites(self.user_id)), 1)
        self.assertTrue(favorites.remove_favorite(self.user_id, "005930"))
        self.assertFalse(favorites.remove_favorite(self.user_id, "005930"))
        self.assertEqual(favorites.list_favorites(self.user_id), [])

    def test_limit_is_enforced(self):
        with patch.object(favorites, "MAX_FAVORITES", 2):
            favorites.add_favorite(self.user_id, self.stock("000001", "첫 종목"))
            favorites.add_favorite(self.user_id, self.stock("000002", "둘째 종목"))
            with self.assertRaisesRegex(ValueError, "최대 2개"):
                favorites.add_favorite(self.user_id, self.stock("000003", "셋째 종목"))
