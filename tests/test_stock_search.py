import unittest
from unittest.mock import patch
from auto_trader.stock_search import normalize, stock_match_rank
from auto_trader.toss import TossClient

STOCKS = [
    {"symbol":"005930","name":"삼성전자","market":"KOSPI","securityType":"STOCK","isCommonShare":True},
    {"symbol":"005935","name":"삼성전자우","market":"KOSPI","securityType":"STOCK","isCommonShare":False},
    {"symbol":"009150","name":"삼성전기","market":"KOSPI","securityType":"STOCK","isCommonShare":True},
    {"symbol":"000660","name":"SK하이닉스","market":"KOSPI","securityType":"STOCK","isCommonShare":True},
    {"symbol":"035420","name":"NAVER","market":"KOSPI","securityType":"STOCK","isCommonShare":True},
    {"symbol":"069500","name":"KODEX 200","market":"KOSPI","securityType":"ETF","isCommonShare":False},
    {"symbol":"247540","name":"에코프로비엠","market":"KOSDAQ","securityType":"STOCK","isCommonShare":True},
]

class StockSearchTests(unittest.TestCase):
    def search(self, query, **options):
        client=TossClient(client_id="id",client_secret="secret")
        # Make Samsung Electric rank first, so exact-name priority is exercised.
        client._domestic_rankings_cache=(None,[{"symbol":"009150","rank":1}])
        with patch.object(client,"_domestic_stock_universe",return_value=STOCKS), \
             patch.object(client,"_authorized_json_request") as external:
            page=client.list_domestic_stocks(query=query,include_quotes=False,**options)
        external.assert_not_called()
        return page

    def test_requested_examples(self):
        for query in ("삼전", "ㅅㅅㅈㅈ", "tt", "tkatjdwjswk", "Samsung", "SAM SUNG"):
            with self.subTest(query=query):
                self.assertIn("005930",[row.symbol for row in self.search(query).results])

    def test_aliases_and_full_names_rank_before_partial_candidates(self):
        for query in ("삼전", "삼성전자", "Samsung", "005930"):
            with self.subTest(query=query):
                self.assertEqual(self.search(query).results[0].symbol,"005930")
        self.assertEqual([row.symbol for row in self.search("삼전").results],["005930"])

    def test_short_initials_keep_multiple_candidates(self):
        for query in ("ㅅㅅ", "tt", "ᄉᄉ"):
            self.assertGreaterEqual(self.search(query).total,3)

    def test_keyboard_name_input_and_initial_input(self):
        self.assertIn("000660",[row.symbol for row in self.search("skgkdlslrtm").results])
        self.assertIn("005930",[row.symbol for row in self.search("ttww").results])
        self.assertIn("247540",[row.symbol for row in self.search("ㅇㅋㅍㄹㅂㅇ").results])

    def test_english_names_spaces_case_and_korean_reading(self):
        self.assertEqual(self.search("hynix").results[0].symbol,"000660")
        self.assertEqual(self.search("SK HYNIX").results[0].symbol,"000660")
        self.assertEqual(self.search("네이버").results[0].symbol,"035420")
        self.assertEqual(self.search("naver").results[0].symbol,"035420")
        self.assertEqual(self.search("ＫＯＤＥＸ２００").results[0].symbol,"069500")

    def test_market_security_type_and_paging_remain_applied(self):
        page=self.search("ㅅㅅ",security_type="COMMON",page_size=1,page=2)
        self.assertEqual(page.total,2)
        self.assertEqual(page.page,2)
        self.assertTrue(page.results[0].is_common_share)
        self.assertEqual(self.search("삼전",security_type="ETF").total,0)
        self.assertEqual(self.search("삼전",market="KOSDAQ").total,0)

    def test_unmatched_and_empty_query(self):
        self.assertEqual(self.search("없는별명").total,0)
        self.assertEqual(self.search("@@@").total,0)
        self.assertEqual(self.search("").total,len(STOCKS))
        self.assertEqual(self.search("0059").total,2)

    def test_no_alias_inheritance_to_similar_company(self):
        self.assertIsNone(stock_match_rank(STOCKS[2],normalize("삼전")))
        self.assertIsNone(stock_match_rank(STOCKS[1],normalize("삼전")))

if __name__ == "__main__":
    unittest.main()
