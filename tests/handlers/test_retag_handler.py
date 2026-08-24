from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Message

from income_stats.config import AppConfig, IncomeConfig
from income_stats.handlers.retag_handler import retag_handler, retag_router
from income_stats.models import IncomeRecord
from income_stats.repositories import RecordsRepository
from income_stats.repositories.records_repository import RetagResult


def _record() -> IncomeRecord:
    return IncomeRecord(
        telegram_message_id=1,
        chat_id=-100,
        user_id=1,
        updated_by=1,
        original_text="оренда 5000",
        amount=Decimal("5000"),
        currency="UAH",
        tags=[],
        type="expense",
        income_date=date(2026, 8, 1),
    )


def test_retag_handler_is_importable() -> None:
    assert callable(retag_handler)
    assert retag_router.name == "retag"


def test_retag_router_is_registered_before_income_catch_all() -> None:
    from income_stats.handlers import income_router, routers

    # income_router's catch-all F.text handler returns (not SkipHandler) on
    # `/`-prefixed text, which aiogram treats as handled — so a command router
    # registered after it would never see /retag.
    assert routers.index(retag_router) < routers.index(income_router)


@pytest.mark.asyncio
async def test_retag_admin_backfills_scoped_to_chat_and_reports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "income_stats.handlers.retag_handler.is_telegram_admin",
        AsyncMock(return_value=True),
    )
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        chat=SimpleNamespace(id=-100),
        answer=AsyncMock(),
    )
    record = _record()
    repository = SimpleNamespace(
        list_tags=AsyncMock(return_value={"rent": ["оренда"]}),
        retag_records=AsyncMock(
            return_value=RetagResult(changed=1, total=3, deltas=[(record, ["rent"])])
        ),
    )
    config = AppConfig(income=IncomeConfig(tags={"card": ["картка"]}))

    await retag_handler(
        cast(Message, message),
        cast(RecordsRepository, repository),
        config,
    )

    repository.list_tags.assert_awaited_once()
    repository.retag_records.assert_awaited_once()
    assert repository.retag_records.await_args.kwargs["chat_id"] == -100
    sent = message.answer.await_args.args[0]
    assert "Оновлено 1 з 3" in sent
    assert "rent" in sent


@pytest.mark.asyncio
async def test_retag_rejects_non_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "income_stats.handlers.retag_handler.is_telegram_admin",
        AsyncMock(return_value=False),
    )
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        chat=SimpleNamespace(id=-100),
        answer=AsyncMock(),
    )
    repository = SimpleNamespace(list_tags=AsyncMock(), retag_records=AsyncMock())
    config = AppConfig()

    await retag_handler(
        cast(Message, message),
        cast(RecordsRepository, repository),
        config,
    )

    repository.retag_records.assert_not_awaited()
    message.answer.assert_awaited_once_with("Ця команда лише для адміністраторів.")


@pytest.mark.asyncio
async def test_retag_rejects_message_without_user() -> None:
    message = SimpleNamespace(
        from_user=None,
        chat=SimpleNamespace(id=-100),
        answer=AsyncMock(),
    )
    repository = SimpleNamespace(list_tags=AsyncMock(), retag_records=AsyncMock())
    config = AppConfig()

    await retag_handler(
        cast(Message, message),
        cast(RecordsRepository, repository),
        config,
    )

    repository.retag_records.assert_not_awaited()
    message.answer.assert_awaited_once_with("Ця команда лише для адміністраторів.")
