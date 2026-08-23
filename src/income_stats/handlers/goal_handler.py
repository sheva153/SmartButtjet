"""/goal command: set or show the monthly income goal."""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from income_stats.config import AppConfig
from income_stats.models import GoalPeriod
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
        idx = 0
        if parts and parts[0].casefold() in _PERIOD_ALIASES:
            period = _PERIOD_ALIASES[parts[0].casefold()]
            idx = 1
        try:
            amount = Decimal(parts[idx].replace(",", "."))
        except (InvalidOperation, IndexError):
            await message.answer("Формат: /goal [month|year] 50000 [UAH]")
            return
        currency = (
            parts[idx + 1].upper()
            if len(parts) > idx + 1
            else app_config.income.default_currency
        )
        if amount <= 0:
            await message.answer("Ціль має бути більшою за нуль.")
            return
        if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
            await message.answer("Валюта — це код з трьох літер, напр. UAH.")
            return
        user = message.from_user
        await repository.set_goal(
            message.chat.id, amount, currency, user.id if user else 0, period=period
        )
        label = "місяць" if period == "month" else "рік"
        await message.answer(f"🎯 Ціль встановлено: {amount:,.0f} {currency}/{label}")
        return
    lines = []
    for period in ("month", "year"):
        p = await goal_service.progress(message.chat.id, today=today, period=period)
        if p is not None:
            lines.append(goal_service.render(p))
    await message.answer(
        "\n\n".join(lines)
        if lines
        else "Ціль ще не задана. Встанови: /goal month 50000"
    )
