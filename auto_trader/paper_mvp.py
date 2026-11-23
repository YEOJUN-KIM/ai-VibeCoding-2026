"""PAPER-MVP-01: connects the DAY-01 Scout pipeline to the existing PAPER engine.

Glue only. Scout decides WHICH symbols may receive a new entry; the existing `TradingEngine` still decides
entry/exit signals, sizing, costs and fills, and `PaperBroker` + `RiskManager` still enforce every limit.
Nothing here can place a real order: the controller only accepts a `PaperBroker`, this module imports no
broker/LIVE order code (checked by a test), and market data comes from an injected `MvpDataSource`.

PAPER-SETTINGS-01: the MVP settings are the only automatic-trading settings. Their limits live in memory on the risk
manager (`AutoLimits`) only while a run is active, are measured against automatic holdings, and never limit manual
orders; the persistent risk settings are never written. Engine parameters are restored when the run ends.

Fail-closed rules (each tested): no data source, stale data, undeclared clock uncertainty, a Scout HOLD or a
provider error all mean "no new entries". Held lots keep their own exit rules; they are never rewritten by a
settings change. Stopping the engine stops BOTH new entries and exit monitoring (existing engine behaviour,
see docs/technical/PAPER_SAFETY_01_STOP_BEHAVIOR.md); status says so explicitly.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from . import day01_scout as sc
from .day01_scout_link import portfolio_limits_for
from .day01_scout_pipeline import PROCEED, run_scout_pipeline
from .ml.day01 import Config as Day01Config
from .models import Stock
from .paper import PaperBroker
from .risk import AutoLimits
from .trading import KST

STALE_AFTER = timedelta(seconds=90)
MAX_DECISIONS, MAX_EQUITY = 200, 1500

PRESETS = {  # UX-02 PC page values (the page marks them as not final policy)
    "안정형": dict(per_unit="percent", per_value=15, max_holdings=8, daily_loss_pct=1, stop_pct=3, take_pct=6, max_days=30, daily_buys=5),
    "균형형": dict(per_unit="percent", per_value=25, max_holdings=6, daily_loss_pct=2, stop_pct=5, take_pct=10, max_days=20, daily_buys=10),
    "적극형": dict(per_unit="percent", per_value=35, max_holdings=4, daily_loss_pct=3, stop_pct=7, take_pct=14, max_days=15, daily_buys=15),
}


class MvpError(ValueError):
    """A request the user can fix (HTTP 422 in the API)."""


class MvpConflict(MvpError):
    """The request is valid but not allowed in the current state (HTTP 409 in the API)."""


class MvpSettings(BaseModel):
    budget: int = Field(ge=0)
    sectors: list[str] = Field(min_length=1)
    profile: str
    per_unit: Literal["percent", "amount"]
    per_value: Decimal = Field(gt=0)
    max_holdings: int = Field(ge=1, le=30)
    daily_loss_pct: Decimal = Field(ge=Decimal("0.1"), le=20)
    daily_buys: int = Field(ge=0, le=100)
    stop_pct: Decimal = Field(ge=Decimal("0.1"), le=50)
    take_pct: Decimal = Field(ge=Decimal("0.1"), le=100)
    max_days: int = Field(ge=1, le=365)


def default_settings(total: Decimal) -> MvpSettings:
    return MvpSettings(budget=int(total / 2), sectors=["FOLIO 추천"], profile="균형형", **PRESETS["균형형"])


@dataclass(frozen=True)
class PipelineInput:
    """Everything the Scout pipeline needs for one decision at `as_of` (data that exists at `as_of`)."""
    as_of: datetime
    snapshot: object                    # day01_scout.RankingSnapshot
    candle_rows: tuple
    orderbooks: tuple
    instruments: tuple
    clock_uncertainty: timedelta | None  # None = not declared -> Scout HOLD
    collection_universe: tuple | None = None


class MvpDataSource(Protocol):
    def pipeline_input(self, now: datetime) -> PipelineInput | None: ...
    def bars(self, symbol: str, now: datetime, count: int) -> list: ...   # completed 1m bars (.timestamp/.close_price/.volume)
    def price(self, symbol: str, now: datetime) -> Decimal | None: ...
    def name(self, symbol: str) -> str: ...


class SourceFeed:
    """Engine `price_feed` adapter over an `MvpDataSource` (same contract as `PaperPriceFeed`)."""

    def __init__(self, source: MvpDataSource, clock):
        self.source, self._clock = source, clock
        self.prices_requested_at = None
        self.unavailable = {}
        self.client = None

    def read(self, symbols, valuation_symbols, period, now):
        self.prices_requested_at = None
        prices, candles, self.unavailable = {}, {}, {}
        for symbol in dict.fromkeys(list(symbols) + list(valuation_symbols)):
            price = self.source.price(symbol, now)
            if price is None or not price.is_finite() or price <= 0:
                if symbol in symbols:
                    self.unavailable[symbol] = f"{symbol}: 유효한 현재가가 없습니다."
                continue
            prices[symbol] = price
            if symbol not in symbols:
                continue
            bars = [b for b in self.source.bars(symbol, now, period) if b.timestamp + timedelta(minutes=1) <= now]
            bars.sort(key=lambda b: b.timestamp)
            if not bars or now - bars[-1].timestamp > timedelta(minutes=3):
                self.unavailable[symbol] = f"{symbol}: 최신 완료 봉이 없습니다."
            elif not any(b.volume > 0 for b in bars[-3:]):
                self.unavailable[symbol] = f"{symbol}: 최근 거래량이 없어 체결을 대기합니다."
            else:
                candles[symbol] = bars[-period:]
        self.prices_requested_at = self._clock()
        return prices, candles


def _honest_message(message: str, synthetic: bool) -> str:
    """The engine's status text is shared with the legacy live-price PAPER flow, where "실제 현재가" is true. With the fictional
    synthetic source (isolated verification only) the same text would wrongly claim real prices, so the MVP status relabels it."""
    if not synthetic:
        return message
    return (message.replace("실제 현재가", "합성(가상) 현재가").replace("실제 시세 기반", "합성(가상) 시세 기반")
            .replace("실제 시세", "합성(가상) 시세"))


class PaperMvp:
    def __init__(self, broker: PaperBroker, engine, *, source: MvpDataSource | None = None, clock=None, bind=True):
        if type(broker) is not PaperBroker:           # LIVE or any other broker is refused outright
            raise TypeError("PAPER-MVP accepts only a PaperBroker")
        self.broker, self.engine, self.source = broker, engine, source
        self._clock = clock or engine._clock
        self.last_pipeline = None
        self.lock = asyncio.Lock()                    # serialises settings/start/stop requests
        self.start_guard = None                       # optional () -> str | None, a reason to refuse starting
        self._legacy = None                           # shared engine/risk settings saved while MVP settings are active
        if bind:
            self.bind()

    def bind(self):
        """Route this engine's entries through Scout. The engine drops the binding itself when its loop ends, so the
        existing (non-MVP) PAPER strategy flow is unaffected once the MVP run stops."""
        self.engine.candidate_provider = self._candidates
        if self.source is not None:
            self.engine.price_feed = SourceFeed(self.source, self._clock)

    # ------------------------------------------------------------------ settings
    def settings(self) -> MvpSettings:
        saved = self.broker.mvp_state().get("settings")
        return MvpSettings.model_validate(saved) if saved else default_settings(self.broker.account().total_asset)

    def _limits(self, s: MvpSettings, *, strict: bool = True):
        """`strict` is for saving/starting (new settings must fit the account). Reporting the status of settings that were
        valid when saved must never fail because the account has since lost value (strict=False)."""
        total = self.broker.account().total_asset
        if strict and s.budget < 1:
            raise MvpError("투자 금액은 1원 이상이어야 합니다.")
        if strict and Decimal(s.budget) > total:
            raise MvpError(f"투자 금액은 모의 총자산 {total:,.0f}원 이하여야 합니다.")
        cap = total * s.per_value / 100 if s.per_unit == "percent" else s.per_value
        cap = min(cap, Decimal(s.budget)).quantize(Decimal(1), rounding=ROUND_DOWN)
        if strict and cap < 1:
            raise MvpError("종목당 한도가 너무 작습니다.")
        loss = max(Decimal(1), (total * s.daily_loss_pct / 100).quantize(Decimal(1), rounding=ROUND_DOWN))
        return total, cap, loss

    def auto_limits(self, s: MvpSettings | None = None, *, strict: bool = True) -> AutoLimits:
        """The automatic run's limits. Held only in memory by the risk manager while a run is active; manual orders never see them."""
        s = s or self.settings()
        _, cap, loss = self._limits(s, strict=strict)
        return AutoLimits(max_order_amount=cap, max_symbol_amount=cap, max_total_investment=Decimal(s.budget), daily_loss_limit=loss,
                          daily_buy_limit=s.daily_buys, session_close=self.engine.trading_end, max_holdings=s.max_holdings)

    def day01_config(self, s: MvpSettings | None = None) -> Day01Config:
        s = s or self.settings()
        total, cap, loss = self._limits(s)
        return Day01Config(
            execution="research_estimated_open", vwap_mode="exact", sizing="AFFORDABLE_UP_TO_CAP", order_quantity=1000,
            initial_cash=total, fee_rate=self.broker.fee_rate, sell_tax_rate=self.broker.sell_tax_rate,
            slippage_rate=self.broker.slippage_rate, max_order_amount=cap, max_symbol_amount=cap,
            max_total_investment=Decimal(s.budget), max_positions=s.max_holdings, min_cash_ratio=Decimal(0),
            daily_loss_limit=loss, daily_order_limit=s.daily_buys or 100)

    def apply(self, s: MvpSettings, *, activate: bool = True) -> MvpSettings:
        """Validate and save the settings. With `activate` (start-of-run) they also become the automatic run's limits and
        engine parameters; the settings page saves with activate=False. Saved settings never touch the persistent risk
        settings (which serve LIVE previews and legacy strategies) and never limit manual orders. Existing lots keep the
        exit rules they were bought with (stored per lot); only future buys use the new values."""
        if self.engine.running:
            raise MvpConflict("자동매매를 일시정지한 뒤 설정을 변경하세요.")
        self._limits(s)                               # validation only
        self.broker.update_mvp(settings=s.model_dump(mode="json"))
        if activate:
            self._activate(s)
        return s

    # ---- run-scoped engine parameters (in memory only; restored when the run ends)
    def _capture_legacy(self) -> dict:
        e = self.engine
        return dict(engine=e.capture_parameters(), price_feed=e.price_feed, count_sells=e.count_sells_in_daily_limit)

    def _activate(self, s: MvpSettings):
        eng = self.engine
        limits = self.auto_limits(s)
        if self._legacy is None:
            self._legacy = self._capture_legacy()
        cooldowns = dict(eng._last_order_at)           # configure() clears them; a restart must not bypass the cooldown
        try:
            eng.configure(interval_seconds=eng.interval_seconds, short_period=eng.short_period, long_period=eng.long_period,
                          order_quantity=eng.order_quantity, target_symbols=[], sizing_mode="AMOUNT", order_amount=limits.max_order_amount,
                          take_profit_rate=s.take_pct, stop_loss_rate=s.stop_pct, max_holding_days=s.max_days,
                          daily_order_limit=s.daily_buys, strategy_name="PAPER-MVP Scout")
            eng._last_order_at.update(cooldowns)
            eng.count_sells_in_daily_limit = False      # the daily limit is a BUY limit; exits are never limited
            self.broker.risk_manager.auto_limits = limits
            eng.run_release = self._release
        except Exception:
            self._release()
            raise
        self._log("CONTROL", "투자 설정 적용", f"{s.profile} · 투자 {s.budget:,}원 · 종목당 {limits.max_order_amount:,}원 · 최대 {s.max_holdings}종목 · "
                  f"하루 손실 {limits.daily_loss_limit:,}원 · 자동매매에만 적용(수동 주문 제외) · 신규 매수부터 적용(기존 보유분 청산 기준 유지)")

    def _release(self):
        """End of an automatic run: drop its limits and put the engine's pre-MVP parameters back (idempotent, in memory only).
        If that fails the engine is BLOCKED: no new automatic run (MVP or legacy) may start until a retry succeeds, and
        the snapshot is kept untouched for that retry. Manual PAPER orders are not affected."""
        legacy, eng, log = self._legacy, self.engine, logging.getLogger("auto_trader.paper_mvp")
        if eng.running:
            return
        self.broker.risk_manager.auto_limits = None    # limits exist only while a run is active
        if legacy is not None:
            try:
                eng.restore_parameters(legacy["engine"])
            except Exception:
                log.exception("Could not restore the engine parameters; new automatic runs stay blocked")
                eng.start_block = "엔진 설정 복구가 끝나지 않아 새 자동매매를 시작할 수 없습니다. 다시 시작하면 복구를 재시도합니다."
                try:
                    self._log("CONTROL", "설정 복구 실패", eng.start_block)
                except Exception:
                    log.exception("Could not record the restore failure")
                return
            self._legacy = None
            eng.price_feed, eng.count_sells_in_daily_limit = legacy["price_feed"], legacy["count_sells"]
            eng.candidate_provider, eng.candidate_message, eng.run_release = None, "", None
        eng.start_block = None

    def _ensure_startable(self):
        """Retry a pending restore first; refuse while the engine is still blocked."""
        if self.engine.start_block or self._legacy is not None:
            self._release()
        if self.engine.start_block:
            raise MvpConflict(self.engine.start_block)

    def limit_legacy_run(self):
        """The existing /strategy/start flow is also automatic trading: it runs under the same in-memory limits (the saved or
        default MVP settings) instead of the persistent risk settings. Call under the paper lock, right before engine.start()."""
        if self.engine.running:
            raise MvpConflict("이미 자동매매가 실행 중입니다.")
        self._ensure_startable()
        self.broker.risk_manager.auto_limits = self.auto_limits()
        self.engine.run_release = self._release        # clears the limits when that run ends

    def _mine(self) -> bool:
        return bool(self.engine.running and self.engine.candidate_provider == self._candidates)

    # ------------------------------------------------------------------ control
    async def start(self):
        if self.engine.running:
            if self._mine():
                return False                   # duplicate click: already running
            raise MvpConflict("다른 PAPER 전략이 실행 중입니다. 해당 전략을 정지한 뒤 시작하세요.")
        if self.engine._task is not None and not self.engine._task.done():
            raise MvpConflict("이전 실행이 아직 정리 중입니다. 잠시 후 다시 시작하세요.")
        reason = self.start_guard() if self.start_guard else None
        if reason:
            raise MvpConflict(reason)
        self._ensure_startable()
        if self.broker.management_scope != "AUTO":
            raise MvpConflict("관리 범위에 수동 보유분이 포함되어 있어 시작할 수 없습니다. 관리 범위를 '자동매매 보유분'으로 바꾸세요.")
        try:
            self.apply(self.settings(), activate=True)   # always run with explicitly validated, applied settings
            self._record_equity(force=True)
            self._log("CONTROL", "자동매매 시작", "신규 후보 탐색과 보유분 청산 감시를 시작합니다. 실제 주문은 없습니다.")
            self.bind()
            return await self.engine.start()
        except BaseException:
            self._release()
            raise

    async def stop(self, *, emergency=False):
        if self.engine.running and not self._mine() and not emergency:
            raise MvpConflict("MVP가 시작한 실행이 아닙니다. 해당 PAPER 전략 화면에서 정지하세요.")
        stopped = await self.engine.stop(emergency=emergency)
        self._release()
        self._record_equity(force=True)
        self._log("CONTROL", "긴급정지" if emergency else "일시정지",
                  "신규 판단·주문과 보유분 청산 감시가 모두 멈췄습니다. 보유 주식은 매도하지 않습니다.")
        return stopped

    # ------------------------------------------------------------------ Scout -> engine
    def _held(self):
        return {p.symbol for p in self.broker.account().positions}

    def _candidates(self, now: datetime) -> list[str]:
        """Called by the engine before each poll. Returns the symbols allowed a NEW entry (possibly none)."""
        self._record_equity()
        try:
            return self._select(now)
        except Exception as exc:                                  # any failure = no new entries
            self._hold("DATA_ERROR", f"후보 판단 오류로 신규 진입을 보류합니다 ({type(exc).__name__}).")
            return []

    def _hold(self, code: str, text: str, *, notes=()):
        self.engine.candidate_message = f"판단 보류 · {text}"
        self._log("HOLD_DATA", "신규 진입 판단 보류", text, code=code, reasons=list(notes))

    def _select(self, now: datetime) -> list[str]:
        if self.source is None:
            self._hold("NO_DATA_SOURCE", "검증된 시장 데이터 소스가 연결되지 않아 신규 진입하지 않습니다.")
            return []
        data = self.source.pipeline_input(now)
        if data is None:
            self._hold("NO_DATA", "시장 데이터를 받지 못해 신규 진입하지 않습니다.")
            return []
        if data.as_of > now or now - data.as_of > STALE_AFTER:
            self._hold("DATA_STALE", "시장 데이터 시각이 현재와 맞지 않아(오래됨 또는 미래) 신규 진입하지 않습니다.")
            return []
        s = self.settings()
        cfg = self.day01_config(s)
        account = self.broker.account()
        risk = self.broker.risk_manager.auto_status(self.auto_limits(s))
        positions = tuple(sc.Position(sym, self.broker.risk_holdings()[sym], self.broker.risk_symbol_value(sym), data.as_of,
                                      self.engine.market.quote(sym).price, data.as_of) for sym in self.broker.risk_symbols())
        portfolio = sc.Portfolio(data.as_of, cash=account.cash, total_asset=account.total_asset, positions=positions,
                                 daily_loss=max(Decimal(0), -risk["daily_profit"]), **portfolio_limits_for(cfg))
        result = run_scout_pipeline(
            as_of=data.as_of, snapshot=data.snapshot, candle_rows=data.candle_rows, orderbook_results=data.orderbooks,
            instruments=data.instruments, portfolio=portfolio, day01_config=cfg, clock_uncertainty=data.clock_uncertainty,
            collection_universe=data.collection_universe)
        self.last_pipeline = result
        if tuple(result.hold_reasons) == ("NO_CANDIDATES",):      # data was fine; nothing met the criteria
            self.engine.candidate_message = "정상 관망 · 기준을 충족한 신규 후보가 없습니다."
            self._log("WATCH", "정상 관망", "Scout 기준을 충족한 신규 후보가 없어 현금을 유지합니다(한도·보유분 반영).")
            return []
        if result.decision != PROCEED:
            self._hold("SCOUT_HOLD", "Scout 판단 보류: " + ", ".join(result.hold_reasons), notes=result.hold_reasons)
            return []
        held = self._held()                                       # symbols NOT to buy again: automatic and manual holdings
        free = max(0, s.max_holdings - len(self.broker.risk_symbols()))   # the holdings cap counts automatic holdings only
        chosen = [sym for sym in result.passed if sym not in held][:free]
        for sym in chosen:
            price = self.source.price(sym, now)
            if price is None or price <= 0:
                chosen = [c for c in chosen if c != sym]
                continue
            self.engine.market.upsert_stock(Stock(symbol=sym, name=self.source.name(sym), market="KRX"), price)
        if not chosen:
            why = "보유 종목 수가 한도에 도달했습니다." if not free else "이미 보유 중인 종목뿐이라 추가 진입하지 않습니다."
            self.engine.candidate_message = "정상 관망 · " + why
            self._log("WATCH", "정상 관망", why, symbols=list(result.passed))
            return []
        self.engine.candidate_message = "후보 " + ", ".join(chosen) + " · 매수 신호 대기"
        self._log("CANDIDATE", "후보 선정", f"Scout 통과 {len(result.passed)}종목 중 신규 진입 후보 {len(chosen)}종목 · 이동평균 매수 신호를 기다립니다.",
                  symbols=chosen)
        return chosen

    # ------------------------------------------------------------------ records
    def _log(self, kind: str, title: str, reason: str, **extra):
        state = self.broker.mvp_state()
        log = state.get("decisions", [])
        key = (kind, title, reason, tuple(extra.get("symbols", ())))
        if log and (log[-1]["kind"], log[-1]["title"], log[-1]["reason"], tuple(log[-1].get("symbols", ()))) == key:
            return
        log.append({"at": self._clock().isoformat(), "kind": kind, "title": title, "reason": reason, **extra})
        self.broker.update_mvp(decisions=log[-MAX_DECISIONS:])

    def _record_equity(self, force=False):
        now = self._clock()
        value = str(self.broker.account().total_asset)
        samples = self.broker.mvp_state().get("equity", [])
        if not force and samples:
            last_at = datetime.fromisoformat(samples[-1][0])
            if last_at.replace(second=0, microsecond=0) == now.replace(second=0, microsecond=0) and samples[-1][1] == value:
                return
        samples.append([now.isoformat(), value])
        self.broker.update_mvp(equity=samples[-MAX_EQUITY:])

    # ------------------------------------------------------------------ status
    def status(self, period: str = "day") -> dict:
        eng, broker = self.engine, self.broker
        now = self._clock()
        account = broker.account()
        strategy = eng.status()
        risk = broker.risk_manager.auto_status(self.auto_limits(strict=False))
        if strategy.running and not self._mine():
            state, label = "other", "다른 PAPER 전략 실행 중"
        elif strategy.running:
            state, label = "running", "자동매매 감시 중"
        elif eng.start_block:
            state, label = "blocked", "설정 복구 필요"
        elif strategy.emergency_stopped:
            state, label = "emergency", "긴급정지"
        elif broker.mvp_state().get("equity"):
            state, label = "stopped", "일시정지"
        else:
            state, label = "idle", "시작 대기"
        held_lots = broker.managed_lots()
        samples = [(datetime.fromisoformat(t), Decimal(v)) for t, v in broker.mvp_state().get("equity", [])]
        pnl = period_pnl(samples, account.total_asset, account.initial_cash, period, now)
        holdings = []
        for p in account.positions:
            lots = [dict(quantity=l["quantity"], average_price=l["average_price"], opened_at=l["opened_at"],
                         stop_pct=l["context"].get("stop_loss_rate"), take_pct=l["context"].get("take_profit_rate"),
                         max_days=l["context"].get("max_holding_days")) for l in held_lots if l["symbol"] == p.symbol]
            holdings.append(dict(symbol=p.symbol, name=p.name, quantity=p.quantity, average_price=str(p.average_price),
                                 price=str(p.current_price), value=str(p.market_value), profit=str(p.unrealized_profit),
                                 profit_pct=str(p.unrealized_profit / (p.average_price * p.quantity) * 100) if p.average_price else None,
                                 lots=lots))
        trades = [dict(id=o.id, symbol=o.symbol, side=o.side.value, status=o.status.value, quantity=o.quantity,
                       price=str(o.price), fee=str(o.fee), tax=str(o.tax), realized=str(o.realized_profit) if o.realized_profit is not None else None,
                       reason=o.reason, message=o.message, at=o.created_at.isoformat(), source=o.source)
                  for o in broker.orders()]
        return dict(
            mode="PAPER", live_blocked=True, real_data_verified=False,
            data_source="SYNTHETIC" if self.source is not None else "NONE",
            run=dict(state=state, label=label, running=strategy.running, message=_honest_message(eng.data_message, self.source is not None),
                     operating_state=strategy.operating_state, tick_count=strategy.tick_count,
                     exit_monitoring=strategy.running, blocked_reason=eng.start_block, held_symbols=sorted(self._held()),
                     stop_note="정지하면 신규 매수·매도 판단과 보유분의 손절·익절 감시가 모두 멈춥니다. 보유 주식은 자동 매도되지 않고 그대로 남습니다."
                               + ("" if strategy.running else " 현재 청산 감시는 꺼져 있습니다.")),
            account=dict(total_asset=str(account.total_asset), cash=str(account.cash), initial_cash=str(account.initial_cash),
                         holdings_value=str(sum((p.market_value for p in account.positions), Decimal(0))),
                         total_profit=str(account.total_profit), return_percent=str(account.return_percent) if account.return_percent is not None else None,
                         realized_profit=str(account.realized_profit), unrealized_profit=str(account.unrealized_profit),
                         total_fees=str(account.total_fees)),
            pnl=pnl, settings=self.settings().model_dump(mode="json"),
            risk=dict(new_buys_allowed=risk["new_buys_allowed"], block_reason=risk["block_reason"], daily_profit=str(risk["daily_profit"]),
                      invested_amount=str(risk["invested_amount"]), max_total_investment=str(risk["max_total_investment"]),
                      max_symbol_amount=str(risk["max_symbol_amount"]), daily_loss_limit=str(risk["daily_loss_limit"]),
                      scope="자동매매 보유분·손익 기준(수동 주문에는 적용되지 않음)"),
            holdings=holdings, trades=trades, decisions=list(reversed(broker.mvp_state().get("decisions", []))),
            series=[[t.isoformat(), str(v)] for t, v in samples], checked_at=now.isoformat())


def period_pnl(samples, total: Decimal, initial: Decimal, period: str, now: datetime) -> dict:
    """Period profit = current total asset minus the asset at the period start (the last sample at or before
    the start, else the first sample). 'all' uses the initial cash. Deposits/withdrawals do not exist in PAPER."""
    local = now.astimezone(KST)
    start = {"day": local.replace(hour=0, minute=0, second=0, microsecond=0), "week": local - timedelta(days=7),
             "month": local - timedelta(days=30), "year": local - timedelta(days=365)}.get(period)
    if period == "all" or start is None or not samples:
        base, basis = initial, "운용 시작 자금 대비"
    else:
        before = [v for t, v in samples if t <= start]
        base = before[-1] if before else samples[0][1]
        basis = "기간 시작 자산 대비" if before else "기록된 첫 자산 대비(기간 전 기록 없음)"
    gain = total - base
    return dict(period=period, base=str(base), gain=str(gain), percent=str(gain / base * 100) if base else None, basis=basis)


_CONTROLLERS: dict = {}


def controller_for(broker, engine, *, source=None, clock=None, bind=False) -> PaperMvp:
    """One controller per engine. Binding to the engine happens in `start()`, not here."""
    key = id(engine)
    if key not in _CONTROLLERS or _CONTROLLERS[key].engine is not engine or _CONTROLLERS[key].broker is not broker:
        _CONTROLLERS[key] = PaperMvp(broker, engine, source=source, clock=clock, bind=bind)
    return _CONTROLLERS[key]
