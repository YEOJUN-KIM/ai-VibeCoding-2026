import unittest
from decimal import Decimal

from auto_trader.dart import DartClient
from auto_trader.industries import industry_name


class DartClientTests(unittest.TestCase):
    def test_financial_metrics_include_year_over_year_change(self):
        metrics = DartClient._metrics([
            {"account_nm": "수익(매출액)", "thstrm_amount": "333,605,938,000,000",
             "frmtrm_amount": "300,870,903,000,000"},
            {"account_nm": "영업이익", "thstrm_amount": "43,601,051,000,000",
             "frmtrm_amount": "32,725,961,000,000"},
        ], ("매출액", "영업이익"))

        self.assertEqual(len(metrics), 2)
        self.assertEqual(metrics[0].value, "333,605,938,000,000")
        self.assertEqual(metrics[0].change_rate_percent.quantize(Decimal("0.1")), Decimal("10.9"))
        self.assertEqual(metrics[1].change_rate_percent.quantize(Decimal("0.1")), Decimal("33.2"))

    def test_industry_code_is_named_and_unknown_code_is_preserved(self):
        self.assertEqual(industry_name("261"), "반도체 제조업")
        self.assertEqual(industry_name("264"), "통신 및 방송 장비 제조업")
        self.assertEqual(industry_name("99999"), "산업분류 99999")


if __name__ == "__main__":
    unittest.main()
