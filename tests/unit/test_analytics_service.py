import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from income_stats.config import AnalyticsConfig, StorageConfig
from income_stats.models import (
    ChatSetting,
    FunSummaryConfig,
    IncomeRecord,
    RecordNote,
)
from income_stats.repositories import RecordsRepository
from income_stats.services.admin_service import AdminService
from income_stats.services.analytics_service import AnalyticsService
from income_stats.utils.files import temporary_artifacts


def make_record(
    record_id: str,
    amount: str,
    *,
    currency: str = "UAH",
    categories: list[str] | None = None,
    tags: list[str] | None = None,
    income_date: date = date(2026, 7, 29),
    chat_id: int = -100,
) -> IncomeRecord:
    return IncomeRecord(
        id=record_id,
        telegram_message_id=len(record_id),
        chat_id=chat_id,
        user_id=7,
        original_text=amount,
        amount=Decimal(amount),
        currency=currency,
        categories=categories or ["other"],
        tags=tags or [],
        income_date=income_date,
        created_at=datetime(2026, 7, 29, 10, tzinfo=UTC),
        updated_at=datetime(2026, 7, 29, 10, tzinfo=UTC),
        updated_by=7,
    )


class FakeAnalyticsRepository:
    def __init__(
        self,
        records: list[IncomeRecord],
        notes: list[RecordNote] | None = None,
    ) -> None:
        self.records = records
        self.notes = notes or []
        self.enabled = True

    async def list_records(self, chat_id: int) -> list[IncomeRecord]:
        return [record for record in self.records if record.chat_id == chat_id]

    async def list_notes(self, record_id: str) -> list[RecordNote]:
        return [note for note in self.notes if note.record_id == record_id]

    async def is_chat_enabled(self, chat_id: int) -> bool:
        return self.enabled

    async def set_chat_enabled(
        self,
        chat_id: int,
        enabled: bool,
        updated_by: int,
    ) -> ChatSetting:
        self.enabled = enabled
        return ChatSetting(
            chat_id=chat_id,
            enabled=enabled,
            updated_by=updated_by,
        )


def as_repository(repository: FakeAnalyticsRepository) -> RecordsRepository:
    return cast(RecordsRepository, repository)


@pytest.fixture
def repository() -> FakeAnalyticsRepository:
    record = make_record(
        "one",
        "500",
        categories=["salary", "debt"],
        tags=["card"],
    )
    note = RecordNote(record_id=record.id, user_id=7, text="paid")
    return FakeAnalyticsRepository([record], [note])


@pytest.fixture
def analytics_service(
    repository: FakeAnalyticsRepository,
    tmp_path: Path,
) -> AnalyticsService:
    return AnalyticsService(
        as_repository(repository),
        AnalyticsConfig(),
        StorageConfig(export_directory=tmp_path),
        timezone="Europe/Kyiv",
    )


async def test_category_breakdown_expands_without_inflating_total(
    analytics_service: AnalyticsService,
) -> None:
    frame = await analytics_service.frame(chat_id=-100)
    breakdown = analytics_service.category_breakdown(frame)

    assert breakdown.loc["salary", "amount"] == 500
    assert breakdown.loc["debt", "amount"] == 500
    assert analytics_service.total(frame) == Decimal("500")
    assert analytics_service.tag_breakdown(frame).loc["card", "amount"] == 500


async def test_summary_keeps_mixed_currencies_separate(tmp_path: Path) -> None:
    repository = FakeAnalyticsRepository(
        [
            make_record("uah", "500", currency="UAH"),
            make_record("usd", "20", currency="USD"),
        ]
    )
    service = AnalyticsService(
        as_repository(repository),
        AnalyticsConfig(),
        StorageConfig(export_directory=tmp_path),
    )

    summary = await service.summary(-100, "all")

    assert "500.00 UAH" in summary
    assert "20.00 USD" in summary
    assert "520.00" not in summary
    with pytest.raises(ValueError, match="mixed currencies"):
        service.total(await service.frame(-100))


async def test_period_and_chat_filtering(tmp_path: Path) -> None:
    repository = FakeAnalyticsRepository(
        [
            make_record("today", "100", income_date=date(2026, 7, 29)),
            make_record("old", "200", income_date=date(2026, 6, 1)),
            make_record("other-chat", "300", chat_id=-200),
        ]
    )
    service = AnalyticsService(
        as_repository(repository),
        AnalyticsConfig(),
        StorageConfig(export_directory=tmp_path),
    )

    frame = await service.frame(-100, "month", today=date(2026, 7, 29))

    assert list(frame["id"]) == ["today"]


async def test_chart_builds_png_and_self_contained_html(
    analytics_service: AnalyticsService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_write_image(self: object, path: Path, **kwargs: object) -> None:
        Path(path).write_bytes(b"png")

    monkeypatch.setattr("plotly.graph_objects.Figure.write_image", fake_write_image)

    first = await analytics_service.build_chart_artifacts(-100, "all")
    second = await analytics_service.build_chart_artifacts(-100, "all")

    assert first.png is not None and first.png.read_bytes() == b"png"
    assert first.html is not None and first.html.exists()
    assert "plotly" in first.html.read_text(encoding="utf-8").casefold()
    assert first != second


async def test_chart_failure_cleans_partial_artifacts(
    analytics_service: AnalyticsService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_write_image(self: object, path: Path, **kwargs: object) -> None:
        raise RuntimeError("no browser")

    monkeypatch.setattr("plotly.graph_objects.Figure.write_image", fail_write_image)

    with pytest.raises(RuntimeError, match="no browser"):
        await analytics_service.build_chart_artifacts(-100, "all")

    assert list(analytics_service.artifact_directory.iterdir()) == []


async def test_chart_respects_optional_formats(
    repository: FakeAnalyticsRepository,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AnalyticsService(
        as_repository(repository),
        AnalyticsConfig(static_preview=False, interactive_html=True),
        StorageConfig(export_directory=tmp_path),
    )

    def unexpected_image(self: object, path: Path, **kwargs: object) -> None:
        raise AssertionError("PNG generation must be disabled")

    monkeypatch.setattr(
        "plotly.graph_objects.Figure.write_image",
        unexpected_image,
    )
    artifacts = await service.build_chart_artifacts(-100, "all")

    assert artifacts.png is None
    assert artifacts.html is not None and artifacts.html.exists()


async def test_empty_chart_is_rejected(
    tmp_path: Path,
) -> None:
    service = AnalyticsService(
        as_repository(FakeAnalyticsRepository([])),
        AnalyticsConfig(),
        StorageConfig(export_directory=tmp_path),
    )

    with pytest.raises(ValueError, match="No data"):
        await service.build_chart_artifacts(-100, "all")


async def test_export_contains_scoped_records_and_notes(
    analytics_service: AnalyticsService,
) -> None:
    archive = await analytics_service.build_export(-100)

    with zipfile.ZipFile(archive) as bundle:
        assert set(bundle.namelist()) == {"records.csv", "record_notes.csv"}
        assert "one" in bundle.read("records.csv").decode()
        assert "paid" in bundle.read("record_notes.csv").decode()
    assert not list(
        path
        for path in analytics_service.artifact_directory.iterdir()
        if path != archive
    )


async def test_export_failure_removes_partial_archive(
    analytics_service: AnalyticsService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_zip(*args: object, **kwargs: object) -> None:
        raise RuntimeError("zip failed")

    monkeypatch.setattr("zipfile.ZipFile", fail_zip)

    with pytest.raises(RuntimeError, match="zip failed"):
        await analytics_service.build_export(-100)

    assert list(analytics_service.artifact_directory.iterdir()) == []


def test_temporary_artifacts_always_cleans_files(tmp_path: Path) -> None:
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.write_text("one")
    second.write_text("two")

    with pytest.raises(RuntimeError), temporary_artifacts(first, second):
        raise RuntimeError("send failed")

    assert not first.exists()
    assert not second.exists()


def test_fun_summary_preserves_number_phrase(
    repository: FakeAnalyticsRepository,
) -> None:
    service = AnalyticsService(
        as_repository(repository),
        AnalyticsConfig(),
        StorageConfig(),
        fun_summary=FunSummaryConfig(number_phrases={"500": "five hundred"}),
    )

    assert service.fun_summary(repository.records[0]) == "five hundred"


async def test_admin_service_wraps_repository(
    repository: FakeAnalyticsRepository,
) -> None:
    service = AdminService(as_repository(repository))

    assert await service.status(-100) is True
    setting = await service.set_status(-100, enabled=False, updated_by=7)
    assert setting.enabled is False
    assert await service.status(-100) is False
