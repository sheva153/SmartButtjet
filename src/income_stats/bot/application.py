"""Telegram application composition and polling lifecycle."""

from copy import deepcopy

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from loguru import logger

from income_stats.config import AppConfig, Secrets
from income_stats.handlers import routers
from income_stats.repositories import CsvRecordsRepository
from income_stats.services import (
    AdminService,
    AnalyticsService,
    IncomeService,
    RecordsService,
)
from income_stats.utils.logging import configure_logging

BOT_COMMANDS = (
    BotCommand(command="start", description="Відкрити головне меню"),
    BotCommand(command="help", description="Показати довідку"),
    BotCommand(command="records", description="Показати останні записи"),
    BotCommand(command="stats", description="Показати статистику"),
    BotCommand(command="chart", description="Створити діаграму"),
    BotCommand(command="export", description="Експортувати записи"),
    BotCommand(command="status", description="Стан запису доходів"),
    BotCommand(command="turn_on", description="Увімкнути запис доходів"),
    BotCommand(command="turn_off", description="Вимкнути запис доходів"),
    BotCommand(command="cancel", description="Скасувати поточну дію"),
)


def build_dispatcher(config: AppConfig) -> Dispatcher:
    """Compose application services and expose them as aiogram workflow data."""
    repository = CsvRecordsRepository(config.storage)
    dispatcher = Dispatcher(
        income_service=IncomeService(repository, config.income),
        records_service=RecordsService(
            repository,
            edit_lock_seconds=config.permissions.edit_lock_seconds,
        ),
        analytics_service=AnalyticsService(
            repository,
            config.analytics,
            config.storage,
            timezone=config.bot.timezone,
            fun_summary=config.fun_summary,
        ),
        admin_service=AdminService(repository),
        app_config=config,
    )
    dispatcher.include_routers(*deepcopy(routers))
    return dispatcher


async def run_bot(config: AppConfig) -> None:
    """Validate runtime secrets and poll Telegram until shutdown."""
    secrets = Secrets()
    token = secrets.telegram_bot_token.strip()
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is required")

    configure_logging(secrets.log_level)
    bot = Bot(token=token)
    try:
        await bot.set_my_commands(list(BOT_COMMANDS))
        logger.info("Income bot started")
        await build_dispatcher(config).start_polling(
            bot,
            close_bot_session=False,
        )
    finally:
        await bot.session.close()
        logger.info("Income bot stopped")
