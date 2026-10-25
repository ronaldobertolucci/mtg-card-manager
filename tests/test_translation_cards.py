"""Contract tests, including the real MongoDB join (TEST_MONGODB_URI)."""
import os
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from app.database import ensure_translation_cards_index
from app.main import app
from app.repository import MongoCardRepository
from app.service import CardSearchService
from app.translation_api import get_translation_service
from app.translation_repository import MongoTranslationRepository
from app.translation_service import TranslationService


@pytest_asyncio.fixture
async def api():
    repository = AsyncMock()
    repository.list_cards.return_value = []
    app.dependency_overrides[get_translation_service] = lambda: TranslationService(repository)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, repository
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("changes,field", [
    ({"lang": None}, "lang"), ({"kind": None}, "kind"),
    ({"lang": "en"}, "lang"), ({"lang": "EN"}, "lang"),
    ({"lang": "portuguese"}, "lang"), ({"kind": "all"}, "kind"),
    ({"translation_status": "invalid"}, "translation_status"),
    ({"name": "  "}, "name"), ({"name": "x" * 201}, "name"),
    ({"limit": 0}, "limit"), ({"limit": 201}, "limit"), ({"limit": "x"}, "limit"),
    ({"offset": -1}, "offset"), ({"offset": 100001}, "offset"),
    ({"format": ""}, "format"), ({"format": "modern,"}, "format"),
    ({"format": "a" * 101}, "format"), ({"format": "$bad"}, "format"),
    ({"format": "legalities.modern"}, "format"),
    ({"legality": "legal"}, "legality"),
    ({"format": "modern", "legality": ""}, "legality"),
    ({"format": "modern", "legality": "legal,"}, "legality"),
    ({"format": "modern", "legality": "invalid"}, "legality"),
    ({"kind": "accessories", "format": "modern"}, "format"),
    ({"kind": "accessories", "legality": "legal"}, "legality"),
    *[({key: "value"}, key) for key in
      ("include_tokens", "fallbackLang", "unknown")],
])
async def test_validation(api, changes, field):
    client, repository = api
    params = {"lang": "pt", "kind": "cards", **changes}
    params = {k: v for k, v in params.items() if v is not None}
    response = await client.get("/translations/cards", params=params)
    assert response.status_code == 422
    assert ["query", field] in [issue["loc"][:2] for issue in response.json()["detail"]]
    repository.list_cards.assert_not_awaited()


async def test_defaults_normalization_limits_and_failure(api):
    client, repository = api
    response = await client.get("/translations/cards", params={"lang": " PT-br ", "kind": "cards"})
    assert response.json() == {"items": [], "limit": 50, "offset": 0, "hasNext": False}
    assert repository.list_cards.call_args.args[0].lang == "pt-BR"
    response = await client.get("/translations/cards", params={
        "lang": "pt", "kind": "cards", "name": " " + "a" * 200 + " ",
        "limit": 200, "offset": 100000,
    })
    assert response.status_code == 200
    assert len(repository.list_cards.call_args.args[0].name) == 200
    repository.list_cards.side_effect = RuntimeError("catalog unavailable")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as failed_client:
        response = await failed_client.get("/translations/cards?lang=pt&kind=cards")
        assert response.status_code == 500


def test_openapi():
    schema = app.openapi()
    operation = schema["paths"]["/translations/cards"]["get"]
    assert {p["name"] for p in operation["parameters"]} == {
        "lang", "kind", "translation_status", "name", "format", "legality", "limit", "offset",
    }
    models = schema["components"]["schemas"]
    assert models["TranslationCardItem"]["properties"]["card"]["$ref"].endswith("/CardResponse")
    assert "translation_id" in models["TranslationCardItem"]["required"]
    assert "hasNext" in models["TranslationCardsPage"]["required"]


@pytest_asyncio.fixture
async def catalog(api):
    uri = os.getenv("TEST_MONGODB_URI")
    if not uri:
        pytest.skip("TEST_MONGODB_URI is required")
    mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    db = mongo[f"test_translation_cards_{uuid4().hex}"]
    # Explicit endpoint/index collation must override a non-binary collection default.
    await db.create_collection("oracle_cards", collation={"locale": "en", "strength": 2})
    await db.oracle_cards.create_index("oracle_id", unique=True)
    await db.translations.create_index([("oracle_id", 1), ("lang", 1)], unique=True)
    await ensure_translation_cards_index(db)
    cards = [
        ("a", "Alpha", "normal", "Creature"),
        ("b", "Alpha", "normal", "Creature"),
        ("c", "Beta [x].* // Back", "transform", "Creature"),
        ("d", "lower", "planar", "Plane"),
        ("e", "Éclair", "scheme", "Scheme"),
        ("f", "Token", "token", "Token"),
        ("g", "Double", "double_faced_token", "Token"),
        ("h", "Emblem", "emblem", "Emblem"),
        ("i", "Dungeon", "normal", "Legendary Dungeon"),
        ("j", "dungeon", "normal", "dungeon"),
    ]
    for identity, name, layout, type_line in reversed(cards):
        doc = {"oracle_id": identity, "id": f"print-{identity}", "lang": "ja",
               "name": name, "layout": layout, "type_line": type_line,
               "legalities": {"modern": "banned"}, "oversized": True}
        if identity in ("c", "g"):
            doc["card_faces"] = [{"name": "Front", "image_uris": {"normal": "front.png"}},
                                 {"name": "Back"}]
        await db.oracle_cards.insert_one(doc)
    # Deliberately unusable translation: presence must not invoke public validation.
    await db.translations.insert_many([
        {"oracle_id": "b", "lang": "pt", "name": ""},
        {"oracle_id": "c", "lang": "pt", "name": "Traduzida", "card_faces": []},
        {"oracle_id": "a", "lang": "es", "name": "Traducida"},
        {"oracle_id": "orphan", "lang": "pt", "name": "Órfã"},
    ])
    app.dependency_overrides[get_translation_service] = lambda: TranslationService(
        MongoTranslationRepository(db)
    )
    try:
        yield api[0], db
    finally:
        await mongo.drop_database(db.name)
        mongo.close()


async def page(client, **params):
    response = await client.get(
        "/translations/cards",
        params={k: v for k, v in {"lang": "pt", "kind": "cards", **params}.items()
                if v is not None},
    )
    assert response.status_code == 200, response.text
    return response.json()


def ids(result):
    return [item["card"]["oracle_id"] for item in result["items"]]


async def test_join_filters_pagination_and_original_contract(catalog):
    client, db = catalog
    assert ids(await page(client)) == ["a", "b", "c", "j", "d", "e"]
    pages = [await page(client, limit=1, offset=offset) for offset in range(6)]
    assert [identity for result in pages for identity in ids(result)] == [
        "a", "b", "c", "j", "d", "e",
    ]
    assert [result["hasNext"] for result in pages] == [True] * 5 + [False]
    assert pages[0]["items"][0]["translation_id"] is None
    assert ids(await page(client, translation_status="present")) == ["b", "c"]
    assert ids(await page(client, lang="es", translation_status="present")) == ["a"]
    assert ids(await page(client, translation_status="missing")) == ["a", "j", "d", "e"]
    for offset, expected, next_page in [(0, ["a", "j"], True), (2, ["d", "e"], False),
                                         (4, [], False), (100000, [], False)]:
        result = await page(client, translation_status="missing", offset=offset, limit=2)
        assert ids(result) == expected
        assert result["hasNext"] is next_page
        assert (result["offset"], result["limit"]) == (offset, 2)
    result = await page(client, name="  ALP  ", translation_status="present", limit=1)
    assert ids(result) == ["b"] and result["hasNext"] is False
    assert (await page(client, name="Traduzida"))["items"] == []
    for name in ("[x].*", "BACK", "\\", "^", "(", "$"):
        result = await page(client, name=name)
        assert ids(result) == (["c"] if name in ("[x].*", "BACK") else [])
    public = CardSearchService(MongoCardRepository(db), None)
    assert await public.get_by_oracle_id("c", "pt") is None
    result = await page(client, name="back", translation_status="present")
    item = result["items"][0]
    assert set(item) == {"card", "translation_id"}
    translation = await db.translations.find_one({"oracle_id": "c"})
    assert item["translation_id"] == str(translation["_id"])
    original = await public.get_by_oracle_id("c", "en")
    assert item["card"] == original.model_dump(mode="json")
    assert item["card"]["lang"] == "en" and item["card"]["printing_lang"] == "ja"
    assert item["card"]["image_uris"] is None
    assert [f["name"] for f in item["card"]["card_faces"]] == ["Front", "Back"]
    assert ids(await page(client, kind="accessories")) == ["g", "i", "h", "f"]
    assert ids(await page(client, kind="accessories", name="dOu")) == ["g"]
    assert (await page(client, kind="accessories", translation_status="present"))["items"] == []


async def test_crud_immediately_changes_existence(catalog):
    client, _ = catalog
    params = {"name": "Alpha", "translation_status": "missing"}
    assert ids(await page(client, **params)) == ["a"]
    created = await client.post(
        "/translations", json={"oracle_id": "a", "lang": "PT", "name": "Alfa"}
    )
    assert created.status_code == 201
    identity = created.json()["id"]
    assert (await page(client, **params))["items"] == []
    assert (await page(client, name="Alpha"))["items"][0]["translation_id"] == identity
    updated = await client.patch(f"/translations/{identity}", json={"name": "Alfa nova"})
    assert updated.status_code == 200
    assert (await page(client, name="Alpha"))["items"][0]["translation_id"] == identity
    assert (await client.get(f"/translations/{identity}")).status_code == 200
    assert (await client.get("/translations/by-card/a/pt")).json()["id"] == identity
    assert isinstance((await client.get("/translations?oracle_id=a")).json(), list)
    assert (await client.delete(f"/translations/{identity}")).status_code == 204
    assert ids(await page(client, **params)) == ["a"]


@pytest.mark.parametrize("translated_count", [0, 7, 497, 500])
async def test_dense_sparse_and_empty_translation_pages(catalog, translated_count):
    """A sorted Python oracle checks every page, combined filters and end boundaries."""
    client, db = catalog
    await db.oracle_cards.delete_many({})
    await db.translations.delete_many({})
    cards = [{
        "oracle_id": f"card-{i:04}", "id": f"print-{i}",
        # Ties, binary ordering, literal punctuation, and interleaved accessories.
        "name": f"{'Alpha [x].*' if i % 2 else 'alpha'} {i // 4:04}",
        "layout": "token" if i % 5 == 0 else "normal", "lang": "ja",
    } for i in reversed(range(500))]
    await db.oracle_cards.insert_many(cards)
    if translated_count:
        await db.translations.insert_many([
            {"oracle_id": f"card-{i:04}", "lang": "pt", "name": ""}
            for i in range(translated_count)
        ])
    # Every card has another language: it must never count toward pt or pt-BR.
    await db.translations.insert_many([
        {"oracle_id": card["oracle_id"], "lang": "es", "name": "Otro"} for card in cards
    ])
    translation_ids = {
        (doc["oracle_id"], doc["lang"]): str(doc["_id"])
        async for doc in db.translations.find({})
    }
    ordered = sorted(cards, key=lambda card: (card["name"], card["oracle_id"]))
    for kind, name, lang in [
        ("cards", None, "pt"), ("accessories", None, "pt"),
        ("cards", "[x].*", "pt"), ("accessories", "ALPHA", "es"),
        ("cards", "not present", "pt"), ("cards", None, " PT-br "),
    ]:
        normalized = "pt-BR" if lang == " PT-br " else lang
        for status in ("missing", "present", "all"):
            expected = [card for card in ordered
                        if (card["layout"] == "token") == (kind == "accessories")
                        and (name is None or name.lower() in card["name"].lower())
                        and (status == "all" or
                             ((card["oracle_id"], normalized) in translation_ids)
                             == (status == "present"))]
            seen = []
            limit = 37
            # Includes initial, consecutive, exact end, and high offsets.
            offsets = sorted({*range(0, len(expected) + limit, limit),
                              len(expected), max(0, len(expected) - limit), 100000})
            for offset in offsets:
                result = await page(client, kind=kind, name=name, lang=lang,
                                    translation_status=status, limit=limit, offset=offset)
                expected_page = expected[offset:offset + limit]
                assert ids(result) == [card["oracle_id"] for card in expected_page]
                assert result["hasNext"] is (offset + limit < len(expected))
                for item in result["items"]:
                    assert item["translation_id"] == translation_ids.get(
                        (item["card"]["oracle_id"], normalized)
                    )
                if offset % limit == 0 and offset < len(expected):
                    seen.extend(ids(result))
            assert seen == [card["oracle_id"] for card in expected]


@pytest.mark.parametrize("with_format", [False, True])
@pytest.mark.parametrize("status", ["missing", "present", "all"])
async def test_index_streams_before_existence_pagination(catalog, with_format, status):
    from app.translation_repository import translation_cards_pipeline
    from app.translation_schemas import TranslationCardsParams

    _, db = catalog
    # Enough data to distinguish an indexed ordered stream from a full scan/join.
    await db.oracle_cards.insert_many([
        {"oracle_id": f"stream-{i:05}", "name": f"Aardvark {i:05}", "layout": "normal",
         "legalities": {"modern": "legal" if i % 2 else "banned"}}
        for i in range(2000)
    ])
    await db.translations.insert_many([
        {"oracle_id": f"stream-{i:05}", "lang": "pt", "name": ""}
        for i in range(1, 2000, 4)
    ])
    params = TranslationCardsParams(lang="pt", kind="cards", translation_status=status,
                                    format="modern" if with_format else None, limit=24)
    explain = await db.command("explain", {
        "aggregate": "oracle_cards", "pipeline": translation_cards_pipeline(params),
        "cursor": {}, "collation": {"locale": "simple"},
    }, verbosity="executionStats")
    stages = explain["stages"]
    cursor = stages[0]["$cursor"]
    assert "ix_oracle_cards_name_oracle_id" in str(cursor["queryPlanner"]["winningPlan"])
    assert "'stage': 'SORT'" not in str(cursor["queryPlanner"]["winningPlan"])
    # The cursor may read ahead a whole batch; bound the actual join work instead.
    lookup = next(stage for stage in stages if "$lookup" in stage)
    # Count only category/legality-compatible candidates up to the 25th result.
    expected_joins = {("all", False): 25, ("all", True): 25,
                      ("missing", False): 33, ("missing", True): 50,
                      ("present", False): 98, ("present", True): 49}
    assert lookup["nReturned"] == expected_joins[status, with_format]
    assert lookup["collectionScans"] == 0


async def test_public_search_keeps_its_order_and_legality_with_translation_index(catalog):
    from app.schemas import SearchParams

    _, db = catalog
    # Public search still sorts name/_id, unlike translation management's name/oracle_id.
    await db.oracle_cards.update_many(
        {"oracle_id": {"$in": ["a", "b"]}},
        {"$set": {"oversized": False, "legalities": {"modern": "legal"}}},
    )
    public = CardSearchService(MongoCardRepository(db))
    first = await public.search(SearchParams(lang="en", name="Alpha", format="modern", limit=1))
    second = await public.search(SearchParams(
        lang="en", name="Alpha", format="modern", limit=1, offset=1,
    ))
    # Fixture inserts b before a, making _id order the reverse of oracle_id order.
    assert [card.oracle_id for card in first.items] == ["b"]
    assert [card.oracle_id for card in second.items] == ["a"]
    assert first.has_next is True and second.has_next is False
    assert all(card.lang == "en" for card in [*first.items, *second.items])


async def test_legality_normalization(api):
    client, repository = api
    for query, formats, legalities in [
        ([("format", " MODERN,modern "), ("format", " Future_Format ")],
         ["modern", "future_format"], ["legal", "restricted"]),
        ([("format", "vintage"), ("legality", " BANNED,legal "), ("legality", "legal")],
         ["vintage"], ["banned", "legal"]),
    ]:
        response = await client.get("/translations/cards", params=[
            ("lang", "pt"), ("kind", "cards"), *query,
        ])
        assert response.status_code == 200, response.text
        params = repository.list_cards.call_args.args[0]
        assert params.format == formats
        assert params.legality == legalities


async def test_format_filters_before_pagination_and_crud(catalog):
    client, db = catalog
    legalities = {
        "a": {"modern": "banned", "pioneer": "legal"},
        "b": {"modern": "restricted", "pioneer": "banned"},
        "c": {"modern": "legal"},
        "d": {}, "e": {"future_format": "legal"},
        "j": {"modern": "not_legal"},
    }
    for identity, values in legalities.items():
        await db.oracle_cards.update_one({"oracle_id": identity}, {"$set": {"legalities": values}})
    await db.oracle_cards.update_one({"oracle_id": "d"}, {"$unset": {"legalities": ""}})
    filters = {"format": "modern,pioneer"}
    assert ids(await page(client, **filters)) == ["a", "b", "c"]
    assert ids(await page(client, **filters, legality="banned")) == ["a", "b"]
    assert ids(await page(client, **filters, legality="not_legal")) == ["j"]
    assert ids(await page(client, format="future_format")) == ["e"]
    assert ids(await page(client, format="unknown_format")) == []
    assert ids(await page(client)) == ["a", "b", "c", "j", "d", "e"]
    for status, expected in [("all", ["a", "b", "c"]), ("missing", ["a"]),
                             ("present", ["b", "c"])]:
        for limit in (1, 2, 3):
            for offset in range(6):
                result = await page(client, **filters, translation_status=status,
                                    limit=limit, offset=offset)
                assert ids(result) == expected[offset:offset + limit]
                assert result["hasNext"] is (offset + limit < len(expected))
    assert ids(await page(client, **filters, name=" ALPHA ",
                          translation_status="present")) == ["b"]
    assert ids(await page(client, **filters, lang="ES", name="Alpha",
                          translation_status="present")) == ["a"]
    original = (await page(client, **filters, name="Back"))["items"][0]
    assert [face["name"] for face in original["card"]["card_faces"]] == ["Front", "Back"]
    created = await client.post("/translations", json={
        "oracle_id": "a", "lang": "pt", "name": "Alfa",
    })
    assert created.status_code == 201
    identity = created.json()["id"]
    assert ids(await page(client, **filters, translation_status="missing")) == []
    assert (await page(client, **filters))["items"][0]["translation_id"] == identity
    assert (await client.delete(f"/translations/{identity}")).status_code == 204
    assert ids(await page(client, **filters, translation_status="missing")) == ["a"]
