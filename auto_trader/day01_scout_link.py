"""Pure link between DAY01 Scout watch results and the DAY-01 replay. No I/O, clock or settings.

Scout produces WATCH candidates and held monitors; DAY-01 owns the trading rules. This module only
(1) derives Scout settings/portfolio limits from the DAY-01 `Config` so both sides use identical
numbers, (2) reports any mismatch, and (3) turns a timeline of `ScoutResult`s into the replay's
universe and a causal `entry_allowed(symbol, at)` veto. It never creates signals or orders.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .day01_scout import OK, Portfolio, ScoutConfig, ScoutResult, WatchEntry

__all__ = ["scout_quantity_for", "scout_config_for", "portfolio_limits_for", "limit_mismatches", "WatchTimeline"]


def scout_quantity_for(day01_config) -> int:
    """Quantity Scout checks affordability with. FIXED sizing: the order quantity itself. Dynamic
    sizing (`AFFORDABLE_UP_TO_CAP`): 1, because Scout only decides whether at least one share can be
    ordered; the final quantity is DAY-01's decision at signal time."""
    return 1 if getattr(day01_config, "sizing", "FIXED") == "AFFORDABLE_UP_TO_CAP" else day01_config.order_quantity


def scout_config_for(day01_config, **overrides) -> ScoutConfig:
    """Scout config whose quantity and cost model equal the DAY-01 order the replay would place
    (or, for dynamic sizing, the minimum 1-share order)."""
    day01_config.validate()
    values = dict(quantity=scout_quantity_for(day01_config), fee_rate=day01_config.fee_rate,
                  buy_slippage_rate=day01_config.slippage_rate)
    values.update(overrides)
    return ScoutConfig(**values)


def portfolio_limits_for(day01_config) -> dict:
    """Keyword arguments for `Portfolio` carrying the DAY-01 position/cash/loss limits."""
    return dict(max_positions=day01_config.max_positions,
                min_cash_ratio_pct=day01_config.min_cash_ratio,
                daily_loss_limit=day01_config.daily_loss_limit)


def limit_mismatches(day01_config, scout_config: ScoutConfig, portfolio: Portfolio,
                     risk_config=None) -> tuple:
    """Names of limits where Scout would use different numbers than DAY-01 (empty = consistent).
    `risk_config` is the object passed to `select_watchlist`; it must expose DAY-01's three
    amount limits (the DAY-01 `Config` itself qualifies)."""
    bad = []
    if scout_config.quantity != scout_quantity_for(day01_config):
        bad.append("QUANTITY")
    if scout_config.fee_rate != day01_config.fee_rate:
        bad.append("FEE_RATE")
    if scout_config.buy_slippage_rate != day01_config.slippage_rate:
        bad.append("BUY_SLIPPAGE_RATE")
    if portfolio.max_positions != day01_config.max_positions:
        bad.append("MAX_POSITIONS")
    if portfolio.min_cash_ratio_pct != day01_config.min_cash_ratio:
        bad.append("MIN_CASH_RATIO")
    if portfolio.daily_loss_limit != day01_config.daily_loss_limit:
        bad.append("DAILY_LOSS_LIMIT")
    if risk_config is not None:
        for name in ("max_order_amount", "max_symbol_amount", "max_total_investment"):
            if getattr(risk_config, name, None) != getattr(day01_config, name):
                bad.append(name.upper())
    return tuple(bad)


def _aware(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")
    return value.astimezone(timezone.utc)


class WatchTimeline:
    """Time-ordered Scout results. All answers depend only on results with `as_of <= at`.

    * `entry_allowed(symbol, at)`: True only if the latest result known at `at` is OK, lists the
      symbol as a candidate, and `at < entry_expires_at`. A newer result that drops the symbol,
      is not OK, or has expired revokes it; an older result is never used as a fallback.
    * `universe()`: every symbol ever candidate or held in the timeline. It is a data filter, not
      a selection signal; entries stay gated by `entry_allowed`, and held symbols are always
      included so their stops, marks and liquidation keep running after rotation. Trades and
      signals are causal for any superset universe, but the replay's reports (equity-curve
      timestamps, reasons) cover every universe symbol's data, including late-arriving candles.
      Comparing runs for prefix invariance therefore requires the SAME pre-declared universe.
    """

    def __init__(self, results):
        items = tuple(results)
        if not all(isinstance(r, ScoutResult) for r in items):
            raise ValueError("ScoutResult objects required")
        ordered = sorted(items, key=lambda r: _aware(r.as_of))
        stamps = [_aware(r.as_of) for r in ordered]
        for earlier, later, a, b in zip(ordered, ordered[1:], stamps, stamps[1:]):
            if a == b and earlier != later:
                raise ValueError("conflicting Scout results at the same as_of")
        unique = []
        for result in ordered:
            if unique and _aware(unique[-1].as_of) == _aware(result.as_of):
                continue
            unique.append(result)
        self._results = tuple(unique)
        self._stamps = tuple(_aware(r.as_of) for r in unique)

    def latest(self, at: datetime):
        moment = _aware(at)
        found = None
        for stamp, result in zip(self._stamps, self._results):
            if stamp <= moment:
                found = result
            else:
                break
        return found

    def entry_allowed(self, symbol: str, at: datetime) -> bool:
        result = self.latest(at)
        if result is None or result.status != OK:
            return False
        moment = _aware(at)
        return any(c.symbol == symbol and moment < _aware(c.entry_expires_at)
                   for c in result.candidates)

    def watchlist_at(self, at: datetime) -> tuple:
        """`WatchEntry` tuple (candidates and held) of the latest result known at `at`."""
        result = self.latest(at)
        return () if result is None else tuple(result.watch_union)

    def universe(self) -> tuple:
        symbols = set()
        for result in self._results:
            symbols.update(c.symbol for c in result.candidates)
            symbols.update(h.symbol for h in result.held)
            symbols.update(w.symbol for w in result.watch_union if isinstance(w, WatchEntry))
        return tuple(sorted(symbols))
