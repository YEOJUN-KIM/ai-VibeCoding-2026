import unittest
from datetime import datetime, timezone
from decimal import Decimal

from auto_trader.long_term import (
    analyze_long_term, long_term_rank, long_term_recommendation_assessment, next_daily_scan_at,
)
from auto_trader.models import (
    CompanyFinancialYear, CompanyMetric, LiveCompanyProfile, LiveStockCandle, LiveStockDetail,
)


class LongTermAnalysisTests(unittest.TestCase):
    def test_score_rank_boundaries(self):
        self.assertEqual(long_term_rank(90), "S")
        self.assertEqual(long_term_rank(89), "A")
        self.assertEqual(long_term_rank(80), "A")
        self.assertEqual(long_term_rank(70), "B")
        self.assertEqual(long_term_rank(60), "C")
        self.assertEqual(long_term_rank(59), "D")
        self.assertIsNone(long_term_rank(None))

    def test_next_daily_scan_uses_korean_time(self):
        before = datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc)  # 한국시간 03:00
        after = datetime(2026, 9, 30, 20, 0, tzinfo=timezone.utc)   # 한국시간 05:00
        self.assertEqual(next_daily_scan_at(before, 4, 30).isoformat(), "2026-10-01T04:30:00+09:00")
        self.assertEqual(next_daily_scan_at(after, 4, 30).isoformat(), "2026-10-02T04:30:00+09:00")

    def test_profitable_growing_company_gets_explainable_metrics(self):
        detail = LiveStockDetail(
            symbol="005930", name="삼성전자", market="KOSPI", security_type="STOCK",
            is_common_share=True, currency="KRW", price=Decimal("80000"),
            market_cap=Decimal("400000000000000"), candles=[
                LiveStockCandle(timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
                    open_price=Decimal("60000"), high_price=Decimal("61000"),
                    low_price=Decimal("59000"), close_price=Decimal("60000"), volume=Decimal("1")),
                LiveStockCandle(timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    open_price=Decimal("80000"), high_price=Decimal("81000"),
                    low_price=Decimal("79000"), close_price=Decimal("80000"), volume=Decimal("1")),
            ],
        )
        profile = LiveCompanyProfile(
            configured=True, available=True, message="사업보고서 기준", fiscal_year="2025",
            industry_name="반도체 제조업", financials=[
                CompanyMetric(label="부채총계", value="50000000000000"),
                CompanyMetric(label="자본총계", value="350000000000000"),
            ], financial_history=[
                CompanyFinancialYear(year="2023", revenue=Decimal("200000000000000"),
                    operating_income=Decimal("20000000000000"), net_income=Decimal("18000000000000")),
                CompanyFinancialYear(year="2025", revenue=Decimal("260000000000000"),
                    operating_income=Decimal("39000000000000"), net_income=Decimal("32000000000000")),
            ], dividends=[CompanyMetric(label="현금배당수익률(%)", value="2.5")],
        )

        result = analyze_long_term(detail, profile)

        self.assertEqual(result.revenue_growth_percent, Decimal("30.0"))
        self.assertEqual(result.operating_margin_percent, Decimal("15.00"))
        self.assertEqual(result.per, Decimal("12.5"))
        self.assertGreaterEqual(result.overall_score, 70)
        self.assertIn(result.rank, {"S", "A", "B"})
        self.assertEqual(len(result.factors), 5)
        self.assertEqual(result.price_return_1y_percent.quantize(Decimal("0.1")), Decimal("33.3"))
        recommended, reasons = long_term_recommendation_assessment(result)
        self.assertTrue(recommended)
        self.assertEqual(reasons, [])

        financial = result.model_copy(update={"industry_name": "기타 금융업"})
        recommended, reasons = long_term_recommendation_assessment(financial)
        self.assertFalse(recommended)
        self.assertTrue(any("금융·지주" in reason for reason in reasons))

        high_drawdown = result.model_copy(update={"max_drawdown_1y_percent": Decimal("-51")})
        recommended, reasons = long_term_recommendation_assessment(high_drawdown)
        self.assertFalse(recommended)
        self.assertTrue(any("최대 낙폭" in reason for reason in reasons))

    def test_missing_dart_data_does_not_become_zero_score(self):
        detail = LiveStockDetail(
            symbol="005930", name="삼성전자", market="KOSPI", security_type="STOCK",
            is_common_share=True, currency="KRW", candles=[],
        )
        profile = LiveCompanyProfile(configured=False, available=False, message="OpenDART 미설정")
        result = analyze_long_term(detail, profile)
        self.assertIsNone(result.overall_score)
        self.assertIsNone(result.rank)
        self.assertEqual(result.grade, "데이터 부족")
        self.assertTrue(all(item.score is None for item in result.factors))
        recommended, reasons = long_term_recommendation_assessment(result)
        self.assertFalse(recommended)
        self.assertGreaterEqual(len(reasons), 3)


if __name__ == "__main__":
    unittest.main()
