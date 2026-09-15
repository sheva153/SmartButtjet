"""Tests for GoalService period-keyed pacing and forecast-driven status."""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from income_stats.config import AnalyticsConfig, StorageConfig
from income_stats.models import ChatGoal, GoalConfig, GoalPeriod, IncomeRecord
from income_stats.repositories import RecordsRepository
from income_stats.services.analytics_service import AnalyticsService, forecast_total
from income_stats.services.goal_service import GoalProgress, GoalService


def make_record(
    record_id: str,
    amount: str,
    income_date: date,
    *,
    currency: str = "UAH",
    chat_id: int = 1,
) -> IncomeRecord:
    return IncomeRecord(
        id=record_id,
        telegram_message_id=len(record_id),
        chat_id=chat_id,
        user_id=7,
        original_text=amount,
        amount=Decimal(amount),
        currency=currency,
        type="income",
        income_date=income_date,
        created_at=datetime(2026, 8, 1, 10, tzinfo=UTC),
        updated_at=datetime(2026, 8, 1, 10, tzinfo=UTC),
        updated_by=7,
    )


class FakeGoalRepository:
    def __init__(
        self,
        records: list[IncomeRecord],
        goal: ChatGoal | None = None,
    ) -> None:
        self.records = records
        self.goal = goal

    async def list_records(self, chat_id: int) -> list[IncomeRecord]:
        return [record for record in self.records if record.chat_id == chat_id]

    async def get_goal(
        self, chat_id: int, period: GoalPeriod = "month"
    ) -> ChatGoal | None:
        if (
            self.goal is not None
            and self.goal.chat_id == chat_id
            and self.goal.period == period
        ):
            return self.goal
        return None


def as_repository(repository: FakeGoalRepository) -> RecordsRepository:
    return cast(RecordsRepository, repository)


def build_service(
    records: list[IncomeRecord],
    goal: ChatGoal | None,
    tmp_path: Path,
    *,
    config: GoalConfig | None = None,
    forecast_method: str = "weighted",
) -> GoalService:
    repository = as_repository(FakeGoalRepository(records, goal))
    analytics = AnalyticsService(
        repository,
        AnalyticsConfig(),
        StorageConfig(export_directory=tmp_path),
        timezone="Europe/Kyiv",
    )
    return GoalService(repository, analytics, config or GoalConfig(), forecast_method)


@pytest.fixture
def goal_service_seeded(tmp_path: Path) -> GoalService:
    # goal 30000 UAH; income-to-date 20000 on day 10 of a 31-day month → on_track
    goal = ChatGoal(chat_id=1, amount=Decimal("30000"), currency="UAH", updated_by=7)
    records = [
        make_record("income-1", "12000", date(2026, 8, 3)),
        make_record("income-2", "8000", date(2026, 8, 9)),
    ]
    return build_service(records, goal, tmp_path)


@pytest.fixture
def goal_service_low(tmp_path: Path) -> GoalService:
    goal = ChatGoal(chat_id=1, amount=Decimal("30000"), currency="UAH", updated_by=7)
    records = [make_record("income-1", "5000", date(2026, 8, 5))]
    return build_service(records, goal, tmp_path)


@pytest.fixture
def goal_service_reached(tmp_path: Path) -> GoalService:
    goal = ChatGoal(chat_id=1, amount=Decimal("10000"), currency="UAH", updated_by=7)
    records = [make_record("income-1", "15000", date(2026, 8, 2))]
    return build_service(records, goal, tmp_path)


@pytest.fixture
def goal_service_no_goal(tmp_path: Path) -> GoalService:
    return build_service([], None, tmp_path)


@pytest.fixture
def goal_service_year(tmp_path: Path) -> GoalService:
    goal = ChatGoal(
        chat_id=1,
        period="year",
        amount=Decimal("300000"),
        currency="UAH",
        updated_by=7,
    )
    records = [make_record("income-1", "20000", date(2026, 3, 15))]
    return build_service(records, goal, tmp_path)


def test_forecast_linear_projects_runrate() -> None:
    # 1000 over 10 elapsed days, 30-day period -> 3000
    daily = [Decimal("100")] * 10
    assert forecast_total(daily, 30, "linear") == Decimal("3000")


def test_forecast_weighted_favours_recent_days() -> None:
    daily = [Decimal("0")] * 9 + [Decimal("100")]  # only last day earned
    linear = forecast_total(daily, 30, "linear")
    weighted = forecast_total(daily, 30, "weighted")
    assert weighted > linear  # recent surge extrapolated stronger


def test_forecast_empty_is_zero() -> None:
    assert forecast_total([], 30, "weighted") == Decimal()


async def test_progress_on_track(goal_service_seeded: GoalService) -> None:
    progress = await goal_service_seeded.progress(1, today=date(2026, 8, 10))
    assert progress is not None
    assert progress.status == "on_track"
    assert progress.actual == Decimal("20000")
    assert progress.amount == Decimal("30000")
    assert progress.currency == "UAH"
    assert progress.period == "month"


async def test_progress_status_off_track_when_forecast_below_goal(
    goal_service_low: GoalService,
) -> None:
    progress = await goal_service_low.progress(
        1, today=date(2026, 8, 20), period="month"
    )
    assert progress is not None
    assert progress.status == "off_track"
    assert progress.forecast < progress.amount
    assert progress.per_day_needed > 0


async def test_reached(goal_service_reached: GoalService) -> None:
    progress = await goal_service_reached.progress(1, today=date(2026, 8, 20))
    assert progress is not None
    assert progress.status == "reached"


async def test_progress_year_period(goal_service_year: GoalService) -> None:
    progress = await goal_service_year.progress(
        1, today=date(2026, 8, 20), period="year"
    )
    assert progress is not None
    assert progress.period == "year"


async def test_progress_none_without_goal(goal_service_no_goal: GoalService) -> None:
    assert await goal_service_no_goal.progress(1, today=date(2026, 8, 20)) is None


async def test_after_save_line_empty_without_goal(
    goal_service_no_goal: GoalService,
) -> None:
    assert await goal_service_no_goal.after_save_line(1, today=date(2026, 8, 20)) == ""


async def test_after_save_line_empty_when_disabled(
    tmp_path: Path,
) -> None:
    goal = ChatGoal(chat_id=1, amount=Decimal("10000"), currency="UAH", updated_by=7)
    service = build_service(
        [make_record("income-1", "15000", date(2026, 8, 2))],
        goal,
        tmp_path,
        config=GoalConfig(enabled=False),
    )
    assert await service.after_save_line(1, today=date(2026, 8, 20)) == ""


async def test_after_save_line_returns_phrase_when_goal_present(
    goal_service_reached: GoalService,
) -> None:
    line = await goal_service_reached.after_save_line(1, today=date(2026, 8, 20))
    assert line == "Ціль досягнута! Ти неймовірна 🎉"


def test_render_on_track_has_stable_prefix() -> None:
    service = GoalService(
        cast(RecordsRepository, FakeGoalRepository([], None)),
        AnalyticsService(
            cast(RecordsRepository, FakeGoalRepository([], None)),
            AnalyticsConfig(),
            StorageConfig(export_directory=Path("/tmp")),
        ),
        GoalConfig(),
        "weighted",
    )
    progress = GoalProgress(
        amount=Decimal("30000"),
        currency="UAH",
        actual=Decimal("20000"),
        forecast=Decimal("40000"),
        per_day_needed=Decimal("0"),
        status="on_track",
        period="month",
    )
    rendered = service.render(progress)
    assert rendered.startswith("🎯 Ціль (місяць): 30,000 UAH")
    assert "Виконано: 20,000 (67%)" in rendered
    assert "Прогноз до кінця: ~40,000 (133%)" in rendered


def test_render_off_track_includes_per_day_needed() -> None:
    service = GoalService(
        cast(RecordsRepository, FakeGoalRepository([], None)),
        AnalyticsService(
            cast(RecordsRepository, FakeGoalRepository([], None)),
            AnalyticsConfig(),
            StorageConfig(export_directory=Path("/tmp")),
        ),
        GoalConfig(),
        "weighted",
    )
    progress = GoalProgress(
        amount=Decimal("30000"),
        currency="UAH",
        actual=Decimal("5000"),
        forecast=Decimal("10000"),
        per_day_needed=Decimal("2273"),
        status="off_track",
        period="month",
    )
    rendered = service.render(progress)
    assert "Треба ~2,273 UAH/день" in rendered


def test_render_includes_forecast_line() -> None:
    service = GoalService(
        cast(RecordsRepository, FakeGoalRepository([], None)),
        AnalyticsService(
            cast(RecordsRepository, FakeGoalRepository([], None)),
            AnalyticsConfig(),
            StorageConfig(export_directory=Path("/tmp")),
        ),
        GoalConfig(),
        "weighted",
    )
    progress = GoalProgress(
        amount=Decimal("30000"),
        currency="UAH",
        actual=Decimal("5000"),
        forecast=Decimal("10000"),
        per_day_needed=Decimal("2273"),
        status="off_track",
        period="month",
    )
    rendered = service.render(progress)
    assert "Прогноз" in rendered


def test_render_year_label() -> None:
    service = GoalService(
        cast(RecordsRepository, FakeGoalRepository([], None)),
        AnalyticsService(
            cast(RecordsRepository, FakeGoalRepository([], None)),
            AnalyticsConfig(),
            StorageConfig(export_directory=Path("/tmp")),
        ),
        GoalConfig(),
        "weighted",
    )
    progress = GoalProgress(
        amount=Decimal("300000"),
        currency="UAH",
        actual=Decimal("100000"),
        forecast=Decimal("400000"),
        per_day_needed=Decimal("0"),
        status="on_track",
        period="year",
    )
    rendered = service.render(progress)
    assert rendered.startswith("🎯 Ціль (рік): 300,000 UAH")
