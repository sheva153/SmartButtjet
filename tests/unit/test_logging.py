from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
from loguru import logger

from income_stats.bot import application as application_module
from income_stats.bot.application import BOT_COMMANDS, build_dispatcher, run_bot
from income_stats.config import AppConfig, StorageConfig
from income_stats.services import (
    AdminService,
    AnalyticsService,
    IncomeService,
    RecordsService,
)
from income_stats.utils import logging as logging_module
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


def test_dispatcher_can_be_built_repeatedly(tmp_path: Path) -> None:
    first_config = AppConfig(storage=StorageConfig(export_directory=tmp_path / "first"))
    second_config = AppConfig(
        storage=StorageConfig(export_directory=tmp_path / "second")
    )

    first = build_dispatcher(first_config)
    second = build_dispatcher(second_config)

    assert first is not second
    assert first["app_config"] is first_config
    assert second["app_config"] is second_config
    expected_names = [router.name for router in application_module.routers]
    assert [router.name for router in first.sub_routers] == expected_names
    assert [router.name for router in second.sub_routers] == expected_names
    assert all(router.parent_router is first for router in first.sub_routers)
    assert all(router.parent_router is second for router in second.sub_routers)
    assert all(router.parent_router is None for router in application_module.routers)


def test_logging_configuration_is_idempotent(tmp_path: Path) -> None:
    log_file = tmp_path / "bot.log"
    configure_logging("INFO", log_file)
    configure_logging("INFO", log_file)

    logger.info("one-marker")
    logger.complete()

    assert log_file.read_text(encoding="utf-8").count("one-marker") == 1


def test_logging_uses_privacy_safe_rotating_sink_options(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add = Mock(wraps=logger.add)
    monkeypatch.setattr(logger, "add", add)

    log_file = tmp_path / "bot.log"
    configure_logging("WARNING", log_file)

    assert add.call_count == 2
    console_call, file_call = add.call_args_list
    assert console_call.args == (logging_module.sys.stderr,)
    assert console_call.kwargs == {
        "level": "WARNING",
        "backtrace": False,
        "diagnose": False,
    }
    assert file_call.args == (log_file,)
    assert file_call.kwargs == {
        "level": "DEBUG",
        "rotation": "10 MB",
        "retention": "14 days",
        "compression": "zip",
        "backtrace": False,
        "diagnose": False,
    }


def test_logging_reconfiguration_rolls_back_partial_sinks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stable_log = tmp_path / "stable.log"
    configure_logging("INFO", stable_log)
    stable_sink_ids = tuple(logging_module._SINK_IDS)
    original_add = cast(Callable[..., int], logger.add)
    partial_sink_ids: list[int] = []
    add_calls = 0

    def fail_second_add(
        sink: object,
        *args: object,
        **kwargs: object,
    ) -> int:
        nonlocal add_calls
        add_calls += 1
        if add_calls == 2:
            raise RuntimeError("file sink failed")
        sink_id = original_add(sink, *args, **cast(dict[str, Any], kwargs))
        partial_sink_ids.append(sink_id)
        return sink_id

    with monkeypatch.context() as patch:
        patch.setattr(logger, "add", fail_second_add)
        with pytest.raises(RuntimeError, match="file sink failed"):
            configure_logging("DEBUG", tmp_path / "replacement.log")

    assert tuple(logging_module._SINK_IDS) == stable_sink_ids
    assert len(partial_sink_ids) == 1
    with pytest.raises(ValueError, match="no existing handler"):
        logger.remove(partial_sink_ids[0])

    logger.info("still-stable")
    logger.complete()
    assert stable_log.read_text(encoding="utf-8").count("still-stable") == 1


async def test_run_bot_rejects_missing_token_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")

    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN is required"):
        await run_bot(AppConfig())


@pytest.mark.parametrize(
    "polling_error",
    [
        pytest.param(None, id="success"),
        pytest.param(RuntimeError("polling failed"), id="polling-failure"),
    ],
)
async def test_run_bot_registers_commands_polls_and_closes_session(
    polling_error: RuntimeError | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "TELEGRAM_BOT_TOKEN",
        "  123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi  ",
    )
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    session = SimpleNamespace(close=AsyncMock())
    bot = SimpleNamespace(session=session, set_my_commands=AsyncMock())
    bot_factory = Mock(return_value=bot)
    dispatcher = SimpleNamespace(
        start_polling=AsyncMock(side_effect=polling_error),
    )
    dispatcher_factory = Mock(return_value=dispatcher)
    logging_configurator = Mock()
    monkeypatch.setattr(application_module, "Bot", bot_factory)
    monkeypatch.setattr(application_module, "build_dispatcher", dispatcher_factory)
    monkeypatch.setattr(
        application_module,
        "configure_logging",
        logging_configurator,
    )
    config = AppConfig()

    if polling_error is None:
        await run_bot(config)
    else:
        with pytest.raises(RuntimeError, match="polling failed"):
            await run_bot(config)

    logging_configurator.assert_called_once_with("WARNING")
    bot_factory.assert_called_once_with(
        token="123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
    )
    bot.set_my_commands.assert_awaited_once_with(list(BOT_COMMANDS))
    dispatcher_factory.assert_called_once_with(config)
    dispatcher.start_polling.assert_awaited_once_with(
        bot,
        close_bot_session=False,
    )
    session.close.assert_awaited_once()
