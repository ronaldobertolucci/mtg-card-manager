from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

LANGUAGE_PATTERN = r"^[A-Za-z]{2}(?:-[A-Za-z]{2})?$"


def normalize_language(value: str) -> str:
    parts = value.split("-")
    return parts[0].lower() if len(parts) == 1 else f"{parts[0].lower()}-{parts[1].upper()}"


class TranslationFace(BaseModel):
    face_index: int = Field(ge=0, strict=True)
    name: str = Field(min_length=1, max_length=300)
    oracle_text: str | None = Field(default=None, max_length=10_000)
    type_line: str | None = Field(default=None, max_length=500)
    flavor_text: str | None = Field(default=None, max_length=5_000)

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class TranslationCreate(BaseModel):
    oracle_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9-]+$")
    lang: str = Field(min_length=2, max_length=16, pattern=LANGUAGE_PATTERN)
    name: str | None = Field(default=None, min_length=1, max_length=300)
    oracle_text: str | None = Field(default=None, max_length=10_000)
    type_line: str | None = Field(default=None, max_length=500)
    flavor_text: str | None = Field(default=None, max_length=5_000)

    card_faces: list[TranslationFace] | None = Field(default=None, min_length=2)

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("lang")
    @classmethod
    def canonicalize_language(cls, value: str) -> str:
        normalized = normalize_language(value)
        if normalized == "en":
            raise ValueError("English content belongs to oracle_cards")
        return normalized


class TranslationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    oracle_text: str | None = Field(default=None, max_length=10_000)
    type_line: str | None = Field(default=None, max_length=500)
    flavor_text: str | None = Field(default=None, max_length=5_000)

    card_faces: list[TranslationFace] | None = Field(default=None, min_length=2)

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @model_validator(mode="after")
    def validate_changes(self) -> "TranslationUpdate":
        if not self.model_fields_set:
            raise ValueError("at least one translated field must be provided")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name cannot be null")
        return self


class TranslationListParams(BaseModel):
    oracle_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9-]+$",
    )
    lang: str | None = Field(default=None, min_length=2, max_length=16, pattern=LANGUAGE_PATTERN)
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0, le=100_000)

    @field_validator("lang")
    @classmethod
    def canonicalize_language(cls, value: str | None) -> str | None:
        return normalize_language(value) if value is not None else None


class TranslationResponse(BaseModel):
    id: str
    oracle_id: str
    lang: str
    name: str
    oracle_text: str | None = None
    type_line: str | None = None
    flavor_text: str | None = None
    card_faces: list[TranslationFace] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
