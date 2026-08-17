"""Command-line entry point for the income tracker."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import get_args
from zoneinfo import ZoneInfo

from income_stats.bot.application import run_bot
from income_stats.config import AppConfig, load_config
from income_stats.models import Period
from income_stats.parsers import parse_income_message
from income_stats.repositories import CsvRecordsRepository
from income_stats.services import AnalyticsService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Telegram income tracker")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="YAML configuration path",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("bot", help="Run Telegram polling")

    parse_command = commands.add_parser("parse", help="Parse an income message")
    parse_command.add_argument("message")

    commands.add_parser("check-config", help="Validate configuration")
    commands.add_parser("check-storage", help="Validate CSV schemas")

    analytics = commands.add_parser("analytics", help="Print income summary")
    analytics.add_argument("--chat-id", type=int, required=True)
    analytics.add_argument(
        "--period",
        choices=get_args(Period),
        default=None,
    )
    analytics.add_argument("--from", dest="date_from", type=str, default=None)
    analytics.add_argument("--to", dest="date_to", type=str, default=None)

    export = commands.add_parser("export", help="Build a scoped CSV export")
    export.add_argument("--chat-id", type=int, required=True)
    return parser


def _analytics_service(config: AppConfig) -> AnalyticsService:
    repository = CsvRecordsRepository(config.storage)
    return AnalyticsService(
        repository,
        config.analytics,
        config.storage,
        timezone=config.bot.timezone,
        fun_summary=config.fun_summary,
    )


def _check_config(config: AppConfig) -> None:
    ZoneInfo(config.bot.timezone)
    print("Configuration is valid.")


def _check_storage(config: AppConfig) -> None:
    repository = CsvRecordsRepository(config.storage)
    records = repository.read_records_sync()
    notes = repository.read_notes_sync()
    settings = repository.read_chat_settings_sync()
    print(
        "storage=ok "
        f"records={len(records)} notes={len(notes)} chat_settings={len(settings)}"
    )


def _parse(config: AppConfig, message: str) -> None:
    today = datetime.now(ZoneInfo(config.bot.timezone)).date()
    parsed = parse_income_message(message, config.income, today=today)
    payload = [item.model_dump(mode="json") for item in parsed]
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


async def _analytics(
    config: AppConfig,
    chat_id: int,
    period: Period | None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> None:
    date_range = None
    if date_from and date_to:
        date_range = (date.fromisoformat(date_from), date.fromisoformat(date_to))
    print(
        await _analytics_service(config).summary(chat_id, period, date_range=date_range)
    )


async def _export(config: AppConfig, chat_id: int) -> None:
    print(await _analytics_service(config).build_export(chat_id))


def main(arguments: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(arguments)
    config = load_config(args.config)

    if args.command == "bot":
        asyncio.run(run_bot(config))
    elif args.command == "parse":
        _parse(config, args.message)
    elif args.command == "check-config":
        _check_config(config)
    elif args.command == "check-storage":
        _check_storage(config)
    elif args.command == "analytics":
        asyncio.run(
            _analytics(config, args.chat_id, args.period, args.date_from, args.date_to)
        )
    elif args.command == "export":
        asyncio.run(_export(config, args.chat_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
