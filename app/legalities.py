"""Scryfall legality values; format identifiers are supplied by clients."""

from enum import StrEnum


class Legality(StrEnum):
    LEGAL = "legal"
    RESTRICTED = "restricted"
    NOT_LEGAL = "not_legal"
    BANNED = "banned"
