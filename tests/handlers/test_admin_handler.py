from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import TelegramMethod
from aiogram.types import Message

from income_stats.config import AppConfig
from income_stats.handlers.admin_handler import admin_router, is_telegram_admin


def test_admin_helper_is_importable() -> None:
    assert callable(is_telegram_admin)
    assert admin_router.name == "admin"


@pytest.mark.asyncio
async def test_admin_lookup_catches_only_telegram_errors() -> None:
    bot = SimpleNamespace(
        get_chat_member=AsyncMock(
            side_effect=TelegramNetworkError(
                method=cast(TelegramMethod[Any], object()), message="offline"
            )
        )
    )
    message = SimpleNamespace(bot=bot, chat=SimpleNamespace(id=-100))
    assert await is_telegram_admin(cast(Message, message), 7, AppConfig()) is False


@pytest.mark.asyncio
async def test_admin_lookup_does_not_hide_programming_errors() -> None:
    bot = SimpleNamespace(get_chat_member=AsyncMock(side_effect=RuntimeError("bug")))
    message = SimpleNamespace(bot=bot, chat=SimpleNamespace(id=-100))
    with pytest.raises(RuntimeError, match="bug"):
        await is_telegram_admin(cast(Message, message), 7, AppConfig())
