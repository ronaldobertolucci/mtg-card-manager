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


@pytest_asyncio.fixture
async def accessory_catalog(catalog):
    database, service = catalog
    definitions = [
        ("a-token", "token", "Token Creature"),
        ("b-double-token", "double_faced_token", "Token Creature"),
        ("c-emblem", "emblem", "Emblem"),
        ("d-dungeon", "normal", "Dungeon"),
        ("e-dungeon-type", "normal", "Legendary Dungeon — Undercity"),
        ("f-card", "normal", "Creature — Wizard"),
        ("g-missing", None, None),
        ("h-plane", "planar", "Plane — Test"),
        ("i-scheme", "scheme", "Scheme"),
        ("j-dungeon-subtype", "normal", "Creature — Dungeon Master"),
        ("k-lowercase", "normal", "dungeon"),
    ]
    for identity, layout, type_line in definitions:
        card = {"_id": identity, "id": identity, "oracle_id": identity, "name": "H5 Shared",
                "colors": ["U"], "power": "2", "legalities": {"vintage": "legal"}}
        if layout is not None:
            card["layout"] = layout
        if type_line is not None:
            card["type_line"] = type_line
        # Translated type deliberately disagrees with the original classification.
        translation = {"oracle_id": identity, "lang": "pt", "name": "H5 Compartilhado",
                       "type_line": "Dungeon" if identity == "f-card" else "Tipo traduzido"}
        await database.oracle_cards.insert_one(card)
        await database.translations.insert_one(translation)
    return database, service


@pytest.mark.parametrize("lang", ["en", "pt"])
@pytest.mark.parametrize("filters,expected", [
    ({"kind": "accessories"}, {"a-token", "b-double-token", "c-emblem", "d-dungeon",
                               "e-dungeon-type", "j-dungeon-subtype"}),
    ({"kind": "cards", "format": "vintage", "legality": "legal,restricted"},
     {"f-card", "g-missing", "k-lowercase"}),
    ({}, {"d-dungeon", "e-dungeon-type", "f-card", "g-missing",
          "j-dungeon-subtype", "k-lowercase"}),
    ({"include_tokens": False}, {"d-dungeon", "e-dungeon-type", "f-card", "g-missing",
                                "j-dungeon-subtype", "k-lowercase"}),
    ({"include_tokens": True}, {"a-token", "b-double-token", "c-emblem", "d-dungeon",
                               "e-dungeon-type", "f-card", "g-missing", "h-plane", "i-scheme",
                               "j-dungeon-subtype", "k-lowercase"}),
])
async def test_kind_uses_official_metadata_independently_of_translation(
    accessory_catalog, lang, filters, expected
):
    _, service = accessory_catalog
    page = await service.search(SearchParams(lang=lang, name="H5", **filters))
    assert {card.oracle_id for card in page.items} == expected


@pytest.mark.parametrize("lang", ["en", "pt"])
async def test_kind_filters_before_pagination_and_combines_with_color_and_format(
    accessory_catalog, lang
):
    _, service = accessory_catalog
    first = await service.search(SearchParams(
        lang=lang, name="H5", kind="cards", colors="U", colors_mode="any",
        format="vintage", legality="legal,restricted", limit=1,
    ))
    last = await service.search(SearchParams(
        lang=lang, name="H5", kind="cards", colors="U", colors_mode="any",
        format="vintage", legality="legal,restricted", limit=1, offset=2,
    ))
    assert [card.oracle_id for card in first.items] == ["f-card"]
    assert first.has_next
    assert [card.oracle_id for card in last.items] == ["k-lowercase"]
    assert not last.has_next


@pytest.mark.parametrize("lang,text", [("en", "Flying"), ("pt", "Voar")])
async def test_accessory_filter_does_not_override_same_face_matching(catalog, lang, text):
    database, service = catalog
    await database.oracle_cards.update_one(
        {"_id": "multi"}, {"$set": {"layout": "double_faced_token"}}
    )
    page = await service.search(SearchParams(
        lang=lang, kind="accessories", oracle_text=text, power="4",
    ))
    assert [card.oracle_id for card in page.items] == ["multi"]
    wrong_face = await service.search(SearchParams(
        lang=lang, kind="accessories", oracle_text=text, power="2",
    ))
    assert wrong_face.items == []
    await database.oracle_cards.update_one(
        {"_id": "normal"}, {"$set": {"legalities.modern": "legal"}}
    )
    cards = await service.search(SearchParams(
        lang=lang, kind="cards", oracle_text=text, format="modern", legality="legal,restricted",
    ))
    assert [card.oracle_id for card in cards.items] == ["normal"]


async def test_accessory_classification_uses_root_type_only_and_does_not_change_batch(catalog):
    from app.schemas import BatchCardsRequest

    database, service = catalog
    await database.oracle_cards.update_one(
        {"_id": "multi"}, {"$set": {"card_faces.1.type_line": "Dungeon"}}
    )
    assert (await service.search(SearchParams(lang="en", kind="accessories"))).items == []
    await database.oracle_cards.update_one({"_id": "normal"}, {"$set": {"layout": "token"}})
    assert (await service.get_by_oracle_id("normal", "pt")).oracle_id == "normal"
    batch = await service.batch(BatchCardsRequest(oracleIds=["normal"], lang="pt"))
    assert [card.oracle_id for card in batch.cards] == ["normal"]


@pytest_asyncio.fixture
async def identity_catalog(color_catalog, catalog):
    database, _ = catalog
    for identity, value in {
        "b-null": None, "c-empty": [], "d-u": ["R"], "e-r": ["U"],
        "f-ur": ["U", "R"], "g-ru": ["R", "U"], "h-wur": ["W", "U", "R"],
        "i-split": ["U", "R"], "j-multi": ["U", "R"],
    }.items():
        await database.oracle_cards.update_one(
            {"_id": identity}, {"$set": {"color_identity": value}}
        )
    return color_catalog


@pytest.mark.parametrize("lang", ["en", "pt"])
@pytest.mark.parametrize("colors", ["U,R", "R,U"])
@pytest.mark.parametrize("mode,expected", [
    ("any", {"d-u", "e-r", "f-ur", "g-ru", "h-wur", "i-split", "j-multi"}),
    ("all", {"f-ur", "g-ru", "h-wur", "i-split", "j-multi"}),
    ("exact", {"f-ur", "g-ru", "i-split", "j-multi"}),
    (None, {"f-ur", "g-ru", "i-split", "j-multi"}),
    ("subset", {"c-empty", "d-u", "e-r", "f-ur", "g-ru", "i-split", "j-multi"}),
])
async def test_identity_operators_match_whole_card(identity_catalog, lang, colors, mode, expected):
    page = await identity_catalog.search(SearchParams(
        lang=lang, name="H4", color_identity=colors, color_identity_mode=mode,
    ))
    assert {card.oracle_id for card in page.items} == expected


@pytest.mark.parametrize("lang", ["en", "pt"])
@pytest.mark.parametrize("filters,expected", [
    ({"identity_colorless": True}, {"c-empty"}),
    ({"identity_colorless": False}, {"d-u", "e-r", "f-ur", "g-ru", "h-wur", "i-split", "j-multi"}),
    ({"color_identity": "", "color_identity_mode": "subset"}, {"c-empty"}),
    ({"color_identity": ""}, {"c-empty"}),
    ({"color_identity": "U", "color_identity_mode": "subset"}, {"c-empty", "e-r"}),
])
async def test_identity_unknown_is_not_colorless(identity_catalog, lang, filters, expected):
    page = await identity_catalog.search(SearchParams(lang=lang, name="H4", **filters))
    assert {card.oracle_id for card in page.items} == expected


@pytest.mark.parametrize("lang,text", [("en", "Flying"), ("pt", "Voar")])
async def test_identity_and_face_colors_are_independent(identity_catalog, lang, text):
    page = await identity_catalog.search(SearchParams(
        lang=lang, name="H4", colorless=True, color_identity="U,R", color_identity_mode="exact",
        oracle_text=text, power="4",
    ))
    assert [card.oracle_id for card in page.items] == ["j-multi"]
    page = await identity_catalog.search(SearchParams(
        lang=lang, name="H4", colors="R", color_identity="U", color_identity_mode="subset",
    ))
    assert [card.oracle_id for card in page.items] == ["e-r"]
    wrong_face = await identity_catalog.search(SearchParams(
        lang=lang, name="H4", colorless=True, color_identity="U,R", color_identity_mode="subset",
        oracle_text=text, power="2",
    ))
    assert wrong_face.items == []


@pytest.mark.parametrize("lang", ["en", "pt"])
async def test_identity_combines_kind_legality_and_pagination(identity_catalog, catalog, lang):
    database, _ = catalog
    await database.oracle_cards.update_many({}, {"$set": {"legalities.commander": "legal"}})
    await database.oracle_cards.update_one({"_id": "c-empty"}, {"$set": {"layout": "token"}})
    pages = [await identity_catalog.search(SearchParams(
        lang=lang, name="H4", kind="cards", format="commander", legality="legal,restricted",
        limit=1, offset=offset,
        color_identity="U", color_identity_mode="subset",
    )) for offset in (0, 1)]
    assert [card.oracle_id for card in pages[0].items] == ["e-r"]
    assert not pages[0].has_next
    assert pages[1].items == [] and not pages[1].has_next


@pytest.mark.parametrize("lang", ["en", "pt"])
async def test_physical_cleanup_is_independent_of_legality_and_language(catalog, lang):
    database, service = catalog
    layouts = [
        "token", "double_faced_token", "emblem", "art_series", "planar", "scheme",
        "vanguard", "normal", "transform", "modal_dfc", "split", "reversible_card",
    ]
    documents = [
        {"id": layout, "oracle_id": layout, "name": "Physical Test", "layout": layout,
         "oversized": False, "legalities": {"custom_format": "legal"}}
        for layout in layouts
    ]
    documents.extend([
        {"id": "oversized", "oracle_id": "oversized", "name": "Physical Test",
         "layout": "normal", "oversized": True, "legalities": {"custom_format": "legal"}},
        {"id": "missing", "oracle_id": "missing", "name": "Physical Test"},
        {"id": "banned", "oracle_id": "banned", "name": "Physical Test", "layout": "normal",
         "legalities": {"custom_format": "banned"}},
    ])
    await database.oracle_cards.insert_many(documents)
    await database.translations.insert_many([
        {"oracle_id": card["oracle_id"], "lang": "pt", "name": "Physical Test"}
        for card in documents
    ])
    playable = {"normal", "transform", "modal_dfc", "split", "reversible_card"}
    for kind in (None, "cards"):
        params = {"lang": lang, "name_exact": "Physical Test", "kind": kind}
        if kind is None:
            page = await service.search(SearchParams(**params))
            assert {card.oracle_id for card in page.items} == playable | {"missing", "banned"}
        legal = await service.search(SearchParams(
            **params, format="custom_format", legality="legal,restricted",
        ))
        assert {card.oracle_id for card in legal.items} == playable
    page = await service.search(SearchParams(
        lang=lang, name_exact="Physical Test", include_tokens=True,
    ))
    assert {card.oracle_id for card in page.items} == {card["oracle_id"] for card in documents}


@pytest.mark.parametrize("lang,text", [("en", "Flying"), ("pt", "Voar")])
@pytest.mark.parametrize("kind", [None, "cards"])
async def test_multiple_formats_preserve_faces_identity_cmc_and_pagination(
    catalog, lang, text, kind
):
    database, service = catalog
    await database.oracle_cards.update_one({"_id": "multi"}, {"$set": {
        "legalities": {"modern": "banned", "custom_format": "restricted"},
        "color_identity": ["U"],
        "layout": "transform",
    }})
    await database.oracle_cards.update_one({"_id": "normal"}, {"$set": {
        "legalities": {"modern": "legal", "custom_format": "banned"},
        "color_identity": ["U"], "layout": "normal",
    }})
    params = {"lang": lang, "kind": kind, "format": "modern,custom_format",
              "legality": "legal,restricted",
              "color_identity": "U", "color_identity_mode": "subset", "oracle_text": text}
    first = await service.search(SearchParams(**params, limit=1))
    last = await service.search(SearchParams(**params, limit=1, offset=1))
    assert [card.oracle_id for page in (first, last) for card in page.items] == ["multi", "normal"]
    assert first.has_next and not last.has_next
    same_face = await service.search(SearchParams(**params, power="4", cmc_gte=2, cmc_lte=4))
    assert [card.oracle_id for card in same_face.items] == ["multi"]
    assert (await service.search(SearchParams(**params, power="2"))).items == []
    assert (await service.search(SearchParams(**params, power="4", cmc=1))).items == []
    legal_only = await service.search(SearchParams(**{**params, "legality": "legal"}))
    assert [card.oracle_id for card in legal_only.items] == ["normal"]


@pytest.mark.parametrize("lang", ["en", "pt"])
async def test_cards_kind_matches_any_playable_format_before_pagination(catalog, lang):
    database, service = catalog
    formats = ["commander", "standard", "modern", "pioneer", "pauper"]
    documents = []
    for index, format_name in enumerate(formats):
        for status in ("legal", "restricted"):
            identity = f"playable-{index}-{status}"
            documents.append({
                "_id": identity, "id": identity, "oracle_id": identity, "name": "Kind Contract",
                "layout": "normal",
                "legalities": {**dict.fromkeys(formats, "banned"), format_name: status},
            })
    for status in ("banned", "not_legal", None):
        identity = f"excluded-{status}"
        card = {"_id": identity, "id": identity, "oracle_id": identity,
                "name": "Kind Contract", "layout": "normal"}
        if status is not None:
            card["legalities"] = dict.fromkeys(formats, status)
        documents.append(card)
    documents.append({
        "_id": "excluded-partial", "id": "excluded-partial", "oracle_id": "excluded-partial",
        "name": "Kind Contract", "legalities": {"modern": "banned"},
    })
    await database.oracle_cards.insert_many(documents)
    await database.translations.insert_many([
        {"oracle_id": card["oracle_id"], "lang": "pt", "name": "Kind Contract"}
        for card in documents
    ])
    params = dict(lang=lang, name_exact="Kind Contract", kind="cards",
                  format=",".join(formats), legality="legal,restricted", limit=3)
    pages = [await service.search(SearchParams(**params, offset=offset))
             for offset in (0, 3, 6, 9, 10)]
    assert [card.oracle_id for page in pages for card in page.items] == [
        f"playable-{index}-{status}" for index in range(5) for status in ("legal", "restricted")
    ]
    assert [page.has_next for page in pages] == [True, True, True, False, False]
    assert [len(page.items) for page in pages] == [3, 3, 3, 1, 0]
    # Legacy explicit not_legal searches must not synthesize a status for missing fields.
    not_legal = await service.search(SearchParams(
        lang=lang, name_exact="Kind Contract", format=",".join(formats), legality="not_legal",
    ))
    assert [card.oracle_id for card in not_legal.items] == ["excluded-not_legal"]


@pytest.mark.parametrize("lang", ["en", "pt"])
async def test_accessories_kind_ignores_stored_legality_before_pagination(accessory_catalog, lang):
    database, service = accessory_catalog
    expected = ["a-token", "b-double-token", "c-emblem", "d-dungeon",
                "e-dungeon-type", "j-dungeon-subtype"]
    statuses = ("legal", "restricted", "banned", "not_legal", None, None)
    for identity, status in zip(expected, statuses, strict=True):
        legalities = {} if status is None else dict.fromkeys(
            ("commander", "standard", "modern", "pioneer", "pauper"), status,
        )
        await database.oracle_cards.update_one(
            {"_id": identity}, {"$set": {"legalities": legalities}},
        )
    await database.oracle_cards.update_one(
        {"_id": "j-dungeon-subtype"}, {"$unset": {"legalities": ""}},
    )
    pages = [await service.search(SearchParams(
        lang=lang, kind="accessories", limit=2, offset=offset,
    )) for offset in (0, 2, 4, 6)]
    assert [card.oracle_id for page in pages for card in page.items] == expected
    assert [page.has_next for page in pages] == [True, True, False, False]
