"""/import command: admin-only bulk CSV record import."""

import csv
import io
import json
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from loguru import logger

from income_stats.config import AppConfig
from income_stats.handlers.admin_handler import is_telegram_admin
from income_stats.models import IncomeRecord
from income_stats.repositories import RecordsRepository

import_router = Router(name="import")


def _has_import_caption(caption: str | None) -> bool:
    return bool(caption) and caption.startswith("/import")


def _record_from_row(
    row: dict[str, Any], *, chat_id: int, updated_by: int
) -> IncomeRecord:
    payload = dict(row)
    payload["categories"] = json.loads(payload.get("categories") or "[]")
    payload["tags"] = json.loads(payload.get("tags") or "[]")
    if not payload.get("type"):
        payload["type"] = "income"
    payload.pop("id", None)
    payload["chat_id"] = chat_id
    payload["updated_by"] = updated_by
    return IncomeRecord.model_validate(payload)


@import_router.message(Command("import"))
async def import_usage_handler(message: Message) -> None:
    await message.answer("Надішли CSV-файл із підписом /import")


@import_router.message(F.document, F.caption.func(_has_import_caption))
async def import_handler(
    message: Message,
    repository: RecordsRepository,
    app_config: AppConfig,
) -> None:
    user = message.from_user
    if user is None or not await is_telegram_admin(message, user.id, app_config):
        await message.answer("Ця команда лише для адміністраторів.")
        return

    document = message.document
    bot = message.bot
    if document is None or bot is None:
        await message.answer("Не вдалося прочитати CSV-файл.")
        return

    buffer = await bot.download(document)
    if buffer is None:
        await message.answer("Не вдалося прочитати CSV-файл.")
        return
    text = buffer.read().decode("utf-8")

    valid: list[IncomeRecord] = []
    invalid = 0
    for row in csv.DictReader(io.StringIO(text)):
        try:
            valid.append(
                _record_from_row(row, chat_id=message.chat.id, updated_by=user.id)
            )
        except Exception:
            invalid += 1

    added, skipped = await repository.import_records(valid)
    logger.bind(
        chat_id=message.chat.id, added=added, skipped=skipped, invalid=invalid
    ).info("CSV import processed")
    await message.answer(f"Імпортовано {added}, пропущено {skipped + invalid}.")
