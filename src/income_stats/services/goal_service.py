"""Income goal pacing, driven by a configurable end-of-period forecast."""

import random
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from income_stats.models import GoalConfig, GoalPeriod
from income_stats.repositories import RecordsRepository
from income_stats.services.analytics_service import (
    AnalyticsService,
    daily_income_series,
    forecast_total,
    period_span,
)

_RNG = random.SystemRandom()


@dataclass(frozen=True)
class GoalProgress:
    amount: Decimal
    currency: str
    actual: Decimal
    forecast: Decimal
    per_day_needed: Decimal
    status: str  # "reached" | "on_track" | "off_track"
    period: GoalPeriod


class GoalService:
    def __init__(
        self,
        repository: RecordsRepository,
        analytics: AnalyticsService,
        config: GoalConfig,
        forecast_method: str,
    ) -> None:
        self._repository = repository
        self._analytics = analytics
        self._config = config
        self._forecast_method = forecast_method

    async def progress(
        self, chat_id: int, *, today: date, period: GoalPeriod = "month"
    ) -> GoalProgress | None:
        goal = await self._repository.get_goal(chat_id, period)
        if goal is None:
            return None
        frame = await self._analytics.frame(chat_id, period, today=today)
        totals = self._analytics.totals_by_type(frame)
        actual = totals.get(goal.currency, {}).get("income", Decimal())
        elapsed, total_days = period_span(period, today)
        daily = daily_income_series(frame, goal.currency, elapsed, today, period)
        forecast = forecast_total(daily, total_days, self._forecast_method)
        days_left = max(1, total_days - elapsed)
        per_day = max(Decimal(), goal.amount - actual) / Decimal(days_left)
        if actual >= goal.amount:
            status = "reached"
        elif forecast >= goal.amount:
            status = "on_track"
        else:
            status = "off_track"
        return GoalProgress(
            goal.amount, goal.currency, actual, forecast, per_day, status, period
        )

    def render(self, progress: GoalProgress) -> str:
        pct = (
            (progress.actual / progress.amount * 100) if progress.amount else Decimal()
        )
        pct_fc = (
            (progress.forecast / progress.amount * 100)
            if progress.amount
            else Decimal()
        )
        label = "місяць" if progress.period == "month" else "рік"
        phrase = self._phrase(progress.status)
        lines = [
            f"🎯 Ціль ({label}): {progress.amount:,.0f} {progress.currency}",
            f"Виконано: {progress.actual:,.0f} ({pct:.0f}%)",
            f"Прогноз до кінця: ~{progress.forecast:,.0f} ({pct_fc:.0f}%)",
        ]
        if progress.status == "off_track":
            lines.append(
                f"Треба ~{progress.per_day_needed:,.0f} {progress.currency}/день"
            )
        lines.append(phrase)
        return "\n".join(lines)

    async def after_save_line(
        self, chat_id: int, *, today: date, period: GoalPeriod = "month"
    ) -> str:
        if not (self._config.enabled and self._config.after_save_line):
            return ""
        progress = await self.progress(chat_id, today=today, period=period)
        return self._phrase(progress.status) if progress else ""

    def _phrase(self, status: str) -> str:
        pool = {
            "on_track": self._config.ahead_phrases,
            "off_track": self._config.behind_phrases,
            "reached": self._config.reached_phrases,
        }[status]
        return _RNG.choice(pool) if pool else ""
