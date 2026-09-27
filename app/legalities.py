"""Versioned Scryfall format identifiers and legality values."""

from enum import StrEnum


class Legality(StrEnum):
    LEGAL = "legal"
    RESTRICTED = "restricted"
    NOT_LEGAL = "not_legal"
    BANNED = "banned"


class GameFormat(StrEnum):
    STANDARD = "standard"
    FUTURE = "future"
    HISTORIC = "historic"
    TIMELESS = "timeless"
    GLADIATOR = "gladiator"
    PIONEER = "pioneer"
    EXPLORER = "explorer"
    MODERN = "modern"
    LEGACY = "legacy"
    PAUPER = "pauper"
    VINTAGE = "vintage"
    PENNY = "penny"
    COMMANDER = "commander"
    OATHBREAKER = "oathbreaker"
    STANDARDBRAWL = "standardbrawl"
    BRAWL = "brawl"
    ALCHEMY = "alchemy"
    PAUPERCOMMANDER = "paupercommander"
    DUEL = "duel"
    OLDSCHOOL = "oldschool"
    PREMODERN = "premodern"
    PREDH = "predh"
