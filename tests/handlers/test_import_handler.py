"""Tests for the /import CSV bulk import handler."""

import csv
import io
import json
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Message

from income_stats.config import AppConfig
from income_stats.handlers.import_handler import (
    _has_import_caption,
    import_handler,
    import_router,
    import_usage_handler,
)
from income_stats.repositories import RecordsRepository


def test_import_handler_is_importable() -> None:
    assert callable(import_handler)
    assert import_router.name == "import"


def test_import_caption_matches_exact_command() -> None:
    assert _has_import_caption("/import") is True
    assert _has_import_caption("/import something") is True
    assert _has_import_caption("/import@my_bot") is True
    assert _has_import_caption("/import@my_bot rows") is True


def test_import_caption_rejects_lookalike_commands() -> None:
    assert _has_import_caption("/importantnote here") is False
    assert _has_import_caption("/imports") is False
    assert _has_import_caption(None) is False
    assert _has_import_caption("") is False


async def test_import_usage_hint_without_document() -> None:
    message = SimpleNamespace(answer=AsyncMock())

    await import_usage_handler(cast(Message, message))

    message.answer.assert_awaited_once()
    (text,), _ = message.answer.await_args
    assert "/import" in text
    assert "CSV" in text


async def test_import_rejects_non_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    admin_lookup = AsyncMock(return_value=False)
    monkeypatch.setattr(
        "income_stats.handlers.import_handler.is_telegram_admin",
        admin_lookup,
    )
    config = AppConfig()
    repository = SimpleNamespace(import_records=AsyncMock())
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        chat=SimpleNamespace(id=-100),
        document=SimpleNamespace(file_id="abc"),
        bot=SimpleNamespace(download=AsyncMock()),
        answer=AsyncMock(),
    )

    await import_handler(
        cast(Message, message),
        cast(RecordsRepository, repository),
        config,
    )

    admin_lookup.assert_awaited_once_with(cast(Message, message), 7, config)
    message.answer.assert_awaited_once_with("Ця команда лише для адміністраторів.")
    repository.import_records.assert_not_awaited()


async def test_import_rejects_message_without_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin_lookup = AsyncMock()
    monkeypatch.setattr(
        "income_stats.handlers.import_handler.is_telegram_admin",
        admin_lookup,
    )
    repository = SimpleNamespace(import_records=AsyncMock())
    message = SimpleNamespace(
        from_user=None,
        chat=SimpleNamespace(id=-100),
        document=SimpleNamespace(file_id="abc"),
        bot=SimpleNamespace(download=AsyncMock()),
        answer=AsyncMock(),
    )

    await import_handler(
        cast(Message, message),
        cast(RecordsRepository, repository),
        AppConfig(),
    )

    admin_lookup.assert_not_awaited()
    message.answer.assert_awaited_once_with("Ця команда лише для адміністраторів.")
    repository.import_records.assert_not_awaited()


def _csv_bytes(rows: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _valid_row(**changes: str) -> dict[str, str]:
    row = {
        "telegram_message_id": "10",
        "source_index": "0",
        "chat_id": "-999",
        "user_id": "7",
        "username": "felix",
        "original_text": "зп 500",
        "amount": "500",
        "currency": "UAH",
        "categories": json.dumps(["salary"]),
        "tags": json.dumps([]),
        "description": "",
        "income_date": "2026-07-29",
    }
    row.update(changes)
    return row


async def test_import_parses_csv_document(monkeypatch: pytest.MonkeyPatch) -> None:
    admin_lookup = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "income_stats.handlers.import_handler.is_telegram_admin",
        admin_lookup,
    )
    csv_bytes = _csv_bytes([_valid_row()])
    config = AppConfig()
    repository = SimpleNamespace(import_records=AsyncMock(return_value=(1, 0)))
    bot = SimpleNamespace(download=AsyncMock(return_value=io.BytesIO(csv_bytes)))
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        chat=SimpleNamespace(id=-100),
        document=SimpleNamespace(file_id="abc"),
        bot=bot,
        answer=AsyncMock(),
    )

    await import_handler(
        cast(Message, message),
        cast(RecordsRepository, repository),
        config,
    )

    bot.download.assert_awaited_once_with(message.document)
    repository.import_records.assert_awaited_once()
    (imported,), _ = repository.import_records.await_args
    assert len(imported) == 1
    assert imported[0].chat_id == -100
    assert imported[0].updated_by == 7
    assert imported[0].categories == ["salary"]
    message.answer.assert_awaited_once_with("Імпортовано 1, пропущено 0.")


async def test_import_counts_invalid_rows_as_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin_lookup = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "income_stats.handlers.import_handler.is_telegram_admin",
        admin_lookup,
    )
    rows = [_valid_row(), _valid_row(amount="not-a-number")]
    csv_bytes = _csv_bytes(rows)
    config = AppConfig()
    repository = SimpleNamespace(import_records=AsyncMock(return_value=(1, 1)))
    bot = SimpleNamespace(download=AsyncMock(return_value=io.BytesIO(csv_bytes)))
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        chat=SimpleNamespace(id=-100),
        document=SimpleNamespace(file_id="abc"),
        bot=bot,
        answer=AsyncMock(),
    )

    await import_handler(
        cast(Message, message),
        cast(RecordsRepository, repository),
        config,
    )

    (imported,), _ = repository.import_records.await_args
    assert len(imported) == 1
    message.answer.assert_awaited_once_with("Імпортовано 1, пропущено 2.")
