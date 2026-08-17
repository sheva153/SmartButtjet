"""/goal command: set or show the monthly income goal."""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from income_stats.config import AppConfig
from income_stats.repositories import RecordsRepository
from income_stats.services import GoalService

goal_router = Router(name="goal")


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
        try:
            amount = Decimal(parts[0].replace(",", "."))
        except (InvalidOperation, IndexError):
            await message.answer("Формат: /goal 50000 [UAH]")
            return
        currency = (
            parts[1].upper() if len(parts) > 1 else app_config.income.default_currency
        )
        if amount <= 0:
            await message.answer("Ціль має бути більшою за нуль.")
            return
        user = message.from_user
        await repository.set_goal(
            message.chat.id, amount, currency, user.id if user else 0
        )
        await message.answer(f"🎯 Ціль встановлено: {amount:,.0f} {currency}/місяць")
        return
    progress = await goal_service.progress(message.chat.id, today=today)
    if progress is None:
        await message.answer("Ціль ще не задана. Встанови: /goal 50000")
        return
    await message.answer(goal_service.render(progress))
