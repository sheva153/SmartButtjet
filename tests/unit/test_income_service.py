from datetime import date
from decimal import Decimal
from typing import cast

import pytest

from income_stats.config import IncomeConfig
from income_stats.models import IncomeRecord, RecordNote
from income_stats.repositories import RecordsRepository
from income_stats.services.income_service import IncomeService


class FakeIncomeRepository:
    def __init__(self) -> None:
        self.records: list[IncomeRecord] = []
        self.create_order: list[int] = []
        self.tags: dict[str, list[str]] = {}
        self.list_tags_calls = 0

    async def list_tags(self) -> dict[str, list[str]]:
        self.list_tags_calls += 1
        return self.tags

    async def create_record(self, record: IncomeRecord) -> IncomeRecord:
        self.create_order.append(record.source_index)
        for existing in self.records:
            if (
                existing.chat_id,
                existing.telegram_message_id,
                existing.source_index,
            ) == (
                record.chat_id,
                record.telegram_message_id,
                record.source_index,
            ):
                return existing
        self.records.append(record)
        return record

    async def export_snapshot(
        self,
        chat_id: int,
    ) -> tuple[list[IncomeRecord], list[RecordNote]]:
        return [record for record in self.records if record.chat_id == chat_id], []


@pytest.fixture
def income_config() -> IncomeConfig:
    return IncomeConfig(
        categories={
            "salary": ["зп", "зарплата"],
            "debt": ["борг"],
            "other": [],
        },
        tags={"card": ["картка", "на картку"]},
    )


@pytest.fixture
def income_service(income_config: IncomeConfig) -> IncomeService:
    repository = FakeIncomeRepository()
    return IncomeService(cast(RecordsRepository, repository), income_config)


async def test_capture_persists_each_unprotected_amount_sequentially(
    income_config: IncomeConfig,
) -> None:
    repository = FakeIncomeRepository()
    service = IncomeService(cast(RecordsRepository, repository), income_config)

    records = await service.capture(
        text="зп 500 і 300 на картку",
        telegram_message_id=10,
        chat_id=-100,
        user_id=7,
        username="felix",
        today=date(2026, 7, 29),
    )

    assert [record.amount for record in records] == [
        Decimal("500.00"),
        Decimal("300.00"),
    ]
    assert repository.create_order == [0, 1]
    assert all(record.categories == ["salary"] for record in records)
    assert all(record.tags == ["card"] for record in records)
    assert all(record.username == "felix" for record in records)
    assert all(record.original_text == "зп 500 і 300 на картку" for record in records)
    assert all(record.income_date == date(2026, 7, 29) for record in records)
    assert [
        (
            record.telegram_message_id,
            record.source_index,
            record.chat_id,
            record.user_id,
            record.updated_by,
        )
        for record in records
    ] == [
        (10, 0, -100, 7, 7),
        (10, 1, -100, 7, 7),
    ]


async def test_capture_ignores_messages_with_only_protected_numbers(
    income_config: IncomeConfig,
) -> None:
    repository = FakeIncomeRepository()
    service = IncomeService(cast(RecordsRepository, repository), income_config)

    records = await service.capture(
        text="зустріч о 15:30, телефон +380 67 123 45 67",
        telegram_message_id=11,
        chat_id=-100,
        user_id=7,
        username="felix",
        today=date(2026, 7, 29),
    )

    assert records == []
    assert repository.records == []


async def test_capture_returns_existing_deduplicated_records(
    income_config: IncomeConfig,
) -> None:
    repository = FakeIncomeRepository()
    service = IncomeService(cast(RecordsRepository, repository), income_config)
    arguments = {
        "text": "500",
        "telegram_message_id": 10,
        "chat_id": -100,
        "user_id": 7,
        "username": "felix",
        "today": date(2026, 7, 29),
    }

    first = await service.capture(**arguments)
    second = await service.capture(**arguments)

    assert second == first
    assert len(repository.records) == 1


async def test_capture_preserves_expense_type(income_service: IncomeService) -> None:
    records = await income_service.capture(
        text="-500 таксі",
        telegram_message_id=10,
        chat_id=1,
        user_id=1,
        username="u",
        today=date(2026, 8, 17),
    )
    assert records[0].type == "expense"


async def test_capture_applies_runtime_tags_from_repository(
    income_config: IncomeConfig,
) -> None:
    repository = FakeIncomeRepository()
    repository.tags = {"gym": ["зал"]}
    service = IncomeService(cast(RecordsRepository, repository), income_config)

    records = await service.capture(
        text="500 зал",
        telegram_message_id=10,
        chat_id=-100,
        user_id=7,
        username="felix",
        today=date(2026, 7, 29),
    )

    assert repository.list_tags_calls == 1
    assert records[0].tags == ["gym"]
