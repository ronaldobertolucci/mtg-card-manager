"""Commander predicates evaluated by MongoDB, not a Python approximation."""

import os
from uuid import uuid4

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from app.repository import MongoCardRepository, _oracle_card_filter
from app.schemas import SearchParams
from app.service import CardSearchService


@pytest_asyncio.fixture
async def commander_catalog():
    uri = os.getenv("TEST_MONGODB_URI")
    if not uri:
        pytest.skip("TEST_MONGODB_URI is required for MongoDB integration tests")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    database = client[f"test_commanders_{uuid4().hex}"]
    cards = [
        ("legend", {"type_line": "Legendary Creature — Elf"}),
        ("artifact", {"type_line": "Legendary Artifact Creature — Golem"}),
        ("enchantment", {"type_line": "Legendary Enchantment Creature — God"}),
        ("walker", {"type_line": "Legendary Planeswalker — Test"}),
        ("exception", {"type_line": "Legendary Planeswalker — Test",
                       "oracle_text": "Test can be your commander."}),
        ("grist", {"name": "Grist, the Hunger Tide",
                   "type_line": "Legendary Planeswalker — Grist"}),
        ("ordinary", {"type_line": "Creature — Elf"}),
        ("subtype", {"type_line": "Creature — Legendary"}),
        ("reference", {"type_line": "Artifact", "oracle_text": "Your commander gains flying."}),
        ("background", {"type_line": "Legendary Enchantment — Background"}),
        ("banned", {"type_line": "Legendary Creature", "legalities": {"commander": "banned"}}),
        ("restricted", {"type_line": "Legendary Creature",
                        "legalities": {"commander": "restricted"}}),
        ("unknown", {"type_line": "Legendary Creature", "legalities": {}}),
        ("empty_faces", {"type_line": "Legendary Creature", "card_faces": []}),
        ("front", {"layout": "modal_dfc", "type_line": "Legendary Creature // Land",
                   "card_faces": [{"name": "Front", "type_line": "Legendary Creature"},
                                  {"name": "Back", "type_line": "Land", "oracle_text": "Flying",
                                   "colors": ["U"]}]}),
        ("back", {"layout": "transform", "type_line": "Creature // Legendary Creature",
                  "card_faces": [{"type_line": "Creature"}, {"type_line": "Legendary Creature"}]}),
        ("back_exception", {"layout": "transform", "oracle_text": "Test can be your commander.",
                            "card_faces": [{"type_line": "Legendary Planeswalker"},
                                           {"oracle_text": "Test can be your commander."}]}),
        ("front_exception", {"layout": "transform",
                             "card_faces": [{"oracle_text": "Test can be your commander."}, {}]}),
        ("missing_front_type", {"type_line": "Legendary Creature", "card_faces": [{}, {}]}),
    ]
    documents = [
        {"id": key, "oracle_id": key, "name": key, "layout": "normal",
         "legalities": {"commander": "legal", "modern": "legal"}, **fields}
        for key, fields in cards
    ]
    try:
        await database.oracle_cards.insert_many(documents)
        await database.translations.insert_one({
            "oracle_id": "front", "lang": "pt", "name": "Frente // Verso",
            "card_faces": [
                {"face_index": 0, "name": "Frente", "type_line": "Criatura Lendária"},
                {"face_index": 1, "name": "Verso", "type_line": "Terreno", "oracle_text": "Voar"},
            ],
        })
        yield database, {key for key, _ in cards}
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.parametrize("flag", [True, False, None])
async def test_commander_eligibility_and_complement(commander_catalog, flag):
    database, all_ids = commander_catalog
    eligible = {"legend", "artifact", "enchantment", "exception", "grist", "empty_faces",
                "front", "front_exception"}
    query = _oracle_card_filter(SearchParams(lang="en", is_commander=flag))
    actual = {card["oracle_id"] async for card in database.oracle_cards.find(query)}
    assert actual == (all_ids if flag is None else eligible if flag else all_ids - eligible)


@pytest.mark.parametrize(
    "lang,text,type_line", [("en", "Flying", "Land"), ("pt", "Voar", "Terreno")],
)
async def test_front_eligibility_combines_with_back_face_filters_and_pagination(
    commander_catalog, lang, text, type_line,
):
    database, _ = commander_catalog
    service = CardSearchService(MongoCardRepository(database))
    filters = dict(
        lang=lang, is_commander=True, oracle_text=text, type_line=type_line,
        colors="U", format="modern,pioneer", limit=1,
    )
    page = await service.search(SearchParams(**filters))
    assert [card.oracle_id for card in page.items] == ["front"]
    assert page.has_next is False
    next_page = await service.search(SearchParams(offset=1, **filters))
    assert next_page.items == []


async def test_commander_legality_cannot_be_bypassed_by_other_formats(commander_catalog):
    database, _ = commander_catalog
    query = _oracle_card_filter(SearchParams(
        lang="en", is_commander=True, format="commander,modern", legality="banned,legal",
    ))
    actual = {card["oracle_id"] async for card in database.oracle_cards.find(query)}
    assert "banned" not in actual
    assert "unknown" not in actual
    assert "legend" in actual
