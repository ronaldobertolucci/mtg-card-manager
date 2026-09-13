from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from app.schemas import SearchParams
from app.service import CardSearchService


class StubRepository:
    def __init__(self) -> None:
        self.received_ids: Sequence[str] | None = None
        self.translation_searches = 0

    async def search_translation_oracle_ids(self, params: SearchParams) -> list[str]:
        self.translation_searches += 1
        return ["oracle-1"]

    async def search_oracle_cards(
        self, params: SearchParams, oracle_ids: Sequence[str] | None = None
    ) -> list[dict[str, Any]]:
        self.received_ids = oracle_ids
        return [
            {
                "_id": "oracle-1",
                "id": "card-1",
                "oracle_id": "oracle-1",
                "name": "Lightning Bolt",
                "oracle_text": "Deal 3 damage.",
                "type_line": "Instant",
                "flavor_text": "English flavor text.",
                "colors": ["R"],
                "cmc": 1,
            }
        ]

    async def get_translations(
        self, oracle_ids: Sequence[str], lang: str
    ) -> Mapping[str, dict[str, Any]]:
        return {
            "oracle-1": {
                "oracle_id": "oracle-1",
                "lang": "pt-BR",
                "name": "Raio",
                "oracle_text": "Causa 3 pontos de dano.",
                "type_line": "Mágica Instantânea",
            }
        }


@pytest.mark.asyncio
async def test_non_english_text_search_uses_two_steps_and_merges_translation() -> None:
    repository = StubRepository()
    service = CardSearchService(repository)

    result = await service.search(SearchParams(lang="pt-BR", name="raio"))

    assert repository.received_ids == ["oracle-1"]
    assert result[0].id == "card-1"
    assert result[0].oracle_id == "oracle-1"
    assert result[0].name == "Raio"
    assert result[0].oracle_text == "Causa 3 pontos de dano."
    assert result[0].flavor_text is None
    assert result[0].lang == "pt-BR"


@pytest.mark.asyncio
async def test_english_search_does_not_query_translations() -> None:
    repository = StubRepository()
    service = CardSearchService(repository)

    result = await service.search(SearchParams(lang="en", name="bolt"))

    assert repository.received_ids is None
    assert repository.translation_searches == 0
    assert result[0].name == "Lightning Bolt"


@pytest.mark.asyncio
async def test_non_english_attribute_search_requires_translation() -> None:
    repository = StubRepository()
    service = CardSearchService(repository)

    result = await service.search(SearchParams(lang="pt-BR", colors="R"))

    assert repository.translation_searches == 1
    assert repository.received_ids == ["oracle-1"]
    assert result[0].name == "Raio"


class NoTranslationRepository(StubRepository):
    async def search_translation_oracle_ids(self, params: SearchParams) -> list[str]:
        self.translation_searches += 1
        return []


@pytest.mark.asyncio
async def test_non_english_search_without_translations_returns_no_cards() -> None:
    repository = NoTranslationRepository()
    service = CardSearchService(repository)

    result = await service.search(SearchParams(lang="pt-BR", colors="U"))

    assert result == []
    assert repository.translation_searches == 1
    assert repository.received_ids is None


class TranslationRemovedRepository(StubRepository):
    async def get_translations(
        self, oracle_ids: Sequence[str], lang: str
    ) -> Mapping[str, dict[str, Any]]:
        return {}


@pytest.mark.asyncio
async def test_does_not_fall_back_to_english_if_translation_disappears() -> None:
    service = CardSearchService(TranslationRemovedRepository())

    result = await service.search(SearchParams(lang="pt-BR", colors="R"))

    assert result == []
