from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.repository import (
    NON_DECK_LAYOUTS,
    MongoCardRepository,
    _attribute_filter,
    _conjoin,
    _oracle_card_filter,
    _text_filter,
)
from app.schemas import SearchParams


@pytest.mark.asyncio
@pytest.mark.parametrize("card", [None, {"id": "printing-1", "oracle_id": "oracle-1"}])
async def test_get_by_oracle_id_uses_stable_identity(card) -> None:
    collection = AsyncMock()
    collection.find_one.return_value = card
    repository = MongoCardRepository(
        SimpleNamespace(oracle_cards=collection, translations=AsyncMock())
    )
    assert await repository.get_by_oracle_id("oracle-1") == card
    collection.find_one.assert_awaited_once_with({"oracle_id": "oracle-1"})


@pytest.mark.parametrize("name", ["Lightning Bolt", "Black (Lotus)", ".*", "Front // Back"])
def test_name_filter_uses_literal_equality(name: str) -> None:
    assert _oracle_card_filter(SearchParams(lang="en", name_exact=name)) == {
        "$and": [
            {"name": name},
            {"layout": {"$nin": list(NON_DECK_LAYOUTS)}, "oversized": {"$ne": True}},
        ]
    }


def test_text_filter_escapes_regex_metacharacters() -> None:
    query = _text_filter(SearchParams(oracle_text="(two)"))
    assert query == {"oracle_text": {"$regex": r"\(two\)", "$options": "i"}}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filters,expected",
    [
        ({"name_exact": "Raio"}, "Raio"),
        ({"name": "ra"}, {"$regex": "ra", "$options": "i"}),
        (
            {"name": "ra", "name_exact": "Raio"},
            {"$regex": "ra", "$options": "i", "$eq": "Raio"},
        ),
    ],
)
async def test_translation_name_filters(filters, expected) -> None:
    collection = MagicMock()
    cursor = MagicMock()
    cursor.__aiter__.return_value = [{"oracle_id": "oracle-1"}]
    collection.find.return_value = cursor
    repository = MongoCardRepository(
        SimpleNamespace(oracle_cards=MagicMock(), translations=collection)
    )

    matches = await repository.search_translation_matches(SearchParams(**filters))

    assert matches == {"oracle-1": None}
    assert collection.find.call_args.args[0] == {"lang": "pt", "name": expected}


def test_cmc_range_query() -> None:
    query = _attribute_filter(SearchParams(colors="U,R", cmc_gte=2, cmc_lte=4))
    assert query == {
        "colors": {"$all": ["U", "R"], "$size": 2}, "cmc": {"$gte": 2.0, "$lte": 4.0}
    }


def test_localized_card_query_uses_scryfall_oracle_id_not_printing_id() -> None:
    query = _oracle_card_filter(
        SearchParams(lang="pt", colors="U"),
        {"oracle-1": None, "oracle-2": None},
    )

    assert query == {
        "$and": [
            {"layout": {"$nin": list(NON_DECK_LAYOUTS)}, "oversized": {"$ne": True}},
            {"$or": [{"$and": [
                {"oracle_id": {"$in": ["oracle-1", "oracle-2"]}},
                {"$or": [
                    {"colors": {"$all": ["U"], "$size": 1}},
                    {"card_faces": {"$elemMatch": {"colors": {"$all": ["U"], "$size": 1}}}},
                ]},
            ]}]},
        ]
    }


@pytest.mark.parametrize(
    "values",
    [
        {"name": "x", "cmc": 2, "cmc_gte": 1},
        {"name": "x", "cmc_gte": 5, "cmc_lte": 2},
        {"colors": "R,R"},
        {"colors": "X"},
    ],
)
def test_conflicting_or_invalid_parameters(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SearchParams(**values)


@pytest.mark.asyncio
async def test_translation_repository_reads_original_faces():
    from app.translation_repository import MongoTranslationRepository

    collection = AsyncMock()
    collection.find_one.return_value = {"card_faces": [{}, {}]}
    repository = MongoTranslationRepository(
        SimpleNamespace(oracle_cards=collection, translations=AsyncMock())
    )
    assert await repository.get_oracle_card("oracle-1") == {"card_faces": [{}, {}]}
    collection.find_one.assert_awaited_once_with({"oracle_id": "oracle-1"}, {"card_faces": 1})


@pytest.mark.parametrize("field", ["name", "oracle_text", "type_line"])
def test_partial_text_filters_preserve_literal_case_insensitive_search(field):
    assert _text_filter(SearchParams(**{field: "(two)"})) == {
        field: {"$regex": r"\(two\)", "$options": "i"}
    }


def test_name_filters_combine_with_and():
    assert _oracle_card_filter(
        SearchParams(lang="en", name="bolt", name_exact="Lightning Bolt")
    ) == {
        "$and": [
            {"name": {"$regex": "bolt", "$options": "i", "$eq": "Lightning Bolt"}},
            {"layout": {"$nin": list(NON_DECK_LAYOUTS)}, "oversized": {"$ne": True}},
        ],
    }


def test_name_exact_is_a_text_filter():
    assert SearchParams(name_exact="Raio").has_text_filters


@pytest.mark.parametrize("lang,matches", [("en", None), ("pt", {"oracle-1": [1]})])
def test_legality_filters_whole_card_alongside_face_filters(lang, matches):
    query = _oracle_card_filter(
        SearchParams(lang=lang, format="vintage", oracle_text="Flying", power="4"), matches
    )
    assert query["$and"].pop(0) == {
        "$or": [{"legalities.vintage": {"$in": ["legal", "restricted"]}}]
    }
    assert query == _oracle_card_filter(
        SearchParams(lang=lang, oracle_text="Flying", power="4"), matches
    )


def test_legality_does_not_filter_translated_text_or_face_attributes():
    params = SearchParams(format="modern", legality="banned")
    assert _text_filter(params) == {}
    assert _attribute_filter(params) == {}
    assert not params.has_text_filters


def test_conjoin_preserves_repeated_logical_and_field_keys():
    clauses = [
        {"$or": [{"a": 1}, {"b": 2}]}, {"$or": [{"c": 3}, {"d": 4}]},
        {"$and": [{"e": 5}, {"f": 6}]}, {"$and": [{"g": 7}]},
        {"$nor": [{"h": 8}]}, {"$nor": [{"i": 9}]}, {"a": 2}, {"a": 3},
    ]
    assert _conjoin({}, *clauses, {}) == {"$and": clauses}
    assert _conjoin({}, {}) == {}
    assert _conjoin({}, clauses[0]) == clauses[0]


@pytest.mark.parametrize("value", [" Modern , future_format,MODERN", ["modern", "FUTURE_FORMAT"]])
def test_formats_are_dynamic_normalized_and_deduplicated(value):
    params = SearchParams(format=value)
    assert params.format == ["modern", "future_format"]
    assert params.legality == ["legal", "restricted"]


@pytest.mark.parametrize("value", ["", [], "modern,", "modern.$ne", "$or", "a.b", [1]])
def test_unsafe_or_empty_formats_are_rejected(value):
    with pytest.raises(ValidationError):
        SearchParams(format=value)


@pytest.mark.parametrize("lang,matches", [("en", None), ("pt", {"oracle-1": [1]})])
def test_formats_accessories_and_face_branches_are_independent(lang, matches):
    query = _oracle_card_filter(SearchParams(
        lang=lang, format="modern,future_format", kind="accessories",
        oracle_text="Flying", power="4", cmc=3, color_identity="U",
    ), matches)
    root, formats, accessories, faces = query["$and"]
    assert root == {"cmc": 3, "color_identity": {"$all": ["U"], "$size": 1}}
    assert formats == {"$or": [
        {"legalities.modern": {"$in": ["legal", "restricted"]}},
        {"legalities.future_format": {"$in": ["legal", "restricted"]}},
    ]}
    assert accessories == {"$or": [
        {"layout": {"$in": ["token", "double_faced_token", "emblem"]}},
        {"type_line": {"$regex": "Dungeon"}},
    ]}
    if lang == "en":
        combined = {"power": "4", "oracle_text": {"$regex": "Flying", "$options": "i"}}
        assert faces == {"$or": [combined, {"card_faces": {"$elemMatch": combined}}]}
    else:
        assert faces == {"$or": [{
            "oracle_id": {"$in": ["oracle-1"]}, "card_faces.1": {"$exists": True},
            "card_faces.1.power": "4",
        }]}
