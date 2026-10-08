"""Hooks of MkDocs for the website of the documentation (mkdocs.yml).

The README shows its pictures with their addresses on raw.githubusercontent.com, so
that they appear wherever it is read, and the guide of the website includes parts of
it. The website shows its own copies instead, those of the commit it is built from,
so that a pull request already shows its new pictures, and lets the switch between
light and dark choose them:

- The address of a file in docs/ on the main branch becomes the path of that file
  on the website, relative to the page.
- A <picture> with a dark variant for prefers-color-scheme becomes the two pictures
  of the Material theme, #only-light and #only-dark, so that the switch in the
  header chooses between them, not only the setting of the system.

The pictures of the README are part of the included Markdown only after the snippets
are resolved, so the hook works on the HTML of each page, before the plugin glightbox
makes each picture open enlarged with a click.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Callable
from typing import Any

try:
    from mkdocs.plugins import event_priority
except ImportError:  # The tests, which run without MkDocs.

    def event_priority(priority: float) -> Callable[[Any], Any]:
        return lambda function: function


# The files of docs/ on the main branch, as the README links them.
DOCS = "https://raw.githubusercontent.com/Dennis-Otto/issue-assistant/main/docs/"
ADDRESS = re.compile(rf'\b(?P<attribute>src|srcset)="{re.escape(DOCS)}(?P<path>[^"]+)"')
PICTURE = re.compile(
    r"<picture>\s*"
    r'<source media="\(prefers-color-scheme: dark\)" srcset="(?P<dark>[^"]+)">\s*'
    r'<img (?P<before>[^>]*?)src="(?P<light>[^"]+)"(?P<after>[^>]*)>\s*'
    r"</picture>"
)


def localized(html: str, url: str) -> str:
    """The HTML of the page at url, with the pictures of the website."""
    folder = posixpath.dirname(url) or "."

    def local(match: re.Match[str]) -> str:
        path = posixpath.relpath(match["path"], folder)
        return f'{match["attribute"]}="{path}"'

    def themed(match: re.Match[str]) -> str:
        before, after = match["before"], match["after"]
        return (
            f'<img {before}src="{match["light"]}#only-light"{after}>'
            f'<img {before}src="{match["dark"]}#only-dark"{after}>'
        )

    return PICTURE.sub(themed, ADDRESS.sub(local, html))


@event_priority(50)
def on_page_content(html: str, page: Any, config: Any, files: Any) -> str:
    return localized(html, page.file.url)
