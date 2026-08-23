from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from aiogram.filters import CommandObject
from aiogram.types import Message

from income_stats.config import AppConfig
from income_stats.handlers.goal_handler import goal_handler, goal_router
from income_stats.repositories import RecordsRepository
from income_stats.services import GoalProgress, GoalService


def test_goal_handler_is_importable() -> None:
    assert callable(goal_handler)
    assert goal_router.name == "goal"


@pytest.mark.asyncio
async def test_goal_set_stores_amount() -> None:
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        chat=SimpleNamespace(id=-100),
        answer=AsyncMock(),
    )
    repository = SimpleNamespace(set_goal=AsyncMock())
    goal_service = SimpleNamespace(progress=AsyncMock(), render=AsyncMock())

    await goal_handler(
        cast(Message, message),
        CommandObject(command="goal", args="50000"),
        cast(RecordsRepository, repository),
        cast(GoalService, goal_service),
        AppConfig(),
    )

    repository.set_goal.assert_awaited_once_with(-100, Decimal("50000"), "UAH", 7)
    message.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_goal_set_with_currency_stores_amount() -> None:
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        chat=SimpleNamespace(id=-100),
        answer=AsyncMock(),
    )
    repository = SimpleNamespace(set_goal=AsyncMock())
    goal_service = SimpleNamespace(progress=AsyncMock(), render=AsyncMock())

    await goal_handler(
        cast(Message, message),
        CommandObject(command="goal", args="1000 usd"),
        cast(RecordsRepository, repository),
        cast(GoalService, goal_service),
        AppConfig(),
    )

    repository.set_goal.assert_awaited_once_with(-100, Decimal("1000"), "USD", 7)


@pytest.mark.asyncio
async def test_goal_set_rejects_non_positive_amount() -> None:
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        chat=SimpleNamespace(id=-100),
        answer=AsyncMock(),
    )
    repository = SimpleNamespace(set_goal=AsyncMock())
    goal_service = SimpleNamespace(progress=AsyncMock(), render=AsyncMock())

    await goal_handler(
        cast(Message, message),
        CommandObject(command="goal", args="0"),
        cast(RecordsRepository, repository),
        cast(GoalService, goal_service),
        AppConfig(),
    )

    repository.set_goal.assert_not_awaited()
    message.answer.assert_awaited_once_with("Ціль має бути більшою за нуль.")


@pytest.mark.asyncio
async def test_goal_set_rejects_garbage_amount() -> None:
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        chat=SimpleNamespace(id=-100),
        answer=AsyncMock(),
    )
    repository = SimpleNamespace(set_goal=AsyncMock())
    goal_service = SimpleNamespace(progress=AsyncMock(), render=AsyncMock())

    await goal_handler(
        cast(Message, message),
        CommandObject(command="goal", args="not-a-number"),
        cast(RecordsRepository, repository),
        cast(GoalService, goal_service),
        AppConfig(),
    )

    repository.set_goal.assert_not_awaited()
    message.answer.assert_awaited_once_with("Формат: /goal 50000 [UAH]")


@pytest.mark.asyncio
async def test_goal_show_renders_progress() -> None:
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        chat=SimpleNamespace(id=-100),
        answer=AsyncMock(),
    )
    repository = SimpleNamespace(set_goal=AsyncMock())
    progress = GoalProgress(
        amount=Decimal("50000"),
        currency="UAH",
        actual=Decimal("10000"),
        forecast=Decimal("5000"),
        per_day_needed=Decimal("100"),
        status="on_track",
        period="month",
    )
    goal_service = SimpleNamespace(
        progress=AsyncMock(return_value=progress),
        render=lambda p: f"Ціль: {p.amount}",
    )

    await goal_handler(
        cast(Message, message),
        CommandObject(command="goal", args=None),
        cast(RecordsRepository, repository),
        cast(GoalService, goal_service),
        AppConfig(),
    )

    message.answer.assert_awaited_once_with("Ціль: 50000")


@pytest.mark.asyncio
async def test_goal_show_without_goal_prompts_to_set() -> None:
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        chat=SimpleNamespace(id=-100),
        answer=AsyncMock(),
    )
    repository = SimpleNamespace(set_goal=AsyncMock())
    goal_service = SimpleNamespace(
        progress=AsyncMock(return_value=None), render=AsyncMock()
    )

    await goal_handler(
        cast(Message, message),
        CommandObject(command="goal", args=None),
        cast(RecordsRepository, repository),
        cast(GoalService, goal_service),
        AppConfig(),
    )

    message.answer.assert_awaited_once_with("Ціль ще не задана. Встанови: /goal 50000")
