import asyncio
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from income_stats.config import StorageConfig
from income_stats.models import IncomeRecord, RecordNote
from income_stats.repositories.records_repository import (
    GOAL_COLUMNS,
    CsvRecordsRepository,
    RecordNotFoundError,
)


@pytest.fixture
def csv_repository(tmp_path: Path) -> CsvRecordsRepository:
    return CsvRecordsRepository(
        StorageConfig(
            records_file=tmp_path / "records.csv",
            notes_file=tmp_path / "notes.csv",
            chat_settings_file=tmp_path / "chat-settings.csv",
            goals_file=tmp_path / "goals.csv",
            export_directory=tmp_path / "exports",
        )
    )


def make_record(**changes: object) -> IncomeRecord:
    values: dict[str, object] = {
        "id": "record-1",
        "telegram_message_id": 10,
        "source_index": 0,
        "chat_id": -100,
        "user_id": 7,
        "username": "felix",
        "original_text": "зп 500 на картку",
        "amount": Decimal("500"),
        "currency": "UAH",
        "categories": ["salary", "debt"],
        "tags": ["card"],
        "description": "зп на картку",
        "income_date": date(2026, 7, 29),
        "created_at": datetime(2026, 7, 29, 10, tzinfo=UTC),
        "updated_at": datetime(2026, 7, 29, 10, tzinfo=UTC),
        "updated_by": 7,
    }
    values.update(changes)
    return IncomeRecord.model_validate(values)


@pytest.fixture
def saved_record(csv_repository: CsvRecordsRepository) -> IncomeRecord:
    return csv_repository.create_record_sync(make_record())


def test_lists_are_serialized_as_compact_json(
    csv_repository: CsvRecordsRepository,
) -> None:
    csv_repository.create_record_sync(make_record())

    frame = csv_repository.read_records_sync()

    assert frame.iloc[0]["categories"] == '["salary","debt"]'
    assert frame.iloc[0]["tags"] == '["card"]'
    assert list(frame.columns) == list(IncomeRecord.model_fields)


def test_sync_create_is_deduplicated_by_message_and_source(
    csv_repository: CsvRecordsRepository,
) -> None:
    first = csv_repository.create_record_sync(make_record(id="first"))
    second = csv_repository.create_record_sync(make_record(id="second"))

    assert second.id == first.id
    assert [record.id for record in csv_repository.list_records_sync(-100)] == [
        first.id
    ]


def test_legacy_category_is_migrated(
    csv_repository: CsvRecordsRepository,
) -> None:
    legacy = make_record().model_dump(mode="json")
    legacy["category"] = "salary"
    legacy.pop("categories")
    legacy.pop("tags")
    pd.DataFrame([legacy]).to_csv(csv_repository.records_path, index=False)

    records = csv_repository.read_records_sync()

    assert json.loads(records.iloc[0]["categories"]) == ["salary"]
    assert json.loads(records.iloc[0]["tags"]) == []
    assert "category" not in records.columns
    assert list(records.columns) == list(IncomeRecord.model_fields)

    # Reads normalize in memory only; the file is upgraded on the explicit
    # write-path step, never as a side effect of reading.
    csv_repository.migrate_records_sync()
    persisted = pd.read_csv(
        csv_repository.records_path,
        dtype=str,
        keep_default_na=False,
    )
    assert list(persisted.columns) == list(IncomeRecord.model_fields)


def test_legacy_records_without_income_date_are_migrated(
    csv_repository: CsvRecordsRepository,
) -> None:
    legacy = make_record().model_dump(mode="json")
    legacy["categories"] = json.dumps(legacy["categories"])
    legacy["tags"] = json.dumps(legacy["tags"])
    legacy.pop("income_date")
    pd.DataFrame([legacy]).to_csv(csv_repository.records_path, index=False)

    migrated = csv_repository.read_records_sync()

    assert migrated.iloc[0]["income_date"] == "2026-07-29"

    csv_repository.migrate_records_sync()
    persisted = pd.read_csv(csv_repository.records_path, dtype=str)
    assert "income_date" in persisted.columns


def test_legacy_records_without_type_are_migrated(
    csv_repository: CsvRecordsRepository,
) -> None:
    legacy = make_record().model_dump(mode="json")
    legacy["categories"] = json.dumps(legacy["categories"])
    legacy["tags"] = json.dumps(legacy["tags"])
    legacy.pop("type")
    pd.DataFrame([legacy]).to_csv(csv_repository.records_path, index=False)

    migrated = csv_repository.list_records_sync(-100)

    assert migrated[0].type == "income"

    csv_repository.migrate_records_sync()
    persisted = pd.read_csv(csv_repository.records_path, dtype=str)
    assert "type" in persisted.columns


def test_read_does_not_rewrite_records_file(
    csv_repository: CsvRecordsRepository,
) -> None:
    legacy = make_record().model_dump(mode="json")
    legacy["category"] = "salary"
    legacy.pop("categories")
    legacy.pop("tags")
    pd.DataFrame([legacy]).to_csv(csv_repository.records_path, index=False)
    before = csv_repository.records_path.read_text(encoding="utf-8")

    csv_repository.read_records_sync()

    # A plain read must never mutate the file on disk: the legacy schema stays
    # untouched until an explicit migration or write happens.
    after = csv_repository.records_path.read_text(encoding="utf-8")
    assert after == before
    assert "category" in after  # legacy column untouched, not migrated away


def test_update_does_not_mutate_changes(
    csv_repository: CsvRecordsRepository,
    saved_record: IncomeRecord,
) -> None:
    changes = {
        "amount": Decimal("1700"),
        "categories": ["sales", "debt"],
    }

    updated = csv_repository.update_record_sync(
        saved_record.id,
        changes,
        updated_by=9,
    )

    assert changes == {
        "amount": Decimal("1700"),
        "categories": ["sales", "debt"],
    }
    assert updated.amount == Decimal("1700")
    assert updated.categories == ["sales", "debt"]
    assert updated.updated_by == 9


def test_update_is_pydantic_validated(
    csv_repository: CsvRecordsRepository,
    saved_record: IncomeRecord,
) -> None:
    with pytest.raises(ValidationError):
        csv_repository.update_record_sync(
            saved_record.id,
            {"amount": Decimal("-1")},
            updated_by=9,
        )

    assert csv_repository.get_record_sync(saved_record.id) == saved_record


@pytest.mark.parametrize("field", ["ammount", "id", "chat_id", "source_index"])
def test_update_rejects_unknown_and_identity_fields(
    csv_repository: CsvRecordsRepository,
    saved_record: IncomeRecord,
    field: str,
) -> None:
    with pytest.raises(ValueError, match="cannot be updated"):
        csv_repository.update_record_sync(
            saved_record.id,
            {field: 999},
            updated_by=9,
        )

    assert csv_repository.get_record_sync(saved_record.id) == saved_record


def test_update_missing_record_raises_domain_error(
    csv_repository: CsvRecordsRepository,
) -> None:
    with pytest.raises(RecordNotFoundError, match="missing") as captured:
        csv_repository.update_record_sync("missing", {"amount": 1}, updated_by=9)

    assert captured.value.record_id == "missing"


def test_delete_is_idempotent_and_removes_notes(
    csv_repository: CsvRecordsRepository,
    saved_record: IncomeRecord,
) -> None:
    csv_repository.add_note_sync(
        RecordNote(record_id=saved_record.id, user_id=7, text="готівкою")
    )

    assert csv_repository.delete_record_sync(saved_record.id) is True
    assert csv_repository.delete_record_sync(saved_record.id) is False
    assert csv_repository.read_notes_sync().empty


def test_delete_retry_cleans_notes_after_second_write_failure(
    csv_repository: CsvRecordsRepository,
    saved_record: IncomeRecord,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    csv_repository.add_note_sync(
        RecordNote(record_id=saved_record.id, user_id=7, text="готівкою")
    )
    original_write = csv_repository._atomic_write
    calls = 0

    def fail_second_write(frame: pd.DataFrame, path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("notes write failed")
        original_write(frame, path)

    monkeypatch.setattr(csv_repository, "_atomic_write", fail_second_write)

    with pytest.raises(OSError, match="notes write failed"):
        csv_repository.delete_record_sync(saved_record.id)

    monkeypatch.setattr(csv_repository, "_atomic_write", original_write)
    assert csv_repository.delete_record_sync(saved_record.id) is False
    assert csv_repository.read_notes_sync().empty


def test_atomic_write_cleans_temporary_file_on_unexpected_error(
    csv_repository: CsvRecordsRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_to_csv(*args: object, **kwargs: object) -> None:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(pd.DataFrame, "to_csv", fail_to_csv)

    with pytest.raises(RuntimeError, match="unexpected"):
        csv_repository._atomic_write(
            pd.DataFrame(columns=["value"]),
            csv_repository.records_path,
        )

    assert list(csv_repository.records_path.parent.glob("*.tmp")) == []


def test_note_for_missing_record_raises_domain_error(
    csv_repository: CsvRecordsRepository,
) -> None:
    with pytest.raises(RecordNotFoundError, match="missing"):
        csv_repository.add_note_sync(
            RecordNote(record_id="missing", user_id=7, text="note")
        )


def test_list_notes_for_missing_record_raises_domain_error(
    csv_repository: CsvRecordsRepository,
) -> None:
    with pytest.raises(RecordNotFoundError, match="missing"):
        csv_repository.list_notes_sync("missing")


def test_export_snapshot_is_chat_scoped(
    csv_repository: CsvRecordsRepository,
) -> None:
    included = csv_repository.create_record_sync(make_record())
    excluded = csv_repository.create_record_sync(
        make_record(
            id="other",
            chat_id=-200,
            telegram_message_id=11,
        )
    )
    csv_repository.add_note_sync(
        RecordNote(record_id=included.id, user_id=7, text="included")
    )
    csv_repository.add_note_sync(
        RecordNote(record_id=excluded.id, user_id=8, text="excluded")
    )

    records, notes = csv_repository.export_snapshot_sync(-100)

    assert [record.id for record in records] == [included.id]
    assert [note.text for note in notes] == ["included"]


def test_chat_setting_is_persisted(
    csv_repository: CsvRecordsRepository,
) -> None:
    assert csv_repository.is_chat_enabled_sync(-100) is True

    setting = csv_repository.set_chat_enabled_sync(
        -100,
        enabled=False,
        updated_by=7,
    )

    assert setting.enabled is False
    assert setting.updated_by == 7
    assert csv_repository.is_chat_enabled_sync(-100) is False

    csv_repository.set_chat_enabled_sync(-200, enabled=True, updated_by=9)
    assert csv_repository.is_chat_enabled_sync(-200) is True
    assert csv_repository.is_chat_enabled_sync(-100) is False


def test_set_and_get_goal(
    csv_repository: CsvRecordsRepository,
) -> None:
    assert csv_repository.get_goal_sync(1) is None

    csv_repository.set_goal_sync(
        chat_id=1,
        amount=Decimal("50000"),
        currency="UAH",
        updated_by=7,
    )
    goal = csv_repository.get_goal_sync(1)

    assert goal is not None
    assert goal.amount == Decimal("50000")
    assert goal.currency == "UAH"
    assert goal.updated_by == 7


def test_set_goal_overwrites(
    csv_repository: CsvRecordsRepository,
) -> None:
    csv_repository.set_goal_sync(1, Decimal("100"), "UAH", 7)
    csv_repository.set_goal_sync(1, Decimal("200"), "UAH", 7)

    goal = csv_repository.get_goal_sync(1)

    assert goal is not None
    assert goal.amount == Decimal("200")


async def test_async_set_and_get_goal(
    csv_repository: CsvRecordsRepository,
) -> None:
    await csv_repository.set_goal(
        chat_id=1,
        amount=Decimal("300"),
        currency="UAH",
        updated_by=7,
    )
    goal = await csv_repository.get_goal(1)

    assert goal is not None
    assert goal.amount == Decimal("300")


def test_month_and_year_goals_are_independent(
    csv_repository: CsvRecordsRepository,
) -> None:
    csv_repository.set_goal_sync(1, Decimal("50000"), "UAH", 7, period="month")
    csv_repository.set_goal_sync(1, Decimal("600000"), "UAH", 7, period="year")

    assert csv_repository.get_goal_sync(1, "month").amount == Decimal("50000")  # type: ignore[union-attr]
    assert csv_repository.get_goal_sync(1, "year").amount == Decimal("600000")  # type: ignore[union-attr]


def _goal_row_without_period() -> dict[str, str]:
    return {
        "chat_id": "1",
        "amount": "50000",
        "currency": "UAH",
        "updated_by": "7",
        "updated_at": "2026-07-29T10:00:00+00:00",
    }


def test_legacy_goals_without_period_are_month(
    csv_repository: CsvRecordsRepository,
) -> None:
    cols = [column for column in GOAL_COLUMNS if column != "period"]
    frame = pd.DataFrame([_goal_row_without_period()], columns=cols)
    csv_repository.goals_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(csv_repository.goals_path, index=False)

    goal = csv_repository.get_goal_sync(1, "month")

    assert goal is not None
    assert goal.amount > 0
    assert goal.period == "month"


def test_missing_csv_columns_are_reported(
    csv_repository: CsvRecordsRepository,
) -> None:
    csv_repository.records_path.write_text("wrong,column\n1,2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="misses columns"):
        csv_repository.read_records_sync()


def test_damaged_csv_preserves_parser_error_as_cause(
    csv_repository: CsvRecordsRepository,
) -> None:
    csv_repository.records_path.write_text('"unterminated', encoding="utf-8")

    with pytest.raises(ValueError) as captured:
        csv_repository.read_records_sync()

    assert captured.value.__cause__ is not None


async def test_async_create_is_serialized_and_deduplicated(
    csv_repository: CsvRecordsRepository,
) -> None:
    first, second = await asyncio.gather(
        csv_repository.create_record(make_record(id="first")),
        csv_repository.create_record(make_record(id="second")),
    )
    listed = await csv_repository.list_records(-100)

    assert first.id == second.id
    assert len(listed) == 1
    assert listed[0].categories == ["salary", "debt"]
    assert listed[0].tags == ["card"]


def test_import_records_adds_and_dedupes(csv_repository: CsvRecordsRepository) -> None:
    record = make_record(telegram_message_id=10, source_index=0)

    assert csv_repository.import_records_sync([record]) == (1, 0)
    assert csv_repository.import_records_sync([record]) == (0, 1)

    listed = csv_repository.list_records_sync(record.chat_id)
    assert len(listed) == 1


def test_import_records_mixes_added_and_skipped(
    csv_repository: CsvRecordsRepository,
) -> None:
    existing = csv_repository.create_record_sync(
        make_record(id="existing", telegram_message_id=1, source_index=0)
    )
    fresh = make_record(id="fresh", telegram_message_id=2, source_index=0)

    added, skipped = csv_repository.import_records_sync([existing, fresh])

    assert (added, skipped) == (1, 1)
    listed = csv_repository.list_records_sync(existing.chat_id)
    assert {record.id for record in listed} == {"existing", "fresh"}


async def test_import_records_async_delegates_to_sync(
    csv_repository: CsvRecordsRepository,
) -> None:
    record = make_record(id="async-import", telegram_message_id=20, source_index=0)

    added, skipped = await csv_repository.import_records([record])

    assert (added, skipped) == (1, 0)
    listed = await csv_repository.list_records(record.chat_id)
    assert any(item.id == "async-import" for item in listed)
