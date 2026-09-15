import re
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.schemas import SearchParams

Document = dict[str, Any]
# None permits root/any-face matching; indices restrict mechanics to translated faces.
TranslationMatches = Mapping[str, list[int] | None]


class CardRepository(Protocol):
    async def get_by_oracle_id(self, oracle_id: str) -> Document | None: ...

    async def search_translation_matches(self, params: SearchParams) -> TranslationMatches: ...

    async def search_oracle_cards(
        self, params: SearchParams, oracle_ids: TranslationMatches | None = None
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


def _root_or_face_filter(filters: Document) -> Document:
    if not filters:
        return {}
    return {"$or": [filters, {"card_faces": {"$elemMatch": filters}}]}


def _oracle_card_filter(
    params: SearchParams, oracle_ids: TranslationMatches | None = None
) -> Document:
    attributes = _attribute_filter(params)
    query: Document = {}
    if "cmc" in attributes:
        query["cmc"] = attributes.pop("cmc")
    if params.lang == "en":
        text = _text_filter(params)
        if "name" in text:
            query["name"] = text.pop("name")
        query.update(_root_or_face_filter({**attributes, **text}))
    elif oracle_ids is not None:
        branches: list[Document] = []
        grouped: dict[int | None, list[str]] = {}
        for oracle_id, indices in oracle_ids.items():
            for index in [None] if indices is None else indices:
                grouped.setdefault(index, []).append(oracle_id)
        for index, ids in grouped.items():
            identity = {"oracle_id": {"$in": ids}}
            if index is None:
                branches.append({**identity, **_root_or_face_filter(attributes)})
            else:
                branches.append(
                    {
                        **identity,
                        f"card_faces.{index}": {"$exists": True},
                        **{
                            f"card_faces.{index}.{field}": value
                            for field, value in attributes.items()
                        },
                    }
                )
        query.update({"$or": branches} if branches else {"oracle_id": {"$in": []}})
    else:
        query.update(_root_or_face_filter(attributes))
    return query


class MongoCardRepository:
    def __init__(self, database: AsyncIOMotorDatabase) -> None:
        self._oracle_cards = database.oracle_cards
        self._translations = database.translations

    async def get_by_oracle_id(self, oracle_id: str) -> Document | None:
        return await self._oracle_cards.find_one({"oracle_id": oracle_id})

    async def search_translation_matches(self, params: SearchParams) -> TranslationMatches:
        text = _text_filter(params)
        query: Document = {"lang": params.lang}
        if "name" in text:
            query["name"] = text.pop("name")
        query.update(_root_or_face_filter(text))
        cursor = self._translations.find(
            query,
            {
                "_id": 0,
                "oracle_id": 1,
                "card_faces": 1,
                "oracle_text": 1,
                "type_line": 1,
            },
        )
        matches: dict[str, list[int] | None] = {}
        for_translation = {
            field: re.compile(condition["$regex"], re.IGNORECASE)
            for field, condition in text.items()
        }

        def matches_text(document: Document) -> bool:
            return all(
                isinstance(document.get(field), str) and pattern.search(document[field])
                for field, pattern in for_translation.items()
            )

        async for document in cursor:
            if matches_text(document):
                matches[document["oracle_id"]] = None
            else:
                indices = [
                    face["face_index"]
                    for face in document.get("card_faces") or []
                    if matches_text(face)
                ]
                if indices:
                    matches[document["oracle_id"]] = indices
        return matches

    async def search_oracle_cards(
        self, params: SearchParams, oracle_ids: TranslationMatches | None = None
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
