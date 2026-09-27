from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.legalities import GameFormat, Legality


class ResolveCardsRequest(BaseModel):
    ids: list[Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9-]+$")]]


class ResolvedCardResponse(BaseModel):
    id: str
    oracle_id: str = Field(serialization_alias="oracleId", min_length=1)
    name: str
    layout: str
    type_line: str = Field(serialization_alias="typeLine")


class SearchParams(BaseModel):
    lang: str = Field(
        default="pt",
        min_length=2,
        max_length=16,
        pattern=r"^[A-Za-z]{2}(?:-[A-Za-z]{2})?$",
    )
    name: str | None = Field(default=None, min_length=1, max_length=200)
    name_exact: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Exact full card name (case-sensitive).",
    )
    oracle_text: str | None = Field(default=None, min_length=1, max_length=1000)
    type_line: str | None = Field(default=None, min_length=1, max_length=200)
    colors: list[str] | None = None
    mana_cost: str | None = Field(default=None, max_length=100)
    cmc: float | None = Field(default=None, ge=0)
    cmc_gte: float | None = Field(default=None, ge=0)
    cmc_lte: float | None = Field(default=None, ge=0)
    power: str | None = Field(default=None, max_length=20)
    toughness: str | None = Field(default=None, max_length=20)
    format: GameFormat | None = Field(default=None, description="Scryfall format identifier.")
    legality: list[Legality] | None = Field(
        default=None,
        min_length=1,
        description="Comma-separated statuses (OR). Requires format; defaults to legal,restricted.",
    )
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0, le=100_000)
    include_tokens: bool = Field(
        default=False,
        description="Include token and double-faced token layouts in search results.",
    )

    @field_validator("format", mode="before")
    @classmethod
    def normalize_format(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("legality", mode="before")
    @classmethod
    def parse_legality(cls, value: Any) -> Any:
        if value is None:
            return value
        values = [value] if isinstance(value, str) else value
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise ValueError("legality must be a comma-separated string")
        parts = (part.strip().lower() for item in values for part in item.split(","))
        return list(dict.fromkeys(parts))

    @field_validator("colors", mode="before")
    @classmethod
    def parse_colors(cls, value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, list):
            values: list[str] = []
            for item in value:
                if not isinstance(item, str):
                    raise ValueError("colors must be a comma-separated string")
                values.extend(color.strip().upper() for color in item.split(",") if color.strip())
            return values
        if not isinstance(value, str):
            raise ValueError("colors must be a comma-separated string")
        if value == "":
            return []
        return [color.strip().upper() for color in value.split(",")]

    @field_validator("colors")
    @classmethod
    def validate_colors(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        allowed = {"W", "U", "B", "R", "G"}
        if len(value) != len(set(value)) or any(color not in allowed for color in value):
            raise ValueError("colors must contain unique values from W,U,B,R,G")
        return value

    @model_validator(mode="after")
    def validate_filters(self) -> "SearchParams":
        if self.legality is not None and self.format is None:
            raise ValueError("legality requires format")
        if self.format is not None and self.legality is None:
            self.legality = [Legality.LEGAL, Legality.RESTRICTED]
        filters = (
            self.format,
            self.name,
            self.name_exact,
            self.oracle_text,
            self.type_line,
            self.colors,
            self.mana_cost,
            self.cmc,
            self.cmc_gte,
            self.cmc_lte,
            self.power,
            self.toughness,
        )
        if all(value is None for value in filters):
            raise ValueError("at least one search filter is required")
        if self.cmc is not None and (self.cmc_gte is not None or self.cmc_lte is not None):
            raise ValueError("cmc cannot be combined with cmc_gte or cmc_lte")
        if self.cmc_gte is not None and self.cmc_lte is not None and self.cmc_gte > self.cmc_lte:
            raise ValueError("cmc_gte cannot be greater than cmc_lte")
        return self

    @property
    def has_text_filters(self) -> bool:
        return any((self.name, self.name_exact, self.oracle_text, self.type_line))


class CardResponse(BaseModel):
    id: str
    oracle_id: str
    lang: str
    legalities: dict[str, Legality] = Field(default_factory=dict)
    name: str
    oracle_text: str | None = None
    type_line: str | None = None
    flavor_text: str | None = None
    colors: list[str] = Field(default_factory=list)
    mana_cost: str | None = None
    cmc: float | None = None
    power: str | None = None
    toughness: str | None = None

    model_config = ConfigDict(extra="allow")
