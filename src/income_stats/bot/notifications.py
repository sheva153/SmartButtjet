"""Outgoing-request middleware that silences Telegram notifications."""

from aiogram import Bot
from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.methods import TelegramMethod
from aiogram.methods.base import Response, TelegramType


class SilentNotifications(BaseRequestMiddleware):
    """Deliver every Telegram message without a notification sound.

    The bot reacts to ordinary group chatter, so pinging members for each
    recorded income (and every reply, chart, or error) would be noisy. Any
    outgoing method that supports ``disable_notification`` and has not set it
    explicitly is flipped to silent, so new send sites inherit this by default.
    """

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        if (
            "disable_notification" in type(method).model_fields
            and getattr(method, "disable_notification", None) is None
        ):
            method = method.model_copy(update={"disable_notification": True})
        return await make_request(bot, method)
