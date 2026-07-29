"""Repository protocol and concurrency-safe CSV implementation."""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast, runtime_checkable

import pandas as pd
from pydantic import BaseModel

from income_stats.config import StorageConfig
from income_stats.models import ChatSetting, IncomeRecord, RecordNote, normalize_label

RECORD_COLUMNS = list(IncomeRecord.model_fields)
NOTE_COLUMNS = list(RecordNote.model_fields)
CHAT_SETTING_COLUMNS = list(ChatSetting.model_fields)
EDITABLE_RECORD_FIELDS = frozenset(
    {
        "amount",
        "currency",
        "categories",
        "tags",
        "income_date",
        "description",
    }
)

_CSV_READ_ERRORS = (
    OSError,
    UnicodeError,
    pd.errors.ParserError,
    pd.errors.EmptyDataError,
)


class RecordNotFoundError(LookupError):
    """Raised when a record disappears before a dependent write."""

    def __init__(self, record_id: str) -> None:
        self.record_id = record_id
        super().__init__(f"Record not found: {record_id}")


@runtime_checkable
class RecordsRepository(Protocol):
    async def create_record(self, record: IncomeRecord) -> IncomeRecord: ...

    async def get_record(self, record_id: str) -> IncomeRecord | None: ...

    async def list_records(self, chat_id: int) -> list[IncomeRecord]: ...

    async def update_record(
        self,
        record_id: str,
        changes: Mapping[str, object],
        updated_by: int,
    ) -> IncomeRecord: ...

    async def delete_record(self, record_id: str) -> bool: ...

    async def add_note(self, note: RecordNote) -> RecordNote: ...

    async def list_notes(self, record_id: str) -> list[RecordNote]: ...

    async def is_chat_enabled(self, chat_id: int) -> bool: ...

    async def set_chat_enabled(
        self,
        chat_id: int,
        enabled: bool,
        updated_by: int,
    ) -> ChatSetting: ...


def _encode_cell(value: object) -> str:
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _to_row(model: BaseModel) -> dict[str, str]:
    return {
        key: _encode_cell(value) for key, value in model.model_dump(mode="json").items()
    }


def _decode_string_list(value: object, *, field: str) -> list[str]:
    try:
        decoded = json.loads(str(value))
    except (json.JSONDecodeError, TypeError) as error:
        raise ValueError(f"Invalid JSON list in {field}") from error
    if not isinstance(decoded, list) or not all(
        isinstance(item, str) for item in decoded
    ):
        raise ValueError(f"{field} must contain a JSON string list")
    return decoded


class CsvRecordsRepository:
    """Persist records, notes, and chat settings in atomic CSV files.

    Async adapters deliberately execute these small local-file critical
    sections directly: the asyncio lock serializes service callers, while the
    re-entrant thread lock also protects synchronous CLI and test callers.
    """

    def __init__(self, config: StorageConfig) -> None:
        self.records_path = config.records_file
        self.notes_path = config.notes_file
        self.chat_settings_path = config.chat_settings_file
        self.export_directory = config.export_directory
        self._async_lock = asyncio.Lock()
        self._sync_lock = threading.RLock()

    @staticmethod
    def _read_existing(path: Path) -> pd.DataFrame:
        try:
            return cast(
                pd.DataFrame,
                pd.read_csv(path, dtype=str, keep_default_na=False),
            )
        except _CSV_READ_ERRORS as error:
            raise ValueError(f"Cannot read CSV {path}: {error}") from error

    @classmethod
    def _read(cls, path: Path, columns: list[str]) -> pd.DataFrame:
        if not path.exists():
            return pd.DataFrame(columns=columns)
        frame = cls._read_existing(path)
        missing = set(columns) - set(frame.columns)
        if missing:
            raise ValueError(f"CSV {path} misses columns: {sorted(missing)}")
        return cast(pd.DataFrame, frame.loc[:, columns].copy())

    @staticmethod
    def _atomic_write(frame: pd.DataFrame, path: Path) -> None:
        temporary: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                frame.to_csv(stream, index=False)
            temporary.replace(path)
        except (OSError, UnicodeError) as error:
            raise ValueError(f"Cannot write CSV {path}: {error}") from error
        finally:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)

    @classmethod
    def _migrate_records(
        cls,
        frame: pd.DataFrame,
        path: Path,
    ) -> pd.DataFrame:
        migrated = False
        if "income_date" not in frame.columns and "created_at" in frame.columns:
            frame["income_date"] = pd.to_datetime(
                frame["created_at"], errors="raise", utc=True
            ).dt.date.astype(str)
            migrated = True

        if "categories" not in frame.columns and "category" in frame.columns:
            frame["categories"] = frame["category"].map(
                lambda value: json.dumps(
                    [normalize_label(value)] if str(value).strip() else ["other"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            migrated = True
        if "tags" not in frame.columns and (
            "categories" in frame.columns or "category" in frame.columns
        ):
            frame["tags"] = "[]"
            migrated = True
        if "category" in frame.columns:
            frame = cast(pd.DataFrame, frame.drop(columns=["category"]))
            migrated = True

        missing = set(RECORD_COLUMNS) - set(frame.columns)
        if missing:
            raise ValueError(f"CSV {path} misses columns: {sorted(missing)}")
        ordered = cast(pd.DataFrame, frame.loc[:, RECORD_COLUMNS].copy())
        if migrated or list(frame.columns) != RECORD_COLUMNS:
            cls._atomic_write(ordered, path)
        return ordered

    @staticmethod
    def _record_from_row(row: Mapping[str, object]) -> IncomeRecord:
        payload = dict(row)
        payload["categories"] = _decode_string_list(
            payload["categories"],
            field="categories",
        )
        payload["tags"] = _decode_string_list(payload["tags"], field="tags")
        return IncomeRecord.model_validate(payload)

    def _read_records_unlocked(self) -> pd.DataFrame:
        if not self.records_path.exists():
            return pd.DataFrame(columns=RECORD_COLUMNS)
        frame = self._read_existing(self.records_path)
        return self._migrate_records(frame, self.records_path)

    def read_records_sync(self) -> pd.DataFrame:
        with self._sync_lock:
            return self._read_records_unlocked()

    def read_notes_sync(self) -> pd.DataFrame:
        with self._sync_lock:
            return self._read(self.notes_path, NOTE_COLUMNS)

    def read_chat_settings_sync(self) -> pd.DataFrame:
        with self._sync_lock:
            return self._read(self.chat_settings_path, CHAT_SETTING_COLUMNS)

    def get_record_sync(self, record_id: str) -> IncomeRecord | None:
        with self._sync_lock:
            frame = self._read_records_unlocked()
            rows = frame[frame["id"] == record_id]
            if rows.empty:
                return None
            return self._record_from_row(rows.iloc[-1].to_dict())

    def list_records_sync(self, chat_id: int) -> list[IncomeRecord]:
        with self._sync_lock:
            frame = self._read_records_unlocked()
            rows = frame[frame["chat_id"] == str(chat_id)]
            return [self._record_from_row(row.to_dict()) for _, row in rows.iterrows()]

    def create_record_sync(self, record: IncomeRecord) -> IncomeRecord:
        with self._sync_lock:
            frame = self._read_records_unlocked()
            duplicate = frame[
                (frame["chat_id"] == str(record.chat_id))
                & (frame["telegram_message_id"] == str(record.telegram_message_id))
                & (frame["source_index"] == str(record.source_index))
            ]
            if not duplicate.empty:
                return self._record_from_row(duplicate.iloc[-1].to_dict())
            frame = pd.concat(
                [frame, pd.DataFrame([_to_row(record)], columns=RECORD_COLUMNS)],
                ignore_index=True,
            )
            self._atomic_write(frame, self.records_path)
            return record

    def update_record_sync(
        self,
        record_id: str,
        changes: Mapping[str, object],
        updated_by: int,
    ) -> IncomeRecord:
        with self._sync_lock:
            invalid_fields = set(changes) - EDITABLE_RECORD_FIELDS
            if invalid_fields:
                raise ValueError(
                    f"Record fields cannot be updated: {sorted(invalid_fields)}"
                )
            frame = self._read_records_unlocked()
            indexes = frame.index[frame["id"] == record_id].tolist()
            if not indexes:
                raise RecordNotFoundError(record_id)
            index = indexes[-1]
            current = self._record_from_row(frame.loc[index].to_dict())
            payload = {
                **current.model_dump(),
                **dict(changes),
                "updated_at": datetime.now(UTC),
                "updated_by": updated_by,
            }
            updated = IncomeRecord.model_validate(payload)
            row = _to_row(updated)
            frame.loc[index, RECORD_COLUMNS] = [
                row[column] for column in RECORD_COLUMNS
            ]
            self._atomic_write(frame, self.records_path)
            return updated

    def delete_record_sync(self, record_id: str) -> bool:
        with self._sync_lock:
            records = self._read_records_unlocked()
            notes = self._read(self.notes_path, NOTE_COLUMNS)
            record_exists = bool((records["id"] == record_id).any())
            related_notes_exist = bool((notes["record_id"] == record_id).any())

            if not record_exists:
                if related_notes_exist:
                    notes = cast(
                        pd.DataFrame,
                        notes.loc[notes["record_id"] != record_id].copy(),
                    )
                    self._atomic_write(notes, self.notes_path)
                return False

            records = cast(
                pd.DataFrame,
                records.loc[records["id"] != record_id].copy(),
            )
            notes = cast(
                pd.DataFrame,
                notes.loc[notes["record_id"] != record_id].copy(),
            )
            self._atomic_write(records, self.records_path)
            self._atomic_write(notes, self.notes_path)
            return True

    def add_note_sync(self, note: RecordNote) -> RecordNote:
        with self._sync_lock:
            records = self._read_records_unlocked()
            if not (records["id"] == note.record_id).any():
                raise RecordNotFoundError(note.record_id)
            notes = self._read(self.notes_path, NOTE_COLUMNS)
            notes = pd.concat(
                [notes, pd.DataFrame([_to_row(note)], columns=NOTE_COLUMNS)],
                ignore_index=True,
            )
            self._atomic_write(notes, self.notes_path)
            return note

    def list_notes_sync(self, record_id: str) -> list[RecordNote]:
        with self._sync_lock:
            frame = self._read(self.notes_path, NOTE_COLUMNS)
            rows = frame[frame["record_id"] == record_id]
            return [
                RecordNote.model_validate(row.to_dict()) for _, row in rows.iterrows()
            ]

    def get_chat_setting_sync(self, chat_id: int) -> ChatSetting | None:
        with self._sync_lock:
            frame = self._read(self.chat_settings_path, CHAT_SETTING_COLUMNS)
            rows = frame[frame["chat_id"] == str(chat_id)]
            if rows.empty:
                return None
            return ChatSetting.model_validate(rows.iloc[-1].to_dict())

    def is_chat_enabled_sync(self, chat_id: int) -> bool:
        setting = self.get_chat_setting_sync(chat_id)
        return setting.enabled if setting else True

    def set_chat_enabled_sync(
        self,
        chat_id: int,
        enabled: bool,
        updated_by: int,
    ) -> ChatSetting:
        with self._sync_lock:
            frame = self._read(self.chat_settings_path, CHAT_SETTING_COLUMNS)
            setting = ChatSetting(
                chat_id=chat_id,
                enabled=enabled,
                updated_by=updated_by,
            )
            row = _to_row(setting)
            indexes = frame.index[frame["chat_id"] == str(chat_id)].tolist()
            if indexes:
                frame.loc[indexes[-1], CHAT_SETTING_COLUMNS] = [
                    row[column] for column in CHAT_SETTING_COLUMNS
                ]
            else:
                frame = pd.concat(
                    [
                        frame,
                        pd.DataFrame([row], columns=CHAT_SETTING_COLUMNS),
                    ],
                    ignore_index=True,
                )
            self._atomic_write(frame, self.chat_settings_path)
            return setting

    async def create_record(self, record: IncomeRecord) -> IncomeRecord:
        async with self._async_lock:
            return self.create_record_sync(record)

    async def get_record(self, record_id: str) -> IncomeRecord | None:
        async with self._async_lock:
            return self.get_record_sync(record_id)

    async def list_records(self, chat_id: int) -> list[IncomeRecord]:
        async with self._async_lock:
            return self.list_records_sync(chat_id)

    async def update_record(
        self,
        record_id: str,
        changes: Mapping[str, object],
        updated_by: int,
    ) -> IncomeRecord:
        async with self._async_lock:
            return self.update_record_sync(record_id, changes, updated_by)

    async def delete_record(self, record_id: str) -> bool:
        async with self._async_lock:
            return self.delete_record_sync(record_id)

    async def add_note(self, note: RecordNote) -> RecordNote:
        async with self._async_lock:
            return self.add_note_sync(note)

    async def list_notes(self, record_id: str) -> list[RecordNote]:
        async with self._async_lock:
            return self.list_notes_sync(record_id)

    async def is_chat_enabled(self, chat_id: int) -> bool:
        async with self._async_lock:
            return self.is_chat_enabled_sync(chat_id)

    async def set_chat_enabled(
        self,
        chat_id: int,
        enabled: bool,
        updated_by: int,
    ) -> ChatSetting:
        async with self._async_lock:
            return self.set_chat_enabled_sync(chat_id, enabled, updated_by)
