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
                    "lang": "pt-BR",
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
                    "lang": "pt-BR",
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
        ({"cmc": 3, "mana_cost": "{1}{U}"}, {"multi"}),
        ({"cmc": 2, "mana_cost": "{1}{U}"}, set()),
        ({"cmc_gte": 2, "cmc_lte": 4}, {"multi"}),
    ],
)
async def test_english_faces(catalog, filters, expected):
    _, service = catalog
    result = await service.search(SearchParams(lang="en", **filters))
    assert {card.oracle_id for card in result} == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filters,expected",
    [
        ({"name": "verso"}, {"multi"}),
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
    result = await service.search(SearchParams(lang="pt-BR", **filters))
    assert {card.oracle_id for card in result} == expected


@pytest.mark.asyncio
async def test_pagination_after_face_matching_and_no_duplicates(catalog):
    database, service = catalog
    # The multiface card sorts first but fails the requested face's mechanics.
    result = await service.search(
        SearchParams(lang="pt-BR", oracle_text="Voar", power="1", limit=1)
    )
    assert [card.oracle_id for card in result] == ["normal"]
    await database.oracle_cards.update_one(
        {"oracle_id": "multi"},
        {"$set": {"card_faces.0.oracle_text": "Flying", "card_faces.1.oracle_text": "Flying"}},
    )
    result = await service.search(SearchParams(lang="en", oracle_text="Flying"))
    assert [card.oracle_id for card in result] == ["multi", "normal"]
    page = await service.search(SearchParams(lang="en", oracle_text="Flying", limit=1, offset=1))
    assert [card.oracle_id for card in page] == ["normal"]
