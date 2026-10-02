from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.api import get_service
from app.main import app
from app.schemas import SearchParams
from app.service import CardSearchService


@pytest.fixture
def card_client():
    repository = AsyncMock()
    repository.get_by_oracle_id.return_value = {
        "_id": "oracle-1",
        "id": "printing-1",
        "oracle_id": "oracle-1",
        "name": "Lightning Bolt",
        "oracle_text": "Deal 3 damage.",
        "flavor_text": "English flavor.",
        "colors": ["R"],
    }
    repository.get_translations.return_value = {
        "oracle-1": {"name": "Raio", "oracle_text": "Causa 3 pontos de dano."}
    }
    original_lifespan = app.router.lifespan_context
    app.router.lifespan_context = no_database_lifespan
    app.dependency_overrides[get_service] = lambda: CardSearchService(repository)
    try:
        with TestClient(app) as client:
            yield client, repository
    finally:
        app.dependency_overrides.clear()
        app.router.lifespan_context = original_lifespan


def test_get_card_by_oracle_id_in_english(card_client) -> None:
    client, repository = card_client
    response = client.get("/cards/oracle-1", params={"lang": "en"})
    assert response.status_code == 200
    card = response.json()
    assert card["oracle_id"] == "oracle-1"
    assert card["id"] == "printing-1"
    assert card["name"] == "Lightning Bolt"
    assert card["lang"] == "en"
    assert "_id" not in card
    repository.get_by_oracle_id.assert_awaited_once_with("oracle-1")
    repository.get_translations.assert_not_awaited()


def test_get_card_defaults_to_portuguese(card_client) -> None:
    client, repository = card_client
    response = client.get("/cards/oracle-1")
    assert response.status_code == 200
    card = response.json()
    assert card["name"] == "Raio"
    assert card["lang"] == "pt"
    assert card["oracle_text"] == "Causa 3 pontos de dano."
    assert card["flavor_text"] is None
    assert card["colors"] == ["R"]
    repository.get_translations.assert_awaited_once_with(["oracle-1"], "pt")


@pytest.mark.parametrize("field", ["name", "name_exact"])
def test_search_accepts_name_filters(card_client, field) -> None:
    client, repository = card_client
    repository.search_oracle_cards.return_value = [repository.get_by_oracle_id.return_value]
    response = client.get("/cards/search", params={"lang": "en", field: "Lightning Bolt"})
    assert response.status_code == 200
    assert response.json()["items"][0]["name"] == "Lightning Bolt"
    params = repository.search_oracle_cards.call_args.args[0]
    assert getattr(params, field) == "Lightning Bolt"


@pytest.mark.parametrize("name", ["", "x" * 201])
def test_search_validates_exact_name(card_client, name) -> None:
    client, repository = card_client
    assert client.get("/cards/search", params={"name_exact": name}).status_code == 400
    repository.search_oracle_cards.assert_not_awaited()


@pytest.mark.parametrize("value,expected", [("true", True), ("false", False)])
def test_search_accepts_include_tokens(card_client, value, expected):
    client, repository = card_client
    repository.search_oracle_cards.return_value = [repository.get_by_oracle_id.return_value]
    response = client.get(
        "/cards/search", params={"lang": "en", "name_exact": "Ornithopter",
                                 "include_tokens": value}
    )
    assert response.status_code == 200
    assert repository.search_oracle_cards.call_args.args[0].include_tokens is expected


def test_get_missing_card_returns_404(card_client) -> None:
    client, repository = card_client
    repository.get_by_oracle_id.return_value = None
    assert client.get("/cards/missing").status_code == 404
    repository.get_translations.assert_not_awaited()


def test_get_card_without_translation_returns_404(card_client) -> None:
    client, repository = card_client
    repository.get_translations.return_value = {}
    assert client.get("/cards/oracle-1").status_code == 404


@pytest.mark.parametrize(
    "url", ["/cards/invalid!", "/cards/" + "a" * 101, "/cards/oracle-1?lang=invalid"]
)
def test_get_card_validates_parameters(card_client, url) -> None:
    client, repository = card_client
    assert client.get(url).status_code == 422
    repository.get_by_oracle_id.assert_not_awaited()


class EmptyRepository:
    async def search_translation_matches(self, params: SearchParams) -> dict[str, list[int] | None]:
        return {}

    async def search_oracle_cards(
        self, params: SearchParams, oracle_ids: Mapping[str, list[int] | None] | None = None
    ) -> list[dict[str, Any]]:
        return []

    async def get_translations(
        self, oracle_ids: Sequence[str], lang: str
    ) -> Mapping[str, dict[str, Any]]:
        return {}


@asynccontextmanager
async def no_database_lifespan(application: Any):
    yield


def test_invalid_query_returns_400() -> None:
    original_lifespan = app.router.lifespan_context
    app.router.lifespan_context = no_database_lifespan
    app.dependency_overrides[get_service] = lambda: CardSearchService(EmptyRepository())
    try:
        with TestClient(app) as client:
            response = client.get("/cards/search?limit=0")
    finally:
        app.dependency_overrides.clear()
        app.router.lifespan_context = original_lifespan
    assert response.status_code == 400


def test_no_matches_returns_empty_page() -> None:
    original_lifespan = app.router.lifespan_context
    app.router.lifespan_context = no_database_lifespan
    app.dependency_overrides[get_service] = lambda: CardSearchService(EmptyRepository())
    try:
        with TestClient(app) as client:
            response = client.get("/cards/search", params={"lang": "en", "name": "missing"})
    finally:
        app.dependency_overrides.clear()
        app.router.lifespan_context = original_lifespan
    assert response.status_code == 200
    assert response.json() == {"items": [], "limit": 50, "offset": 0, "hasNext": False}


@pytest.mark.parametrize(
    "legality,expected",
    [(None, ["legal", "restricted"]), ("LEGAL", ["legal"]),
     (" Restricted, BANNED ", ["restricted", "banned"]),
     ("not_legal", ["not_legal"]), ("legal,legal", ["legal"])],
)
def test_search_legality_contract(card_client, legality, expected):
    client, repository = card_client
    repository.get_by_oracle_id.return_value["legalities"] = {
        "vintage": "restricted", "future_format": "banned"
    }
    repository.search_oracle_cards.return_value = [repository.get_by_oracle_id.return_value]
    query = {"lang": "en", "format": " VINTAGE "}
    if legality is not None:
        query["legality"] = legality
    response = client.get("/cards/search", params=query)
    assert response.status_code == 200
    params = repository.search_oracle_cards.call_args.args[0]
    assert params.format == "vintage"
    assert params.legality == expected
    assert response.json()["items"][0]["legalities"] == {
        "vintage": "restricted", "future_format": "banned"
    }


@pytest.mark.parametrize("query", [
    {"legality": "legal", "name": "Bolt"},
    {"format": "unknown"}, {"format": "vintage.$ne"}, {"format": ""},
    {"format": "vintage", "legality": "unknown"},
    {"format": "vintage", "legality": ""},
    {"format": "vintage", "legality": "legal,"},
])
def test_search_rejects_invalid_legality_filters(card_client, query):
    client, repository = card_client
    assert client.get("/cards/search", params=query).status_code == 400
    repository.search_oracle_cards.assert_not_awaited()
    repository.search_translation_matches.assert_not_awaited()


def test_legalities_are_preserved_in_translation(card_client):
    client, repository = card_client
    repository.get_by_oracle_id.return_value["legalities"] = {"modern": "banned"}
    repository.get_translations.return_value["oracle-1"]["legalities"] = {"modern": "legal"}
    assert client.get("/cards/oracle-1").json()["legalities"] == {"modern": "banned"}


def test_missing_legalities_returns_empty_map(card_client):
    client, _ = card_client
    assert client.get("/cards/oracle-1").json()["legalities"] == {}


@pytest.mark.parametrize("lang", ["en", "pt"])
def test_public_card_contract_preserves_printing_and_hides_source_extras(card_client, lang):
    client, repository = card_client
    source = repository.get_by_oracle_id.return_value
    source.update({
        "lang": "ja", "layout": "normal", "color_identity": ["R"],
        "image_uris": {"normal": "https://example.test/card.jpg", "internal": "hidden"},
        "internal_metadata": {"secret": True},
    })
    # Deliberately no authentication headers or cookies.
    response = client.get("/cards/oracle-1", params={"lang": lang})
    assert response.status_code == 200
    card = response.json()
    assert card["lang"] == lang
    assert card["printing_lang"] == "ja"
    assert source["lang"] == "ja"
    assert card["layout"] == "normal"
    assert card["color_identity"] == ["R"]
    assert card["image_uris"]["normal"] == "https://example.test/card.jpg"
    assert card["image_uris"]["png"] is None
    assert "internal" not in card["image_uris"]
    assert "internal_metadata" not in card
    assert card["card_faces"] == []


def test_unknown_colors_are_distinct_from_colorless(card_client):
    client, repository = card_client
    source = repository.get_by_oracle_id.return_value
    source.pop("colors")
    unknown = client.get("/cards/oracle-1?lang=en").json()
    for field in ("colors", "color_identity", "layout", "printing_lang", "image_uris"):
        assert field in unknown and unknown[field] is None
    source.update(colors=[], color_identity=[])
    known = client.get("/cards/oracle-1?lang=en").json()
    assert known["colors"] == known["color_identity"] == []


def test_multiface_contract_in_search_and_translated_lookup(card_client):
    client, repository = card_client
    source = repository.get_by_oracle_id.return_value
    source.update({
        "lang": "en", "layout": "transform", "color_identity": ["U"],
        "card_faces": [
            {"name": "Front", "mana_cost": "{U}", "colors": ["U"],
             "image_uris": {"normal": "https://example.test/front.jpg"}},
            {"name": "Back", "mana_cost": "", "colors": [], "power": "*",
             "image_uris": {"normal": "https://example.test/back.jpg"}},
        ],
    })
    repository.get_translations.return_value = {"oracle-1": {
        "card_faces": [{"face_index": 1, "name": "Verso"},
                       {"face_index": 0, "name": "Frente"}],
    }}
    repository.search_translation_matches.return_value = {"oracle-1": None}
    repository.search_oracle_cards.return_value = [source]
    card = client.get("/cards/oracle-1?lang=pt").json()
    search = client.get("/cards/search?name=Frente&lang=pt")
    assert search.status_code == 200
    assert search.json() == {"items": [card], "limit": 50, "offset": 0, "hasNext": False}
    assert card["lang"] == "pt" and card["printing_lang"] == "en"
    assert card["color_identity"] == ["U"]
    assert card["image_uris"] is None
    assert [face["name"] for face in card["card_faces"]] == ["Frente", "Verso"]
    assert card["card_faces"][0]["image_uris"]["normal"].endswith("/front.jpg")
    assert card["card_faces"][1]["colors"] == []
    assert card["card_faces"][1]["mana_cost"] == ""
    assert card["card_faces"][1]["power"] == "*"
    assert card["card_faces"][1]["oracle_text"] is None


def test_openapi_documents_dto_public_reads_and_actual_errors(card_client):
    client, _ = card_client
    schema = client.get("/openapi.json").json()
    models = schema["components"]["schemas"]
    props = models["CardResponse"]["properties"]
    for field in ("layout", "color_identity", "printing_lang", "image_uris", "card_faces"):
        assert field in props
    assert props["card_faces"]["items"]["$ref"].endswith("/CardFaceResponse")
    assert set(models["CardResponse"]["required"]) == set(props)
    assert "additionalProperties" not in models["CardResponse"]
    assert "normal" in models["CardImages"]["properties"]
    expected = {
        ("/cards/search", "get"): {"200", "400"},
        ("/cards/{oracle_id}", "get"): {"200", "404", "422"},
        ("/cards/resolve", "post"): {"200", "404", "422", "502"},
        ("/translations", "post"): {"201", "404", "409", "422"},
        ("/translations/{translation_id}", "patch"): {"200", "400", "404", "422"},
    }
    for (path, method), statuses in expected.items():
        operation = schema["paths"][path][method]
        assert set(operation["responses"]) == statuses
        if path.startswith("/cards"):
            assert not operation.get("security")
            assert "No authentication required" in operation["description"]
    from app.schemas import ErrorResponse, ValidationErrorResponse

    invalid_search = client.get("/cards/search?limit=0")
    assert invalid_search.status_code == 400
    ValidationErrorResponse.model_validate(invalid_search.json())
    invalid_card = client.get("/cards/invalid!")
    assert invalid_card.status_code == 422
    ValidationErrorResponse.model_validate(invalid_card.json())
    _, repository = card_client
    repository.get_by_oracle_id.return_value = None
    ErrorResponse.model_validate(client.get("/cards/missing").json())


@pytest.mark.parametrize("lang", ["en", "pt"])
def test_catalog_without_filters_returns_page(card_client, lang):
    client, repository = card_client
    repository.search_oracle_cards.return_value = [repository.get_by_oracle_id.return_value]
    repository.search_translation_matches.return_value = {"oracle-1": None}
    response = client.get("/cards/search", params={"lang": lang, "limit": 1})
    assert response.status_code == 200
    page = response.json()
    assert len(page["items"]) == 1
    assert page["items"][0]["lang"] == lang
    assert page["items"][0]["cmc"] is None
    assert page["limit"] == 1 and page["offset"] == 0 and page["hasNext"] is False
    query = repository.search_oracle_cards.call_args.args[0]
    assert query.cmc is query.cmc_gte is query.cmc_lte is None


def test_search_openapi_describes_pagination(card_client):
    client, _ = card_client
    schema = client.get("/openapi.json").json()
    response = schema["paths"]["/cards/search"]["get"]["responses"]["200"]
    assert response["content"]["application/json"]["schema"]["$ref"].endswith("/CardSearchResponse")
    model = schema["components"]["schemas"]["CardSearchResponse"]
    assert set(model["required"]) == {"items", "limit", "offset", "hasNext"}
    assert model["properties"]["items"]["items"]["$ref"].endswith("/CardResponse")


@pytest.mark.parametrize("query", [
    {"limit": 201}, {"limit": 0}, {"offset": -1}, {"offset": 100001},
])
def test_pagination_bounds_remain_enforced(card_client, query):
    client, repository = card_client
    assert client.get("/cards/search", params=query).status_code == 400
    repository.search_oracle_cards.assert_not_awaited()
