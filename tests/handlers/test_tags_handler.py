from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from aiogram.filters import CommandObject
from aiogram.types import Message

from income_stats.config import AppConfig, IncomeConfig
from income_stats.handlers.tags_handler import tags_handler, tags_router
from income_stats.models import TagAlias
from income_stats.repositories import RecordsRepository


def test_tags_handler_is_importable() -> None:
    assert callable(tags_handler)
    assert tags_router.name == "tags"


def test_tags_router_is_registered_before_income_catch_all() -> None:
    from income_stats.handlers import income_router, routers

    # income_router's catch-all F.text handler must not shadow /tags: it
    # returns (rather than raising SkipHandler) on `text.startswith("/")`,
    # which aiogram treats as fully handled, so command routers registered
    # after it would never see command text.
    assert routers.index(tags_router) < routers.index(income_router)


@pytest.mark.asyncio
async def test_tags_list_merges_config_and_runtime_tags() -> None:
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        chat=SimpleNamespace(id=-100),
        answer=AsyncMock(),
    )
    repository = SimpleNamespace(
        list_tags=AsyncMock(return_value={"gym": ["зал", "спортзал"]})
    )
    config = AppConfig(income=IncomeConfig(tags={"card": ["картка"]}))

    await tags_handler(
        cast(Message, message),
        CommandObject(command="tags", args=None),
        cast(RecordsRepository, repository),
        config,
    )

    repository.list_tags.assert_awaited_once()
    sent = message.answer.await_args.args[0]
    assert "card" in sent
    assert "картка" in sent
    assert "gym" in sent
    assert "зал" in sent
    assert "спортзал" in sent


@pytest.mark.asyncio
async def test_tags_list_without_any_tags_says_none_configured() -> None:
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        chat=SimpleNamespace(id=-100),
        answer=AsyncMock(),
    )
    repository = SimpleNamespace(list_tags=AsyncMock(return_value={}))
    config = AppConfig(income=IncomeConfig(tags={}))

    await tags_handler(
        cast(Message, message),
        CommandObject(command="tags", args=None),
        cast(RecordsRepository, repository),
        config,
    )

    message.answer.assert_awaited_once_with("Теги ще не задані.")


@pytest.mark.asyncio
async def test_tags_add_admin_calls_add_tag_and_confirms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin_lookup = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "income_stats.handlers.tags_handler.is_telegram_admin", admin_lookup
    )
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        chat=SimpleNamespace(id=-100),
        answer=AsyncMock(),
    )
    repository = SimpleNamespace(
        add_tag=AsyncMock(
            return_value=TagAlias(tag="gym", aliases=["зал", "спортзал"], updated_by=7)
        )
    )
    config = AppConfig()

    await tags_handler(
        cast(Message, message),
        CommandObject(command="tags", args="add gym зал спортзал"),
        cast(RecordsRepository, repository),
        config,
    )

    admin_lookup.assert_awaited_once_with(cast(Message, message), 7, config)
    repository.add_tag.assert_awaited_once_with("gym", ["зал", "спортзал"], 7)
    message.answer.assert_awaited_once()
    sent = message.answer.await_args.args[0]
    assert "gym" in sent
    assert "зал" in sent
    assert "спортзал" in sent


@pytest.mark.asyncio
async def test_tags_add_rejects_non_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    admin_lookup = AsyncMock(return_value=False)
    monkeypatch.setattr(
        "income_stats.handlers.tags_handler.is_telegram_admin", admin_lookup
    )
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        chat=SimpleNamespace(id=-100),
        answer=AsyncMock(),
    )
    repository = SimpleNamespace(add_tag=AsyncMock())
    config = AppConfig()

    await tags_handler(
        cast(Message, message),
        CommandObject(command="tags", args="add gym зал"),
        cast(RecordsRepository, repository),
        config,
    )

    repository.add_tag.assert_not_awaited()
    message.answer.assert_awaited_once_with("Ця команда лише для адміністраторів.")


@pytest.mark.asyncio
async def test_tags_add_rejects_message_without_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin_lookup = AsyncMock()
    monkeypatch.setattr(
        "income_stats.handlers.tags_handler.is_telegram_admin", admin_lookup
    )
    message = SimpleNamespace(
        from_user=None,
        chat=SimpleNamespace(id=-100),
        answer=AsyncMock(),
    )
    repository = SimpleNamespace(add_tag=AsyncMock())
    config = AppConfig()

    await tags_handler(
        cast(Message, message),
        CommandObject(command="tags", args="add gym зал"),
        cast(RecordsRepository, repository),
        config,
    )

    admin_lookup.assert_not_awaited()
    repository.add_tag.assert_not_awaited()
    message.answer.assert_awaited_once_with("Ця команда лише для адміністраторів.")


@pytest.mark.asyncio
async def test_tags_add_bad_usage_hints_format(monkeypatch: pytest.MonkeyPatch) -> None:
    admin_lookup = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "income_stats.handlers.tags_handler.is_telegram_admin", admin_lookup
    )
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        chat=SimpleNamespace(id=-100),
        answer=AsyncMock(),
    )
    repository = SimpleNamespace(add_tag=AsyncMock())
    config = AppConfig()

    await tags_handler(
        cast(Message, message),
        CommandObject(command="tags", args="add gym"),
        cast(RecordsRepository, repository),
        config,
    )

    repository.add_tag.assert_not_awaited()
    message.answer.assert_awaited_once_with("Формат: /tags add <тег> <аліас> [аліас…]")


@pytest.mark.asyncio
async def test_tags_unknown_subcommand_hints_format() -> None:
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        chat=SimpleNamespace(id=-100),
        answer=AsyncMock(),
    )
    repository = SimpleNamespace(add_tag=AsyncMock(), list_tags=AsyncMock())
    config = AppConfig()

    await tags_handler(
        cast(Message, message),
        CommandObject(command="tags", args="rename gym"),
        cast(RecordsRepository, repository),
        config,
    )

    repository.add_tag.assert_not_awaited()
    repository.list_tags.assert_not_awaited()
    message.answer.assert_awaited_once_with("Формат: /tags add <тег> <аліас> [аліас…]")
