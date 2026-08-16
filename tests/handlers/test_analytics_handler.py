from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.types import CallbackQuery, Message

from income_stats.bot.ui import ChartPeriod
from income_stats.config import AppConfig
from income_stats.handlers.analytics_handler import (
    analytics_router,
    chart_handler,
    chart_period_callback,
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
async def test_chart_handler_shows_period_picker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bound_logger = Mock()
    bind = Mock(return_value=bound_logger)
    monkeypatch.setattr("income_stats.handlers.analytics_handler.logger.bind", bind)
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        chat=SimpleNamespace(id=-100),
        answer=AsyncMock(),
    )

    await chart_handler(cast(Message, message))

    bind.assert_called_once_with(chat_id=-100, user_id=7)
    bound_logger.info.assert_called_once_with("Chart period requested")
    message.answer.assert_awaited_once()
    assert message.answer.await_args is not None
    assert message.answer.await_args.kwargs.get("reply_markup") is not None


@pytest.mark.asyncio
async def test_chart_period_callback_builds_and_sends(tmp_path: Path) -> None:
    png = tmp_path / "chart.png"
    png.write_bytes(b"\x89PNG")
    query_message = Mock(spec=Message)
    query_message.chat = SimpleNamespace(id=-100)
    query_message.answer_photo = AsyncMock()
    query_message.answer_document = AsyncMock()
    query_message.answer = AsyncMock()
    query = SimpleNamespace(message=query_message, answer=AsyncMock())
    service = SimpleNamespace(
        build_chart_artifacts=AsyncMock(return_value=ChartArtifacts(png, None))
    )

    await chart_period_callback(
        cast(CallbackQuery, query),
        ChartPeriod(period="week"),
        cast(AnalyticsService, service),
    )

    service.build_chart_artifacts.assert_awaited_once_with(-100, "week")
    query_message.answer_photo.assert_awaited_once()


@pytest.mark.asyncio
async def test_chart_period_callback_reports_build_failure() -> None:
    query_message = Mock(spec=Message)
    query_message.chat = SimpleNamespace(id=-100)
    query_message.answer = AsyncMock()
    query = SimpleNamespace(message=query_message, answer=AsyncMock())
    service = SimpleNamespace(
        build_chart_artifacts=AsyncMock(side_effect=RuntimeError("render boom"))
    )

    await chart_period_callback(
        cast(CallbackQuery, query),
        ChartPeriod(period="year"),
        cast(AnalyticsService, service),
    )

    query_message.answer.assert_awaited_once_with("Не вдалося побудувати діаграму.")


@pytest.mark.asyncio
async def test_chart_period_callback_rejects_unknown_period() -> None:
    query_message = Mock(spec=Message)
    query = SimpleNamespace(message=query_message, answer=AsyncMock())
    service = SimpleNamespace(build_chart_artifacts=AsyncMock())

    await chart_period_callback(
        cast(CallbackQuery, query),
        ChartPeriod(period="decade"),
        cast(AnalyticsService, service),
    )

    service.build_chart_artifacts.assert_not_awaited()


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
