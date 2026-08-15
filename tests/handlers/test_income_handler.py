from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from income_stats.config import AppConfig
from income_stats.handlers.income_handler import income_message_handler, income_router
from income_stats.models import IncomeRecord
from income_stats.parsers import IncomeParseError


class UnhashableAwaitable:
    __hash__ = None  # pyright: ignore[reportAssignmentType]

    def __init__(self, awaited: Mock) -> None:
        self.awaited = awaited

    def __await__(self):  # type: ignore[no-untyped-def]
        async def complete() -> None:
            self.awaited()

        return complete().__await__()


def test_income_handler_is_importable() -> None:
    assert callable(income_message_handler)
    assert income_router.name == "income"


def record() -> IncomeRecord:
    return IncomeRecord(
        telegram_message_id=1,
        chat_id=-100,
        user_id=7,
        original_text="500",
        amount=Decimal("500"),
        currency="UAH",
        income_date=date(2026, 8, 1),
        updated_by=7,
    )


@pytest.mark.asyncio
async def test_income_handler_captures_and_replies_for_every_record() -> None:
    message = SimpleNamespace(
        text="500",
        from_user=SimpleNamespace(id=7, username="felix", is_bot=False),
        chat=SimpleNamespace(id=-100),
        message_id=10,
        reply=AsyncMock(),
        answer=AsyncMock(),
    )
    income = SimpleNamespace(capture=AsyncMock(return_value=[record(), record()]))
    admin = SimpleNamespace(status=AsyncMock(return_value=True))
    analytics = SimpleNamespace(fun_summary=Mock(return_value="fun"))
    await income_message_handler(message, income, admin, analytics, AppConfig())
    income.capture.assert_awaited_once()
    assert message.reply.await_count == 2


@pytest.mark.asyncio
async def test_income_handler_audits_each_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bound_logger = Mock()
    bind = Mock(return_value=bound_logger)
    monkeypatch.setattr("income_stats.handlers.income_handler.logger.bind", bind)
    message = SimpleNamespace(
        text="500",
        from_user=SimpleNamespace(id=7, username="felix", is_bot=False),
        chat=SimpleNamespace(id=-100),
        message_id=10,
        reply=AsyncMock(),
        answer=AsyncMock(),
    )
    audit_record = record()

    await income_message_handler(
        message,
        SimpleNamespace(capture=AsyncMock(return_value=[audit_record])),
        SimpleNamespace(status=AsyncMock(return_value=True)),
        SimpleNamespace(fun_summary=Mock(return_value="fun")),
        AppConfig(),
    )

    bind.assert_called_once_with(
        record_id=audit_record.id,
        chat_id=-100,
        user_id=7,
        amount="500.00",
        currency="UAH",
    )
    bound_logger.info.assert_called_once_with("Income recorded")


@pytest.mark.asyncio
async def test_income_handler_accepts_unhashable_aiogram_awaitables() -> None:
    awaited = Mock()
    reply = Mock(side_effect=lambda *args, **kwargs: UnhashableAwaitable(awaited))
    message = SimpleNamespace(
        text="500",
        from_user=SimpleNamespace(id=7, username="felix", is_bot=False),
        chat=SimpleNamespace(id=-100),
        message_id=10,
        reply=reply,
        answer=AsyncMock(),
    )
    income = SimpleNamespace(capture=AsyncMock(return_value=[record(), record()]))

    await income_message_handler(
        message,
        income,
        SimpleNamespace(status=AsyncMock(return_value=True)),
        SimpleNamespace(fun_summary=Mock(return_value="fun")),
        AppConfig(),
    )

    assert reply.call_count == 2
    assert awaited.call_count == 2


@pytest.mark.asyncio
async def test_income_handler_never_captures_menu_or_commands() -> None:
    income = SimpleNamespace(capture=AsyncMock())
    for text in ("📊 Аналітика", "/status"):
        message = SimpleNamespace(
            text=text,
            from_user=SimpleNamespace(id=7, username=None, is_bot=False),
            chat=SimpleNamespace(id=-100),
            message_id=10,
            reply=AsyncMock(),
            answer=AsyncMock(),
        )
        await income_message_handler(
            message,
            income,
            SimpleNamespace(status=AsyncMock()),
            SimpleNamespace(fun_summary=Mock()),
            AppConfig(),
        )
    income.capture.assert_not_awaited()


@pytest.mark.asyncio
async def test_income_handler_reports_invalid_date() -> None:
    message = SimpleNamespace(
        text="отримав 500 грн 32.13",
        from_user=SimpleNamespace(id=7, username="felix", is_bot=False),
        chat=SimpleNamespace(id=-100),
        message_id=10,
        reply=AsyncMock(),
        answer=AsyncMock(),
    )
    income = SimpleNamespace(
        capture=AsyncMock(side_effect=IncomeParseError("invalid date"))
    )

    await income_message_handler(
        message,
        income,
        SimpleNamespace(status=AsyncMock(return_value=True)),
        SimpleNamespace(fun_summary=Mock()),
        AppConfig(),
    )

    message.answer.assert_awaited_once()
    message.reply.assert_not_awaited()
