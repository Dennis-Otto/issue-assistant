---
description: The fourteen languages of the issue assistant, how it chooses one, the summary for the maintainers, and how to add a language.
---

# Languages

[← All features](README.md)

The assistant answers every reporter in their language, with fourteen catalogs of its own words and a folded summary for maintainers who don't read that language.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/diagrams/how-the-assistant-chooses-the-language-of-a-comment.dark.svg">
  <img src="../images/diagrams/how-the-assistant-chooses-the-language-of-a-comment.svg" alt="How the assistant chooses the language of a comment: The AI answers in the reporter's language and names its code. A code of a catalog in locales/, such as fr, gives the assistant's own words and days from that catalog; other, for a language without a catalog, gives English words around the AI's text and days in ISO 8601. The hidden marker of the comment keeps the code. A comment without AI, such as a reminder, a closing or the note of a release, takes the code from the marker of the last analysis or follow-up; without one, from the reporter's own words, the language whose stopwords and letters of its own, such as Hangul or kana, they use most, English on a tie.">
</picture>

The assistant answers every reporter in their language. Its own words, from the thanks and the headings to the footer, the reminders and the notes of a release, come from a catalog for each language in [`locales/`](https://github.com/Dennis-Otto/issue-assistant/tree/main/locales), which has fourteen:

| Code | Language | Addresses a reporter with | A day |
| --- | --- | --- | --- |
| `de` | German | du | 2. November 2026 |
| `en` | English | you | November 2, 2026 |
| `es` | Spanish | tú | 2 de noviembre de 2026 |
| `fr` | French | vous | 2 novembre 2026 |
| `it` | Italian | tu | 2 novembre 2026 |
| `ja` | Japanese | さん | 2026年11月2日 |
| `ko` | Korean | 님 | 2026년 11월 2일 |
| `nl` | Dutch | je | 2 november 2026 |
| `pl` | Polish | ty | 2 listopada 2026 |
| `pt` | Portuguese | você | 2 de novembro de 2026 |
| `ru` | Russian | вы | 2 ноября 2026 г. |
| `tr` | Turkish | siz | 2 Kasım 2026 |
| `uk` | Ukrainian | ви | 2 листопада 2026 р. |
| `zh` | Simplified Chinese | 您 | 2026年11月2日 |

- **The AI names the language of its answer.** The prompt lists the codes of the catalogs, and the schema of the answer takes them and `other`. For a language without a catalog, such as Vietnamese, the AI answers with `other` and writes in Vietnamese, and the assistant's own words around its text are English, with days in ISO 8601, such as 2026-11-02.
- **The comments without AI keep it.** The hidden marker of an analysis or a follow-up records the language, such as `lang=fr`, and the reminder, the closing, the closing of a duplicate and the comment of a release read it there. Before the first analysis, such as on an issue that a maintainer marks as a duplicate, the reporter's own words decide: the language of the catalog whose stopwords they use most, English on a tie or when no other language has at least two. A letter that only one language of the catalogs writes counts for it as a stopword does: Hangul for Korean, kana for Japanese, together with the Chinese characters beside them, Chinese characters without kana for Chinese, and ё, ъ, ы and э for Russian, ґ, є, і and ї for Ukrainian. Chinese and Japanese leave no space between their words, so their catalogs have no stopwords, and a Chinese issue gets a Chinese reminder, also with an English log in it.
- **Each catalog is complete.** The tests hold every catalog to English: the same texts, the same placeholders in braces, twelve months and stopwords that no other catalog has. A text that a catalog still lacks comes from English, so that a comment never fails for want of one. The English and German texts are written by hand; the French, Spanish, Portuguese, Italian, Dutch, Polish, Turkish, Russian, Ukrainian, Chinese, Japanese and Korean ones are machine drafts, which native speakers are welcome to improve. Russian and Ukrainian name a number of days in three forms, as in 1 день, 2 дня and 5 дней, by the rules of the Unicode CLDR: their catalogs have `days.few` beside `days.one` and `days.other`. The section *What happens with your issue* that `install` writes comes in English and German.

**A summary for the maintainers.** The maintainers may not read the reporter's language. `[maintainers] language` of `config.toml` names theirs, as the code of a catalog, `en` if left out. When the AI answers in another language, in a first analysis, a follow-up or a first answer in Discussions, it also writes a summary for them in theirs: the key facts of the issue in a faithful translation, such as the versions, the error and what the reporter tried, and the gist of its answer, in at most 700 characters. So a maintainer who reads only English can triage an issue in Japanese and check what the AI claims, without a translator and without a second comment, which a translating action would post.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/diagrams/when-a-comment-folds-a-summary-for-the-maintainers.dark.svg">
  <img src="../images/diagrams/when-a-comment-folds-a-summary-for-the-maintainers.svg" alt="When a comment folds a summary for the maintainers: The AI answers in the reporter's language and also writes a summary for the maintainers in the language of [maintainers] language, English by default. When the answer is in the maintainers' language, the apply job leaves the summary out and says so in the log; so does an empty summary, and the comment on a possible vulnerability never has one. Otherwise the comment ends with the summary, cleaned as every text of the AI, folded under a line from the maintainers' catalog, such as For the maintainers, in English, and its hidden marker records maint=en.">
</picture>

- **It stays out of the reporter's way.** The comment folds the summary below its text, under a line from the maintainers' catalog, such as *For the maintainers, in English*, so the reporter sees one short, labelled line. The summary is cleaned as every text of the AI, and the hidden marker of the comment records its language, such as `maint=en`.
- **Only where it helps.** In the maintainers' own language a summary would only repeat the answer: the apply job leaves it out and says so in the log, as it leaves out an empty one. The comment on a possible vulnerability has none, because it repeats nothing of the issue. The page *What reporters see* of the website shows one, for maintainers who read English and for those who read German.
- **A code without a catalog,** such as `sv` or `pt-BR`, makes `check` fail with the code that it probably means, such as `pt`; until then the other commands write the summary in English.

**To add a language,** copy `locales/en.toml` to `locales/<code>.toml`, named after the code of the language in ISO 639-1, such as `sv.toml`. Translate each text and keep its placeholders, such as `{author}`, and set the name of the language in English, how it addresses a reporter, how it writes a day, its twelve months and at least twenty stopwords: frequent words of the language that no other catalog has. A language that leaves no space between its words, such as Thai, has no stopwords; its letters go into `OWN_LETTERS` of `issue_assistant/languages.py` instead. A language whose word for a number of days has more forms than one for 1 and one for the rest, such as Czech, needs its rule in `plural_form` too. Nothing else changes: the prompt, the schema of the answer and the guess of a reporter's language take the new file. `bash scripts/check.sh` runs the tests that check it.
