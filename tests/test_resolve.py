from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import get_service
from app.main import app
from app.repository import MongoCardRepository
from app.schemas import ResolvedCardResponse
from app.scryfall import ScryfallCardNotFound, ScryfallClient, ScryfallUnavailable
from app.service import CardSearchService


def card(card_id):
    return {
        "id": card_id,
        "oracle_id": "shared-oracle",
        "name": "Lightning Bolt",
        "layout": "normal",
        "type_line": "Instant",
        "_id": "internal",
    }


@pytest.mark.parametrize("local_ids", [[], ["printing-1"], ["printing-2", "printing-1"]])
async def test_resolve_local_first_and_only_fetch_missing(local_ids):
    repository = AsyncMock()
    repository.get_by_ids.return_value = [card(value) for value in local_ids]
    scryfall = AsyncMock(spec=ScryfallClient)
    missing = [value for value in ["printing-1", "printing-2"] if value not in local_ids]
    scryfall.get_by_ids.return_value = {
        value: ResolvedCardResponse.model_validate(card(value)) for value in missing
    }
    ids = ["printing-1", "printing-2", "printing-1"]
    result = await CardSearchService(repository, scryfall).resolve(ids)
    assert [item.id for item in result] == ids
    assert all(item.oracle_id == "shared-oracle" for item in result)
    repository.get_by_ids.assert_awaited_once_with(["printing-1", "printing-2"])
    if missing:
        scryfall.get_by_ids.assert_awaited_once_with(missing)
    else:
        scryfall.get_by_ids.assert_not_awaited()
    repository.get_translations.assert_not_awaited()


async def test_empty_resolution_skips_io():
    repository, scryfall = AsyncMock(), AsyncMock()
    assert await CardSearchService(repository, scryfall).resolve([]) == []
    repository.get_by_ids.assert_not_awaited()
    scryfall.get_by_ids.assert_not_awaited()


async def test_repository_queries_printing_id():
    collection = MagicMock()
    collection.find.return_value.__aiter__.return_value = [card("printing-1")]
    repository = MongoCardRepository(
        SimpleNamespace(oracle_cards=collection, translations=MagicMock())
    )
    assert await repository.get_by_ids(["printing-1"]) == [card("printing-1")]
    collection.find.assert_called_once_with({"id": {"$in": ["printing-1"]}})


@pytest.fixture
def scryfall_transport(monkeypatch):
    original_client = httpx.AsyncClient

    def install(handler):
        transport = httpx.MockTransport(handler)
        monkeypatch.setattr(
            "app.scryfall.httpx.AsyncClient",
            lambda **kwargs: original_client(transport=transport, **kwargs),
        )

    return install


async def test_scryfall_get_mapping_and_headers(scryfall_transport):
    paths = []

    def handler(request):
        assert request.headers["Accept"] == "application/json"
        assert request.headers["User-Agent"].startswith("mtg-card-manager/")
        assert request.url.host == "api.scryfall.com"
        paths.append(request.url.path)
        return httpx.Response(200, json=card(request.url.path.rsplit("/", 1)[1]))

    scryfall_transport(handler)
    result = await ScryfallClient().get_by_ids(["printing-1", "printing-2"])
    assert paths == ["/cards/printing-1", "/cards/printing-2"]
    assert result["printing-2"].oracle_id == "shared-oracle"


@pytest.mark.parametrize(
    "status,payload,error",
    [
        (404, {}, ScryfallCardNotFound),
        (429, {}, ScryfallUnavailable),
        (500, {}, ScryfallUnavailable),
        (200, {}, ScryfallUnavailable),
        (200, {**card("printing-1"), "oracle_id": None}, ScryfallUnavailable),
        (200, card("wrong-id"), ScryfallUnavailable),
    ],
)
async def test_scryfall_failures(scryfall_transport, status, payload, error):
    scryfall_transport(lambda request: httpx.Response(status, json=payload))
    with pytest.raises(error):
        await ScryfallClient().get_by_ids(["printing-1"])


async def test_scryfall_timeout(scryfall_transport):
    def handler(request):
        raise httpx.ReadTimeout("timeout", request=request)

    scryfall_transport(handler)
    with pytest.raises(ScryfallUnavailable):
        await ScryfallClient().get_by_ids(["printing-1"])


@pytest.fixture
def resolve_client():
    repository, scryfall = AsyncMock(), AsyncMock(spec=ScryfallClient)
    repository.get_by_ids.return_value = [card("printing-1")]
    scryfall.get_by_ids.return_value = {
        "printing-2": ResolvedCardResponse.model_validate(card("printing-2"))
    }

    @asynccontextmanager
    async def lifespan(application):
        yield

    original = app.router.lifespan_context
    app.router.lifespan_context = lifespan
    app.dependency_overrides[get_service] = lambda: CardSearchService(repository, scryfall)
    try:
        with TestClient(app) as client:
            yield client, repository, scryfall
    finally:
        app.dependency_overrides.clear()
        app.router.lifespan_context = original


def test_endpoint_exact_contract(resolve_client):
    client, _, _ = resolve_client
    response = client.post("/cards/resolve", json={"ids": ["printing-2", "printing-1"]})
    assert response.status_code == 200
    assert response.json() == [
        {
            "id": value,
            "oracleId": "shared-oracle",
            "name": "Lightning Bolt",
            "layout": "normal",
            "typeLine": "Instant",
        }
        for value in ["printing-2", "printing-1"]
    ]


@pytest.mark.parametrize(
    "body",
    [{}, {"ids": "x"}, {"ids": [1]}, {"ids": [""]}, {"ids": ["../search?q=x"]}, {"ids": None}],
)
def test_endpoint_invalid_input(resolve_client, body):
    client, repository, scryfall = resolve_client
    assert client.post("/cards/resolve", json=body).status_code == 422
    repository.get_by_ids.assert_not_awaited()
    scryfall.get_by_ids.assert_not_awaited()


def test_endpoint_empty_input(resolve_client):
    client, _, _ = resolve_client
    response = client.post("/cards/resolve", json={"ids": []})
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize(
    "error,status",
    [(ScryfallCardNotFound("missing"), 404), (ScryfallUnavailable("unavailable"), 502)],
)
def test_endpoint_upstream_errors(resolve_client, error, status):
    client, _, scryfall = resolve_client
    scryfall.get_by_ids.side_effect = error
    response = client.post("/cards/resolve", json={"ids": ["printing-1", "missing"]})
    assert response.status_code == status
    assert "detail" in response.json()
