"""Analytics, dual-chart, and export Telegram handlers."""

import asyncio
from pathlib import Path
from typing import cast

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, Message
from loguru import logger

from income_stats.bot.ui import (
    MENU_ANALYTICS,
    MENU_CHART,
    ChartPeriod,
    chart_period_keyboard,
)
from income_stats.config import AppConfig
from income_stats.handlers.admin_handler import is_telegram_admin
from income_stats.models import CHART_PERIODS, Period
from income_stats.services import AnalyticsService
from income_stats.utils import temporary_artifacts

analytics_router = Router(name="analytics")


async def send_chart(
    message: Message, service: AnalyticsService, period: Period | None = None
) -> None:
    artifacts = await service.build_chart_artifacts(message.chat.id, period)
    paths = tuple(path for path in (artifacts.png, artifacts.html) if path is not None)
    with temporary_artifacts(*paths):
        deliveries = []
        if artifacts.png is not None:
            deliveries.append(_send_photo(message, artifacts.png))
        if artifacts.html is not None:
            deliveries.append(_send_document(message, artifacts.html))
        results = await asyncio.gather(*deliveries, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                logger.bind(chat_id=message.chat.id).opt(exception=result).error(
                    "Chart artifact delivery failed"
                )


async def _send_photo(message: Message, path: Path) -> None:
    await message.answer_photo(FSInputFile(path))


async def _send_document(message: Message, path: Path) -> None:
    await message.answer_document(FSInputFile(path))


@analytics_router.message(Command("stats"))
@analytics_router.message(F.text == MENU_ANALYTICS)
async def stats_handler(message: Message, analytics_service: AnalyticsService) -> None:
    user = message.from_user
    logger.bind(
        chat_id=message.chat.id,
        user_id=user.id if user is not None else None,
    ).info("Analytics requested")
    await message.answer(await analytics_service.summary(message.chat.id))


@analytics_router.message(Command("chart"))
@analytics_router.message(F.text == MENU_CHART)
async def chart_handler(message: Message) -> None:
    user = message.from_user
    logger.bind(
        chat_id=message.chat.id,
        user_id=user.id if user is not None else None,
    ).info("Chart period requested")
    await message.answer(
        "Оберіть період для діаграми:", reply_markup=chart_period_keyboard()
    )


@analytics_router.callback_query(ChartPeriod.filter())
async def chart_period_callback(
    query: CallbackQuery,
    callback_data: ChartPeriod,
    analytics_service: AnalyticsService,
) -> None:
    await query.answer()
    if callback_data.period not in CHART_PERIODS or not isinstance(
        query.message, Message
    ):
        return
    period = cast(Period, callback_data.period)
    logger.bind(chat_id=query.message.chat.id, period=period).info("Chart requested")
    try:
        await send_chart(query.message, analytics_service, period)
    except ValueError:
        await query.message.answer("Немає даних для діаграми.")
    except Exception:
        logger.bind(chat_id=query.message.chat.id).exception("Chart build failed")
        await query.message.answer("Не вдалося побудувати діаграму.")


@analytics_router.message(Command("export"))
async def export_handler(
    message: Message,
    analytics_service: AnalyticsService,
    app_config: AppConfig,
) -> None:
    user = message.from_user
    if user is None or not await is_telegram_admin(message, user.id, app_config):
        await message.answer("Ця команда лише для адміністраторів.")
        return
    archive = await analytics_service.build_export(message.chat.id)
    with temporary_artifacts(archive):
        await message.answer_document(FSInputFile(archive))
