### Bug fixes

- A report in German or another language gets its comments in its own language again when it holds a table with a cell too many, a diagram with comments or a drawing: the notes that take the place of such parts no longer count as English words. The language, the search for older issues, the area, the box of the opt-out and the denial of a fix read a space where the AI reads a note, so the AI still learns that something was there.
- Finding the language of a long issue with many such parts takes about half the time it took.
- A drawing or math between `doesn't` and `close #12` in the description of a pull request no longer splits the sentence: the issue stays open. An answer of the area field with a drawing or math beside it chooses its label again.
