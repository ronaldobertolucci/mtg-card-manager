import re
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.schemas import SearchParams

Document = dict[str, Any]


class CardRepository(Protocol):
    async def search_translation_oracle_ids(self, params: SearchParams) -> list[str]: ...

    async def search_oracle_cards(
        self, params: SearchParams, oracle_ids: Sequence[str] | None = None
    ) -> list[Document]: ...

    async def get_translations(
        self, oracle_ids: Sequence[str], lang: str
    ) -> Mapping[str, Document]: ...


def _text_filter(params: SearchParams) -> Document:
    result: Document = {}
    for field in ("name", "oracle_text", "type_line"):
        value = getattr(params, field)
        if value is not None:
            result[field] = {"$regex": re.escape(value), "$options": "i"}
    return result


def _attribute_filter(params: SearchParams) -> Document:
    result: Document = {}
    if params.colors is not None:
        # Equality is array equality in MongoDB: same values in the same order.
        result["colors"] = params.colors
    for field in ("mana_cost", "power", "toughness"):
        value = getattr(params, field)
        if value is not None:
            result[field] = value
    if params.cmc is not None:
        result["cmc"] = params.cmc
    elif params.cmc_gte is not None or params.cmc_lte is not None:
        comparison: Document = {}
        if params.cmc_gte is not None:
            comparison["$gte"] = params.cmc_gte
        if params.cmc_lte is not None:
            comparison["$lte"] = params.cmc_lte
        result["cmc"] = comparison
    return result


def _oracle_card_filter(
    params: SearchParams, oracle_ids: Sequence[str] | None = None
) -> Document:
    query = _attribute_filter(params)
    if params.lang == "en" and params.has_text_filters:
        query.update(_text_filter(params))
    if oracle_ids is not None:
        query["oracle_id"] = {"$in": list(oracle_ids)}
    return query


class MongoCardRepository:
    def __init__(self, database: AsyncIOMotorDatabase) -> None:
        self._oracle_cards = database.oracle_cards
        self._translations = database.translations

    async def search_translation_oracle_ids(self, params: SearchParams) -> list[str]:
        query = {"lang": params.lang, **_text_filter(params)}
        cursor = self._translations.find(query, {"oracle_id": 1, "_id": 0})
        return [document["oracle_id"] async for document in cursor]

    async def search_oracle_cards(
        self, params: SearchParams, oracle_ids: Sequence[str] | None = None
    ) -> list[Document]:
        query = _oracle_card_filter(params, oracle_ids)
        cursor = (
            self._oracle_cards.find(query)
            .sort([("name", 1), ("_id", 1)])
            .skip(params.offset)
            .limit(params.limit)
        )
        return [document async for document in cursor]

    async def get_translations(
        self, oracle_ids: Sequence[str], lang: str
    ) -> Mapping[str, Document]:
        if not oracle_ids:
            return {}
        query = {"oracle_id": {"$in": list(oracle_ids)}, "lang": lang}
        cursor = self._translations.find(query, {"_id": 0})
        return {document["oracle_id"]: document async for document in cursor}
