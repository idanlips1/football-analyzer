"""Football-specific keyword lists with associated excitement weights."""
# TODO: rewview and optimize this further (add words etc)

from __future__ import annotations

from typing import NamedTuple


class KeywordEntry(NamedTuple):
    keyword: str  # lowercase, may contain spaces (matched by substring)
    weight: float  # 0.0–1.0


HIGH: list[KeywordEntry] = [
    KeywordEntry("goal", 1.0),
    KeywordEntry("what a goal", 1.0),
    KeywordEntry("incredible", 0.95),
    KeywordEntry("unbelievable", 0.95),
    KeywordEntry("he scores", 0.95),
    KeywordEntry("she scores", 0.95),
    KeywordEntry("penalty", 0.9),
    KeywordEntry("red card", 0.9),
    KeywordEntry("sent off", 0.9),
    KeywordEntry("off the post", 0.9),
    KeywordEntry("off the bar", 0.9),
    KeywordEntry("what a save", 0.9),
    KeywordEntry("var", 0.85),
    KeywordEntry("video review", 0.85),
    KeywordEntry("handball", 0.85),
]

MEDIUM: list[KeywordEntry] = [
    KeywordEntry("shot", 0.6),
    KeywordEntry("cross", 0.55),
    KeywordEntry("corner", 0.55),
    KeywordEntry("free kick", 0.6),
    KeywordEntry("header", 0.6),
    KeywordEntry("volley", 0.65),
    KeywordEntry("counter-attack", 0.75),
    KeywordEntry("counter", 0.7),
    KeywordEntry("one on one", 0.75),
    KeywordEntry("saves it", 0.7),
    KeywordEntry("yellow card", 0.65),
    KeywordEntry("foul", 0.5),
    KeywordEntry("tackle", 0.5),
    KeywordEntry("chance", 0.6),
    KeywordEntry("dangerous", 0.55),
    KeywordEntry("offside", 0.5),
]

LOW: list[KeywordEntry] = [
    KeywordEntry("attack", 0.3),
    KeywordEntry("through ball", 0.35),
    KeywordEntry("substitution", 0.25),
    KeywordEntry("injury", 0.3),
    KeywordEntry("pressure", 0.2),
    KeywordEntry("long ball", 0.2),
    KeywordEntry("goalkeeper", 0.15),
    KeywordEntry("defender", 0.15),
    KeywordEntry("pass", 0.1),
    KeywordEntry("midfield", 0.1),
    KeywordEntry("possession", 0.1),
]

KEYWORDS: list[KeywordEntry] = HIGH + MEDIUM + LOW
KEYWORD_WEIGHTS: dict[str, float] = {e.keyword: e.weight for e in KEYWORDS}
