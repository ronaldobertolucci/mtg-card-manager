from app.repository import CardRepository, Document
from app.schemas import CardResponse, SearchParams

TRANSLATABLE_FIELDS = ("name", "oracle_text", "type_line", "flavor_text")


class CardSearchService:
    def __init__(self, repository: CardRepository) -> None:
        self._repository = repository

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
                    localized_cards.append(self._merge_translation(card, translation))
            cards = localized_cards

        return [self._serialize(card, params.lang) for card in cards]

    @staticmethod
    def _merge_translation(card: Document, translation: Document | None) -> Document:
        merged = dict(card)
        if translation is not None:
            for field in TRANSLATABLE_FIELDS:
                # A localized response must never fall back to English text.
                merged[field] = translation.get(field)
        return merged

    @staticmethod
    def _serialize(card: Document, lang: str) -> CardResponse:
        payload = dict(card)
        payload.pop("_id", None)
        payload["lang"] = lang
        return CardResponse.model_validate(payload)
