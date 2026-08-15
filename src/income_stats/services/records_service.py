"""Record browsing, editing, notes, deletion, and edit locks."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from re import Pattern
from re import compile as compile_pattern
from typing import Literal

from income_stats.models import IncomeRecord, RecordNote
from income_stats.repositories import RecordNotFoundError, RecordsRepository

RecordField = Literal[
    "amount",
    "currency",
    "categories",
    "tags",
    "income_date",
    "description",
]

_EDITABLE_FIELDS: frozenset[str] = frozenset(
    {
        "amount",
        "currency",
        "categories",
        "tags",
        "income_date",
        "description",
    }
)
_DATE_INPUT_PATTERN: Pattern[str] = compile_pattern(
    r"(?P<day>\d{1,2})(?P<separator>[./-])(?P<month>\d{1,2})"
    r"(?:(?P=separator)(?P<year>\d{4}))?"
)
_DECIMAL_PLACES = Decimal("0.01")


@dataclass(frozen=True)
class RecordPage:
    records: list[IncomeRecord]
    page: int
    total_pages: int


class EditLockError(RuntimeError):
    """Raised when a caller no longer owns a record's active edit lock."""

    def __init__(self, record_id: str) -> None:
        self.record_id = record_id
        super().__init__(f"Record is locked by another user: {record_id}")


class EditLocks:
    """In-memory record edit ownership with deterministic expiry."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._locks: dict[str, tuple[int, datetime]] = {}
        self._in_progress: set[str] = set()
        self._clock = clock or (lambda: datetime.now(UTC))

    def _active_owner(
        self,
        record_id: str,
        *,
        now: datetime | None = None,
    ) -> tuple[int, datetime] | None:
        owner = self._locks.get(record_id)
        if (
            owner is not None
            and record_id not in self._in_progress
            and owner[1] <= (now or self._clock())
        ):
            self._locks.pop(record_id, None)
            return None
        return owner

    def acquire(self, record_id: str, user_id: int, seconds: int) -> bool:
        now = self._clock()
        owner = self._active_owner(record_id, now=now)
        if owner is not None and owner[0] != user_id:
            return False
        if record_id in self._in_progress:
            return owner is not None and owner[0] == user_id
        self._locks[record_id] = (
            user_id,
            now + timedelta(seconds=seconds),
        )
        return True

    def release(self, record_id: str, user_id: int) -> None:
        owner = self._active_owner(record_id)
        if owner is not None and owner[0] == user_id:
            if record_id in self._in_progress:
                return
            self._locks.pop(record_id, None)

    def release_record(self, record_id: str) -> None:
        if record_id in self._in_progress:
            return
        self._locks.pop(record_id, None)

    def is_owned_by(self, record_id: str, user_id: int) -> bool:
        owner = self._active_owner(record_id)
        return owner is not None and owner[0] == user_id

    def begin_operation(self, record_id: str, user_id: int) -> bool:
        """Pin a valid lease across repository awaits."""
        owner = self._active_owner(record_id)
        if owner is None or owner[0] != user_id or record_id in self._in_progress:
            return False
        self._in_progress.add(record_id)
        return True

    def abort_operation(self, record_id: str, user_id: int, seconds: int) -> None:
        """Unpin a failed operation while keeping the owner's edit lease."""
        owner = self._locks.get(record_id)
        if owner is None or owner[0] != user_id:
            return
        self._in_progress.discard(record_id)
        self._locks[record_id] = (
            user_id,
            self._clock() + timedelta(seconds=seconds),
        )

    def complete_operation(self, record_id: str, user_id: int) -> None:
        """Release a pin and its lease from the operation's own completion path."""
        owner = self._locks.get(record_id)
        if owner is None or owner[0] != user_id:
            return
        self._in_progress.discard(record_id)
        self._locks.pop(record_id, None)


class RecordsService:
    """Coordinate record operations without Telegram or CSV dependencies."""

    def __init__(
        self,
        repository: RecordsRepository,
        *,
        edit_lock_seconds: int = 120,
        page_size: int = 8,
        edit_locks: EditLocks | None = None,
    ) -> None:
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        if edit_lock_seconds < 1:
            raise ValueError("edit_lock_seconds must be positive")
        self._repository = repository
        self._edit_lock_seconds = edit_lock_seconds
        self._page_size = page_size
        self._edit_locks = edit_locks or EditLocks()

    async def get_record(self, record_id: str) -> IncomeRecord | None:
        return await self._repository.get_record(record_id)

    async def get(self, record_id: str) -> IncomeRecord | None:
        return await self.get_record(record_id)

    async def list_records(self, chat_id: int) -> list[IncomeRecord]:
        records = await self._repository.list_records(chat_id)
        return sorted(
            records,
            key=lambda record: (
                record.income_date,
                _utc_timestamp(record.created_at),
            ),
            reverse=True,
        )

    async def page(self, chat_id: int, page: int = 0) -> RecordPage:
        records = await self.list_records(chat_id)
        total_pages = max(
            1,
            (len(records) + self._page_size - 1) // self._page_size,
        )
        bounded_page = max(0, min(page, total_pages - 1))
        start = bounded_page * self._page_size
        return RecordPage(
            records=records[start : start + self._page_size],
            page=bounded_page,
            total_pages=total_pages,
        )

    async def list_notes(self, record_id: str) -> list[RecordNote]:
        return await self._repository.list_notes(record_id)

    def acquire_edit(self, record_id: str, user_id: int) -> bool:
        return self._edit_locks.acquire(
            record_id,
            user_id,
            self._edit_lock_seconds,
        )

    def release_edit(self, record_id: str, user_id: int) -> None:
        self._edit_locks.release(record_id, user_id)

    def cancel_edit(self, record_id: str, user_id: int) -> None:
        self.release_edit(record_id, user_id)

    def navigate_away(self, record_id: str, user_id: int) -> None:
        self.release_edit(record_id, user_id)

    async def update_field(
        self,
        record_id: str,
        field: RecordField,
        raw_value: str,
        user_id: int,
        *,
        today: date | None = None,
    ) -> IncomeRecord:
        if field not in _EDITABLE_FIELDS:
            raise ValueError(f"Unsupported record field: {field}")
        if not self._edit_locks.is_owned_by(record_id, user_id):
            raise EditLockError(record_id)
        value = self._parse_field(field, raw_value, today=today)
        current = await self._repository.get_record(record_id)
        if current is None:
            self.release_edit(record_id, user_id)
            raise RecordNotFoundError(record_id)
        if not self._edit_locks.begin_operation(record_id, user_id):
            raise EditLockError(record_id)
        release_lock = False
        try:
            validated = IncomeRecord.model_validate(
                {
                    **current.model_dump(),
                    field: value,
                }
            )
            value = getattr(validated, field)
            updated = await self._repository.update_record(
                record_id,
                {field: value},
                updated_by=user_id,
            )
        except RecordNotFoundError:
            release_lock = True
            raise
        else:
            release_lock = True
            return updated
        finally:
            if release_lock:
                self._edit_locks.complete_operation(record_id, user_id)
            else:
                self._edit_locks.abort_operation(
                    record_id,
                    user_id,
                    self._edit_lock_seconds,
                )

    async def add_note(
        self,
        record_id: str,
        *,
        user_id: int,
        username: str,
        text: str,
    ) -> RecordNote:
        if not self._edit_locks.begin_operation(record_id, user_id):
            raise EditLockError(record_id)
        release_lock = False
        try:
            note = RecordNote(
                record_id=record_id,
                user_id=user_id,
                username=username,
                text=text,
            )
            saved = await self._repository.add_note(note)
        except RecordNotFoundError:
            release_lock = True
            raise
        else:
            release_lock = True
            return saved
        finally:
            if release_lock:
                self._edit_locks.complete_operation(record_id, user_id)
            else:
                self._edit_locks.abort_operation(
                    record_id,
                    user_id,
                    self._edit_lock_seconds,
                )

    async def delete(self, record_id: str) -> bool:
        deleted = await self._repository.delete_record(record_id)
        self._edit_locks.release_record(record_id)
        return deleted

    @staticmethod
    def _parse_field(
        field: RecordField,
        raw_value: str,
        *,
        today: date | None,
    ) -> object:
        raw = raw_value.strip()
        if field == "amount":
            return _parse_amount(raw)
        if field == "currency":
            return raw.upper()
        if field in {"categories", "tags"}:
            if not raw:
                return []
            return [value.strip() for value in raw.split(",")]
        if field == "income_date":
            return _parse_date(raw, today=today)
        return raw


def _parse_amount(raw: str) -> Decimal:
    compact = raw.replace(" ", "").replace("\u00a0", "").replace(",", ".")
    try:
        with localcontext() as context:
            context.prec = max(
                28,
                sum(character.isdigit() for character in compact) + 2,
            )
            amount = Decimal(compact)
            if not amount.is_finite() or amount <= 0:
                raise ValueError("Amount must be positive")
            return amount.quantize(_DECIMAL_PLACES)
    except InvalidOperation as error:
        raise ValueError(f"Invalid amount: {raw}") from error


def _utc_timestamp(value: datetime) -> float:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).timestamp()


def _parse_date(raw: str, *, today: date | None) -> date:
    matched = _DATE_INPUT_PATTERN.fullmatch(raw)
    if matched is None:
        raise ValueError("Date must use DD.MM or DD.MM.YYYY")
    current_date = today or datetime.now(UTC).date()
    try:
        return date(
            int(matched.group("year") or current_date.year),
            int(matched.group("month")),
            int(matched.group("day")),
        )
    except ValueError as error:
        raise ValueError(f"Invalid income date: {raw}") from error
