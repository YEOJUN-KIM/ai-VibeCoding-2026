"""국내 종목의 장기 관찰용 재무·가치 지표를 계산한다."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from .models import LiveCompanyProfile, LiveStockDetail, LongTermAnalysis, LongTermFactor


MIN_RECOMMENDATION_SCORE = 70
_SPECIALIZED_INDUSTRY_TERMS = (
    "금융", "은행", "보험", "증권", "신탁", "투자회사", "지주회사",
)


def _number(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value).replace(",", "").replace("%", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _metric(profile: LiveCompanyProfile, label: str) -> Decimal | None:
    row = next((item for item in profile.financials if item.label == label), None)
    return _number(row.value) if row else None


def _dividend_yield(profile: LiveCompanyProfile) -> Decimal | None:
    row = next((item for item in profile.dividends if "배당수익률" in item.label), None)
    return _number(row.value) if row else None


def _factor(key: str, label: str, score: int | None, maximum: int,
            value: str, detail: str) -> LongTermFactor:
    if score is None:
        status = "데이터 부족"
    elif score >= maximum * 0.75:
        status = "양호"
    elif score >= maximum * 0.45:
        status = "보통"
    else:
        status = "주의"
    return LongTermFactor(key=key, label=label, score=score, max_score=maximum,
                          status=status, value=value, detail=detail)


def long_term_rank(score: int | None) -> str | None:
    if score is None:
        return None
    if score >= 90:
        return "S"
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    return "D"


def next_daily_scan_at(now: datetime, hour: int, minute: int) -> datetime:
    """한국시간 기준 다음 일일 분석 시각을 반환한다."""
    kst = timezone(timedelta(hours=9))
    current = now.astimezone(kst)
    target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return target if target > current else target + timedelta(days=1)


def long_term_recommendation_assessment(analysis: LongTermAnalysis) -> tuple[bool, list[str]]:
    """일반 기업용 점수로 자동 추천해도 되는지와 제외 이유를 반환한다."""
    reasons: list[str] = []
    industry = (analysis.industry_name or "").replace(" ", "")
    if any(term.replace(" ", "") in industry for term in _SPECIALIZED_INDUSTRY_TERMS):
        reasons.append("업종별 전용 평가가 필요한 금융·지주 계열입니다.")
    if analysis.overall_score is None or analysis.overall_score < MIN_RECOMMENDATION_SCORE:
        reasons.append(f"자동 추천 기준인 {MIN_RECOMMENDATION_SCORE}점에 미달합니다.")
    scored_factors = sum(factor.score is not None for factor in analysis.factors)
    if scored_factors < 4:
        reasons.append("평가 가능한 핵심 항목이 4개보다 적습니다.")
    if len(analysis.financial_history) < 2:
        reasons.append("연간 실적 추세를 비교할 데이터가 부족합니다.")
    if analysis.operating_margin_percent is None or analysis.operating_margin_percent <= 0:
        reasons.append("최근 영업이익이 흑자로 확인되지 않습니다.")
    if analysis.debt_ratio_percent is None:
        reasons.append("재무안정성 확인에 필요한 부채비율이 없습니다.")
    if analysis.max_drawdown_1y_percent is not None and analysis.max_drawdown_1y_percent <= Decimal("-45"):
        reasons.append("최근 1년 최대 낙폭이 -45% 이하입니다.")
    return not reasons, reasons


def analyze_long_term(detail: LiveStockDetail, profile: LiveCompanyProfile) -> LongTermAnalysis:
    history = sorted(profile.financial_history, key=lambda item: item.year)
    latest = history[-1] if history else None
    earliest = history[0] if len(history) >= 2 else None
    revenue_growth = None
    if latest and earliest and latest.revenue is not None and earliest.revenue and earliest.revenue > 0:
        revenue_growth = (latest.revenue / earliest.revenue - Decimal("1")) * Decimal("100")

    revenue = latest.revenue if latest else _metric(profile, "매출액")
    operating_income = latest.operating_income if latest else _metric(profile, "영업이익")
    net_income = latest.net_income if latest else _metric(profile, "당기순이익")
    equity = _metric(profile, "자본총계")
    liabilities = _metric(profile, "부채총계")
    operating_margin = (operating_income / revenue * 100
                        if operating_income is not None and revenue and revenue > 0 else None)
    debt_ratio = (liabilities / equity * 100
                  if liabilities is not None and equity and equity > 0 else None)
    per = (detail.market_cap / net_income
           if detail.market_cap is not None and net_income and net_income > 0 else None)
    pbr = (detail.market_cap / equity
           if detail.market_cap is not None and equity and equity > 0 else None)
    dividend_yield = _dividend_yield(profile)

    closes = [item.close_price for item in detail.candles if item.close_price > 0]
    price_return = ((closes[-1] / closes[0] - 1) * 100 if len(closes) >= 2 else None)
    max_drawdown = None
    if closes:
        peak = closes[0]
        drawdowns = []
        for close in closes:
            peak = max(peak, close)
            drawdowns.append((close / peak - 1) * 100)
        max_drawdown = min(drawdowns)

    growth_score = None
    if revenue_growth is not None:
        growth_score = 25 if revenue_growth >= 20 else 20 if revenue_growth >= 5 else 14 if revenue_growth >= 0 else 5
    growth = _factor("growth", "성장성", growth_score, 25,
                     f"{revenue_growth:+.1f}%" if revenue_growth is not None else "확인 불가",
                     "최근 확인 가능한 연간 매출의 최초 연도 대비 변화율입니다.")

    profitability_score = None
    if operating_margin is not None:
        margin_score = 12 if operating_margin >= 15 else 9 if operating_margin >= 5 else 6 if operating_margin > 0 else 0
        net_score = 8 if net_income is not None and net_income > 0 else 0
        consistency = 5 if len(history) >= 2 and all(
            item.operating_income is not None and item.operating_income > 0 for item in history
        ) else 0
        profitability_score = margin_score + net_score + consistency
    profitability = _factor("profitability", "수익성", profitability_score, 25,
                            f"영업이익률 {operating_margin:.1f}%" if operating_margin is not None else "확인 불가",
                            "영업이익률, 순이익 흑자 여부와 최근 연도의 이익 지속성을 함께 봅니다.")

    stability_score = None
    if debt_ratio is not None:
        stability_score = 20 if debt_ratio <= 50 else 16 if debt_ratio <= 100 else 10 if debt_ratio <= 200 else 4
    stability = _factor("stability", "재무안정성", stability_score, 20,
                        f"부채비율 {debt_ratio:.1f}%" if debt_ratio is not None else "확인 불가",
                        "최근 사업보고서의 부채총계 ÷ 자본총계입니다.")

    valuation_score = None
    if per is not None or pbr is not None:
        per_score = (10 if per is not None and per <= 10 else 8 if per is not None and per <= 20
                     else 5 if per is not None and per <= 35 else 2 if per is not None else 0)
        pbr_score = (10 if pbr is not None and pbr <= 1 else 8 if pbr is not None and pbr <= 2
                     else 5 if pbr is not None and pbr <= 4 else 2 if pbr is not None else 0)
        available_max = (10 if per is not None else 0) + (10 if pbr is not None else 0)
        valuation_score = round((per_score + pbr_score) / available_max * 20) if available_max else None
    valuation_text = " · ".join(filter(None, [
        f"PER {per:.1f}배" if per is not None else None,
        f"PBR {pbr:.1f}배" if pbr is not None else None,
    ])) or "확인 불가"
    valuation = _factor("valuation", "가치지표", valuation_score, 20, valuation_text,
                        "현재 시가총액과 최근 연간 순이익·자본으로 단순 계산한 참고치입니다.")

    dividend_score = None
    if profile.available:
        dividend_score = (10 if dividend_yield is not None and dividend_yield >= 3 else
                          7 if dividend_yield is not None and dividend_yield >= 1 else
                          4 if dividend_yield is not None and dividend_yield > 0 else 0)
    dividend = _factor("dividend", "배당", dividend_score, 10,
                       f"배당수익률 {dividend_yield:.2f}%" if dividend_yield is not None else "배당 확인 불가",
                       "최근 사업보고서의 보통주 현금배당수익률입니다.")

    factors = [growth, profitability, stability, valuation, dividend]
    scored = [item for item in factors if item.score is not None]
    overall = (round(sum(item.score for item in scored) / sum(item.max_score for item in scored) * 100)
               if scored else None)
    rank = long_term_rank(overall)
    grade = ({"S": "최상위 관찰", "A": "관찰 매력 높음", "B": "균형 관찰",
              "C": "선별 확인", "D": "주의 확인"}.get(rank, "데이터 부족"))

    opportunities = []
    risks = []
    if revenue_growth is not None:
        (opportunities if revenue_growth > 5 else risks).append(
            f"매출 변화율이 {revenue_growth:+.1f}%입니다."
        )
    if operating_margin is not None:
        (opportunities if operating_margin >= 5 else risks).append(
            f"최근 영업이익률이 {operating_margin:.1f}%입니다."
        )
    if debt_ratio is not None:
        (opportunities if debt_ratio <= 100 else risks).append(
            f"부채비율이 {debt_ratio:.1f}%입니다."
        )
    if max_drawdown is not None and max_drawdown <= -20:
        risks.append(f"최근 1년 관측 최대 낙폭이 {max_drawdown:.1f}%입니다.")
    if dividend_yield is not None and dividend_yield > 0:
        opportunities.append(f"최근 현금배당수익률은 {dividend_yield:.2f}%입니다.")
    if not profile.available:
        risks.append(profile.message)

    summary = (f"확인 가능한 {len(scored)}개 항목을 기준으로 {grade} 단계입니다. "
               "점수는 종목 간 단순 비교를 돕는 관찰 지표이며 매수 추천이 아닙니다.")
    return LongTermAnalysis(
        symbol=detail.symbol, name=detail.name, market=detail.market, price=detail.price,
        market_cap=detail.market_cap, industry_name=profile.industry_name,
        fiscal_year=profile.fiscal_year, overall_score=overall, rank=rank, grade=grade, summary=summary,
        factors=factors, opportunities=opportunities or ["추가로 확인된 긍정 요인이 없습니다."],
        risks=risks or ["추가로 확인된 정량 위험 신호가 없습니다."],
        financial_history=history, per=per, pbr=pbr, revenue_growth_percent=revenue_growth,
        operating_margin_percent=operating_margin, debt_ratio_percent=debt_ratio,
        dividend_yield_percent=dividend_yield, price_return_1y_percent=price_return,
        max_drawdown_1y_percent=max_drawdown, candles=detail.candles,
        data_message=profile.message, generated_at=datetime.now(timezone.utc),
    )
