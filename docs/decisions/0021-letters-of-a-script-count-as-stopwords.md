# Letters of a script of their own count as stopwords, and a third form of a number of days

- Status: accepted
- Date: 2026-10-25

## Context

[Decision 0013](0013-a-catalog-for-each-language.md) guesses a reporter's language before the first analysis from the stopwords of each catalog, and names a number of days in two forms, one and other. Chinese and Japanese leave no space between their words, so no word of them can be counted, and Russian and Ukrainian name a number of days in three forms: 1 день, 2 дня, 5 дней.

## Options

1. Stopwords only. A Chinese or Japanese issue would get an English reminder.
2. A dependency that identifies languages, such as a model of character n-grams. It adds a package, its data and its updates to every run of the sweep, for 14 catalogs, and its guess can't be reasoned about from the catalogs.
3. A letter that only one language of the catalogs writes counts for it as a stopword does, in the same vote.

## Decision

Option 3. Hangul counts for Korean. Kana count for Japanese, together with the Chinese characters around them, as long as there is at least one kana per ten Chinese characters, since Japanese writes kana between its kanji and Chinese none. Chinese characters without enough kana count for Chinese. ё, ъ, ы and э count for Russian, ґ, є, і and ї for Ukrainian. Each character is one vote beside the stopwords of the Latin-script catalogs, so a Chinese issue with an English log stays Chinese and an English issue that quotes a Chinese label stays English. The scripts are `OWN_LETTERS`, `KANA` and `HAN` in `issue_assistant/languages.py`, and property tests check that no letter belongs to two of them.

Russian and Ukrainian get `days.few` beside `days.one` and `days.other`, chosen by the plural rules of the Unicode CLDR in `plural_form`.

## Consequences

A new language is no longer always one file: one without spaces between its words needs its letters in the code, and one with more plural forms needs its rule in `plural_form`. The catalogs of Chinese and Japanese have no stopwords. A stopword of a catalog that is also a word of technical English, such as the Polish `jest` and `nic`, still counts for its language but stays in the search for older issues.
