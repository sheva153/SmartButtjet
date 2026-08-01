from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from income_stats.bot.ui import RecordAction
from income_stats.config import AppConfig, PermissionsConfig
from income_stats.handlers import routers
from income_stats.handlers.records_handler import (
    confirm_delete_callback,
    edit_value_handler,
    handle_menu_during_interaction,
    notes_callback,
    open_callback,
    records_page_callback,
    records_router,
)
from income_stats.models import IncomeRecord
from income_stats.services import AnalyticsService, RecordPage, RecordsService


def test_records_menu_helper_is_importable() -> None:
    assert callable(handle_menu_during_interaction)
    assert records_router.name == "records"


def make_record(*, chat_id: int = -100, user_id: int = 7) -> IncomeRecord:
    return IncomeRecord(
        telegram_message_id=1,
        chat_id=chat_id,
        user_id=user_id,
        original_text="500",
        amount=Decimal("500"),
        currency="UAH",
        income_date=date(2026, 7, 29),
        updated_by=user_id,
    )


def make_query(*, chat_id: int = -100, user_id: int = 7) -> SimpleNamespace:
    message = Mock(spec=Message)
    message.chat = SimpleNamespace(id=chat_id)
    message.answer = AsyncMock()
    message.edit_text = AsyncMock()
    message.edit_reply_markup = AsyncMock()
    return SimpleNamespace(
        message=message,
        from_user=SimpleNamespace(id=user_id),
        answer=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_menu_during_edit_clears_state_and_releases_lock() -> None:
    message = SimpleNamespace(
        text="📊 Аналітика",
        from_user=SimpleNamespace(id=7),
        chat=SimpleNamespace(id=-100),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={"record_id": "one"}), clear=AsyncMock()
    )
    records = SimpleNamespace(navigate_away=Mock())
    analytics = SimpleNamespace(summary=AsyncMock(return_value="summary"))
    assert await handle_menu_during_interaction(
        cast(Message, message),
        cast(FSMContext, state),
        cast(RecordsService, records),
        cast(AnalyticsService, analytics),
    )
    records.navigate_away.assert_called_once_with("one", 7)
    state.clear.assert_awaited_once()
    message.answer.assert_awaited_once_with("summary")


@pytest.mark.asyncio
async def test_chart_menu_during_edit_reports_empty_data() -> None:
    message = SimpleNamespace(
        text="📈 Діаграма",
        from_user=SimpleNamespace(id=7),
        chat=SimpleNamespace(id=-100),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={"record_id": "one"}),
        clear=AsyncMock(),
    )
    records = SimpleNamespace(navigate_away=Mock())
    analytics = SimpleNamespace(
        build_chart_artifacts=AsyncMock(side_effect=ValueError("empty"))
    )

    assert await handle_menu_during_interaction(
        cast(Message, message),
        cast(FSMContext, state),
        cast(RecordsService, records),
        cast(AnalyticsService, analytics),
    )

    message.answer.assert_awaited_once_with("Немає даних для діаграми.")


@pytest.mark.asyncio
async def test_invalid_edit_keeps_state_and_lock() -> None:
    message = SimpleNamespace(
        text="bad",
        from_user=SimpleNamespace(id=7, username="felix"),
        chat=SimpleNamespace(id=-100),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={"record_id": "one", "field": "amount"}),
        clear=AsyncMock(),
    )
    records = SimpleNamespace(update_field=AsyncMock(side_effect=ValueError("bad")))
    await edit_value_handler(message, state, records, SimpleNamespace())
    state.clear.assert_not_awaited()


def test_state_navigation_router_precedes_ordinary_routes() -> None:
    callbacks = [
        handler.callback.__name__
        for handler in records_router.observers["message"].handlers
    ]

    assert routers[0] is records_router
    assert callbacks.index("interaction_menu_handler") < callbacks.index(
        "records_handler"
    )
    assert callbacks.index("interaction_menu_handler") < callbacks.index(
        "edit_value_handler"
    )


@pytest.mark.asyncio
async def test_open_callback_rejects_record_from_another_chat() -> None:
    query = make_query(chat_id=-100)
    service = SimpleNamespace(
        get_record=AsyncMock(return_value=make_record(chat_id=-200))
    )

    await open_callback(
        cast(CallbackQuery, query),
        RecordAction(action="open", record_id="record"),
        cast(RecordsService, service),
    )

    query.answer.assert_awaited_once_with("Запис не знайдено.", show_alert=True)
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirm_delete_rechecks_permission() -> None:
    query = make_query(user_id=8)
    record = make_record(user_id=7)
    service = SimpleNamespace(
        get_record=AsyncMock(return_value=record),
        delete=AsyncMock(),
    )
    config = AppConfig(
        permissions=PermissionsConfig(
            author_can_delete=True,
            admin_can_delete=False,
        )
    )

    await confirm_delete_callback(
        cast(CallbackQuery, query),
        RecordAction(action="confirm_delete", record_id=record.id),
        cast(RecordsService, service),
        config,
    )

    service.delete.assert_not_awaited()
    query.answer.assert_awaited_once_with("Недостатньо прав.", show_alert=True)


@pytest.mark.asyncio
async def test_notes_and_pagination_callbacks_answer() -> None:
    query = make_query()
    record = make_record()
    service = SimpleNamespace(
        get_record=AsyncMock(return_value=record),
        list_notes=AsyncMock(return_value=[]),
        page=AsyncMock(return_value=RecordPage([record], 0, 1)),
    )

    await notes_callback(
        cast(CallbackQuery, query),
        RecordAction(action="notes", record_id=record.id),
        cast(RecordsService, service),
    )
    await records_page_callback(
        cast(CallbackQuery, query),
        RecordAction(action="records", record_id="-", value="0"),
        cast(RecordsService, service),
    )

    assert query.answer.await_count == 2
    query.message.edit_text.assert_awaited_once()
    query.message.answer.assert_awaited_once()
