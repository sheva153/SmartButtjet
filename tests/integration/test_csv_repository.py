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
    persisted = pd.read_csv(
        csv_repository.records_path,
        dtype=str,
        keep_default_na=False,
    )
    assert list(persisted.columns) == list(IncomeRecord.model_fields)


def test_update_does_not_mutate_changes(
    csv_repository: CsvRecordsRepository,
    saved_record: IncomeRecord,
) -> None:
    changes = {"categories": ["salary", "debt"]}

    updated = csv_repository.update_record_sync(
        saved_record.id,
        changes,
        updated_by=9,
    )

    assert changes == {"categories": ["salary", "debt"]}
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
    assert csv_repository.is_chat_enabled_sync(-100) is False


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
