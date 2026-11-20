### Bug fixes

- The AI reads a few more link reference definitions and footnotes as GitHub shows them: as nothing. That covers a definition over a row of `=` or `-` without the `>` of its quote or the indentation of its list item, one after indented code that starts with `>` or a list mark, one after HTML in a quote or a list item, and a footnote whose only reference stands in code in a quote or a list item or where HTML could hold it. A row of `=` or `-` that underlines a heading still ends a definition, as GitHub shows the heading.
