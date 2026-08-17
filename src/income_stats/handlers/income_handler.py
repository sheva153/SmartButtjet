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
from income_stats.models import IncomeRecord
from income_stats.parsers import IncomeParseError
from income_stats.services import (
    AdminService,
    AnalyticsService,
    GoalService,
    IncomeService,
)

income_router = Router(name="income")


@income_router.message(default_state, F.text)
async def income_message_handler(
    message: Message,
    income_service: IncomeService,
    admin_service: AdminService,
    analytics_service: AnalyticsService,
    app_config: AppConfig,
    goal_service: GoalService,
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
    today = datetime.now(ZoneInfo(app_config.bot.timezone)).date()
    try:
        records = await income_service.capture(
            text=text,
            telegram_message_id=message.message_id,
            chat_id=message.chat.id,
            user_id=user.id,
            username=user.username or "",
            today=today,
        )
    except IncomeParseError:
        await message.answer("Некоректна дата. Виправ повідомлення та надішли ще раз.")
        return

    for record in records:
        logger.bind(
            record_id=record.id,
            chat_id=record.chat_id,
            user_id=record.user_id,
            amount=f"{record.amount:.2f}",
            currency=record.currency,
        ).info("Income recorded")

    goal_line = await goal_service.after_save_line(message.chat.id, today=today)

    async def deliver_reply(record: IncomeRecord) -> None:
        extra = goal_line if record.type == "income" else ""
        body = format_success(record, analytics_service.fun_summary(record))
        if extra:
            body = f"{body}\n\n{extra}"
        await message.reply(body, reply_markup=success_keyboard(record))

    results = await asyncio.gather(
        *(deliver_reply(record) for record in records),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, BaseException):
            logger.bind(chat_id=message.chat.id, user_id=user.id).opt(
                exception=result
            ).error("Income reply delivery failed")
