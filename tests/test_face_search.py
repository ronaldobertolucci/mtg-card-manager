"""MongoDB integration tests: set TEST_MONGODB_URI to an isolated test server."""

import os
from uuid import uuid4

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from app.repository import MongoCardRepository
from app.schemas import SearchParams
from app.service import CardSearchService


@pytest_asyncio.fixture
async def catalog():
    uri = os.getenv("TEST_MONGODB_URI")
    if not uri:
        pytest.skip("TEST_MONGODB_URI is required for MongoDB integration tests")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    database = client[f"test_faces_{uuid4().hex}"]
    try:
        await database.oracle_cards.insert_many(
            [
                {
                    "_id": "multi",
                    "id": "printing-multi",
                    "oracle_id": "multi",
                    "name": "Front // Back",
                    "cmc": 3,
                    "card_faces": [
                        {
                            "name": "Front",
                            "oracle_text": "Draw (two).",
                            "type_line": "Creature",
                            "mana_cost": "{1}{U}",
                            "colors": ["U"],
                            "power": "2",
                            "toughness": "3",
                        },
                        {
                            "name": "Back",
                            "oracle_text": "Flying",
                            "type_line": "Land",
                            "mana_cost": "",
                            "colors": [],
                            "power": "4",
                            "toughness": "5",
                        },
                    ],
                },
                {
                    "_id": "normal",
                    "id": "printing-normal",
                    "oracle_id": "normal",
                    "name": "Normal",
                    "cmc": 1,
                    "oracle_text": "Flying",
                    "type_line": "Creature",
                    "mana_cost": "{U}",
                    "colors": ["U"],
                    "power": "1",
                    "toughness": "1",
                },
            ]
        )
        await database.translations.insert_many(
            [
                {
                    "oracle_id": "multi",
                    "lang": "pt",
                    "name": "Frente // Verso",
                    "card_faces": [
                        {
                            "face_index": 1,
                            "name": "Verso",
                            "oracle_text": "Voar",
                            "type_line": "Terreno",
                        },
                        {
                            "face_index": 0,
                            "name": "Frente",
                            "oracle_text": "Compre (duas).",
                            "type_line": "Criatura",
                        },
                    ],
                },
                {
                    "oracle_id": "normal",
                    "lang": "pt",
                    "name": "Normal",
                    "oracle_text": "Voar",
                    "type_line": "Criatura",
                },
            ]
        )
        yield database, CardSearchService(MongoCardRepository(database))
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filters,expected",
    [
        ({"name": "back"}, {"multi"}),
        ({"name_exact": "Front // Back"}, {"multi"}),
        ({"name_exact": "Back"}, set()),
        ({"name_exact": "front // back"}, set()),
        ({"name_exact": "Front.*"}, set()),
        ({"name_exact": "Normal"}, {"normal"}),
        ({"name_exact": "Norm"}, set()),
        ({"oracle_text": "draw (two)"}, {"multi"}),
        ({"oracle_text": "draw .*"}, set()),
        ({"type_line": "land"}, {"multi"}),
        ({"mana_cost": "{1}{U}"}, {"multi"}),
        ({"mana_cost": "{U}"}, {"normal"}),
        ({"mana_cost": ""}, {"multi"}),
        ({"colors": ""}, {"multi"}),
        ({"colors": "U"}, {"multi", "normal"}),
        ({"colors": "U,R"}, set()),
        ({"power": "4", "toughness": "5"}, {"multi"}),
        ({"power": "2", "toughness": "5"}, set()),
        ({"oracle_text": "Flying", "type_line": "Creature"}, {"normal"}),
        ({"type_line": "Land", "power": "2"}, set()),
        ({"name": "Back", "power": "2"}, {"multi"}),
        ({"name_exact": "Front // Back", "power": "2"}, {"multi"}),
        ({"cmc": 3, "mana_cost": "{1}{U}"}, {"multi"}),
        ({"cmc": 2, "mana_cost": "{1}{U}"}, set()),
        ({"cmc_gte": 2, "cmc_lte": 4}, {"multi"}),
    ],
)
async def test_english_faces(catalog, filters, expected):
    _, service = catalog
    result = await service.search(SearchParams(lang="en", **filters))
    assert {card.oracle_id for card in result.items} == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filters,expected",
    [
        ({"name": "verso"}, {"multi"}),
        ({"name_exact": "Frente // Verso"}, {"multi"}),
        ({"name_exact": "Verso"}, set()),
        ({"name_exact": "frente // verso"}, set()),
        ({"name_exact": "Frente.*"}, set()),
        ({"name_exact": "Normal"}, {"normal"}),
        ({"name_exact": "Norm"}, set()),
        ({"oracle_text": "compre (duas)"}, {"multi"}),
        ({"oracle_text": "compre .*"}, set()),
        ({"oracle_text": "Voar", "power": "4"}, {"multi"}),
        ({"oracle_text": "Voar", "power": "2"}, set()),
        ({"type_line": "Terreno", "mana_cost": ""}, {"multi"}),
        ({"type_line": "Terreno", "colors": "U"}, set()),
        ({"oracle_text": "Voar", "type_line": "Criatura"}, {"normal"}),
        ({"oracle_text": "Flying"}, set()),
        ({"mana_cost": "{1}{U}"}, {"multi"}),
        ({"cmc": 3, "oracle_text": "Voar"}, {"multi"}),
        ({"cmc": 2, "oracle_text": "Voar"}, set()),
    ],
)
async def test_translated_faces_keep_mechanics_on_matching_index(catalog, filters, expected):
    _, service = catalog
    result = await service.search(SearchParams(lang="pt", **filters))
    assert {card.oracle_id for card in result.items} == expected


@pytest.mark.asyncio
async def test_pagination_after_face_matching_and_no_duplicates(catalog):
    database, service = catalog
    # The multiface card sorts first but fails the requested face's mechanics.
    result = await service.search(
        SearchParams(lang="pt", oracle_text="Voar", power="1", limit=1)
    )
    assert [card.oracle_id for card in result.items] == ["normal"]
    await database.oracle_cards.update_one(
        {"oracle_id": "multi"},
        {"$set": {"card_faces.0.oracle_text": "Flying", "card_faces.1.oracle_text": "Flying"}},
    )
    result = await service.search(SearchParams(lang="en", oracle_text="Flying"))
    assert [card.oracle_id for card in result.items] == ["multi", "normal"]
    page = await service.search(SearchParams(lang="en", oracle_text="Flying", limit=1, offset=1))
    assert [card.oracle_id for card in page.items] == ["normal"]


@pytest.mark.parametrize("lang", ["en", "pt"])
@pytest.mark.parametrize("name_filter", ["name", "name_exact"])
async def test_same_name_tokens_are_excluded_before_pagination(catalog, lang, name_filter):
    database, service = catalog
    for identity, layout in [
        ("a-token", "token"),
        ("b-token", "double_faced_token"),
        ("z-card", "normal"),
    ]:
        await database.oracle_cards.insert_one(
            {"_id": identity, "id": identity, "oracle_id": identity,
             "name": "Ornithopter", "layout": layout}
        )
        await database.translations.insert_one(
            {"oracle_id": identity, "lang": "pt", "name": "Ornithopter"}
        )
    filters = {name_filter: "Ornithopter"}
    result = await service.search(SearchParams(lang=lang, limit=1, **filters))
    assert [card.oracle_id for card in result.items] == ["z-card"]
    page = await service.search(SearchParams(lang=lang, limit=1, offset=1, **filters))
    assert page.items == []
    assert not page.has_next
    included = await service.search(SearchParams(lang=lang, include_tokens=True, **filters))
    assert [card.oracle_id for card in included.items] == ["a-token", "b-token", "z-card"]
    assert (await service.get_by_oracle_id("a-token", lang)).oracle_id == "a-token"


@pytest.mark.parametrize("lang,text", [("en", "Flying"), ("pt", "Voar")])
async def test_legality_with_faces_translation_and_pagination(catalog, lang, text):
    database, service = catalog
    await database.oracle_cards.update_one(
        {"oracle_id": "multi"}, {"$set": {"legalities.vintage": "banned"}}
    )
    await database.oracle_cards.update_one(
        {"oracle_id": "normal"}, {"$set": {"legalities.vintage": "restricted"}}
    )
    result = await service.search(
        SearchParams(lang=lang, format="vintage", oracle_text=text, limit=1)
    )
    assert [card.oracle_id for card in result.items] == ["normal"]
    assert result.items[0].legalities == {"vintage": "restricted"}
    assert (await service.search(
        SearchParams(lang=lang, format="vintage", legality="legal")
    )).items == []
    result = await service.search(
        SearchParams(lang=lang, format="vintage", legality="banned", oracle_text=text, power="4")
    )
    assert [card.oracle_id for card in result.items] == ["multi"]
    result = await service.search(
        SearchParams(lang=lang, format="vintage", legality="banned,restricted", offset=1, limit=1)
    )
    assert [card.oracle_id for card in result.items] == ["normal"]


@pytest.mark.parametrize("status", ["legal", "restricted", "banned", "not_legal"])
async def test_missing_legality_never_matches(catalog, status):
    _, service = catalog
    page = await service.search(SearchParams(lang="en", format="modern", legality=status))
    assert page.items == []


async def test_translated_pagination_counts_valid_cards_in_mongo(catalog):
    database, service = catalog
    # Three batches of candidates: incomplete first batch, two valid tied names,
    # invalid gaps and invalid tail. _id breaks ties deterministically.
    cards = [
        {"_id": f"h2-{index:04}", "id": f"printing-{index}", "oracle_id": f"h2-{index:04}",
         "name": "H2 tied", "card_faces": [{"name": "Front"}, {"name": "Back"}]}
        for index in range(405)
    ]
    translations = [
        {"oracle_id": card["oracle_id"], "lang": "pt", "name": "H2 traduzida",
         "card_faces": [{"face_index": 0, "name": "Frente"}]}
        for card in cards
    ]
    for index in (200, 400):
        translations[index]["card_faces"].append({"face_index": 1, "name": "Verso"})
    await database.oracle_cards.insert_many(cards)
    await database.translations.insert_many(translations)
    for offset, expected, has_next in [
        (0, ["h2-0200"], True), (1, ["h2-0400"], False), (2, [], False),
    ]:
        page = await service.search(SearchParams(name="H2", limit=1, offset=offset))
        assert [card.oracle_id for card in page.items] == expected
        assert page.has_next is has_next


@pytest.mark.parametrize("lang", ["en", "pt"])
async def test_catalog_without_filters_includes_missing_and_null_cmc(catalog, lang):
    database, service = catalog
    await database.oracle_cards.update_one({"_id": "normal"}, {"$unset": {"cmc": ""}})
    await database.oracle_cards.update_one({"_id": "multi"}, {"$set": {"cmc": None}})
    page = await service.search(SearchParams(lang=lang, limit=1))
    assert [card.oracle_id for card in page.items] == ["multi"]
    assert page.has_next
    last = await service.search(SearchParams(lang=lang, limit=1, offset=1))
    assert [card.oracle_id for card in last.items] == ["normal"]
    assert not last.has_next
    empty = await service.search(SearchParams(lang=lang, limit=1, offset=2))
    assert empty.items == [] and not empty.has_next


async def test_malformed_translated_face_matching_does_not_break_search(catalog):
    database, service = catalog
    await database.translations.update_one(
        {"oracle_id": "multi"},
        {"$set": {"card_faces": [None, {"oracle_text": "Voar"},
                                 {"face_index": [], "name": "Bad", "oracle_text": "Voar"}]}},
    )
    page = await service.search(SearchParams(oracle_text="Voar", limit=1))
    assert [card.oracle_id for card in page.items] == ["normal"]
    assert not page.has_next


async def test_batch_mongo_preserves_partial_results_and_explicit_fallback(catalog):
    from app.schemas import BatchCardsRequest

    database, service = catalog
    request = {"oracleIds": ["normal", "absent", "multi", "normal"], "lang": "pt"}
    result = await service.batch(BatchCardsRequest(**request))
    assert [card.oracle_id for card in result.cards] == ["normal", "multi"]
    assert result.missing[0].reason == "card_not_found"
    await database.translations.update_one(
        {"oracle_id": "multi"}, {"$pop": {"card_faces": 1}}
    )
    partial = await service.batch(BatchCardsRequest(**request))
    assert [card.oracle_id for card in partial.cards] == ["normal"]
    assert [item.reason for item in partial.missing] == ["card_not_found", "translation_invalid"]
    fallback = await service.batch(BatchCardsRequest(**request, fallbackLang="en"))
    assert [card.lang for card in fallback.cards] == ["pt", "en"]
    assert fallback.cards[1].fallback_reason == "translation_invalid"
    assert [face.name for face in fallback.cards[1].card_faces] == ["Front", "Back"]
    # Batch fallback must not change the strict translated search or individual lookup.
    assert await service.get_by_oracle_id("multi", "pt") is None
    assert [card.oracle_id for card in (await service.search(SearchParams())).items] == ["normal"]


@pytest_asyncio.fixture
async def color_catalog(catalog):
    database, service = catalog
    cards = []
    translations = []
    for identity, attributes in [
        ("a-missing", {}), ("b-null", {"colors": None}), ("c-empty", {"colors": []}),
        ("d-u", {"colors": ["U"]}), ("e-r", {"colors": ["R"]}),
        ("f-ur", {"colors": ["U", "R"]}), ("g-ru", {"colors": ["R", "U"]}),
        ("h-wur", {"colors": ["W", "U", "R"]}),
    ]:
        cards.append({"_id": identity, "id": identity, "oracle_id": identity,
                      "name": "H4 Card", **attributes})
        translations.append({"oracle_id": identity, "lang": "pt", "name": "H4 Carta"})
    for identity, front_colors, back_colors in [
        ("i-split", ["U"], ["R"]), ("j-multi", ["U", "R"], []),
    ]:
        cards.append({
            "_id": identity, "id": identity, "oracle_id": identity,
            "name": "H4 Front // H4 Back",
            "card_faces": [
                {"name": "H4 Front", "colors": front_colors, "power": "2",
                 "type_line": "Creature", "oracle_text": "Draw"},
                {"name": "H4 Back", "colors": back_colors, "power": "4",
                 "type_line": "Land", "oracle_text": "Flying"},
            ],
        })
        translations.append({
            "oracle_id": identity, "lang": "pt", "name": "H4 Frente // H4 Verso",
            "card_faces": [
                {"face_index": 1, "name": "H4 Verso", "oracle_text": "Voar",
                 "type_line": "Terreno"},
                {"face_index": 0, "name": "H4 Frente", "oracle_text": "Comprar",
                 "type_line": "Criatura"},
            ],
        })
    await database.oracle_cards.insert_many(cards)
    await database.translations.insert_many(translations)
    return service


@pytest.mark.parametrize("lang", ["en", "pt"])
@pytest.mark.parametrize("colors", ["U,R", "R,U"])
@pytest.mark.parametrize("mode,expected", [
    ("any", {"d-u", "e-r", "f-ur", "g-ru", "h-wur", "i-split", "j-multi"}),
    ("all", {"f-ur", "g-ru", "h-wur", "j-multi"}),
    ("exact", {"f-ur", "g-ru", "j-multi"}),
    (None, {"f-ur", "g-ru", "j-multi"}),
])
async def test_color_operators_are_order_independent(color_catalog, lang, colors, mode, expected):
    page = await color_catalog.search(
        SearchParams(lang=lang, name="H4", colors=colors, colors_mode=mode)
    )
    assert {card.oracle_id for card in page.items} == expected
    assert not page.has_next


@pytest.mark.parametrize("lang", ["en", "pt"])
@pytest.mark.parametrize("filters,expected", [
    ({"colorless": True}, {"c-empty", "j-multi"}),
    ({"colors": ""}, {"c-empty", "j-multi"}),
    ({"colors": "", "colors_mode": "exact"}, {"c-empty", "j-multi"}),
    ({"colorless": False}, {"d-u", "e-r", "f-ur", "g-ru", "h-wur", "i-split", "j-multi"}),
])
async def test_colorlessness_requires_known_colors(color_catalog, lang, filters, expected):
    page = await color_catalog.search(SearchParams(lang=lang, name="H4", **filters))
    assert {card.oracle_id for card in page.items} == expected


@pytest.mark.parametrize("lang,text", [("en", "Flying"), ("pt", "Voar")])
@pytest.mark.parametrize("filters,expected", [
    ({"colors": "U", "colors_mode": "any"}, []),
    ({"colors": "R", "colors_mode": "all"}, ["i-split"]),
    ({"colors": "R", "colors_mode": "exact"}, ["i-split"]),
    ({"colors": "U,R", "colors_mode": "all"}, []),
    ({"colorless": True}, ["j-multi"]),
    ({"colorless": True, "power": "2"}, []),
    ({"colorless": False}, ["i-split"]),
])
async def test_colors_and_text_match_same_face(color_catalog, lang, text, filters, expected):
    page = await color_catalog.search(
        SearchParams(lang=lang, name="H4", oracle_text=text, **filters)
    )
    assert [card.oracle_id for card in page.items] == expected


@pytest.mark.parametrize("lang", ["en", "pt"])
async def test_exact_color_filter_runs_before_pagination(color_catalog, lang):
    first = await color_catalog.search(
        SearchParams(lang=lang, name="H4", colors="R,U", colors_mode="exact", limit=1)
    )
    second = await color_catalog.search(
        SearchParams(lang=lang, name="H4", colors="U,R", colors_mode="exact", limit=1, offset=1)
    )
    last = await color_catalog.search(
        SearchParams(lang=lang, name="H4", colors="R,U", colors_mode="exact", limit=1, offset=2)
    )
    assert [card.oracle_id for page in (first, second, last) for card in page.items] == [
        "f-ur", "g-ru", "j-multi",
    ]
    assert first.has_next and second.has_next and not last.has_next
