"""/retag command: admin-only backfill of tags across this chat's records."""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from loguru import logger

from income_stats.bot.ui import short_id
from income_stats.config import AppConfig
from income_stats.handlers.admin_handler import is_telegram_admin
from income_stats.parsers import detect_tags, merge_extra_tags
from income_stats.repositories import RecordsRepository

retag_router = Router(name="retag")

# Telegram messages cap at ~4096 chars; cap the per-record detail so a big
# backfill still fits in one reply (the summary line always shows the totals).
_MAX_DELTA_LINES = 25


@retag_router.message(Command("retag"))
async def retag_handler(
    message: Message,
    repository: RecordsRepository,
    app_config: AppConfig,
) -> None:
    """Re-detect tags from each record's original text and add the new ones.

    Scoped to the chat the command runs in; existing tags are kept (union),
    never removed. Admin-only, mirroring /import and /tags add.
    """
    user = message.from_user
    if user is None or not await is_telegram_admin(message, user.id, app_config):
        await message.answer("Ця команда лише для адміністраторів.")
        return

    taxonomy = merge_extra_tags(app_config.income.tags, await repository.list_tags())
    result = await repository.retag_records(
        taxonomy, detect_tags, chat_id=message.chat.id
    )
    logger.bind(
        chat_id=message.chat.id,
        user_id=user.id,
        changed=result.changed,
        total=result.total,
    ).info("Retag processed")

    lines = [f"Оновлено {result.changed} з {result.total}"]
    for record, added in result.deltas[:_MAX_DELTA_LINES]:
        lines.append(
            f"{short_id(record.id)} {record.income_date:%d.%m.%Y}: "
            f"+[{', '.join(added)}]"
        )
    if len(result.deltas) > _MAX_DELTA_LINES:
        lines.append(f"…та ще {len(result.deltas) - _MAX_DELTA_LINES}")
    await message.answer("\n".join(lines))
