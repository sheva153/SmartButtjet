from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram import Bot
from aiogram.methods import EditMessageText, SendMessage

from income_stats.bot.notifications import SilentNotifications


@pytest.mark.asyncio
async def test_send_methods_are_silenced_by_default() -> None:
    middleware = SilentNotifications()
    make_request = AsyncMock(return_value="ok")
    method = SendMessage(chat_id=-100, text="hi")

    result = await middleware(make_request, cast(Bot, Mock()), method)

    assert result == "ok"
    assert make_request.await_args is not None
    forwarded = make_request.await_args.args[1]
    assert forwarded.disable_notification is True


@pytest.mark.asyncio
async def test_explicit_notification_choice_is_preserved() -> None:
    middleware = SilentNotifications()
    make_request = AsyncMock(return_value="ok")
    method = SendMessage(chat_id=-100, text="hi", disable_notification=False)

    await middleware(make_request, cast(Bot, Mock()), method)

    assert make_request.await_args is not None
    forwarded = make_request.await_args.args[1]
    assert forwarded.disable_notification is False


@pytest.mark.asyncio
async def test_methods_without_the_field_pass_through() -> None:
    middleware = SilentNotifications()
    make_request = AsyncMock(return_value="ok")
    method = EditMessageText(chat_id=-100, message_id=1, text="edit")

    await middleware(make_request, cast(Bot, Mock()), method)

    assert make_request.await_args is not None
    forwarded = make_request.await_args.args[1]
    assert forwarded is method
