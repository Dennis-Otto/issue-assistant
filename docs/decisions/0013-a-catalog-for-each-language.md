# A catalog for each language of the comments, and English around any other

- Status: accepted
- Date: 2026-10-14

## Context

The assistant answered in English or German. Its texts were a dictionary in `issue_assistant.py`, and the two languages were also written into the schema of the answer, the guess of a reporter's language, the dates and the prompt, so a third language meant changing all five, and nothing held the two dictionaries to the same keys: a text added to English alone would have failed for every German reporter. Other assistants, such as Dosu, gh-aw and CodeRabbit, answer in the reporter's language.

## Options

1. Keep the dictionary in the code and add languages to it.
2. A catalog for each language in `locales/<code>.toml`, read with `tomllib` of the standard library ([0003](0003-the-standard-library-alone.md)), with the texts, how the language names a day and its stopwords; everything else takes the list of languages from the catalogs.
3. gettext with `.po` files, the format of most translation platforms.

## Decision

Option 2. TOML is what the settings of the assistant are written in already, a translator edits one plain file, and the standard library reads it. gettext would need compiled `.mo` files, or a reader of `.po` files of our own, for no gain while the catalogs are few. The AI names the language of its answer from the list in the prompt, or `other` for a language without a catalog: it still writes in the reporter's language, and the assistant's own words around it are English, with days in ISO 8601. The marker of an analysis records the language, so the sweep writes in it without the AI.

## Consequences

A new language is one file, which the tests hold to English: the same texts, the same placeholders, twelve months and stopwords that no other catalog has. Stopwords count for one language each, so a word that two languages share, such as `de` or `en`, decides nothing. The French, Spanish, Portuguese, Italian and Dutch catalogs start as machine drafts and need native speakers to review them. The notice *What happens with your issue* that `install` writes stays in English and German until it has more templates.
