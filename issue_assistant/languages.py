"""The languages of the comments: the catalogs of locales/, and a guess of a text's language."""

from __future__ import annotations

import re
import tomllib
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .paths import TOOL


@dataclass(frozen=True)
class Locale:
    """A language of the comments, from its catalog in locales/."""

    # The language in English, as the prompt names it to the AI.
    name: str
    # How the comments address a reporter, such as "du", which the AI does too.
    address: str
    # How a comment names a day, with {day}, {month} and {year}; empty for ISO 8601.
    date: str
    months: tuple[str, ...]
    # Frequent words of the language, by which the assistant guesses a reporter's.
    stopwords: frozenset[str]
    texts: dict[str, str]


def load_locales(folder: Path) -> dict[str, Locale]:
    """The catalog of each language in the folder, by its code, such as "de" for
    de.toml. A text that a catalog lacks comes from English, so that a comment never
    fails for want of one; the tests hold every catalog of this repository complete.
    """
    catalogs = {
        path.stem: tomllib.loads(path.read_text(encoding="utf-8"))
        for path in sorted(folder.glob("*.toml"))
    }
    english = catalogs["en"]["text"]
    return {
        code: Locale(
            name=catalog["name"],
            address=catalog.get("address", ""),
            date=catalog.get("date", ""),
            months=tuple(catalog.get("months", ())),
            stopwords=frozenset(catalog.get("stopwords", ())),
            texts=english | catalog["text"],
        )
        for code, catalog in catalogs.items()
    }


# The languages of the comments: a catalog for each in locales/, with its texts, how
# it names a day, and its stopwords. A new language is one more file there; the
# prompt, the schema of the answer and the guess of a reporter's language take it.
LOCALES = load_locales(TOOL / "locales")
TEXT = {code: locale.texts for code, locale in LOCALES.items()}
LANGUAGES = tuple(sorted(LOCALES))
# The language of an answer that no catalog has: the AI writes in the reporter's
# language, and the assistant's own words are English, with days in ISO 8601.
OTHER = "other"
ANSWER_LANGUAGES = [*LANGUAGES, OTHER]
# The letters of a word in any language, for the guess of a reporter's language.
LETTERS = re.compile(r"[^\W\d_]+")
# Letters that only one language of the catalogs writes, each of which counts for it
# as one of its stopwords does: Chinese and Japanese leave no space between their
# words, so the guess counts their letters, and Russian and Ukrainian, which share
# most of the Cyrillic alphabet, each have four that the other lacks. Hangul is
# Korean only and kana are Japanese only; a Chinese character counts for Japanese
# in a text with at least one kana for every ten of them, and for Chinese otherwise.
OWN_LETTERS = {
    "ko": re.compile("[\u1100-\u11ff\u3131-\u318e\uac00-\ud7a3]"),
    "ru": re.compile("[ёъыэ]"),
    "uk": re.compile("[ґєії]"),
}
# Hiragana and katakana, without the middle dot that Chinese writes too.
KANA = re.compile(
    "[\u3041-\u3096\u309d-\u309f\u30a1-\u30fa\u30fc-\u30ff\u31f0-\u31ff\uff66-\uff9d]"
)
# The Chinese characters, which Japanese writes as kanji.
HAN = re.compile("[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
# The languages of the catalogs whose word for a number of days takes a third form,
# "days.few", by the rules of the Unicode CLDR for Russian and Ukrainian: "days.one"
# after 1, 21 and 31, "days.few" after 2 to 4 and 22 to 24, and "days.other" after
# every other number, such as 5, 11 or 25. Every other catalog has "days.one" for 1
# and "days.other" for the rest.
FEW = frozenset({"ru", "uk"})
# Words that tell little of what an issue is about, which the search for older
# issues like it leaves out.
# The stopwords of every catalog are among them, except those that are also common
# words of technical English titles, such as the Jest test framework, a network
# card (NIC), "ama" and "bile": they still tell Polish and Turkish, but the search
# keeps them.
TECHNICAL_WORDS = frozenset({"jest", "nic", "ama", "bile"})
COMMON_WORDS = frozenset(
    frozenset[str]().union(*(locale.stopwords for locale in LOCALES.values()))
    - TECHNICAL_WORDS
    | {
        "in", "no",
        "are", "was", "were", "from", "into", "doesn", "don", "isn", "won",
        "cannot", "can't", "anymore", "there", "should", "would", "could",
        "issue", "bug", "error", "problem", "feature", "request", "help",
        "please", "work", "working", "any", "all", "new", "use", "using",
        "sich", "oder", "für", "fuer", "von", "beim", "seit", "mehr", "fehler",
        "kein", "einen", "einem", "einer", "bitte", "geht",
    }
)  # fmt: skip


def plural_form(count: int, language: str) -> str:
    """The form of a word after the number in the language, as FEW describes them:
    "one", "few" or "other"."""
    if language not in FEW:
        return "one" if count == 1 else "other"
    if count % 10 == 1 and count % 100 != 11:
        return "one"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return "few"
    return "other"


def detect_language(text: str) -> str:
    """The language of the catalog whose stopwords and letters of its own, as
    OWN_LETTERS describes them, the text has the most of; English when no other
    language has more, or fewer than two, so that one stray short word or letter in
    a log or in code doesn't decide."""
    lowered = text.lower()
    words = Counter(LETTERS.findall(lowered))
    letters = own_letters(lowered)
    hits = {
        code: sum(words[word] for word in locale.stopwords) + letters.get(code, 0)
        for code, locale in LOCALES.items()
    }
    best = max(LANGUAGES, key=lambda code: (hits[code], code == "en"))
    return best if hits[best] >= 2 and hits[best] > hits["en"] else "en"


def own_letters(text: str) -> dict[str, int]:
    """How many letters of its own the text has of each language that writes some,
    as OWN_LETTERS describes them, with the Chinese characters for Japanese or for
    Chinese."""
    counts = {code: len(found.findall(text)) for code, found in OWN_LETTERS.items()}
    kana = len(KANA.findall(text))
    han = len(HAN.findall(text))
    if kana * 10 >= han:
        return counts | {"ja": kana + han, "zh": 0}
    return counts | {"ja": kana, "zh": han}


def format_date(moment: datetime, language: str) -> str:
    """A day as the comments of the language name it, such as "2. November 2026"; in
    ISO 8601 for a language without a catalog or a catalog without a date."""
    locale = LOCALES.get(language)
    if locale is None or not locale.date:
        return moment.date().isoformat()
    return locale.date.format(
        day=moment.day, month=locale.months[moment.month - 1], year=moment.year
    )


def texts(language: str) -> dict[str, str]:
    """The texts of the comments in the language, from its catalog in locales/; in
    English for OTHER, a language without a catalog, whose answer the AI still
    writes in the reporter's language."""
    return TEXT.get(language, TEXT["en"])


def say(language: str, key: str, **values: object) -> str:
    return texts(language)[key].format(**values)
