"""Hooks of MkDocs for the website of the documentation (mkdocs.yml).

The README shows its pictures with their addresses on raw.githubusercontent.com, so
that they appear wherever it is read, and the guide of the website includes parts of
it. The website shows its own copies instead, those of the commit it is built from,
so that a pull request already shows its new pictures, and lets the switch between
light and dark choose them:

- The address of a file in docs/ on the main branch becomes the path of that file
  on the website, relative to the page.
- A <picture> with a dark variant for prefers-color-scheme becomes one picture for
  each theme of the Material theme, #only-light and #only-dark, so that the switch in
  the header chooses between them, not only the setting of the system. Each keeps the
  <source>s of its theme, such as the portrait animation for phones and the still
  picture for readers who asked for less motion.
- Each of these pictures loads lazily, so the browser never fetches the copy of the
  theme that is hidden. Its width and height, from the header of its file, keep the
  space for it while it loads. On the first visit, the theme of the system applies
  as soon as the header is there, not only when the script of the theme has loaded,
  so that the browser doesn't load the light copies in the meantime.
- A Mermaid diagram scrolls sideways on a small screen instead of shrinking its text
  to a few pixels: docs/stylesheets/diagrams.css gives it its least width.

The pictures of the README are part of the included Markdown only after the snippets
are resolved, so the hook works on the HTML of each page, before the plugin glightbox
makes each picture open enlarged with a click.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

try:
    from mkdocs.plugins import event_priority
except ImportError:  # The tests, which run without MkDocs.

    def event_priority(priority: float) -> Callable[[Any], Any]:
        return lambda function: function


# The files of docs/ on the main branch, as the README links them.
DOCS = "https://raw.githubusercontent.com/Dennis-Otto/issue-assistant/main/docs/"
ADDRESS = re.compile(rf'\b(?P<attribute>src|srcset)="{re.escape(DOCS)}(?P<path>[^"]+)"')
# A picture: a <source> for each variant, the dark ones among them, and the <img> of
# the light theme, which browsers show when no condition of a <source> holds.
PICTURE = re.compile(
    r"<picture>(?P<sources>(?:\s*<source [^>]*>)*)\s*<img (?P<image>[^>]*)>\s*</picture>"
)
SOURCE = re.compile(r'<source media="(?P<media>[^"]+)" srcset="(?P<srcset>[^"]+)">')
ATTRIBUTE = re.compile(r'(?P<name>[\w-]+)="(?P<value>[^"]*)"')
DARK = "(prefers-color-scheme: dark)"
THEMES = ("light", "dark")
# Keeps a picture with variants out of the lightbox of the plugin glightbox, whose
# link around the <img> would cut it off from its <source>s.
NO_LIGHTBOX = "off-glb"
DIAGRAM = re.compile(r'<pre class="mermaid">.*?</pre>', re.S)
# What the script of the Material theme does on the first visit, when no theme is
# stored: it takes the first one of the switch whose media query holds. Without it, a
# page starts light until the script has loaded.
SCHEME = (
    '<script>try{if(!__md_get("__palette"))'
    'for(var input of document.querySelectorAll("input[data-md-color-media]"))'
    'if(matchMedia(input.getAttribute("data-md-color-media")).matches){'
    'for(var key of["media","scheme","primary","accent"])'
    'document.body.setAttribute("data-md-color-"+key,'
    'input.getAttribute("data-md-color-"+key));break}}catch(error){}</script>'
)


def size(path: Path) -> tuple[int, int]:
    """The width and height of a PNG or WebP picture, from the header of its file."""
    with path.open("rb") as file:
        header = file.read(30)
    if header[:8] == b"\x89PNG\r\n\x1a\n" and header[12:16] == b"IHDR":
        return (
            int.from_bytes(header[16:20], "big"),
            int.from_bytes(header[20:24], "big"),
        )
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        chunk = header[12:16]
        if chunk == b"VP8X":
            # An animation, or a picture with more than its pixels: the size of the
            # canvas less one, in three bytes each.
            return (
                int.from_bytes(header[24:27], "little") + 1,
                int.from_bytes(header[27:30], "little") + 1,
            )
        if chunk == b"VP8L":
            # Lossless: after its signature, 14 bits each of the size less one.
            bits = int.from_bytes(header[21:25], "little")
            return (bits & 0x3FFF) + 1, (bits >> 14 & 0x3FFF) + 1
        if chunk == b"VP8 ":
            # Lossy: after the frame tag and the start code, 14 bits each.
            return (
                int.from_bytes(header[26:28], "little") & 0x3FFF,
                int.from_bytes(header[28:30], "little") & 0x3FFF,
            )
    raise ValueError(f"{path} is neither a PNG nor a WebP picture")


def tag(name: str, attributes: dict[str, str]) -> str:
    pairs = " ".join(f'{key}="{value}"' for key, value in attributes.items())
    return f"<{name} {pairs}>"


def localized(html: str, url: str, docs: Path) -> str:
    """The HTML of the page at url, with the pictures and diagrams of the website.

    docs is the folder of the pages, where the files of the pictures are.
    """
    folder = posixpath.dirname(url) or "."

    def local(match: re.Match[str]) -> str:
        path = posixpath.relpath(match["path"], folder)
        return f'{match["attribute"]}="{path}"'

    def dimensions(address: str) -> dict[str, str]:
        """The width and height of the picture at an address relative to the page."""
        if "://" in address or address.startswith(("/", "data:")):
            return {}
        width, height = size(docs / posixpath.normpath(posixpath.join(folder, address)))
        return {"width": str(width), "height": str(height)}

    def themed(match: re.Match[str]) -> str:
        image = dict(ATTRIBUTE.findall(match["image"]))
        fallbacks = {"light": image["src"]}
        variants: dict[str, list[dict[str, str]]] = {theme: [] for theme in THEMES}
        for source in SOURCE.finditer(match["sources"]):
            terms = source["media"].split(" and ")
            theme = "dark" if DARK in terms else "light"
            media = " and ".join(term for term in terms if term != DARK)
            if not media:
                fallbacks[theme] = source["srcset"]
                continue
            variants[theme].append(
                {"media": media, "srcset": source["srcset"]}
                | dimensions(source["srcset"])
            )
        if "dark" not in fallbacks:
            return match[0]
        if match["sources"].count("<source") != len(SOURCE.findall(match["sources"])):
            # A <source> written in another way would be lost in both copies.
            raise ValueError(f"unsupported <source> in a picture: {match[0]}")
        copies = []
        for theme in THEMES:
            attributes = image | {"src": f"{fallbacks[theme]}#only-{theme}"}
            attributes.setdefault("loading", "lazy")
            attributes.setdefault("decoding", "async")
            if "width" not in attributes and "height" not in attributes:
                attributes |= dimensions(fallbacks[theme])
            if not variants[theme]:
                copies.append(tag("img", attributes))
                continue
            attributes["class"] = " ".join(
                [*attributes.get("class", "").split(), NO_LIGHTBOX]
            )
            sources = "".join(tag("source", source) for source in variants[theme])
            copies.append(f"<picture>{sources}{tag('img', attributes)}</picture>")
        return "".join(copies)

    def scrolling(match: re.Match[str]) -> str:
        # Focusable, as the theme makes its code blocks, so that keys scroll it too.
        return f'<div class="diagram" tabindex="0">{match[0]}</div>'

    html = PICTURE.sub(themed, ADDRESS.sub(local, html))
    return DIAGRAM.sub(scrolling, html)


def early_theme(output: str) -> str:
    """The page with the theme of the system right after the header, where the switch
    between light and dark is."""
    return output.replace("</header>", f"</header>{SCHEME}", 1)


@event_priority(50)
def on_page_content(html: str, page: Any, config: Any, files: Any) -> str:
    return localized(html, page.file.url, Path(config["docs_dir"]))


def on_post_page(output: str, page: Any, config: Any) -> str:
    return early_theme(output)
