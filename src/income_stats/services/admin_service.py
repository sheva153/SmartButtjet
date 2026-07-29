"""Per-chat recording state use cases."""

from income_stats.models import ChatSetting
from income_stats.repositories import RecordsRepository


class AdminService:
    def __init__(self, repository: RecordsRepository) -> None:
        self._repository = repository

    async def status(self, chat_id: int) -> bool:
        return await self._repository.is_chat_enabled(chat_id)

    async def set_status(
        self,
        chat_id: int,
        *,
        enabled: bool,
        updated_by: int,
    ) -> ChatSetting:
        return await self._repository.set_chat_enabled(
            chat_id,
            enabled=enabled,
            updated_by=updated_by,
        )
