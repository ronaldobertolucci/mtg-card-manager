from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api import get_service
from app.main import app
from app.repository import CardRepository, MongoCardRepository
from app.schemas import BatchCardsRequest
from app.scryfall import ScryfallClient
from app.service import CardSearchService


def source(oracle_id, **fields):
    return {
        "_id": oracle_id, "id": f"printing-{oracle_id}", "oracle_id": oracle_id,
        "name": f"English {oracle_id}", "lang": "ja", "layout": "normal",
        "color_identity": ["U"], "image_uris": {"normal": "https://example.test/card.jpg"},
        **fields,
    }


@pytest.fixture
def batch_service():
    repository = AsyncMock(spec=CardRepository)
    scryfall = AsyncMock(spec=ScryfallClient)
    repository.get_by_oracle_ids.return_value = [
        source("invalid", layout="transform", card_faces=[{"name": "Front"}, {"name": "Back"}]),
        source("untranslated"), source("available"),
    ]
    repository.get_translations.return_value = {
        "available": {"name": "Traduzida", "oracle_text": "Texto em português"},
        "invalid": {"card_faces": [{"face_index": 0, "name": "Frente"}]},
    }
    return CardSearchService(repository, scryfall), repository, scryfall


@pytest.fixture
def batch_client(batch_service):
    service, repository, scryfall = batch_service

    @asynccontextmanager
    async def lifespan(application):
        yield

    original = app.router.lifespan_context
    app.router.lifespan_context = lifespan
    app.dependency_overrides[get_service] = lambda: service
    try:
        with TestClient(app) as client:
            yield client, repository, scryfall
    finally:
        app.dependency_overrides.clear()
        app.router.lifespan_context = original


def test_partial_batch_distinguishes_all_missing_reasons_without_auth(batch_client):
    client, repository, scryfall = batch_client
    response = client.post("/cards/batch", json={
        "oracleIds": ["absent", "available", "invalid", "untranslated", "available", "absent"],
    })
    assert response.status_code == 200
    body = response.json()
    assert list(body) == ["cards", "missing"]
    assert [card["oracle_id"] for card in body["cards"]] == ["available"]
    assert body["cards"][0]["name"] == "Traduzida"
    assert body["cards"][0]["lang"] == body["cards"][0]["requested_lang"] == "pt"
    assert body["cards"][0]["fallback_reason"] is None
    assert body["cards"][0]["printing_lang"] == "ja"
    assert body["missing"] == [
        {"oracle_id": "absent", "reason": "card_not_found"},
        {"oracle_id": "invalid", "reason": "translation_invalid"},
        {"oracle_id": "untranslated", "reason": "translation_missing"},
    ]
    repository.get_by_oracle_ids.assert_awaited_once_with(
        ["absent", "available", "invalid", "untranslated"]
    )
    repository.get_translations.assert_awaited_once()
    assert set(repository.get_translations.call_args.args[0]) == {
        "available", "invalid", "untranslated",
    }
    assert repository.get_translations.call_args.args[1] == "pt"
    repository.get_by_oracle_id.assert_not_awaited()
    repository.get_by_ids.assert_not_awaited()
    scryfall.get_by_ids.assert_not_awaited()


def test_explicit_fallback_preserves_order_images_and_complete_english_faces(batch_client):
    client, repository, scryfall = batch_client
    original = deepcopy(repository.get_by_oracle_ids.return_value)
    response = client.post("/cards/batch", json={
        "oracleIds": ["untranslated", "available", "invalid", "untranslated", "absent"],
        "lang": "PT", "fallbackLang": "en",
    })
    assert response.status_code == 200
    body = response.json()
    assert [card["oracle_id"] for card in body["cards"]] == ["untranslated", "available", "invalid"]
    first, available, invalid = body["cards"]
    assert first["lang"] == invalid["lang"] == "en"
    assert first["requested_lang"] == invalid["requested_lang"] == "pt"
    assert first["fallback_reason"] == "translation_missing"
    assert invalid["fallback_reason"] == "translation_invalid"
    assert first["name"] == "English untranslated"
    assert [face["name"] for face in invalid["card_faces"]] == ["Front", "Back"]
    assert available["name"] == "Traduzida" and available["fallback_reason"] is None
    assert all(card["printing_lang"] == "ja" for card in body["cards"])
    assert all(card["image_uris"]["normal"] == "https://example.test/card.jpg"
               for card in body["cards"])
    assert body["missing"] == [{"oracle_id": "absent", "reason": "card_not_found"}]
    assert repository.get_by_oracle_ids.return_value == original
    scryfall.get_by_ids.assert_not_awaited()


@pytest.mark.parametrize("fallback", [None, "en"])
def test_english_batch_skips_translations_and_preserves_order(batch_client, fallback):
    client, repository, scryfall = batch_client
    response = client.post("/cards/batch", json={
        "oracleIds": ["available", "invalid", "untranslated", "available"],
        "lang": "en", "fallbackLang": fallback,
    })
    assert response.status_code == 200
    body = response.json()
    assert [card["oracle_id"] for card in body["cards"]] == ["available", "invalid", "untranslated"]
    assert body["missing"] == []
    assert all(card["requested_lang"] == "en" and card["fallback_reason"] is None
               for card in body["cards"])
    repository.get_translations.assert_not_awaited()
    scryfall.get_by_ids.assert_not_awaited()


def test_valid_multiface_translation_preserves_face_order(batch_client):
    client, repository, _ = batch_client
    repository.get_translations.return_value["invalid"] = {
        "card_faces": [{"face_index": 1, "name": "Verso"}, {"face_index": 0, "name": "Frente"}]
    }
    response = client.post("/cards/batch", json={"oracleIds": ["invalid"], "fallbackLang": "en"})
    card = response.json()["cards"][0]
    assert card["name"] == "Frente // Verso"
    assert [face["name"] for face in card["card_faces"]] == ["Frente", "Verso"]
    assert card["lang"] == "pt" and card["fallback_reason"] is None


def test_empty_batch_skips_all_io(batch_client):
    client, repository, scryfall = batch_client
    response = client.post("/cards/batch", json={"oracleIds": []})
    assert response.status_code == 200
    assert response.json() == {"cards": [], "missing": []}
    repository.get_by_oracle_ids.assert_not_awaited()
    repository.get_translations.assert_not_awaited()
    scryfall.get_by_ids.assert_not_awaited()


def test_all_cards_absent_still_returns_200_and_skips_translation_lookup(batch_client):
    client, repository, _ = batch_client
    repository.get_by_oracle_ids.return_value = []
    response = client.post("/cards/batch", json={
        "oracleIds": ["absent", "absent"], "fallbackLang": "en",
    })
    assert response.status_code == 200
    assert response.json() == {
        "cards": [], "missing": [{"oracle_id": "absent", "reason": "card_not_found"}],
    }
    repository.get_translations.assert_not_awaited()


def test_all_translations_unavailable_still_returns_200(batch_client):
    client, _, _ = batch_client
    response = client.post("/cards/batch", json={"oracleIds": ["invalid", "untranslated"]})
    assert response.status_code == 200
    assert response.json() == {
        "cards": [], "missing": [
            {"oracle_id": "invalid", "reason": "translation_invalid"},
            {"oracle_id": "untranslated", "reason": "translation_missing"},
        ],
    }


@pytest.mark.parametrize("payload", [
    {}, {"oracleIds": None}, {"oracleIds": "available"}, {"oracleIds": [1]},
    {"oracleIds": [""]}, {"oracleIds": ["x" * 101]}, {"oracleIds": ["../search"]},
    {"oracleIds": ["available"] * 201}, {"oracleIds": ["available"], "lang": "invalid"},
    {"oracleIds": ["available"], "fallbackLang": "pt"},
    {"oracleIds": ["available"], "lang": None},
    {"oracleIds": ["available"], "fallback_lang": "en"},
    {"oracle_ids": ["available"]}, {"oracleIds": ["available"], "unknown": True},
])
def test_invalid_batch_returns_422_before_io(batch_client, payload):
    client, repository, scryfall = batch_client
    response = client.post("/cards/batch", json=payload)
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    repository.get_by_oracle_ids.assert_not_awaited()
    repository.get_translations.assert_not_awaited()
    scryfall.get_by_ids.assert_not_awaited()


def test_200_positions_are_accepted_and_deduplicated(batch_client):
    client, repository, _ = batch_client
    response = client.post("/cards/batch", json={"oracleIds": ["available"] * 200, "lang": "en"})
    assert response.status_code == 200
    assert len(response.json()["cards"]) == 1
    repository.get_by_oracle_ids.assert_awaited_once_with(["available"])


def test_batch_openapi_documents_exact_contract(batch_client):
    client, _, _ = batch_client
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/cards/batch"]["post"]
    assert not operation.get("security")
    assert "No authentication required" in operation["description"]
    assert set(operation["responses"]) == {"200", "422"}
    request = schema["components"]["schemas"]["BatchCardsRequest"]
    assert request["properties"]["oracleIds"]["maxItems"] == 200
    assert request["required"] == ["oracleIds"]
    assert "fallbackLang" in request["properties"]
    card = schema["components"]["schemas"]["BatchCardResponse"]
    assert {"requested_lang", "fallback_reason", "printing_lang"} <= set(card["required"])


async def test_repository_fetches_all_oracle_ids_in_one_query():
    collection = MagicMock()
    documents = [source("one"), source("two")]
    collection.find.return_value.__aiter__.return_value = documents
    repository = MongoCardRepository(
        SimpleNamespace(oracle_cards=collection, translations=MagicMock())
    )
    assert await repository.get_by_oracle_ids(["one", "two"]) == documents
    collection.find.assert_called_once_with({"oracle_id": {"$in": ["one", "two"]}})


async def test_empty_repository_batch_skips_query():
    collection = MagicMock()
    repository = MongoCardRepository(
        SimpleNamespace(oracle_cards=collection, translations=MagicMock())
    )
    assert await repository.get_by_oracle_ids([]) == []
    collection.find.assert_not_called()


def test_regional_language_is_canonicalized():
    request = BatchCardsRequest(oracleIds=[], lang="PT-br")
    assert request.lang == "pt-BR"
