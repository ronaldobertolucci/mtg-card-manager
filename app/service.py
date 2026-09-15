from app.repository import CardRepository, Document
from app.schemas import CardResponse, SearchParams

TRANSLATABLE_FIELDS = ("name", "oracle_text", "type_line", "flavor_text")


class CardSearchService:
    def __init__(self, repository: CardRepository) -> None:
        self._repository = repository

    async def get_by_oracle_id(self, oracle_id: str, lang: str) -> CardResponse | None:
        card = await self._repository.get_by_oracle_id(oracle_id)
        if card is None:
            return None
        if lang != "en":
            translations = await self._repository.get_translations([oracle_id], lang)
            translation = translations.get(oracle_id)
            if translation is None:
                return None
            card = self._merge_translation(card, translation)
            if card is None:
                return None
        return self._serialize(card, lang)

    async def search(self, params: SearchParams) -> list[CardResponse]:
        translated_oracle_ids: list[str] | None = None
        if params.lang != "en":
            translated_oracle_ids = await self._repository.search_translation_oracle_ids(params)
            if not translated_oracle_ids:
                return []

        cards = await self._repository.search_oracle_cards(params, translated_oracle_ids)
        if params.lang != "en" and cards:
            translations = await self._repository.get_translations(
                [str(card["oracle_id"]) for card in cards], params.lang
            )
            localized_cards: list[Document] = []
            for card in cards:
                translation = translations.get(str(card["oracle_id"]))
                if translation is not None:
                    localized = self._merge_translation(card, translation)
                    if localized is not None:
                        localized_cards.append(localized)
            cards = localized_cards

        return [self._serialize(card, params.lang) for card in cards]

    @staticmethod
    def _merge_translation(card: Document, translation: Document | None) -> Document | None:
        merged = dict(card)
        if translation is not None:
            for field in TRANSLATABLE_FIELDS:
                # A localized response must never fall back to English text.
                merged[field] = translation.get(field)
            original_faces = card.get("card_faces") or []
            if len(original_faces) >= 2:
                faces = translation.get("card_faces") or []
                indices = [face.get("face_index") for face in faces]
                if (
                    len(indices) != len(original_faces)
                    or set(indices) != set(range(len(original_faces)))
                    or any(not face.get("name") for face in faces)
                ):
                    return None
                by_index = {face["face_index"]: face for face in faces}
                merged["card_faces"] = [
                    {**face, **{field: by_index[index].get(field) for field in TRANSLATABLE_FIELDS}}
                    for index, face in enumerate(original_faces)
                ]
                merged["name"] = " // ".join(face["name"] for face in merged["card_faces"])
        return merged

    @staticmethod
    def _serialize(card: Document, lang: str) -> CardResponse:
        payload = dict(card)
        payload.pop("_id", None)
        payload["lang"] = lang
        return CardResponse.model_validate(payload)
