from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SearchParams(BaseModel):
    lang: str = Field(
        default="pt-BR",
        min_length=2,
        max_length=16,
        pattern=r"^[A-Za-z]{2}(?:-[A-Za-z]{2})?$",
    )
    name: str | None = Field(default=None, min_length=1, max_length=200)
    oracle_text: str | None = Field(default=None, min_length=1, max_length=1000)
    type_line: str | None = Field(default=None, min_length=1, max_length=200)
    colors: list[str] | None = None
    mana_cost: str | None = Field(default=None, max_length=100)
    cmc: float | None = Field(default=None, ge=0)
    cmc_gte: float | None = Field(default=None, ge=0)
    cmc_lte: float | None = Field(default=None, ge=0)
    power: str | None = Field(default=None, max_length=20)
    toughness: str | None = Field(default=None, max_length=20)
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0, le=100_000)

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
        filters = (
            self.name,
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
        return any((self.name, self.oracle_text, self.type_line))


class CardResponse(BaseModel):
    id: str
    oracle_id: str
    lang: str
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
