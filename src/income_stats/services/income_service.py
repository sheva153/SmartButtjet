"""Income capture use case."""

from datetime import date

from income_stats.config import IncomeConfig
from income_stats.models import IncomeRecord
from income_stats.parsers import parse_income_message
from income_stats.repositories import RecordsRepository


class IncomeService:
    """Parse one message and persist each detected amount in source order."""

    def __init__(
        self,
        repository: RecordsRepository,
        config: IncomeConfig,
    ) -> None:
        self._repository = repository
        self._config = config

    async def capture(
        self,
        *,
        text: str,
        telegram_message_id: int,
        chat_id: int,
        user_id: int,
        username: str,
        today: date,
    ) -> list[IncomeRecord]:
        parsed = parse_income_message(text, self._config, today=today)
        created: list[IncomeRecord] = []
        for source_index, item in enumerate(parsed):
            if item.amount is None:
                continue
            record = IncomeRecord(
                telegram_message_id=telegram_message_id,
                source_index=source_index,
                chat_id=chat_id,
                user_id=user_id,
                username=username,
                original_text=text,
                amount=item.amount,
                currency=item.currency,
                categories=item.categories,
                tags=item.tags,
                description=item.description,
                income_date=item.income_date,
                updated_by=user_id,
            )
            created.append(await self._repository.create_record(record))
        return created
