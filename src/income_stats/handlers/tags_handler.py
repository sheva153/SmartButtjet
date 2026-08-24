"""/tags command: list configured and runtime-defined tags with their aliases."""

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from income_stats.config import AppConfig
from income_stats.handlers.admin_handler import is_telegram_admin
from income_stats.parsers import merge_extra_tags
from income_stats.repositories import RecordsRepository

tags_router = Router(name="tags")

_USAGE_HINT = "Формат: /tags add <тег> <аліас> [аліас…]"


async def _merged_tags(
    app_config: AppConfig, repository: RecordsRepository
) -> dict[str, list[str]]:
    """Merge configured tags with runtime-defined ones from the tag store.

    Runtime aliases extend the configured aliases for a given tag rather than
    replacing them — same taxonomy the parser matches on, so /tags shows
    exactly what /income would detect.
    """
    return merge_extra_tags(app_config.income.tags, await repository.list_tags())


def _render_tags(tags: dict[str, list[str]]) -> str:
    if not tags:
        return "Теги ще не задані."
    lines = [
        f"🏷 {tag}: {', '.join(aliases)}" if aliases else f"🏷 {tag}"
        for tag, aliases in sorted(tags.items())
    ]
    return "Теги:\n" + "\n".join(lines)


@tags_router.message(Command("tags"))
async def tags_handler(
    message: Message,
    command: CommandObject,
    repository: RecordsRepository,
    app_config: AppConfig,
) -> None:
    args = (command.args or "").split()
    if not args:
        tags = await _merged_tags(app_config, repository)
        await message.answer(_render_tags(tags))
        return

    if args[0].casefold() != "add":
        await message.answer(_USAGE_HINT)
        return

    user = message.from_user
    if user is None or not await is_telegram_admin(message, user.id, app_config):
        await message.answer("Ця команда лише для адміністраторів.")
        return

    parts = args[1:]
    tag = parts[0] if parts else ""
    aliases = parts[1:]
    if not tag.strip() or not aliases or not all(alias.strip() for alias in aliases):
        await message.answer(_USAGE_HINT)
        return

    tag_alias = await repository.add_tag(tag, aliases, user.id)
    await message.answer(
        f"🏷 Тег «{tag_alias.tag}» оновлено: {', '.join(tag_alias.aliases)}"
    )
