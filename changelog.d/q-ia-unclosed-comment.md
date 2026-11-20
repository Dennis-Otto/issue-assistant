### Bug fixes

- The AI reads what follows an HTML comment that never ends as GitHub shows it in a few more places: as nothing. That covers a comment at the start of a quote or a list item, of any mark and at any depth, one anywhere in a row of HTML such as `<div>` or `<p>`, also after the end of the quote or the list that holds it, and a quoted value of a tag in such a row that doesn't end on its row. A `<!--` in a paragraph still reaches the AI as text, as GitHub shows it.
