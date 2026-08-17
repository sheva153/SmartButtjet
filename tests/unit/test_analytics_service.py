import asyncio
import random
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from threading import Event
from typing import Any, cast

import pytest
from plotly import graph_objects as go

from income_stats.config import AnalyticsConfig, StorageConfig
from income_stats.models import (
    ChatSetting,
    FunItem,
    FunSummaryConfig,
    IncomeRecord,
    Period,
    RecordNote,
    RecordType,
)
from income_stats.repositories import RecordsRepository
from income_stats.services import analytics_service as analytics_module
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
    record_type: RecordType = "income",
) -> IncomeRecord:
    return IncomeRecord(
        id=record_id,
        telegram_message_id=len(record_id),
        chat_id=chat_id,
        user_id=7,
        original_text=amount,
        amount=Decimal(amount),
        currency=currency,
        type=record_type,
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

    async def export_snapshot(
        self,
        chat_id: int,
    ) -> tuple[list[IncomeRecord], list[RecordNote]]:
        records = [record for record in self.records if record.chat_id == chat_id]
        record_ids = {record.id for record in records}
        notes = [note for note in self.notes if note.record_id in record_ids]
        return records, notes

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


@pytest.fixture
def mixed_repository() -> FakeAnalyticsRepository:
    return FakeAnalyticsRepository(
        [
            make_record("income-one", "5000", chat_id=1, record_type="income"),
            make_record("income-two", "7000", chat_id=1, record_type="income"),
            make_record("expense-one", "3000", chat_id=1, record_type="expense"),
        ]
    )


@pytest.fixture
def analytics_service_with_mixed(
    mixed_repository: FakeAnalyticsRepository,
    tmp_path: Path,
) -> AnalyticsService:
    return AnalyticsService(
        as_repository(mixed_repository),
        AnalyticsConfig(),
        StorageConfig(export_directory=tmp_path),
        timezone="Europe/Kyiv",
    )


async def test_totals_by_type_splits_currencies(
    analytics_service_with_mixed: AnalyticsService,
) -> None:
    frame = await analytics_service_with_mixed.frame(1, "all")
    totals = AnalyticsService.totals_by_type(frame)

    assert totals["UAH"]["income"] == Decimal("12000")
    assert totals["UAH"]["expense"] == Decimal("3000")


async def test_net_subtracts_expense(
    analytics_service_with_mixed: AnalyticsService,
) -> None:
    frame = await analytics_service_with_mixed.frame(1, "all")

    assert AnalyticsService.net(frame)["UAH"] == Decimal("9000")


async def test_summary_shows_income_expense_net(
    analytics_service_with_mixed: AnalyticsService,
) -> None:
    text = await analytics_service_with_mixed.summary(chat_id=1, period="all")

    assert "Дохід" in text
    assert "Витрати" in text
    assert "Чистими" in text


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

    assert "UAH: Дохід 500.00 · Витрати 0.00 · Чистими 500.00" in summary
    assert "USD: Дохід 20.00 · Витрати 0.00 · Чистими 20.00" in summary
    assert "520.00" not in summary
    with pytest.raises(ValueError, match="mixed currencies"):
        service.total(await service.frame(-100))


async def test_summary_aggregates_same_currency_records(tmp_path: Path) -> None:
    service = AnalyticsService(
        as_repository(
            FakeAnalyticsRepository(
                [make_record("first", "1500"), make_record("second", "500")]
            )
        ),
        AnalyticsConfig(),
        StorageConfig(export_directory=tmp_path),
    )

    summary = await service.summary(-100, "all")

    assert "Записів: 2" in summary
    assert "UAH: Дохід 2,000.00 · Витрати 0.00 · Чистими 2,000.00" in summary


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

    today = await service.frame(-100, "today", today=date(2026, 7, 29))
    assert list(today["id"]) == ["today"]

    year = await service.frame(-100, "year", today=date(2026, 7, 29))
    assert sorted(year["id"]) == ["old", "today"]


async def test_invalid_runtime_period_is_rejected(
    analytics_service: AnalyticsService,
) -> None:
    with pytest.raises(ValueError, match="Unsupported analytics period"):
        await analytics_service.frame(-100, cast(Period, "bogus"))


async def test_chart_builds_png_and_self_contained_html(
    analytics_service: AnalyticsService,
) -> None:
    first = await analytics_service.build_chart_artifacts(
        -100, "month", today=date(2026, 7, 29)
    )
    second = await analytics_service.build_chart_artifacts(
        -100, "month", today=date(2026, 7, 29)
    )

    assert first.png is not None and first.png.read_bytes().startswith(b"\x89PNG")
    assert first.html is not None and first.html.exists()
    assert "plotly" in first.html.read_text(encoding="utf-8").casefold()
    assert first != second


async def test_chart_failure_falls_back_to_html(
    analytics_service: AnalyticsService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_render(*args: object, **kwargs: object) -> None:
        raise RuntimeError("render failed")

    monkeypatch.setattr(analytics_module, "render_report_png", fail_render)

    artifacts = await analytics_service.build_chart_artifacts(
        -100, "month", today=date(2026, 7, 29)
    )

    assert artifacts.png is None
    assert artifacts.html is not None and artifacts.html.exists()


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


async def test_chart_rejects_when_all_formats_are_disabled(
    repository: FakeAnalyticsRepository,
    tmp_path: Path,
) -> None:
    service = AnalyticsService(
        as_repository(repository),
        AnalyticsConfig(static_preview=False, interactive_html=False),
        StorageConfig(export_directory=tmp_path),
    )

    with pytest.raises(ValueError, match="At least one chart format"):
        await service.build_chart_artifacts(-100, "all")


async def test_mixed_currency_chart_uses_separate_grouped_facets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = FakeAnalyticsRepository(
        [
            make_record("uah", "500", currency="UAH"),
            make_record("usd", "20", currency="USD"),
        ]
    )
    service = AnalyticsService(
        as_repository(repository),
        AnalyticsConfig(static_preview=False, interactive_html=True),
        StorageConfig(export_directory=tmp_path),
    )
    figures: list[go.Figure] = []

    def capture_html(self: go.Figure, path: Path, **kwargs: object) -> None:
        figures.append(self)
        Path(path).write_text("plotly", encoding="utf-8")

    monkeypatch.setattr("plotly.graph_objects.Figure.write_html", capture_html)

    await service.build_chart_artifacts(-100, "all")

    figure = figures[0]
    layout = cast(dict[str, object], figure.to_plotly_json()["layout"])
    assert layout["barmode"] == "group"
    assert len(cast(list[object], layout["annotations"])) == 2


async def test_chart_rejects_amount_outside_exact_display_range(
    tmp_path: Path,
) -> None:
    repository = FakeAnalyticsRepository([make_record("huge", "90071992547409.92")])
    service = AnalyticsService(
        as_repository(repository),
        AnalyticsConfig(static_preview=False, interactive_html=True),
        StorageConfig(export_directory=tmp_path),
    )

    with pytest.raises(ValueError, match="exact display range"):
        await service.build_chart_artifacts(-100, "all")


async def test_chart_accepts_adjacent_cents_inside_safe_range(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = FakeAnalyticsRepository(
        [
            make_record("first", "35184372088830.90", currency="UAH"),
            make_record("second", "35184372088830.91", currency="USD"),
        ]
    )
    service = AnalyticsService(
        as_repository(repository),
        AnalyticsConfig(static_preview=False, interactive_html=True),
        StorageConfig(export_directory=tmp_path),
    )
    figures: list[go.Figure] = []

    def fake_html(self: go.Figure, path: Path, **kwargs: object) -> None:
        figures.append(self)
        Path(path).write_text("plotly", encoding="utf-8")

    monkeypatch.setattr("plotly.graph_objects.Figure.write_html", fake_html)

    artifacts = await service.build_chart_artifacts(-100, "all")

    assert artifacts.html is not None and artifacts.html.exists()
    figure = cast(Any, figures[0])
    plotted = [float(trace.y[0]) for trace in figure.data]
    assert len(set(plotted)) == 2


async def test_cancelled_worker_cleans_returned_artifact(tmp_path: Path) -> None:
    started = Event()
    finish_signal = Event()
    artifact = tmp_path / "late.html"

    def write_late() -> Path:
        started.set()
        finish_signal.wait()
        artifact.write_text("late", encoding="utf-8")
        return artifact

    task = asyncio.create_task(
        analytics_module._run_blocking(
            write_late,
            cancelled_result_cleanup=lambda path: path.unlink(missing_ok=True),
        )
    )
    while not started.is_set():
        await asyncio.sleep(0.01)
    task.cancel()
    finish_signal.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert not artifact.exists()


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
    repository: FakeAnalyticsRepository,
    tmp_path: Path,
) -> None:
    other = make_record("other", "999", chat_id=-200)
    repository.records.append(other)
    repository.notes.append(RecordNote(record_id=other.id, user_id=8, text="excluded"))
    analytics_service = AnalyticsService(
        as_repository(repository),
        AnalyticsConfig(),
        StorageConfig(export_directory=tmp_path),
    )

    archive = await analytics_service.build_export(-100)

    with zipfile.ZipFile(archive) as bundle:
        assert set(bundle.namelist()) == {"records.csv", "record_notes.csv"}
        records_csv = bundle.read("records.csv").decode()
        notes_csv = bundle.read("record_notes.csv").decode()
        assert "one" in records_csv
        assert "other" not in records_csv
        assert "paid" in notes_csv
        assert "excluded" not in notes_csv
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


def test_fun_summary_uses_phrase_and_three_affordable_items() -> None:
    config = FunSummaryConfig(
        comparisons_per_message=3,
        phrases=["Гаманець аплодує!"],
        items={
            "burger": FunItem(label="бургерів", emoji="🍔", price_uah=Decimal("150")),
            "coffee": FunItem(label="чашок кави", emoji="☕", price_uah=Decimal("50")),
            "cucumber": FunItem(label="огірків", emoji="🥒", price_uah=Decimal("25")),
            "iphone": FunItem(label="айфонів", emoji="📱", price_uah=Decimal("45000")),
        },
    )
    service = AnalyticsService(
        as_repository(FakeAnalyticsRepository([])),
        AnalyticsConfig(),
        StorageConfig(),
        fun_summary=config,
    )

    summary = service.fun_summary(
        make_record("record", "500"),
        random.Random(7),
    )

    assert "Гаманець аплодує!" in summary
    assert "На ці гроші приблизно можна купити:" in summary
    assert sum(emoji in summary for emoji in ("🍔", "☕", "🥒")) == 3
    assert "📱" not in summary


def test_fun_summary_skips_comparisons_for_foreign_currency() -> None:
    service = AnalyticsService(
        as_repository(FakeAnalyticsRepository([])),
        AnalyticsConfig(),
        StorageConfig(),
        fun_summary=FunSummaryConfig(
            phrases=["Красиво!"],
            items={
                "burger": FunItem(
                    label="бургерів", emoji="🍔", price_uah=Decimal("150")
                )
            },
        ),
    )

    assert service.fun_summary(make_record("record", "500", currency="USD")) == (
        "Красиво!"
    )


def test_fun_summary_uses_number_ending() -> None:
    service = AnalyticsService(
        as_repository(FakeAnalyticsRepository([])),
        AnalyticsConfig(),
        StorageConfig(),
        fun_summary=FunSummaryConfig(
            phrases=["Випадкова фраза"],
            ending_phrases={"77": "Подвійна сімка!"},
        ),
    )

    assert service.fun_summary(make_record("record", "1277")) == "Подвійна сімка!"


async def test_admin_service_wraps_repository(
    repository: FakeAnalyticsRepository,
) -> None:
    service = AdminService(as_repository(repository))

    assert await service.status(-100) is True
    setting = await service.set_status(-100, enabled=False, updated_by=7)
    assert setting.enabled is False
    assert await service.status(-100) is False
