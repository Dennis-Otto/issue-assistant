### Bug fixes

- The AI no longer reads the title of a link or an image, the `"…"` after its address, which GitHub shows only while the pointer rests on the link. `[log](https://example.com/a "text")` reaches it as `[log](https://example.com/a)`: the link, its text and the description of an image stay. The search for older issues, the language of the comments, the area, and whether a pull request denies that it fixes an issue no longer count the words of a title either, and no file named only in a title is downloaded as an attachment. The log counts the titles that went.
