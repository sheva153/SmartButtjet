from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.types import Message

from income_stats.config import AppConfig
from income_stats.handlers.analytics_handler import (
    analytics_router,
    chart_handler,
    export_handler,
    send_chart,
    stats_handler,
)
from income_stats.services import AnalyticsService, ChartArtifacts


class UnhashableAwaitable:
    __hash__ = None  # pyright: ignore[reportAssignmentType]

    def __init__(self, awaited: Mock) -> None:
        self.awaited = awaited

    def __await__(self):  # type: ignore[no-untyped-def]
        async def complete() -> None:
            self.awaited()

        return complete().__await__()


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
async def test_chart_accepts_unhashable_aiogram_awaitables(tmp_path: Path) -> None:
    png, html = tmp_path / "chart.png", tmp_path / "chart.html"
    png.write_bytes(b"png")
    html.write_text("html")
    service = SimpleNamespace(
        build_chart_artifacts=AsyncMock(return_value=ChartArtifacts(png, html))
    )
    photo_awaited = Mock()
    document_awaited = Mock()
    message = SimpleNamespace(
        chat=SimpleNamespace(id=-100),
        answer_photo=Mock(
            side_effect=lambda *args, **kwargs: UnhashableAwaitable(photo_awaited)
        ),
        answer_document=Mock(
            side_effect=lambda *args, **kwargs: UnhashableAwaitable(document_awaited)
        ),
    )

    await send_chart(cast(Message, message), cast(AnalyticsService, service))

    photo_awaited.assert_called_once()
    document_awaited.assert_called_once()


@pytest.mark.asyncio
async def test_stats_audits_requesting_user(monkeypatch: pytest.MonkeyPatch) -> None:
    bound_logger = Mock()
    bind = Mock(return_value=bound_logger)
    monkeypatch.setattr("income_stats.handlers.analytics_handler.logger.bind", bind)
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        chat=SimpleNamespace(id=-100),
        answer=AsyncMock(),
    )
    service = SimpleNamespace(summary=AsyncMock(return_value="summary"))

    await stats_handler(cast(Message, message), cast(AnalyticsService, service))

    bind.assert_called_once_with(chat_id=-100, user_id=7)
    bound_logger.info.assert_called_once_with("Analytics requested")


@pytest.mark.asyncio
async def test_chart_audits_requesting_user(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bound_logger = Mock()
    bind = Mock(return_value=bound_logger)
    monkeypatch.setattr("income_stats.handlers.analytics_handler.logger.bind", bind)
    html = tmp_path / "chart.html"
    html.write_text("html")
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        chat=SimpleNamespace(id=-100),
        answer=AsyncMock(),
        answer_document=AsyncMock(),
    )
    service = SimpleNamespace(
        build_chart_artifacts=AsyncMock(return_value=ChartArtifacts(None, html))
    )

    await chart_handler(cast(Message, message), cast(AnalyticsService, service))

    bind.assert_called_once_with(chat_id=-100, user_id=7)
    bound_logger.info.assert_called_once_with("Chart requested")


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
