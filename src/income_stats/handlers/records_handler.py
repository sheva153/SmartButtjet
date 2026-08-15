"""Thin record browsing and FSM edit handlers."""

from typing import cast

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from pydantic import ValidationError

from income_stats.bot.ui import (
    MENU_ANALYTICS,
    MENU_CHART,
    MENU_HELP,
    MENU_LABELS,
    MENU_RECORDS,
    EditState,
    RecordAction,
    format_record,
    record_keyboard,
    records_keyboard,
)
from income_stats.config import AppConfig
from income_stats.handlers.admin_handler import is_telegram_admin
from income_stats.handlers.analytics_handler import send_chart
from income_stats.models import IncomeRecord
from income_stats.repositories import RecordNotFoundError
from income_stats.services import AnalyticsService, RecordField, RecordsService
from income_stats.services.records_service import EditLockError

records_router = Router(name="records")


async def _record_for_query(
    query: CallbackQuery,
    service: RecordsService,
    record_id: str,
) -> IncomeRecord | None:
    if not isinstance(query.message, Message):
        await query.answer("Повідомлення більше недоступне.", show_alert=True)
        return None
    record = await service.get_record(record_id)
    if record is None or record.chat_id != query.message.chat.id:
        await query.answer("Запис не знайдено.", show_alert=True)
        return None
    return record


async def _can_delete(
    message: Message,
    user_id: int,
    record: IncomeRecord,
    config: AppConfig,
) -> bool:
    if config.permissions.author_can_delete and record.user_id == user_id:
        return True
    return config.permissions.admin_can_delete and await is_telegram_admin(
        message,
        user_id,
        config,
    )


async def _can_edit(
    message: Message,
    user_id: int,
    record: IncomeRecord,
    config: AppConfig,
) -> bool:
    if config.permissions.everyone_can_edit:
        return True
    if record.user_id == user_id:
        return True
    return await is_telegram_admin(message, user_id, config)


async def _clear_interaction(
    state: FSMContext, service: RecordsService, user_id: int
) -> None:
    data = await state.get_data()
    if record_id := data.get("record_id"):
        service.navigate_away(str(record_id), user_id)
    await state.clear()


async def handle_menu_during_interaction(
    message: Message,
    state: FSMContext,
    records_service: RecordsService,
    analytics_service: AnalyticsService | None = None,
) -> bool:
    if message.text not in MENU_LABELS:
        return False
    if message.from_user is not None:
        await _clear_interaction(state, records_service, message.from_user.id)
    else:
        await state.clear()
    if message.text == MENU_RECORDS:
        await send_records_page(message, records_service, 0)
    elif message.text == MENU_ANALYTICS and analytics_service is not None:
        await message.answer(await analytics_service.summary(message.chat.id))
    elif message.text == MENU_CHART and analytics_service is not None:
        try:
            await send_chart(message, analytics_service)
        except ValueError:
            await message.answer("Немає даних для діаграми.")
    elif message.text == MENU_HELP:
        await message.answer("Дію скасовано. Використай /help.")
    else:
        await message.answer("Дію скасовано. Обери розділ ще раз.")
    return True


async def send_records_page(
    message: Message, service: RecordsService, page: int
) -> None:
    result = await service.page(message.chat.id, page)
    if not result.records:
        await message.answer("Записів ще немає.")
        return
    await message.answer(
        f"🗂 Доходи · сторінка {result.page + 1}/{result.total_pages}\n\nОбери запис:",
        reply_markup=records_keyboard(result.records, result.page, result.total_pages),
    )


@records_router.message(Command("cancel"))
async def cancel_handler(
    message: Message, state: FSMContext, records_service: RecordsService
) -> None:
    if message.from_user is not None:
        await _clear_interaction(state, records_service, message.from_user.id)
    else:
        await state.clear()
    await message.answer("Дію скасовано.")


@records_router.message(EditState.waiting_value, F.text.in_(MENU_LABELS))
async def interaction_menu_handler(
    message: Message,
    state: FSMContext,
    records_service: RecordsService,
    analytics_service: AnalyticsService,
) -> None:
    await handle_menu_during_interaction(
        message,
        state,
        records_service,
        analytics_service,
    )


@records_router.message(Command("records"))
@records_router.message(F.text == MENU_RECORDS)
async def records_handler(message: Message, records_service: RecordsService) -> None:
    await send_records_page(message, records_service, 0)


@records_router.message(EditState.waiting_value, F.text)
async def edit_value_handler(
    message: Message,
    state: FSMContext,
    records_service: RecordsService,
    analytics_service: AnalyticsService,
) -> None:
    if await handle_menu_during_interaction(
        message, state, records_service, analytics_service
    ):
        return
    user = message.from_user
    if user is None or message.text is None:
        return
    data = await state.get_data()
    record_id = str(data["record_id"])
    field = str(data["field"])
    try:
        if field == "note":
            await records_service.add_note(
                record_id,
                user_id=user.id,
                username=user.username or "",
                text=message.text,
            )
            response = "📝 Нотатку додано."
        else:
            record = await records_service.update_field(
                record_id, cast(RecordField, field), message.text, user.id
            )
            response = format_record(record)
    except (ValueError, ValidationError):
        await message.answer("Некоректне значення. Спробуй ще раз.")
        return
    except EditLockError:
        await state.clear()
        await message.answer("Час редагування минув. Відкрий запис ще раз.")
        return
    except RecordNotFoundError:
        records_service.release_edit(record_id, user.id)
        await state.clear()
        await message.answer("Запис більше не існує.")
        return
    await state.clear()
    await message.answer(response)


async def _start_edit(
    query: CallbackQuery,
    state: FSMContext,
    service: RecordsService,
    config: AppConfig,
    record_id: str,
    field: str,
) -> None:
    record = await _record_for_query(query, service, record_id)
    if record is None:
        return
    user = query.from_user
    if not await _can_edit(cast(Message, query.message), user.id, record, config):
        await query.answer(
            "Редагувати може лише автор запису або адміністратор.",
            show_alert=True,
        )
        return
    if not service.acquire_edit(record_id, user.id):
        await query.answer("Цей запис зараз редагує інший учасник.", show_alert=True)
        return
    await state.set_state(EditState.waiting_value)
    await state.set_data({"record_id": record_id, "field": field})
    await query.answer()
    if isinstance(query.message, Message):
        await query.message.answer("Надішли нове значення або /cancel.")


@records_router.callback_query(RecordAction.filter(F.action == "edit"))
async def edit_callback(
    query: CallbackQuery,
    callback_data: RecordAction,
    state: FSMContext,
    records_service: RecordsService,
    app_config: AppConfig,
) -> None:
    await _start_edit(
        query,
        state,
        records_service,
        app_config,
        callback_data.record_id,
        callback_data.value,
    )


@records_router.callback_query(RecordAction.filter(F.action == "note"))
async def note_callback(
    query: CallbackQuery,
    callback_data: RecordAction,
    state: FSMContext,
    records_service: RecordsService,
    app_config: AppConfig,
) -> None:
    await _start_edit(
        query, state, records_service, app_config, callback_data.record_id, "note"
    )


@records_router.callback_query(RecordAction.filter(F.action == "open"))
async def open_callback(
    query: CallbackQuery, callback_data: RecordAction, records_service: RecordsService
) -> None:
    record = await _record_for_query(query, records_service, callback_data.record_id)
    if record is None:
        return
    await query.answer()
    if isinstance(query.message, Message):
        await query.message.edit_text(
            format_record(record), reply_markup=record_keyboard(record)
        )


@records_router.callback_query(RecordAction.filter(F.action == "notes"))
async def notes_callback(
    query: CallbackQuery,
    callback_data: RecordAction,
    records_service: RecordsService,
) -> None:
    record = await _record_for_query(query, records_service, callback_data.record_id)
    if record is None:
        return
    try:
        notes = await records_service.list_notes(record.id)
    except RecordNotFoundError:
        await query.answer("Запис більше не існує.", show_alert=True)
        return
    if notes:
        lines = [f"• {note.text} — @{note.username or note.user_id}" for note in notes]
        text = f"📋 Нотатки до #{record.id[:8]}\n\n" + "\n".join(lines)
    else:
        text = f"📋 Нотатки до #{record.id[:8]}\n\nНотаток ще немає."
    builder = InlineKeyboardBuilder()
    builder.button(
        text="➕ Додати",
        callback_data=RecordAction(action="note", record_id=record.id),
    )
    builder.button(
        text="⬅️ До запису",
        callback_data=RecordAction(action="open", record_id=record.id),
    )
    await query.answer()
    if isinstance(query.message, Message):
        await query.message.edit_text(text, reply_markup=builder.as_markup())


@records_router.callback_query(RecordAction.filter(F.action == "records"))
async def records_page_callback(
    query: CallbackQuery,
    callback_data: RecordAction,
    records_service: RecordsService,
) -> None:
    if not isinstance(query.message, Message):
        await query.answer("Повідомлення більше недоступне.", show_alert=True)
        return
    try:
        page = int(callback_data.value or "0")
    except ValueError:
        page = 0
    await query.answer()
    await send_records_page(query.message, records_service, page)


@records_router.callback_query(RecordAction.filter(F.action == "confirm_delete"))
async def confirm_delete_callback(
    query: CallbackQuery,
    callback_data: RecordAction,
    records_service: RecordsService,
    app_config: AppConfig,
) -> None:
    record = await _record_for_query(query, records_service, callback_data.record_id)
    if record is None or not isinstance(query.message, Message):
        return
    if not await _can_delete(
        query.message,
        query.from_user.id,
        record,
        app_config,
    ):
        await query.answer("Недостатньо прав.", show_alert=True)
        return
    deleted = await records_service.delete(record.id)
    await query.answer(
        "Запис видалено." if deleted else "Запис уже видалено.", show_alert=True
    )
    # Drop the inline keyboard so the now-deleted record has no live buttons to
    # tap (each would only round-trip to a "record not found" reply).
    if isinstance(query.message, Message):
        await query.message.edit_reply_markup(reply_markup=None)


@records_router.callback_query(RecordAction.filter(F.action == "delete"))
async def delete_callback(
    query: CallbackQuery,
    callback_data: RecordAction,
    records_service: RecordsService,
    app_config: AppConfig,
) -> None:
    record = await _record_for_query(query, records_service, callback_data.record_id)
    if record is None:
        return
    user = query.from_user
    allowed = isinstance(query.message, Message) and await _can_delete(
        query.message,
        user.id,
        record,
        app_config,
    )
    if not allowed:
        await query.answer("Недостатньо прав.", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Так, видалити",
        callback_data=RecordAction(action="confirm_delete", record_id=record.id),
    )
    builder.button(
        text="↩️ Скасувати",
        callback_data=RecordAction(action="open", record_id=record.id),
    )
    await query.answer()
    if isinstance(query.message, Message):
        await query.message.edit_reply_markup(reply_markup=builder.as_markup())
