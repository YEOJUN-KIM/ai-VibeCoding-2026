"""Calendar-based chart buckets built from official minute/daily candles."""
from datetime import timedelta, timezone
from .models import LiveStockCandle

KST = timezone(timedelta(hours=9))
CHART_RANGES = {
    "1m": {"1D": 390},
    "1h": {"1D": 390, "1W": 1950},
    "1d": {"1M": 25, "3M": 70, "1Y": 260, "3Y": 780},
    "1w": {"1Y": 260, "3Y": 780, "5Y": 1300},
    "1mo": {"1Y": 260, "3Y": 780, "5Y": 1300, "10Y": 2600},
    "1y": {"5Y": 1300, "10Y": 2600},
}

def bucket_start(timestamp, interval):
    value = timestamp.astimezone(KST)
    if interval == "1m": return value.replace(second=0, microsecond=0)
    if interval == "1h": return value.replace(minute=0, second=0, microsecond=0)
    value = value.replace(hour=0, minute=0, second=0, microsecond=0)
    if interval == "1w": return value - timedelta(days=value.weekday())
    if interval == "1mo": return value.replace(day=1)
    if interval == "1y": return value.replace(month=1, day=1)
    return value

def chart_candles(rows, interval, period):
    if not rows: return []
    end = rows[-1].timestamp.astimezone(KST)
    days = {"1W": 7, "1M": 31, "3M": 93, "1Y": 366, "3Y": 1096,
            "5Y": 1827, "10Y": 3653}
    start = end.replace(hour=0, minute=0, second=0, microsecond=0)
    if period != "1D": start -= timedelta(days=days[period]-1)
    grouped = {}
    for row in rows:
        if row.timestamp.astimezone(KST) < start: continue
        key = bucket_start(row.timestamp, interval)
        if key not in grouped:
            grouped[key] = row.model_copy(update={"timestamp": key})
        else:
            candle = grouped[key]
            candle.high_price = max(candle.high_price, row.high_price)
            candle.low_price = min(candle.low_price, row.low_price)
            candle.close_price = row.close_price
            candle.volume += row.volume
    return list(grouped.values())
