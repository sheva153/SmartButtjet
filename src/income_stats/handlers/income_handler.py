"""Thin handler for ordinary income messages."""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.types import Message
from loguru import logger

from income_stats.bot.ui import MENU_LABELS, format_success, success_keyboard
from income_stats.config import AppConfig
from income_stats.parsers import IncomeParseError
from income_stats.services import AdminService, AnalyticsService, IncomeService

income_router = Router(name="income")


@income_router.message(default_state, F.text)
async def income_message_handler(
    message: Message,
    income_service: IncomeService,
    admin_service: AdminService,
    analytics_service: AnalyticsService,
    app_config: AppConfig,
    state: FSMContext | None = None,
) -> None:
    text = message.text or ""
    user = message.from_user
    if (
        not text
        or user is None
        or user.is_bot
        or text.startswith("/")
        or text in MENU_LABELS
        or (state is not None and await state.get_state() is not None)
    ):
        return
    if (
        app_config.bot.allowed_chat_ids
        and message.chat.id not in app_config.bot.allowed_chat_ids
    ):
        return
    if not await admin_service.status(message.chat.id):
        return
    try:
        records = await income_service.capture(
            text=text,
            telegram_message_id=message.message_id,
            chat_id=message.chat.id,
            user_id=user.id,
            username=user.username or "",
            today=datetime.now(ZoneInfo(app_config.bot.timezone)).date(),
        )
    except IncomeParseError:
        await message.answer("Некоректна дата. Виправ повідомлення та надішли ще раз.")
        return
    results = await asyncio.gather(
        *(
            message.reply(
                format_success(record, analytics_service.fun_summary(record)),
                reply_markup=success_keyboard(record),
            )
            for record in records
        ),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, BaseException):
            logger.bind(chat_id=message.chat.id, user_id=user.id).opt(
                exception=result
            ).error("Income reply delivery failed")
