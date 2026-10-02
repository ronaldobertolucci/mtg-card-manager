from app.repository import CardRepository, Document
from app.schemas import CardResponse, CardSearchResponse, ResolvedCardResponse, SearchParams
from app.scryfall import ScryfallClient

TRANSLATABLE_FIELDS = ("name", "oracle_text", "type_line", "flavor_text")


class CardSearchService:
    def __init__(
        self, repository: CardRepository, scryfall: ScryfallClient | None = None
    ) -> None:
        self._repository = repository
        self._scryfall = scryfall if scryfall is not None else ScryfallClient()

    async def resolve(self, ids: list[str]) -> list[ResolvedCardResponse]:
        if not ids:
            return []
        unique_ids = list(dict.fromkeys(ids))
        local_cards = await self._repository.get_by_ids(unique_ids)
        cards = {card["id"]: ResolvedCardResponse.model_validate(card) for card in local_cards}
        missing_ids = [card_id for card_id in unique_ids if card_id not in cards]
        if missing_ids:
            cards.update(await self._scryfall.get_by_ids(missing_ids))
        return [cards[card_id] for card_id in ids]

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

    async def search(self, params: SearchParams) -> CardSearchResponse:
        if params.lang == "en":
            # Internal lookahead may be 201; the public limit still cannot exceed 200.
            query = params.model_copy(update={"limit": params.limit + 1})
            cards = await self._repository.search_oracle_cards(query)
            return CardSearchResponse(
                items=[self._serialize(card, params.lang) for card in cards[:params.limit]],
                limit=params.limit, offset=params.offset, has_next=len(cards) > params.limit,
            )

        matches = await self._repository.search_translation_matches(params)
        items: list[CardResponse] = []
        page = CardSearchResponse(
            items=items, limit=params.limit, offset=params.offset, has_next=False
        )
        if not matches:
            return page

        # Candidate offsets are internal. Public offsets count only usable translations.
        # Scan in bounded batches until a valid lookahead is found or candidates end.
        batch_size = 200
        candidate_offset = 0
        valid_skipped = 0
        while True:
            query = params.model_copy(update={"offset": candidate_offset, "limit": batch_size})
            cards = await self._repository.search_oracle_cards(query, matches)
            if not cards:
                break
            translations = await self._repository.get_translations(
                [str(card["oracle_id"]) for card in cards], params.lang
            )
            for card in cards:
                localized = self._merge_translation(
                    card, translations.get(str(card["oracle_id"]))
                )
                if localized is None:
                    continue
                if valid_skipped < params.offset:
                    valid_skipped += 1
                    continue
                if len(page.items) == params.limit:
                    page.has_next = True
                    return page
                page.items.append(self._serialize(localized, params.lang))
            if len(cards) < batch_size:
                break
            candidate_offset += len(cards)
        return page

    @staticmethod
    def _merge_translation(card: Document, translation: Document | None) -> Document | None:
        if translation is None:
            return None

        def valid_text(document: Document) -> bool:
            name = document.get("name")
            return (
                isinstance(name, str) and bool(name.strip())
                and all(document.get(field) is None or isinstance(document[field], str)
                        for field in TRANSLATABLE_FIELDS[1:])
            )

        original_faces = card.get("card_faces") or []
        faces = translation.get("card_faces")
        if len(original_faces) >= 2:
            if not isinstance(faces, list) or len(faces) != len(original_faces):
                return None
            if any(
                not isinstance(face, dict)
                or type(face.get("face_index")) is not int
                or not valid_text(face)
                for face in faces
            ):
                return None
            if {face["face_index"] for face in faces} != set(range(len(original_faces))):
                return None
            by_index = {face["face_index"]: face for face in faces}
            merged = dict(card)
            merged["card_faces"] = [
                {**face, **{field: by_index[index].get(field) for field in TRANSLATABLE_FIELDS}}
                for index, face in enumerate(original_faces)
            ]
            merged.update({field: None for field in TRANSLATABLE_FIELDS[1:]})
            merged["name"] = " // ".join(face["name"] for face in merged["card_faces"])
            return merged
        if faces is not None or not valid_text(translation):
            return None
        # A localized response must never fall back to English text.
        return {**card, **{field: translation.get(field) for field in TRANSLATABLE_FIELDS}}

    @staticmethod
    def _serialize(card: Document, lang: str) -> CardResponse:
        payload = dict(card)
        payload.pop("_id", None)
        payload["printing_lang"] = card.get("lang")
        payload["lang"] = lang
        return CardResponse.model_validate(payload)
