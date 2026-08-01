from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from income_stats.handlers.records_handler import (
    edit_value_handler,
    handle_menu_during_interaction,
    records_router,
)
from income_stats.services import AnalyticsService, RecordsService


def test_records_menu_helper_is_importable() -> None:
    assert callable(handle_menu_during_interaction)
    assert records_router.name == "records"


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
