import gzip
import json
from pathlib import Path
from typing import Any

import pytest
import requests

from sync_scryfall import bulk_download_url, iter_cards, prepare_card_document, sync_cards


class FakeCollection:
    def __init__(self) -> None:
        self.batch_sizes: list[int] = []
        self.documents: dict[str, dict[str, Any]] = {}

    def bulk_write(self, operations: list[Any], ordered: bool) -> None:
        assert ordered is False
        self.batch_sizes.append(len(operations))
        for operation in operations:
            oracle_id = operation._filter["oracle_id"]
            assert operation._doc["$setOnInsert"]["_id"] == oracle_id
            self.documents[oracle_id] = dict(operation._doc["$set"])


def test_streams_and_flushes_in_batches(tmp_path: Path) -> None:
    path = tmp_path / "cards.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": f"id-{index}",
                    "oracle_id": f"oracle-{index}",
                    "name": "Card",
                }
                for index in range(5)
            ]
        )
    )
    collection = FakeCollection()

    total = sync_cards(collection, path, batch_size=2)  # type: ignore[arg-type]

    assert total == 5
    assert collection.batch_sizes == [2, 2, 1]
    assert set(collection.documents) == {f"oracle-{index}" for index in range(5)}
    assert len(list(iter_cards(path))) == 5


def test_prepares_oracle_id_as_mongo_id_and_preserves_printing_id() -> None:
    prepared = prepare_card_document(
        {"id": "printing-1", "oracle_id": "oracle-1", "name": "Card"}
    )

    assert prepared == (
        "oracle-1",
        {"id": "printing-1", "oracle_id": "oracle-1", "name": "Card"},
    )


def test_rejects_card_without_oracle_id() -> None:
    assert prepare_card_document({"id": "printing-1", "name": "Card"}) is None


def test_new_representative_replaces_printing_for_same_oracle_id(tmp_path: Path) -> None:
    path = tmp_path / "cards.json"
    collection = FakeCollection()
    path.write_text(
        json.dumps(
            [{"id": "printing-old", "oracle_id": "oracle-1", "name": "Card"}]
        )
    )
    sync_cards(collection, path, batch_size=100)  # type: ignore[arg-type]

    path.write_text(
        json.dumps(
            [{"id": "printing-new", "oracle_id": "oracle-1", "name": "Card"}]
        )
    )
    sync_cards(collection, path, batch_size=100)  # type: ignore[arg-type]

    assert list(collection.documents) == ["oracle-1"]
    assert collection.documents["oracle-1"]["id"] == "printing-new"


def test_reads_gzip_compressed_jsonl_and_normalizes_numbers(tmp_path: Path) -> None:
    path = tmp_path / "cards.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as output:
        output.write(
            json.dumps(
                {"id": "id-1", "oracle_id": "oracle-1", "name": "Card 1", "cmc": 0.5}
            )
            + "\n"
        )
        output.write(
            json.dumps(
                {"id": "id-2", "oracle_id": "oracle-2", "name": "Card 2", "cmc": 2}
            )
            + "\n"
        )

    cards = list(iter_cards(path))

    assert [card["id"] for card in cards] == ["id-1", "id-2"]
    assert cards[0]["cmc"] == 0.5
    assert isinstance(cards[0]["cmc"], float)


class FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload

    def close(self) -> None:
        return None


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {"type": "oracle_cards", "jsonl_download_uri": "https://example/cards.jsonl.gz"},
            "https://example/cards.jsonl.gz",
        ),
        (
            {
                "data": [
                    {"type": "all_cards", "jsonl_download_uri": "https://example/all.jsonl.gz"},
                    {"type": "oracle_cards", "download_uri": "https://example/oracle.json"},
                ]
            },
            "https://example/oracle.json",
        ),
    ],
)
def test_resolves_current_and_legacy_download_urls(
    monkeypatch: pytest.MonkeyPatch, payload: Any, expected: str
) -> None:
    monkeypatch.setattr(
        "sync_scryfall.requests.get",
        lambda *args, **kwargs: FakeResponse(payload),
    )

    assert bulk_download_url("https://example/metadata") == expected


def test_metadata_error_lists_received_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "sync_scryfall.requests.get",
        lambda *args, **kwargs: FakeResponse({"type": "oracle_cards", "name": "Oracle Cards"}),
    )

    with pytest.raises(RuntimeError, match="available fields: name, type"):
        bulk_download_url("https://example/metadata")


def test_metadata_request_retries_after_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    responses: list[Any] = [
        requests.exceptions.ReadTimeout("slow response"),
        FakeResponse(
            {
                "type": "oracle_cards",
                "jsonl_download_uri": "https://example/cards.jsonl.gz",
            }
        ),
    ]
    waits: list[int] = []

    def fake_get(*args: Any, **kwargs: Any) -> FakeResponse:
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("sync_scryfall.requests.get", fake_get)
    monkeypatch.setattr("sync_scryfall.time.sleep", waits.append)

    assert bulk_download_url("https://example/metadata") == "https://example/cards.jsonl.gz"
    assert waits == [1]
