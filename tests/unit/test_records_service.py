import asyncio
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from income_stats.models import ChatSetting, IncomeRecord, RecordNote
from income_stats.repositories import RecordNotFoundError
from income_stats.services.records_service import (
    EditLockError,
    EditLocks,
    RecordField,
    RecordsService,
)


def make_record(index: int = 0, **changes: object) -> IncomeRecord:
    values: dict[str, object] = {
        "id": f"record-{index}",
        "telegram_message_id": 10 + index,
        "source_index": 0,
        "chat_id": -100,
        "user_id": 7,
        "username": "felix",
        "original_text": "500",
        "amount": Decimal("500"),
        "currency": "UAH",
        "categories": ["other"],
        "tags": [],
        "income_date": date(2026, 7, 20) + timedelta(days=index),
        "created_at": datetime(2026, 7, 20 + index, 10, tzinfo=UTC),
        "updated_at": datetime(2026, 7, 20 + index, 10, tzinfo=UTC),
        "updated_by": 7,
    }
    values.update(changes)
    return IncomeRecord.model_validate(values)


class FakeRecordsRepository:
    def __init__(self, records: list[IncomeRecord]) -> None:
        self.records = {record.id: record for record in records}
        self.notes: list[RecordNote] = []

    async def create_record(self, record: IncomeRecord) -> IncomeRecord:
        self.records[record.id] = record
        return record

    async def get_record(self, record_id: str) -> IncomeRecord | None:
        return self.records.get(record_id)

    async def list_records(self, chat_id: int) -> list[IncomeRecord]:
        return [record for record in self.records.values() if record.chat_id == chat_id]

    async def update_record(
        self,
        record_id: str,
        changes: Mapping[str, object],
        updated_by: int,
    ) -> IncomeRecord:
        current = self.records.get(record_id)
        if current is None:
            raise RecordNotFoundError(record_id)
        updated = IncomeRecord.model_validate(
            {
                **current.model_dump(),
                **dict(changes),
                "updated_by": updated_by,
                "updated_at": datetime.now(UTC),
            }
        )
        self.records[record_id] = updated
        return updated

    async def delete_record(self, record_id: str) -> bool:
        return self.records.pop(record_id, None) is not None

    async def add_note(self, note: RecordNote) -> RecordNote:
        if note.record_id not in self.records:
            raise RecordNotFoundError(note.record_id)
        self.notes.append(note)
        return note

    async def list_notes(self, record_id: str) -> list[RecordNote]:
        if record_id not in self.records:
            raise RecordNotFoundError(record_id)
        return [note for note in self.notes if note.record_id == record_id]

    async def is_chat_enabled(self, chat_id: int) -> bool:
        return True

    async def set_chat_enabled(
        self,
        chat_id: int,
        enabled: bool,
        updated_by: int,
    ) -> ChatSetting:
        return ChatSetting(
            chat_id=chat_id,
            enabled=enabled,
            updated_by=updated_by,
        )


@pytest.fixture
def record() -> IncomeRecord:
    return make_record()


@pytest.fixture
def repository(record: IncomeRecord) -> FakeRecordsRepository:
    return FakeRecordsRepository([record])


@pytest.fixture
def records_service(repository: FakeRecordsRepository) -> RecordsService:
    return RecordsService(repository, edit_lock_seconds=120)


async def test_repeated_delete_has_stable_result(
    records_service: RecordsService,
    record: IncomeRecord,
) -> None:
    assert await records_service.delete(record.id) is True
    assert await records_service.delete(record.id) is False


async def test_invalid_edit_keeps_lock(
    records_service: RecordsService,
    record: IncomeRecord,
) -> None:
    assert records_service.acquire_edit(record.id, user_id=7)

    with pytest.raises(ValueError):
        await records_service.update_field(record.id, "amount", "zero", 7)

    assert not records_service.acquire_edit(record.id, user_id=8)


async def test_non_owner_cannot_update_locked_record(
    records_service: RecordsService,
    record: IncomeRecord,
) -> None:
    assert records_service.acquire_edit(record.id, user_id=7)

    with pytest.raises(EditLockError):
        await records_service.update_field(
            record.id,
            "amount",
            "999",
            user_id=8,
        )

    unchanged = await records_service.get(record.id)
    assert unchanged is not None
    assert unchanged.amount == Decimal("500")


async def test_expired_owner_cannot_update_after_reacquisition(
    record: IncomeRecord,
) -> None:
    current = datetime(2026, 7, 29, 10, tzinfo=UTC)
    locks = EditLocks(clock=lambda: current)
    service = RecordsService(
        FakeRecordsRepository([record]),
        edit_lock_seconds=60,
        edit_locks=locks,
    )
    assert service.acquire_edit(record.id, user_id=7)
    current += timedelta(seconds=61)
    assert service.acquire_edit(record.id, user_id=8)

    with pytest.raises(EditLockError):
        await service.update_field(record.id, "amount", "999", user_id=7)


async def test_owner_cannot_update_if_lease_changes_during_read(
    record: IncomeRecord,
) -> None:
    current = datetime(2026, 7, 29, 10, tzinfo=UTC)
    repository = FakeRecordsRepository([record])
    read_started = asyncio.Event()
    allow_read = asyncio.Event()
    original_get = repository.get_record

    async def paused_get(record_id: str) -> IncomeRecord | None:
        read_started.set()
        await allow_read.wait()
        return await original_get(record_id)

    repository.get_record = paused_get  # type: ignore[method-assign]
    service = RecordsService(
        repository,
        edit_lock_seconds=60,
        edit_locks=EditLocks(clock=lambda: current),
    )
    assert service.acquire_edit(record.id, user_id=7)
    update = asyncio.create_task(
        service.update_field(record.id, "amount", "999", user_id=7)
    )
    await read_started.wait()
    current += timedelta(seconds=61)
    assert service.acquire_edit(record.id, user_id=8)
    allow_read.set()

    with pytest.raises(EditLockError):
        await update

    unchanged = await service.get(record.id)
    assert unchanged is not None
    assert unchanged.amount == Decimal("500")


async def test_navigation_cannot_release_pin_during_update(
    record: IncomeRecord,
) -> None:
    repository = FakeRecordsRepository([record])
    update_started = asyncio.Event()
    allow_update = asyncio.Event()
    original_update = repository.update_record

    async def paused_update(
        record_id: str,
        changes: Mapping[str, object],
        updated_by: int,
    ) -> IncomeRecord:
        update_started.set()
        await allow_update.wait()
        return await original_update(record_id, changes, updated_by)

    repository.update_record = paused_update  # type: ignore[method-assign]
    service = RecordsService(repository, edit_lock_seconds=60)
    assert service.acquire_edit(record.id, user_id=7)
    update = asyncio.create_task(
        service.update_field(record.id, "amount", "999", user_id=7)
    )
    await update_started.wait()

    service.navigate_away(record.id, user_id=7)
    assert not service.acquire_edit(record.id, user_id=8)

    allow_update.set()
    assert (await update).amount == Decimal("999")
    assert service.acquire_edit(record.id, user_id=8)


@pytest.mark.parametrize(
    ("field", "raw", "expected"),
    [
        ("amount", "1 500,25", Decimal("1500.25")),
        ("currency", " usd ", "USD"),
        ("categories", "Salary, Debt, salary", ["salary", "debt"]),
        ("tags", "Card, cash, card", ["card", "cash"]),
        ("income_date", "29.07.2026", date(2026, 7, 29)),
        ("description", "  консультація  ", "консультація"),
    ],
)
async def test_update_field_parses_and_releases_lock(
    records_service: RecordsService,
    record: IncomeRecord,
    field: RecordField,
    raw: str,
    expected: object,
) -> None:
    assert records_service.acquire_edit(record.id, user_id=7)

    updated = await records_service.update_field(
        record.id,
        field,
        raw,
        user_id=7,
        today=date(2026, 7, 29),
    )

    assert getattr(updated, field) == expected
    assert records_service.acquire_edit(record.id, user_id=8)


async def test_missing_update_releases_lock(
    records_service: RecordsService,
    repository: FakeRecordsRepository,
    record: IncomeRecord,
) -> None:
    assert records_service.acquire_edit(record.id, user_id=7)
    repository.records.clear()

    with pytest.raises(RecordNotFoundError):
        await records_service.update_field(
            record.id,
            "description",
            "new",
            user_id=7,
        )

    assert records_service.acquire_edit(record.id, user_id=8)


async def test_large_amount_edit_round_trips(
    records_service: RecordsService,
    record: IncomeRecord,
) -> None:
    assert records_service.acquire_edit(record.id, user_id=7)

    updated = await records_service.update_field(
        record.id,
        "amount",
        "123456789012345678901234567890.12",
        user_id=7,
    )

    assert updated.amount == Decimal("123456789012345678901234567890.12")


async def test_invalid_model_value_keeps_lock(
    records_service: RecordsService,
    record: IncomeRecord,
) -> None:
    assert records_service.acquire_edit(record.id, user_id=7)

    with pytest.raises(ValidationError):
        await records_service.update_field(
            record.id,
            "currency",
            "EURO",
            user_id=7,
        )

    assert not records_service.acquire_edit(record.id, user_id=8)


async def test_add_note_translates_missing_record_and_releases_lock(
    records_service: RecordsService,
    repository: FakeRecordsRepository,
    record: IncomeRecord,
) -> None:
    assert records_service.acquire_edit(record.id, user_id=7)
    repository.records.clear()

    with pytest.raises(RecordNotFoundError):
        await records_service.add_note(
            record.id,
            user_id=7,
            username="felix",
            text="note",
        )

    assert records_service.acquire_edit(record.id, user_id=8)


async def test_note_success_and_missing_list_have_domain_outcomes(
    records_service: RecordsService,
    repository: FakeRecordsRepository,
    record: IncomeRecord,
) -> None:
    assert records_service.acquire_edit(record.id, user_id=7)
    note = await records_service.add_note(
        record.id,
        user_id=7,
        username="felix",
        text="  paid in cash  ",
    )

    assert note.text == "paid in cash"
    assert await records_service.list_notes(record.id) == [note]
    assert records_service.acquire_edit(record.id, user_id=8)

    repository.records.clear()
    with pytest.raises(RecordNotFoundError):
        await records_service.list_notes(record.id)


async def test_non_owner_cannot_add_note(
    records_service: RecordsService,
    repository: FakeRecordsRepository,
    record: IncomeRecord,
) -> None:
    assert records_service.acquire_edit(record.id, user_id=7)

    with pytest.raises(EditLockError):
        await records_service.add_note(
            record.id,
            user_id=8,
            username="other",
            text="note",
        )

    assert repository.notes == []


async def test_page_is_latest_first_and_clamped() -> None:
    records = [make_record(index) for index in range(5)]
    service = RecordsService(FakeRecordsRepository(records), page_size=2)

    first = await service.page(-100, page=-5)
    last = await service.page(-100, page=99)

    assert [record.id for record in first.records] == ["record-4", "record-3"]
    assert first.page == 0
    assert first.total_pages == 3
    assert [record.id for record in last.records] == ["record-0"]
    assert last.page == 2


async def test_page_sorts_mixed_naive_and_aware_timestamps() -> None:
    same_day = date(2026, 7, 29)
    records = [
        make_record(
            1,
            income_date=same_day,
            created_at=datetime(2026, 7, 29, 9),
        ),
        make_record(
            2,
            income_date=same_day,
            created_at=datetime(2026, 7, 29, 10, tzinfo=UTC),
        ),
    ]

    page = await RecordsService(FakeRecordsRepository(records)).page(-100)

    assert [item.id for item in page.records] == ["record-2", "record-1"]


@pytest.mark.parametrize("raw", ["29.07/2026", "29/07-2026", "29-07.2026"])
async def test_date_edit_rejects_mixed_separators(
    records_service: RecordsService,
    record: IncomeRecord,
    raw: str,
) -> None:
    assert records_service.acquire_edit(record.id, user_id=7)

    with pytest.raises(ValueError, match="Date must use"):
        await records_service.update_field(
            record.id,
            "income_date",
            raw,
            user_id=7,
        )


def test_lock_cancel_navigation_and_expiry_release() -> None:
    now = datetime(2026, 7, 29, 10, tzinfo=UTC)
    locks = EditLocks(clock=lambda: now)
    service = RecordsService(
        FakeRecordsRepository([]),
        edit_lock_seconds=60,
        edit_locks=locks,
    )
    assert service.acquire_edit("one", user_id=7)
    service.cancel_edit("one", user_id=7)
    assert service.acquire_edit("one", user_id=8)
    service.navigate_away("one", user_id=8)
    assert service.acquire_edit("one", user_id=9)

    expired_locks = EditLocks(clock=lambda: now)
    assert expired_locks.acquire("two", user_id=7, seconds=0)
    assert expired_locks.acquire("two", user_id=8, seconds=60)


def test_page_size_is_bounded() -> None:
    with pytest.raises(ValueError, match="page_size"):
        RecordsService(FakeRecordsRepository([]), page_size=0)
    with pytest.raises(ValueError, match="page_size"):
        RecordsService(FakeRecordsRepository([]), page_size=101)
