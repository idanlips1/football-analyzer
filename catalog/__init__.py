"""Curated match catalog — users pick from these; videos live in blob storage."""

from catalog.loader import CatalogMatch, get_match, list_matches, load_catalog

__all__ = [
    "CatalogMatch",
    "get_match",
    "list_matches",
    "load_catalog",
]
