from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from app.schemas import SearchParams
from app.service import CardSearchService


class StubRepository:
    def __init__(self) -> None:
        self.received_ids: Mapping[str, list[int] | None] | None = None
        self.translation_searches = 0

    async def search_translation_matches(self, params: SearchParams) -> dict[str, list[int] | None]:
        self.translation_searches += 1
        return {"oracle-1": None}

    async def search_oracle_cards(
        self, params: SearchParams, oracle_ids: Mapping[str, list[int] | None] | None = None
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

    assert repository.received_ids == {"oracle-1": None}
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
    assert repository.received_ids == {"oracle-1": None}
    assert result[0].name == "Raio"


class NoTranslationRepository(StubRepository):
    async def search_translation_matches(self, params: SearchParams) -> dict[str, list[int] | None]:
        self.translation_searches += 1
        return {}


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


class MultifaceRepository(StubRepository):
    async def get_by_oracle_id(self, oracle_id):
        return {
            "id": "printing-1",
            "oracle_id": oracle_id,
            "name": "Front // Back",
            "cmc": 2,
            "card_faces": [
                {
                    "name": "Front",
                    "oracle_text": "English",
                    "flavor_text": "English flavor",
                    "mana_cost": "{1}{U}",
                    "colors": ["U"],
                    "power": "2",
                    "toughness": "2",
                },
                {
                    "name": "Back",
                    "oracle_text": "Flying",
                    "type_line": "Creature",
                    "mana_cost": "",
                    "colors": ["U"],
                    "power": "3",
                    "toughness": "3",
                },
            ],
        }

    async def search_oracle_cards(self, params, oracle_ids=None):
        return [await self.get_by_oracle_id("oracle-1")]

    async def get_translations(self, oracle_ids, lang):
        return {
            "oracle-1": {
                "name": "Frente // Verso",
                "card_faces": [
                    {"face_index": 1, "name": "Verso", "oracle_text": "Voar"},
                    {"face_index": 0, "name": "Frente"},
                ],
            }
        }


@pytest.mark.asyncio
async def test_multiface_localized_get_and_search_preserve_mechanics():
    repository = MultifaceRepository()
    service = CardSearchService(repository)
    original = await repository.get_by_oracle_id("oracle-1")
    card = await service.get_by_oracle_id("oracle-1", "pt-BR")
    assert card.name == "Frente // Verso"
    faces = card.model_dump()["card_faces"]
    assert faces[0]["name"] == "Frente"
    assert faces[0]["oracle_text"] is None
    assert faces[0]["flavor_text"] is None
    assert faces[1]["oracle_text"] == "Voar"
    assert faces[1]["type_line"] is None
    for index, face in enumerate(faces):
        for field in ("mana_cost", "colors", "power", "toughness"):
            assert face[field] == original["card_faces"][index][field]
    assert original["card_faces"][0]["name"] == "Front"
    assert (await service.search(SearchParams(name="Frente")))[0] == card
    english = await service.get_by_oracle_id("oracle-1", "en")
    assert english.model_dump()["card_faces"] == original["card_faces"]


@pytest.mark.asyncio
async def test_incomplete_face_translation_is_unavailable():
    from unittest.mock import AsyncMock

    repository = MultifaceRepository()
    repository.get_translations = AsyncMock(
        return_value={
            "oracle-1": {"name": "Incompleta", "card_faces": [{"face_index": 0, "name": "Frente"}]}
        }
    )
    service = CardSearchService(repository)
    assert await service.get_by_oracle_id("oracle-1", "pt-BR") is None
    assert await service.search(SearchParams(name="Incompleta")) == []
