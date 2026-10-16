"""Original moving-average crossover and two-bar confirmation rules."""

from decimal import Decimal

from .base import ExitSignalContext, SignalContext, StrategySignal


class MovingAverageStrategy:
    def generate_exit_signal(self, context: ExitSignalContext) -> str | None:
        closes, short, long = context.history, context.short_period, context.long_period
        if len(closes) >= long + 1:
            previous = sum(closes[-short-1:-1]) / short - sum(closes[-long-1:-1]) / long
            current = sum(closes[-short:]) / short - sum(closes[-long:]) / long
            if previous > 0 and current <= 0:
                return '이동평균 하향 교차'
        return None

    def generate_signal(self, context: SignalContext) -> StrategySignal:
        history = context.history
        short_average = sum(list(history)[-context.short_period :], Decimal("0")) / context.short_period
        long_average = sum(history, Decimal("0")) / context.long_period
        trend = "ABOVE" if short_average > long_average else "BELOW"
        armed = context.entry_armed
        count = context.confirmation_count
        if not context.new_bar:
            return StrategySignal(short_average, long_average, trend, armed, count,
                                  "보유 중 · 청산 조건 감시" if context.position_quantity()
                                  else "다음 완료 1분봉 대기")
        if trend == "BELOW":
            armed = False
            count = 0
            decision = "단기선 열세 · 상향 교차 대기"
        else:
            count += 1
            if context.previous_trend == "BELOW":
                armed = True
            decision = (f"상향 교차 확인 {min(count, 2)}/2"
                        if armed else "상승 중 · 새 상향 교차 대기")

        # Costs remain in broker fills; entry still requires two bars and trend.
        rising = history[-1] > history[0] and context.price >= short_average
        action = "HOLD"
        reason = ""
        if armed and count >= 2 and rising and not context.position_quantity():
            action = "BUY"
            reason = "상향 교차 후 2봉 확인 · 가격 추세 확인"
        elif armed and count >= 2:
            decision = "2봉 확인 · 가격 추세 확인 대기"
        elif context.previous_trend == "ABOVE" and trend == "BELOW":
            action = "SELL"
            reason = "단기 이동평균이 장기 이동평균을 하향 돌파"
        return StrategySignal(short_average, long_average, trend, armed, count,
                              decision, action, rising, reason)
