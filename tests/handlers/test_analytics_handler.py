from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Message

from income_stats.handlers.analytics_handler import analytics_router, send_chart
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
