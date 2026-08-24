"""/goal command: set or show the monthly and yearly income goals."""

import asyncio
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from income_stats.config import AppConfig
from income_stats.models import GoalPeriod
from income_stats.models.domain import normalize_currency
from income_stats.repositories import RecordsRepository
from income_stats.services import GoalService

goal_router = Router(name="goal")

_PERIOD_ALIASES: dict[str, GoalPeriod] = {
    "month": "month",
    "місяць": "month",
    "рік": "year",
    "year": "year",
}


@goal_router.message(Command("goal"))
async def goal_handler(
    message: Message,
    command: CommandObject,
    repository: RecordsRepository,
    goal_service: GoalService,
    app_config: AppConfig,
) -> None:
    today = datetime.now(ZoneInfo(app_config.bot.timezone)).date()
    if command.args:
        parts = command.args.split()
        period: GoalPeriod = "month"
        if parts and parts[0].casefold() in _PERIOD_ALIASES:
            period = _PERIOD_ALIASES[parts.pop(0).casefold()]
        try:
            amount = Decimal(parts[0].replace(",", "."))
        except (InvalidOperation, IndexError):
            await message.answer("Формат: /goal [month|year] 50000 [UAH]")
            return
        currency_raw = (
            parts[1] if len(parts) > 1 else app_config.income.default_currency
        )
        if amount <= 0:
            await message.answer("Ціль має бути більшою за нуль.")
            return
        try:
            currency = normalize_currency(currency_raw)
        except ValueError:
            await message.answer("Валюта — це код з трьох літер, напр. UAH.")
            return
        user = message.from_user
        await repository.set_goal(
            message.chat.id, amount, currency, user.id if user else 0, period=period
        )
        label = "місяць" if period == "month" else "рік"
        await message.answer(f"🎯 Ціль встановлено: {amount:,.0f} {currency}/{label}")
        return
    month_progress, year_progress = await asyncio.gather(
        goal_service.progress(message.chat.id, today=today, period="month"),
        goal_service.progress(message.chat.id, today=today, period="year"),
    )
    lines = [
        goal_service.render(progress)
        for progress in (month_progress, year_progress)
        if progress is not None
    ]
    await message.answer(
        "\n\n".join(lines)
        if lines
        else "Ціль ще не задана. Встанови: /goal month 50000"
    )
