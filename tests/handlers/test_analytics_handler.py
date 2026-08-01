from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Message

from income_stats.config import AppConfig
from income_stats.handlers.analytics_handler import (
    analytics_router,
    export_handler,
    send_chart,
)
from income_stats.services import AnalyticsService, ChartArtifacts


def test_chart_sender_is_importable() -> None:
    assert callable(send_chart)
    assert analytics_router.name == "analytics"


@pytest.mark.asyncio
async def test_chart_sends_both_and_cleans_on_failure(tmp_path: Path) -> None:
    png, html = tmp_path / "chart.png", tmp_path / "chart.html"
    png.write_bytes(b"png")
    html.write_text("html")
    service = SimpleNamespace(
        build_chart_artifacts=AsyncMock(return_value=ChartArtifacts(png, html))
    )
    message = SimpleNamespace(
        chat=SimpleNamespace(id=-100),
        answer_photo=AsyncMock(),
        answer_document=AsyncMock(side_effect=RuntimeError("send")),
    )
    await send_chart(cast(Message, message), cast(AnalyticsService, service))
    message.answer_photo.assert_awaited_once()
    message.answer_document.assert_awaited_once()
    assert not png.exists() and not html.exists()


@pytest.mark.asyncio
async def test_chart_attempts_html_when_png_delivery_fails(tmp_path: Path) -> None:
    png, html = tmp_path / "chart.png", tmp_path / "chart.html"
    png.write_bytes(b"png")
    html.write_text("html")
    service = SimpleNamespace(
        build_chart_artifacts=AsyncMock(return_value=ChartArtifacts(png, html))
    )
    message = SimpleNamespace(
        chat=SimpleNamespace(id=-100),
        answer_photo=AsyncMock(side_effect=RuntimeError("photo")),
        answer_document=AsyncMock(),
    )

    await send_chart(cast(Message, message), cast(AnalyticsService, service))

    message.answer_document.assert_awaited_once()
    assert not png.exists() and not html.exists()


@pytest.mark.asyncio
async def test_export_rejects_non_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin_lookup = AsyncMock(return_value=False)
    monkeypatch.setattr(
        "income_stats.handlers.analytics_handler.is_telegram_admin",
        admin_lookup,
    )
    config = AppConfig()
    service = SimpleNamespace(build_export=AsyncMock())
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        chat=SimpleNamespace(id=-100),
        answer=AsyncMock(),
        answer_document=AsyncMock(),
    )

    await export_handler(
        cast(Message, message),
        cast(AnalyticsService, service),
        config,
    )

    admin_lookup.assert_awaited_once_with(cast(Message, message), 7, config)
    message.answer.assert_awaited_once_with("Ця команда лише для адміністраторів.")
    service.build_export.assert_not_awaited()
    message.answer_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_export_rejects_message_without_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin_lookup = AsyncMock()
    monkeypatch.setattr(
        "income_stats.handlers.analytics_handler.is_telegram_admin",
        admin_lookup,
    )
    service = SimpleNamespace(build_export=AsyncMock())
    message = SimpleNamespace(
        from_user=None,
        chat=SimpleNamespace(id=-100),
        answer=AsyncMock(),
        answer_document=AsyncMock(),
    )

    await export_handler(
        cast(Message, message),
        cast(AnalyticsService, service),
        AppConfig(),
    )

    admin_lookup.assert_not_awaited()
    message.answer.assert_awaited_once_with("Ця команда лише для адміністраторів.")
    service.build_export.assert_not_awaited()
    message.answer_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_export_allows_admin_and_cleans_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = tmp_path / "export.zip"
    archive.write_bytes(b"zip")
    admin_lookup = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "income_stats.handlers.analytics_handler.is_telegram_admin",
        admin_lookup,
    )
    config = AppConfig()
    service = SimpleNamespace(build_export=AsyncMock(return_value=archive))
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        chat=SimpleNamespace(id=-100),
        answer=AsyncMock(),
        answer_document=AsyncMock(),
    )

    await export_handler(
        cast(Message, message),
        cast(AnalyticsService, service),
        config,
    )

    admin_lookup.assert_awaited_once_with(cast(Message, message), 7, config)
    service.build_export.assert_awaited_once_with(-100)
    message.answer_document.assert_awaited_once()
    assert not archive.exists()
