"""Analytics, chart, export, and playful-summary use cases."""

import json
import random
import tempfile
import zipfile
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px

from income_stats.config import AnalyticsConfig, StorageConfig
from income_stats.models import FunSummaryConfig, IncomeRecord, Period, RecordNote
from income_stats.repositories import RecordsRepository


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
        frame = await self.frame(
            chat_id,
            period or self._config.default_period,
            today=today,
        )
        if frame.empty:
            raise ValueError("No data for chart")
        daily = (
            frame.assign(
                day=frame["income_date"],
                amount=frame["amount"].map(float),
            )
            .groupby(["day", "currency"], as_index=False)["amount"]
            .sum()
        )
        figure = px.bar(
            daily,
            x="day",
            y="amount",
            color="currency",
            title="Доходи за днями",
            labels={"day": "Дата", "amount": "Сума", "currency": "Валюта"},
        )
        self.artifact_directory.mkdir(parents=True, exist_ok=True)
        token = uuid4().hex
        png = (
            self.artifact_directory / f"income-chart-{token}.png"
            if self._config.static_preview
            else None
        )
        html = (
            self.artifact_directory / f"income-chart-{token}.html"
            if self._config.interactive_html
            else None
        )
        completed = False
        try:
            if html is not None:
                figure.write_html(
                    html,
                    include_plotlyjs=True,
                    full_html=True,
                )
            if png is not None:
                figure.write_image(
                    png,
                    format="png",
                    width=1200,
                    height=700,
                    scale=2,
                )
            completed = True
        finally:
            if not completed:
                for path in (png, html):
                    if path is not None:
                        with suppress(OSError):
                            path.unlink(missing_ok=True)
        return ChartArtifacts(png=png, html=html)

    async def build_export(self, chat_id: int) -> Path:
        records = await self._repository.list_records(chat_id)
        notes: list[RecordNote] = []
        for record in records:
            notes.extend(await self._repository.list_notes(record.id))
        self.artifact_directory.mkdir(parents=True, exist_ok=True)
        archive = self.artifact_directory / f"income-export-{uuid4().hex}.zip"
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

    def fun_summary(
        self,
        record: IncomeRecord,
        rng: random.Random | random.SystemRandom | None = None,
    ) -> str:
        return build_fun_summary(record, self._fun_summary, rng)


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
