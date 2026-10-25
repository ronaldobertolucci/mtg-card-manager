"""Scryfall legality values; format identifiers are supplied by clients."""

from enum import StrEnum
from typing import Annotated, Any

from pydantic import Field


class Legality(StrEnum):
    LEGAL = "legal"
    RESTRICTED = "restricted"
    NOT_LEGAL = "not_legal"
    BANNED = "banned"


FormatIdentifier = Annotated[
    str, Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]*$")
]


def normalize_formats(value: Any) -> Any:
    if value is None:
        return value
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
        raise ValueError("format must be a comma-separated string or list of identifiers")
    parts = (part.strip().lower() for item in values for part in item.split(","))
    return list(dict.fromkeys(parts))


def normalize_legalities(value: Any) -> Any:
    if value is None:
        return value
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
        raise ValueError("legality must be a comma-separated string")
    parts = (part.strip().lower() for item in values for part in item.split(","))
    return list(dict.fromkeys(parts))


def legality_predicate(formats: list[str], statuses: list[Legality]) -> dict[str, Any]:
    """Missing legality fields do not match any status, including not_legal."""
    return {"$or": [
        {f"legalities.{format_name}": {"$in": statuses}} for format_name in formats
    ]}
