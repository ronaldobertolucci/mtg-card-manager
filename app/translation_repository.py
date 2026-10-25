import re
from collections.abc import Mapping
from typing import Any, Protocol

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from app.repository import accessory_predicates
from app.translation_schemas import TranslationCardsParams

TranslationDocument = dict[str, Any]


class TranslationRepository(Protocol):
    async def list_cards(self, params: TranslationCardsParams) -> list[TranslationDocument]: ...

    async def get_oracle_card(self, oracle_id: str) -> TranslationDocument | None: ...

    async def create(self, document: TranslationDocument) -> TranslationDocument: ...

    async def get_by_id(self, translation_id: ObjectId) -> TranslationDocument | None: ...

    async def get_by_card_language(
        self, oracle_id: str, lang: str
    ) -> TranslationDocument | None: ...

    async def list(
        self, filters: Mapping[str, Any], limit: int, offset: int
    ) -> list[TranslationDocument]: ...

    async def update(
        self, translation_id: ObjectId, changes: Mapping[str, Any]
    ) -> TranslationDocument | None: ...

    async def delete(self, translation_id: ObjectId) -> bool: ...


def translation_cards_pipeline(
    params: TranslationCardsParams, translations_collection: str = "translations",
) -> list[TranslationDocument]:
    """Stream in binary name/identity order, filtering existence before pagination."""
    filters: TranslationDocument = {
        "$or" if params.kind == "accessories" else "$nor": accessory_predicates(),
    }
    if params.name is not None:
        filters["name"] = {"$regex": re.escape(params.name), "$options": "i"}
    pipeline: list[TranslationDocument] = [
        {"$match": filters},
        {"$sort": {"name": 1, "oracle_id": 1}},
    ]
    pagination: list[TranslationDocument] = [
        {"$skip": params.offset}, {"$limit": params.limit + 1},
    ]
    # Without an existence filter, only the requested page needs the join.
    if params.translation_status == "all":
        pipeline.extend(pagination)
    pipeline.append({"$lookup": {
        "from": translations_collection,
        "let": {"oracle_id": "$oracle_id"},
        "pipeline": [
            {"$match": {"$expr": {"$and": [
                {"$eq": ["$oracle_id", "$$oracle_id"]},
                {"$eq": ["$lang", params.lang]},
            ]}}},
            {"$project": {"_id": 1}},
        ],
        "as": "_translation_records",
    }})
    if params.translation_status != "all":
        pipeline.append({"$match": {
            "_translation_records.0": {"$exists": params.translation_status == "present"},
        }})
        # Existence must be evaluated BEFORE skip/limit, even for empty pages.
        pipeline.extend(pagination)
    return pipeline


class MongoTranslationRepository:
    def __init__(self, database: AsyncIOMotorDatabase) -> None:
        self._oracle_cards = database.oracle_cards
        self._translations = database.translations

    async def list_cards(self, params: TranslationCardsParams) -> list[TranslationDocument]:
        pipeline = translation_cards_pipeline(params, self._translations.name)
        # The unique oracle_id index ensures one item per card, without unwinding faces.
        cursor = self._oracle_cards.aggregate(pipeline, collation={"locale": "simple"})
        return [document async for document in cursor]

    async def get_oracle_card(self, oracle_id: str) -> TranslationDocument | None:
        return await self._oracle_cards.find_one({"oracle_id": oracle_id}, {"card_faces": 1})

    async def create(self, document: TranslationDocument) -> TranslationDocument:
        result = await self._translations.insert_one(document)
        return {**document, "_id": result.inserted_id}

    async def get_by_id(self, translation_id: ObjectId) -> TranslationDocument | None:
        return await self._translations.find_one({"_id": translation_id})

    async def get_by_card_language(self, oracle_id: str, lang: str) -> TranslationDocument | None:
        return await self._translations.find_one({"oracle_id": oracle_id, "lang": lang})

    async def list(
        self, filters: Mapping[str, Any], limit: int, offset: int
    ) -> list[TranslationDocument]:
        cursor = (
            self._translations.find(dict(filters))
            .sort([("lang", 1), ("oracle_id", 1), ("_id", 1)])
            .skip(offset)
            .limit(limit)
        )
        return [document async for document in cursor]

    async def update(
        self, translation_id: ObjectId, changes: Mapping[str, Any]
    ) -> TranslationDocument | None:
        return await self._translations.find_one_and_update(
            {"_id": translation_id},
            {"$set": dict(changes)},
            return_document=ReturnDocument.AFTER,
        )

    async def delete(self, translation_id: ObjectId) -> bool:
        result = await self._translations.delete_one({"_id": translation_id})
        return result.deleted_count == 1
