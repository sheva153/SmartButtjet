"""Analytics, dual-chart, and export Telegram handlers."""

import asyncio
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message
from loguru import logger

from income_stats.bot.ui import MENU_ANALYTICS, MENU_CHART
from income_stats.config import AppConfig
from income_stats.handlers.admin_handler import is_telegram_admin
from income_stats.services import AnalyticsService
from income_stats.utils import temporary_artifacts

analytics_router = Router(name="analytics")


async def send_chart(message: Message, service: AnalyticsService) -> None:
    artifacts = await service.build_chart_artifacts(message.chat.id)
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
async def chart_handler(message: Message, analytics_service: AnalyticsService) -> None:
    user = message.from_user
    logger.bind(
        chat_id=message.chat.id,
        user_id=user.id if user is not None else None,
    ).info("Chart requested")
    try:
        await send_chart(message, analytics_service)
    except ValueError:
        await message.answer("Немає даних для діаграми.")


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
