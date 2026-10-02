"""Pagination regressions with invalid translations spanning candidate batches."""

from copy import deepcopy

import pytest

from app.schemas import SearchParams
from app.service import CardSearchService


class PaginationRepository:
    def __init__(self, size=405, valid_indices=(200, 400)):
        self.cards = [
            {"id": f"printing-{index}", "oracle_id": f"oracle-{index}",
             "name": f"Card {index:04}", "lang": "en",
             "card_faces": [{"name": "Front"}, {"name": "Back"}]}
            for index in range(size)
        ]
        self.translations = {
            card["oracle_id"]: {
                "card_faces": [{"face_index": 0, "name": "Frente"}]
            } for card in self.cards
        }
        for index in valid_indices:
            self.translations[f"oracle-{index}"]["card_faces"].append(
                {"face_index": 1, "name": "Verso"}
            )
        self.calls = []

    async def search_translation_matches(self, params):
        return {card["oracle_id"]: None for card in self.cards}

    async def search_oracle_cards(self, params, oracle_ids=None):
        self.calls.append((params.offset, params.limit))
        return self.cards[params.offset:params.offset + params.limit]

    async def get_translations(self, oracle_ids, lang):
        return {key: self.translations[key] for key in oracle_ids if key in self.translations}


@pytest.mark.parametrize("offset,expected,has_next", [
    (0, ["oracle-200"], True), (1, ["oracle-400"], False), (2, [], False), (1000, [], False),
])
async def test_translated_pagination_skips_whole_invalid_batches(offset, expected, has_next):
    repository = PaginationRepository()
    service = CardSearchService(repository)
    page = await service.search(SearchParams(limit=1, offset=offset))
    assert [card.oracle_id for card in page.items] == expected
    assert page.has_next is has_next
    assert page.limit == 1 and page.offset == offset
    assert repository.calls == [(0, 200), (200, 200), (400, 200)]


async def test_invalid_tail_does_not_claim_next_page():
    repository = PaginationRepository(valid_indices=(1, 2))
    page = await CardSearchService(repository).search(SearchParams(limit=2))
    assert [card.oracle_id for card in page.items] == ["oracle-1", "oracle-2"]
    assert not page.has_next


async def test_all_invalid_or_disappeared_translations_are_an_empty_page():
    repository = PaginationRepository(valid_indices=())
    repository.translations.pop("oracle-1")
    page = await CardSearchService(repository).search(SearchParams())
    assert page.model_dump(by_alias=True) == {
        "items": [], "limit": 50, "offset": 0, "hasNext": False,
    }


@pytest.mark.parametrize("size,offset,limit,count,has_next", [
    (201, 0, 200, 200, True), (200, 0, 200, 200, False),
    (200, 199, 2, 1, False), (200, 200, 2, 0, False),
])
async def test_english_lookahead_and_last_page(size, offset, limit, count, has_next):
    repository = PaginationRepository(size=size, valid_indices=())
    page = await CardSearchService(repository).search(
        SearchParams(lang="en", offset=offset, limit=limit)
    )
    assert len(page.items) == count
    assert page.has_next is has_next
    assert repository.calls == [(offset, limit + 1)]


@pytest.mark.parametrize("invalid", [
    {"card_faces": None}, {"card_faces": "broken"}, {"card_faces": [None, {}]},
    {"card_faces": [{"face_index": [], "name": "Frente"}, {"face_index": 1, "name": "Verso"}]},
    {"card_faces": [{"face_index": True, "name": "Frente"}, {"face_index": 0, "name": "Verso"}]},
    {"card_faces": [{"face_index": 0, "name": "Frente"}, {"face_index": 0, "name": "Verso"}]},
    {"card_faces": [{"face_index": 0, "name": " "}, {"face_index": 1, "name": "Verso"}]},
    {"card_faces": [{"face_index": 0, "name": "Frente", "oracle_text": []},
                    {"face_index": 1, "name": "Verso"}]},
])
async def test_malformed_multiface_translation_does_not_break_page(invalid):
    repository = PaginationRepository(size=2, valid_indices=(1,))
    repository.translations["oracle-0"] = invalid
    source = deepcopy(repository.cards)
    page = await CardSearchService(repository).search(SearchParams(limit=1))
    assert [card.oracle_id for card in page.items] == ["oracle-1"]
    assert not page.has_next
    assert repository.cards == source


@pytest.mark.parametrize("invalid", [{}, {"name": None}, {"name": " "}, {"name": 123},
                                     {"name": "Nome", "oracle_text": []},
                                     {"name": "Nome", "card_faces": []}])
async def test_invalid_single_face_translation_does_not_break_page(invalid):
    repository = PaginationRepository(size=2, valid_indices=(1,))
    repository.cards[0].pop("card_faces")
    repository.translations["oracle-0"] = invalid
    page = await CardSearchService(repository).search(SearchParams(limit=1))
    assert [card.oracle_id for card in page.items] == ["oracle-1"]
    assert not page.has_next
