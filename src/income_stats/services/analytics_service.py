"""Analytics, chart, export, and playful-summary use cases."""

import asyncio
import json
import random
import tempfile
import zipfile
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import cast
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
from loguru import logger

from income_stats.config import AnalyticsConfig, StorageConfig
from income_stats.models import (
    CHART_PERIODS,
    FunSummaryConfig,
    IncomeRecord,
    Period,
    RecordNote,
)
from income_stats.repositories import RecordsRepository
from income_stats.services.report_chart import PERIOD_TITLES, render_report_png

_MAX_EXACT_CHART_AMOUNT = Decimal(2**45 - 1)


@dataclass(frozen=True)
class ChartArtifacts:
    png: Path | None
    html: Path | None


class AnalyticsService:
    def __init__(
        self,
        repository: RecordsRepository,
        config: AnalyticsConfig,
        storage: StorageConfig,
        *,
        timezone: str = "Europe/Kyiv",
        fun_summary: FunSummaryConfig | None = None,
    ) -> None:
        self._repository = repository
        self._config = config
        self._timezone = timezone
        self._fun_summary = fun_summary or FunSummaryConfig()
        self.artifact_directory = storage.export_directory

    async def frame(
        self,
        chat_id: int,
        period: Period = "all",
        *,
        today: date | None = None,
    ) -> pd.DataFrame:
        records = await self._repository.list_records(chat_id)
        if not records:
            return pd.DataFrame(columns=list(IncomeRecord.model_fields))
        frame = pd.DataFrame(
            [record.model_dump(mode="python") for record in records],
            columns=list(IncomeRecord.model_fields),
        )
        current_date = today or datetime.now(ZoneInfo(self._timezone)).date()
        if period == "all":
            return frame
        dates = frame["income_date"]
        if period == "today":
            return cast(pd.DataFrame, frame.loc[dates == current_date].copy())
        if period == "week":
            start = current_date - timedelta(days=current_date.weekday())
            return cast(
                pd.DataFrame,
                frame.loc[(dates >= start) & (dates <= current_date)].copy(),
            )
        if period == "month":
            return cast(
                pd.DataFrame,
                frame.loc[
                    dates.map(
                        lambda value: (
                            (
                                value.year,
                                value.month,
                            )
                            == (current_date.year, current_date.month)
                        )
                    )
                ].copy(),
            )
        if period == "year":
            return cast(
                pd.DataFrame,
                frame.loc[
                    dates.map(lambda value: value.year == current_date.year)
                ].copy(),
            )
        raise ValueError(f"Unsupported analytics period: {period}")

    @staticmethod
    def totals(frame: pd.DataFrame) -> dict[str, Decimal]:
        if frame.empty:
            return {}
        totals: dict[str, Decimal] = {}
        for currency, rows in frame.groupby("currency", sort=True):
            totals[str(currency)] = sum(rows["amount"], start=Decimal())
        return totals

    @classmethod
    def total(cls, frame: pd.DataFrame) -> Decimal:
        totals = cls.totals(frame)
        if len(totals) > 1:
            raise ValueError("Cannot total mixed currencies")
        return next(iter(totals.values()), Decimal())

    @staticmethod
    def _label_breakdown(frame: pd.DataFrame, column: str) -> pd.DataFrame:
        if frame.empty:
            return pd.DataFrame(columns=["amount"])
        exploded = frame.explode(column)
        exploded = cast(pd.DataFrame, exploded.loc[exploded[column].notna()].copy())
        currencies = exploded["currency"].nunique()
        groups = [column] if currencies <= 1 else [column, "currency"]
        return cast(
            pd.DataFrame,
            exploded.groupby(groups, sort=True)[["amount"]].sum(),
        )

    @classmethod
    def category_breakdown(cls, frame: pd.DataFrame) -> pd.DataFrame:
        return cls._label_breakdown(frame, "categories")

    @classmethod
    def tag_breakdown(cls, frame: pd.DataFrame) -> pd.DataFrame:
        return cls._label_breakdown(frame, "tags")

    async def summary(
        self,
        chat_id: int,
        period: Period | None = None,
        *,
        today: date | None = None,
    ) -> str:
        frame = await self.frame(
            chat_id,
            period or self._config.default_period,
            today=today,
        )
        if frame.empty:
            return "Записів ще немає."
        lines = [f"Записів: {len(frame)}", "Загалом:"]
        lines.extend(
            f"• {amount:,.2f} {currency}"
            for currency, amount in self.totals(frame).items()
        )
        return "\n".join(lines)

    async def build_chart_artifacts(
        self,
        chat_id: int,
        period: Period | None = None,
        *,
        today: date | None = None,
    ) -> ChartArtifacts:
        resolved_period = period or self._config.default_period
        reference = today or datetime.now(ZoneInfo(self._timezone)).date()
        frame = await self.frame(chat_id, resolved_period, today=reference)
        if frame.empty:
            raise ValueError("No data for chart")
        if not self._config.static_preview and not self._config.interactive_html:
            raise ValueError("At least one chart format must be enabled")
        return await _run_blocking(
            partial(
                _write_chart_artifacts,
                frame,
                self.artifact_directory,
                self._config,
                resolved_period,
                reference,
            ),
            cancelled_result_cleanup=_remove_chart_artifacts,
        )

    async def build_export(self, chat_id: int) -> Path:
        records, notes = await self._repository.export_snapshot(chat_id)
        return await _run_blocking(
            partial(
                _write_export,
                records,
                notes,
                self.artifact_directory,
            ),
            cancelled_result_cleanup=lambda path: path.unlink(missing_ok=True),
        )

    def fun_summary(
        self,
        record: IncomeRecord,
        rng: random.Random | random.SystemRandom | None = None,
    ) -> str:
        return build_fun_summary(record, self._fun_summary, rng)


async def _run_blocking[ResultT](
    operation: Callable[[], ResultT],
    *,
    cancelled_result_cleanup: Callable[[ResultT], None] | None = None,
) -> ResultT:
    """Run blocking chart/export work without relying on asyncio's broken executor."""
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="income-artifact")
    future = executor.submit(operation)

    async def poll_result() -> ResultT:
        while not future.done():
            await asyncio.sleep(0.01)
        return future.result()

    polling = asyncio.create_task(poll_result())
    try:
        return await asyncio.shield(polling)
    except asyncio.CancelledError:
        try:
            result = await asyncio.shield(polling)
        except Exception:
            pass
        else:
            if cancelled_result_cleanup is not None:
                cancelled_result_cleanup(result)
        raise
    finally:
        executor.shutdown(
            wait=polling.done(),
            cancel_futures=True,
        )


def _remove_chart_artifacts(artifacts: ChartArtifacts) -> None:
    for path in (artifacts.png, artifacts.html):
        if path is not None:
            path.unlink(missing_ok=True)


def _write_chart_artifacts(
    frame: pd.DataFrame,
    artifact_directory: Path,
    config: AnalyticsConfig,
    period: Period,
    reference: date,
) -> ChartArtifacts:
    if any(abs(amount) > _MAX_EXACT_CHART_AMOUNT for amount in frame["amount"]):
        raise ValueError("Chart amount exceeds exact display range")
    artifact_directory.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    png = (
        artifact_directory / f"income-chart-{token}.png"
        if config.static_preview and period in CHART_PERIODS
        else None
    )
    html = (
        artifact_directory / f"income-chart-{token}.html"
        if config.interactive_html
        else None
    )
    completed = False
    try:
        if html is not None:
            # The interactive HTML is a per-day breakdown of the same period the
            # PNG summarises; its daily groupby is only needed here.
            daily = cast(
                pd.DataFrame,
                (
                    frame.assign(day=frame["income_date"])
                    .groupby(["day", "currency"], as_index=False)["amount"]
                    .agg(lambda values: sum(values, start=Decimal()))
                ),
            )
            daily["amount"] = daily["amount"].map(float)
            figure = px.bar(
                daily,
                x="day",
                y="amount",
                color="currency",
                facet_row="currency",
                barmode="group",
                title=f"{PERIOD_TITLES.get(period, 'Доходи')} · за днями",
                labels={"day": "Дата", "amount": "Сума", "currency": "Валюта"},
            )
            figure.write_html(html, include_plotlyjs=True, full_html=True)
        if png is not None:
            try:
                render_report_png(frame, period, reference, png)
            except Exception as error:
                if html is None:
                    raise
                png.unlink(missing_ok=True)
                png = None
                logger.warning("PNG report unavailable; using HTML only: {}", error)
        completed = True
    finally:
        if not completed:
            for path in (png, html):
                if path is not None:
                    with suppress(OSError):
                        path.unlink(missing_ok=True)
    return ChartArtifacts(png=png, html=html)


def _write_export(
    records: list[IncomeRecord],
    notes: list[RecordNote],
    artifact_directory: Path,
) -> Path:
    artifact_directory.mkdir(parents=True, exist_ok=True)
    archive = artifact_directory / f"income-export-{uuid4().hex}.zip"
    completed = False
    try:
        with tempfile.TemporaryDirectory(prefix="income-export-") as temporary:
            root = Path(temporary)
            records_path = root / "records.csv"
            notes_path = root / "record_notes.csv"
            _export_frame(records, IncomeRecord.model_fields).to_csv(
                records_path,
                index=False,
            )
            _export_frame(notes, RecordNote.model_fields).to_csv(
                notes_path,
                index=False,
            )
            with zipfile.ZipFile(
                archive,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
            ) as bundle:
                bundle.write(records_path, arcname="records.csv")
                bundle.write(notes_path, arcname="record_notes.csv")
        completed = True
    finally:
        if not completed:
            with suppress(OSError):
                archive.unlink(missing_ok=True)
    return archive


def _export_frame(
    models: list[IncomeRecord] | list[RecordNote],
    fields: Mapping[str, object],
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for model in models:
        row = model.model_dump(mode="json")
        for key, value in row.items():
            if isinstance(value, list):
                row[key] = json.dumps(
                    value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
        rows.append(row)
    return pd.DataFrame(rows, columns=list(fields))


def build_fun_summary(
    record: IncomeRecord,
    config: FunSummaryConfig,
    rng: random.Random | random.SystemRandom | None = None,
) -> str:
    if not config.enabled:
        return ""
    chooser = rng or random.SystemRandom()
    phrase = ""
    if record.amount == record.amount.to_integral_value():
        amount_key = str(int(record.amount))
        phrase = config.number_phrases.get(amount_key, "")
        if not phrase:
            for ending, ending_phrase in sorted(
                config.ending_phrases.items(),
                key=lambda item: len(item[0]),
                reverse=True,
            ):
                if amount_key.endswith(ending):
                    phrase = ending_phrase
                    break
    if not phrase:
        phrase = chooser.choice(config.phrases) if config.phrases else "Молодець! 🔥"
    lines = [phrase]
    if record.currency != "UAH" or not config.items:
        return "\n".join(lines)

    available = [
        item for item in config.items.values() if record.amount >= item.price_uah
    ]
    if not available:
        return "\n".join([*lines, "Навіть маленький дохід — це плюс до балансу ✨"])
    selected = chooser.sample(
        available,
        k=min(config.comparisons_per_message, len(available)),
    )
    comparisons: list[str] = []
    for item in selected:
        quantity = record.amount / item.price_uah
        value = f"{quantity:.1f}" if item.fractional else str(int(quantity))
        comparisons.append(f"{item.emoji} {value} {item.label}")
    return "\n".join(
        [
            *lines,
            "",
            "На ці гроші приблизно можна купити:",
            *comparisons,
        ]
    )
