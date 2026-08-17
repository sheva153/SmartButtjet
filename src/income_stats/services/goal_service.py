"""Monthly income goal pacing."""

import calendar
import random
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from income_stats.models import GoalConfig
from income_stats.repositories import RecordsRepository
from income_stats.services.analytics_service import AnalyticsService

_RNG = random.SystemRandom()


@dataclass(frozen=True)
class GoalProgress:
    amount: Decimal
    currency: str
    actual: Decimal
    expected: Decimal
    per_day_needed: Decimal
    status: str  # "ahead" | "behind" | "reached"


class GoalService:
    def __init__(
        self,
        repository: RecordsRepository,
        analytics: AnalyticsService,
        config: GoalConfig,
    ) -> None:
        self._repository = repository
        self._analytics = analytics
        self._config = config

    async def progress(self, chat_id: int, *, today: date) -> GoalProgress | None:
        goal = await self._repository.get_goal(chat_id)
        if goal is None:
            return None
        frame = await self._analytics.frame(chat_id, "month", today=today)
        totals = self._analytics.totals_by_type(frame)
        actual = totals.get(goal.currency, {}).get("income", Decimal())
        days_in_month = calendar.monthrange(today.year, today.month)[1]
        expected = goal.amount * Decimal(today.day) / Decimal(days_in_month)
        days_left = max(1, days_in_month - today.day)
        remaining = max(Decimal(), goal.amount - actual)
        per_day = remaining / Decimal(days_left)
        if actual >= goal.amount:
            status = "reached"
        elif actual >= expected:
            status = "ahead"
        else:
            status = "behind"
        return GoalProgress(
            goal.amount, goal.currency, actual, expected, per_day, status
        )

    def render(self, progress: GoalProgress) -> str:
        pct = (
            (progress.actual / progress.amount * 100) if progress.amount else Decimal()
        )
        phrase = self._phrase(progress.status)
        lines = [
            f"🎯 Ціль: {progress.amount:,.0f} {progress.currency}/місяць",
            f"Виконано: {progress.actual:,.0f} ({pct:.0f}%)",
        ]
        if progress.status == "behind":
            lines.append(
                f"Треба ~{progress.per_day_needed:,.0f} {progress.currency}/день"
            )
        lines.append(phrase)
        return "\n".join(lines)

    async def after_save_line(self, chat_id: int, *, today: date) -> str:
        if not (self._config.enabled and self._config.after_save_line):
            return ""
        progress = await self.progress(chat_id, today=today)
        return self._phrase(progress.status) if progress else ""

    def _phrase(self, status: str) -> str:
        pool = {
            "ahead": self._config.ahead_phrases,
            "behind": self._config.behind_phrases,
            "reached": self._config.reached_phrases,
        }[status]
        return _RNG.choice(pool) if pool else ""
