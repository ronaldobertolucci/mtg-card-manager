import argparse
import gzip
import io
import os
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO

import ijson
import requests
from pymongo import MongoClient, UpdateOne
from pymongo.collection import Collection
from pymongo.errors import BulkWriteError

DEFAULT_METADATA_URL = "https://api.scryfall.com/bulk-data/oracle-cards"
USER_AGENT = os.getenv(
    "SCRYFALL_USER_AGENT",
    "mtg-card-manager/0.1 (bulk catalog synchronizer)",
)
REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json;q=0.9,*/*;q=0.8",
}
MAX_HTTP_ATTEMPTS = 4
METADATA_TIMEOUT = (15, 120)
DOWNLOAD_TIMEOUT = (30, 600)
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _is_retryable(error: requests.RequestException) -> bool:
    if error.response is None:
        return True
    return error.response.status_code in RETRYABLE_STATUS_CODES


def _wait_before_retry(attempt: int) -> None:
    time.sleep(min(2 ** (attempt - 1), 8))


def _get_json_with_retry(url: str) -> Any:
    last_error: requests.RequestException | None = None
    for attempt in range(1, MAX_HTTP_ATTEMPTS + 1):
        response: requests.Response | None = None
        try:
            response = requests.get(url, headers=REQUEST_HEADERS, timeout=METADATA_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as error:
            last_error = error
            if attempt == MAX_HTTP_ATTEMPTS or not _is_retryable(error):
                break
            _wait_before_retry(attempt)
        finally:
            if response is not None:
                response.close()

    raise RuntimeError(
        f"Failed to fetch Scryfall metadata after {attempt} attempts: {last_error}"
    ) from last_error


def bulk_download_url(metadata_url: str) -> str:
    payload = _get_json_with_retry(metadata_url)
    metadata = _select_oracle_metadata(payload)

    # Scryfall migrated bulk files from JSON arrays to compressed JSONL in 2026.
    # Keep the legacy field as a fallback so downloaded fixtures and older mirrors work.
    for field in ("jsonl_download_uri", "download_uri"):
        download_uri = metadata.get(field)
        if isinstance(download_uri, str) and download_uri:
            return download_uri

    available_fields = ", ".join(sorted(metadata))
    raise RuntimeError(
        "Scryfall metadata did not contain jsonl_download_uri or download_uri "
        f"(available fields: {available_fields})"
    )


def _select_oracle_metadata(payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError("Scryfall metadata response must be a JSON object")

    data = payload.get("data")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("type") == "oracle_cards":
                return item
        raise RuntimeError("Scryfall bulk data list did not contain oracle_cards")

    return payload


def download_to_file(url: str, destination: Path) -> None:
    last_error: requests.RequestException | None = None
    for attempt in range(1, MAX_HTTP_ATTEMPTS + 1):
        try:
            with requests.get(
                url,
                headers=REQUEST_HEADERS,
                stream=True,
                timeout=DOWNLOAD_TIMEOUT,
            ) as response:
                response.raise_for_status()
                with destination.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            output.write(chunk)
            return
        except requests.RequestException as error:
            last_error = error
            destination.unlink(missing_ok=True)
            if attempt == MAX_HTTP_ATTEMPTS or not _is_retryable(error):
                break
            _wait_before_retry(attempt)

    raise RuntimeError(
        f"Failed to download Scryfall bulk data after {attempt} attempts: {last_error}"
    ) from last_error


@contextmanager
def _open_bulk_stream(path: Path) -> Iterator[BinaryIO]:
    with path.open("rb") as raw_source:
        if raw_source.peek(2)[:2] == b"\x1f\x8b":
            with gzip.GzipFile(fileobj=raw_source, mode="rb") as gzip_source:
                with io.BufferedReader(gzip_source) as buffered_source:
                    yield buffered_source
        else:
            yield raw_source


def iter_cards(path: Path) -> Iterator[dict[str, Any]]:
    with _open_bulk_stream(path) as source:
        first_token = source.peek(256).lstrip()[:1]
        if first_token == b"[":
            cards = ijson.items(source, "item", use_float=True)
        elif first_token == b"{":
            cards = ijson.items(source, "", multiple_values=True, use_float=True)
        else:
            raise RuntimeError("Scryfall bulk file is neither a JSON array nor JSONL")

        for card in cards:
            if isinstance(card, dict):
                yield card


def prepare_card_document(card: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    card_id = card.get("id")
    oracle_id = card.get("oracle_id")
    if not isinstance(card_id, str) or not isinstance(oracle_id, str):
        return None
    return oracle_id, dict(card)


def flush_batch(collection: Collection[dict[str, Any]], cards: list[dict[str, Any]]) -> int:
    operations: list[UpdateOne] = []
    for card in cards:
        prepared = prepare_card_document(card)
        if prepared is None:
            continue
        oracle_id, document = prepared
        operations.append(
            UpdateOne(
                {"oracle_id": oracle_id},
                {"$set": document, "$setOnInsert": {"_id": oracle_id}},
                upsert=True,
            )
        )
    if not operations:
        return 0
    try:
        collection.bulk_write(operations, ordered=False)
    except BulkWriteError as error:
        raise RuntimeError(f"MongoDB bulk write failed: {error.details}") from error
    return len(operations)


def sync_cards(collection: Collection[dict[str, Any]], path: Path, batch_size: int) -> int:
    batch: list[dict[str, Any]] = []
    processed = 0
    for card in iter_cards(path):
        batch.append(card)
        if len(batch) >= batch_size:
            processed += flush_batch(collection, batch)
            batch.clear()
    if batch:
        processed += flush_batch(collection, batch)
    return processed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Synchronize Scryfall Oracle Cards into MongoDB")
    parser.add_argument(
        "--file",
        type=Path,
        help="Use an existing JSON, JSONL, or gzip-compressed bulk file",
    )
    parser.add_argument("--batch-size", type=int, default=int(os.getenv("SYNC_BATCH_SIZE", "1000")))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be greater than zero")

    mongo_uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
    database_name = os.getenv("MONGODB_DATABASE", "card_manager")
    metadata_url = os.getenv("SCRYFALL_BULK_METADATA_URL", DEFAULT_METADATA_URL)

    client: MongoClient[dict[str, Any]] = MongoClient(mongo_uri)
    try:
        collection = client[database_name].oracle_cards
        collection.create_index(
            [("oracle_id", 1)],
            unique=True,
            name="uq_oracle_cards_oracle_id",
        )
        if args.file is not None:
            total = sync_cards(collection, args.file, args.batch_size)
        else:
            with tempfile.TemporaryDirectory(prefix="scryfall-") as temporary_directory:
                bulk_file = Path(temporary_directory) / "oracle-cards.bulk"
                download_to_file(bulk_download_url(metadata_url), bulk_file)
                total = sync_cards(collection, bulk_file, args.batch_size)
        print(f"Synchronized {total} oracle cards")
    finally:
        client.close()


if __name__ == "__main__":
    main()
