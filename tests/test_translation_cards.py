"""Contract tests, including the real MongoDB join (TEST_MONGODB_URI)."""
import os
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

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
    *[({key: "value"}, key) for key in
      ("format", "legality", "include_tokens", "fallbackLang", "unknown")],
])
async def test_validation(api, changes, field):
    client, repository = api
    params = {"lang": "pt", "kind": "cards", **changes}
    params = {k: v for k, v in params.items() if v is not None}
    response = await client.get("/translations/cards", params=params)
    assert response.status_code == 422
    assert ["query", field] in [issue["loc"] for issue in response.json()["detail"]]
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
        "lang", "kind", "translation_status", "name", "limit", "offset",
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
    await db.oracle_cards.create_index("oracle_id", unique=True)
    await db.translations.create_index([("oracle_id", 1), ("lang", 1)], unique=True)
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
        "/translations/cards", params={"lang": "pt", "kind": "cards", **params}
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
