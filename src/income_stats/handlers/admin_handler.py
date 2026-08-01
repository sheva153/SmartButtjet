"""Help and recording-state Telegram handlers."""

from aiogram import F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import Message
from loguru import logger

from income_stats.bot.ui import MENU_HELP, main_menu
from income_stats.config import AppConfig
from income_stats.services import AdminService

admin_router = Router(name="admin")


async def is_telegram_admin(message: Message, user_id: int, config: AppConfig) -> bool:
    if user_id in config.bot.admin_user_ids:
        return True
    bot = message.bot
    if bot is None:
        return False
    try:
        member = await bot.get_chat_member(message.chat.id, user_id)
    except TelegramAPIError:
        logger.bind(chat_id=message.chat.id, user_id=user_id).warning(
            "Telegram admin lookup failed"
        )
        return False
    return member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}


@admin_router.message(Command("help"))
@admin_router.message(F.text == MENU_HELP)
async def help_handler(message: Message) -> None:
    await message.answer(
        "Надішли суму доходу. Команди: /status, /turn_on, /turn_off, "
        "/stats, /chart, /export, /cancel.",
        reply_markup=main_menu(),
    )


@admin_router.message(Command("status"))
async def status_handler(message: Message, admin_service: AdminService) -> None:
    enabled = await admin_service.status(message.chat.id)
    await message.answer(
        "Запис доходів увімкнено." if enabled else "Запис доходів вимкнено."
    )


async def _set_status(
    message: Message, service: AdminService, config: AppConfig, enabled: bool
) -> None:
    user = message.from_user
    if user is None or not await is_telegram_admin(message, user.id, config):
        await message.answer("Ця команда лише для адміністраторів.")
        return
    await service.set_status(message.chat.id, enabled=enabled, updated_by=user.id)
    await message.answer(
        "Запис доходів увімкнено." if enabled else "Запис доходів вимкнено."
    )


@admin_router.message(Command("turn_on"))
async def turn_on_handler(
    message: Message, admin_service: AdminService, app_config: AppConfig
) -> None:
    await _set_status(message, admin_service, app_config, True)


@admin_router.message(Command("turn_off"))
async def turn_off_handler(
    message: Message, admin_service: AdminService, app_config: AppConfig
) -> None:
    await _set_status(message, admin_service, app_config, False)
