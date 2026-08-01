from pathlib import Path

import pytest
from loguru import logger

from income_stats.bot.application import build_dispatcher, run_bot
from income_stats.config import AppConfig, StorageConfig
from income_stats.services import (
    AdminService,
    AnalyticsService,
    IncomeService,
    RecordsService,
)
from income_stats.utils.logging import configure_logging


def test_dispatcher_contains_injected_services(tmp_path: Path) -> None:
    config = AppConfig(
        storage=StorageConfig(
            records_file=tmp_path / "records.csv",
            notes_file=tmp_path / "notes.csv",
            chat_settings_file=tmp_path / "chat-settings.csv",
            export_directory=tmp_path / "exports",
        )
    )

    dispatcher = build_dispatcher(config)

    assert isinstance(dispatcher["income_service"], IncomeService)
    assert isinstance(dispatcher["records_service"], RecordsService)
    assert isinstance(dispatcher["analytics_service"], AnalyticsService)
    assert isinstance(dispatcher["admin_service"], AdminService)
    assert dispatcher["app_config"] is config


def test_logging_configuration_is_idempotent(tmp_path: Path) -> None:
    log_file = tmp_path / "bot.log"
    configure_logging("INFO", log_file)
    configure_logging("INFO", log_file)

    logger.info("one-marker")
    logger.complete()

    assert log_file.read_text(encoding="utf-8").count("one-marker") == 1


async def test_run_bot_rejects_missing_token_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")

    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN is required"):
        await run_bot(AppConfig())
