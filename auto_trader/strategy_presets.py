"""Editable PAPER starting points; selection rules are not return forecasts."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Lock
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from .database import connect
from .models import LiveStockSearchResult, LiveStrategyWrite


_snapshot_lock = Lock()
_SNAPSHOT_KEY = "starter-v1"
_KST = ZoneInfo("Asia/Seoul")


def preset_week(now=None):
    local = (now or datetime.now(timezone.utc)).astimezone(_KST)
    monday = local.date() - timedelta(days=local.weekday())
    return monday


def weekly_preset(kind, amount, fetch_candidates, *, fee_rate, slippage_rate, now=None):
    """Refresh the shared market snapshot on the week's first preview, not saved strategies."""
    week = preset_week(now)
    with _snapshot_lock:
        with connect() as conn:
            row = conn.execute(
                "SELECT week_start,candidates,generated_at FROM strategy_preset_market WHERE snapshot_key=%s",
                (_SNAPSHOT_KEY,),
            ).fetchone()
        if row is None or row['week_start'] != week:
            candidates = fetch_candidates()
            # Validate before replacing last week's usable snapshot.
            draft = build_preset(kind, amount, candidates, fee_rate=fee_rate, slippage_rate=slippage_rate)
            generated = now or datetime.now(timezone.utc)
            encoded = [{'stock': stock.model_dump(mode='json'), 'detail': detail} for stock, detail in candidates]
            with connect() as conn:
                conn.execute(
                    """INSERT INTO strategy_preset_market(snapshot_key,week_start,candidates,generated_at)
                       VALUES (%s,%s,%s,%s) ON CONFLICT(snapshot_key) DO UPDATE
                       SET week_start=excluded.week_start,candidates=excluded.candidates,generated_at=excluded.generated_at""",
                    (_SNAPSHOT_KEY, week, Jsonb(encoded), generated),
                )
        else:
            candidates = [(LiveStockSearchResult(**item['stock']), item['detail']) for item in row['candidates']]
            generated = row['generated_at']
            draft = build_preset(kind, amount, candidates, fee_rate=fee_rate, slippage_rate=slippage_rate)
    label = week.strftime('%m/%d')
    draft.update(week_start=week.isoformat(), next_refresh_on=(week + timedelta(days=7)).isoformat(),
                 generated_at=generated)
    draft['payload']['name'] = f"기본 {draft['name']} · {label}주"
    draft['notes'][0] = "매주 월요일(KST) 기준 첫 미리보기에서 후보 데이터를 갱신하고, 그 주에는 같은 데이터를 사용합니다."
    draft['notes'].insert(1, "마음에 드는 프리셋은 이름을 바꿔 내 전략으로 저장하세요. 저장한 전략은 주간 갱신으로 변경되지 않습니다.")
    return draft


PRESETS = {
    "popular": ("인기 · 거래대금 중심", "거래대금 상위에서 예산으로 1주 이상 살 수 있는 보통주"),
    "affordable": ("가성비 · 소액 분산", "거래대금 상위에서 예산으로 3주 이상 살 수 있는 보통주"),
}


def build_preset(kind, amount, candidates, *, fee_rate, slippage_rate):
    if kind not in PRESETS:
        raise ValueError("지원하지 않는 기본 프리셋입니다.")
    amount = Decimal(amount)
    if not amount.is_finite() or not Decimal("10000") <= amount <= Decimal("1000000"):
        raise ValueError("기본 프리셋의 1회 예산은 1만원부터 100만원까지입니다.")
    minimum_shares = 3 if kind == "affordable" else 1
    selected = []
    seen = set()
    for stock, detail in sorted(candidates, key=lambda row: row[0].trading_amount_rank or 1000000):
        market = detail.get("koreanMarketDetail") or {}
        price, change = stock.price, stock.change_rate_percent
        if (stock.symbol in seen or not stock.is_common_share or stock.security_type != "STOCK"
                or stock.market not in {"KOSPI", "KOSDAQ"} or stock.currency != "KRW"
                or not stock.trading_amount_rank or stock.trading_amount_rank > 50
                or detail.get("status") != "ACTIVE"
                or market.get("krxTradingSuspended") is not False
                or market.get("liquidationTrading")
                or price is None or not price.is_finite() or price < 1000
                or change is None or not change.is_finite() or abs(change) > 5
                or stock.trading_amount is None or stock.trading_amount < 10000000000
                or stock.market_cap is None or stock.market_cap < 1000000000000):
            continue
        cost = price * (1 + slippage_rate) * (1 + fee_rate)
        quantity = int(amount // cost)
        if quantity < minimum_shares:
            continue
        seen.add(stock.symbol)
        selected.append({"symbol": stock.symbol, "stock_name": stock.name,
                         "price": price, "quantity_estimate": quantity,
                         "rank": stock.trading_amount_rank})
        if len(selected) == 3:
            break
    if not selected:
        raise ValueError("현재 시세에서 조건을 만족하는 종목이 없습니다. 예산을 조정하거나 나중에 다시 확인하세요.")
    name, basis = PRESETS[kind]
    payload = LiveStrategyWrite(
        name=f"기본 {name}", symbol=selected[0]["symbol"], symbols=[s["symbol"] for s in selected],
        enabled=False, execution_mode="DRY_RUN", sizing_mode="AMOUNT", order_amount=amount,
        short_period=10, long_period=40, order_quantity=1,
        take_profit_rate=Decimal("4"), stop_loss_rate=Decimal("2"), max_holding_days=3,
        trading_start="09:10", trading_end="15:20", daily_order_limit=6, cooldown_minutes=60,
    )
    return {"kind": kind, "name": name, "basis": basis, "payload": payload.model_dump(mode="json"),
            "targets": selected, "generated_at": datetime.now(timezone.utc),
            "planned_budget": amount * len(selected),
            "notes": ["최근 제공된 거래대금 순위·시세 기준이며 종목은 저장 시 고정됩니다.",
                      "거래대금 상위 50위·100억원 이상, 시가총액 1조원 이상, 등락률 ±5% 이내만 선별합니다.",
                      "거래정지·정리매매·우선주·ETF는 제외하며 조건 부족 시 3종목보다 적을 수 있습니다.",
                      "가성비는 수량 확보 기준이며 기업의 저평가 판단이 아닙니다.",
                      "예상 수량은 수수료·슬리피지를 반영한 참고값입니다. 실제 PAPER 매수에는 잔액·계좌 한도가 추가 적용됩니다.",
                      "이동평균 상향 교차가 두 완료 봉에서 유지될 때 진입합니다. 생성만으로 즉시 매수하지 않습니다.",
                      "운영 시간 밖·서버 정지 중에는 청산하지 않습니다. 손절률은 손실 상한을 보장하지 않습니다."]}
