"""Hooks of MkDocs for the website of the documentation (mkdocs.yml).

The pages of the website include the parts of the README, so that nothing is written
twice. The hooks add what the Markdown of the pages can't say itself.

The pictures. The README shows its pictures with their addresses on
raw.githubusercontent.com, so that they appear wherever it is read. The website shows
its own copies instead, those of the commit it is built from, so that a pull request
already shows its new pictures, and lets the switch between light and dark choose
them:

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
- The pictures of a diagram, which scripts/diagrams.sh draws from its source in
  docs/diagrams/, stay as they are: the hook of the blueprint,
  .github/mkdocs_blueprint.py, runs after this one and draws the diagram live from
  its source, in the colours of the theme, whatever path the page gives them.
- A diagram scrolls sideways on a small screen instead of shrinking its text to a few
  pixels: docs/stylesheets/diagrams.css gives it its least width. The hook wraps it
  on the finished page, once the hook of the blueprint has drawn it.

The pictures of the README are part of the included Markdown only after the snippets
are resolved, so the hook works on the HTML of each page, before the plugin glightbox
makes each picture open enlarged with a click.

What the code knows:

- The comments on the page What reporters see, docs/comments.md: every comment that
  the assistant posts, in English and in German, made by the functions and with the
  texts of issue_assistant.py of the commit that the website is built from, for the
  demo project of the pictures. No file holds them.
- The tables of the inputs and outputs of the action, from action.yml, on a page
  with the line <!-- action.yml: inputs --> or <!-- action.yml: outputs -->.

How each page presents itself to search engines and where a link to it is shared:

- A description of its own: that of its front matter, or else its first paragraph.
- The title of the home page from its front matter, without the name of the website
  a second time.

Links from before: the guide was one page, and a link to one of its anchors, such as
guide.html#settings, leads to the page that has the anchor now.
"""

from __future__ import annotations

import importlib.util
import json
import posixpath
import re
import sys
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any, NamedTuple

import yaml

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
# The <img> of the pictures of a diagram, as scripts/diagrams.sh --extract writes it:
# docs/images/diagrams/NAME.svg, from the folder of the page.
DRAWN = re.compile(r'\bsrc="(?:[^"]*/)?images/diagrams/[\w.-]+\.svg"')
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
    """The HTML of the page at url, with the pictures of the website.

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
        if DRAWN.search(match["image"]):
            # A diagram, which the hook of the blueprint draws live.
            return match[0]
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

    return PICTURE.sub(themed, ADDRESS.sub(local, html))


def scrolling(output: str) -> str:
    """The page with each diagram in a box that scrolls sideways on a small screen."""
    # Focusable, as the theme makes its code blocks, so that keys scroll it too.
    return DIAGRAM.sub(
        lambda match: f'<div class="diagram" tabindex="0">{match[0]}</div>', output
    )


def early_theme(output: str) -> str:
    """The page with the theme of the system right after the header, where the switch
    between light and dark is."""
    return output.replace("</header>", f"</header>{SCHEME}", 1)


# The descriptions of the pages, for search engines and the preview card of a shared
# link: what a page says in its front matter, or else its first paragraph, cut after
# the last sentence that fits.
DESCRIPTION = 160
DANGLING = {"a", "an", "and", "as", "for", "in", "of", "or", "the", "to", "with"}
LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")


def lead(markdown: str) -> str:
    """The first paragraph of prose of a page, as plain text; empty without one.

    Headings, lists, tables, quotes, pictures, includes and a line of links, such as
    the way back to the README above a page, are no prose.
    """
    for block in re.split(r"\n[ \t]*\n", markdown):
        block = block.strip()
        if (
            not block
            or block[0] in "#[<!|-*>`=:"
            or block.startswith("--8<--")
            or re.match(r"\d+\. ", block)
        ):
            continue
        return " ".join(re.sub(r"\*|`", "", LINK.sub(r"\1", block)).split())
    return ""


def shortened(text: str, limit: int = DESCRIPTION) -> str:
    """The sentences at the start of the text that fit into limit characters, or,
    when they say too little, its words up to the limit with an ellipsis."""
    kept = ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        longer = f"{kept} {sentence}".strip()
        if len(longer) > limit:
            break
        kept = longer
    if len(kept) == len(text.strip()) or len(kept) >= limit // 2:
        return kept
    words = text[: limit - 1].split()[:-1]
    # Not after a word that leads to the next one, such as "and".
    while words and words[-1].lower() in DANGLING:
        words.pop()
    if not words:
        # A first word longer than the limit, such as an address, or only such words
        # as "and": the characters up to the limit.
        return text[: limit - 1].rstrip() + "…"
    return " ".join(words).rstrip(",;:") + "…"


def described(markdown: str, meta: dict[str, Any]) -> None:
    """Give the page the description of its first paragraph, unless it has one."""
    if not meta.get("description"):
        found = lead(markdown)
        if found:
            meta["description"] = shortened(found)


def homepage_title(output: str, title: str, site: str) -> str:
    """The home page with its own title alone: the theme adds the name of the website,
    which the title of the home page names already."""
    return output.replace(
        f"<title>{title} - {site}</title>", f"<title>{title}</title>", 1
    )


# The inputs and outputs of the action, as tables from action.yml.
ROOT = Path(__file__).resolve().parent.parent
ACTION_TABLE = re.compile(
    r"^<!-- action\.yml: (?P<part>inputs|outputs) -->$", re.MULTILINE
)


def cell(text: object) -> str:
    """Text for a cell of a Markdown table: on one line, its bars no columns."""
    return " ".join(str(text).split()).replace("|", "\\|")


def action_tables(markdown: str, action: dict[str, Any]) -> str:
    """The page with a table in place of each line that asks for one."""

    def table(match: re.Match[str]) -> str:
        if match["part"] == "inputs":
            rows = [
                "| Input | Required | Default | What it is |",
                "| --- | --- | --- | --- |",
            ]
            for name, spec in action["inputs"].items():
                default = str(spec.get("default", ""))
                rows.append(
                    f"| `{name}` | {'yes' if spec.get('required') else 'no'} | "
                    f"{f'`{default}`' if default else 'none'} | "
                    f"{cell(spec['description'])} |"
                )
        else:
            rows = ["| Output | What it is |", "| --- | --- |"]
            for name, spec in action["outputs"].items():
                rows.append(f"| `{name}` | {cell(spec['description'])} |")
        return "\n".join(rows)

    return ACTION_TABLE.sub(table, markdown)


def action_tables_of(markdown: str) -> str:
    """The page with the tables of action.yml, read only for a page that asks."""
    if not ACTION_TABLE.search(markdown):
        return markdown
    action = yaml.safe_load((ROOT / "action.yml").read_text("utf-8"))
    return action_tables(markdown, action)


# What reporters see: every comment of the assistant, made by its own functions and
# with its own texts, for the demo project of the pictures in docs/images.
LANGUAGES = {"en": "English", "de": "Deutsch"}
DEMO = "demo-org/plant-monitor"
# The related repository of the demo project, in [related] of its config.toml.
FIRMWARE = "demo-org/plant-hub-firmware"
REPORTER = "alex-demo"
MAINTAINER = "sam-demo"
# The days after the closing that the example of a locked issue names; the default
# of lock_closed_after_days, 0, locks none.
LOCK_EXAMPLE = 90
DEMO_SHA = "4f1c2b7d9e0a3c5b6d8e1f2a4b6c8d0e2f4a6b8c"
CHART = "src/plant_monitor/chart.py"
TROUBLESHOOTING = "docs/troubleshooting.md"
# The files of the demo project that the comments link to, which git tracks there:
# its notice to reporters, which the footers link, too.
DEMO_FILES = {
    CHART: "".join(f"# line {number}\n" for number in range(1, 61)),
    TROUBLESHOOTING: "# Troubleshooting\n\n## The chart stays empty\n\nCollect the "
    "log of the hub with `log_level: debug`.\n",
    "SUPPORT.md": "# Support\n\n## What happens with your issue\n\nThe issue "
    "assistant, which uses Claude, an AI by Anthropic, takes a first look.\n",
}
# What the AI writes in the examples; everything else is the assistant's own text.
WORDS: dict[str, dict[str, Any]] = {
    "en": {
        "summary": "After the sensor reconnects, the moisture chart stays empty while "
        "the current value keeps updating.",
        "analysis": "The chart subscribes to the history stream once. After a "
        "reconnect, `ChartFeed.start()` returns early because `subscribed` is still "
        "true, so the chart never gets the new stream. The current value has a "
        "subscription of its own and recovers.",
        "next_steps": [
            "Reload the dashboard after a reconnect; the chart fills again.",
            "Until a fix ships, set `chart.refresh` to `30s`.",
        ],
        "missing": [
            "The firmware version of the sensor.",
            "The log of the hub from the reconnect, with `log_level: debug`.",
        ],
        "related": [
            "The same symptom after a restart of the hub, fixed in 1.2.0.",
            "How the chart loads the history of a sensor.",
        ],
        "references": [
            "Returns early while `subscribed` is still set.",
            "How to collect the log.",
        ],
        "duplicate": [
            "A button that exports the moisture history of a plant as a CSV file.",
            "The dashboard has no export yet. `ChartFeed.history()` returns the points "
            "that the chart shows, which an export could write.",
            "Asks for the same export of the history as a CSV file.",
        ],
        "elsewhere": [
            "Every night at 3 a.m., the sensors drop off the network for a few minutes, "
            "and the chart shows a gap.",
            "The dashboard only shows what the hub reports, and the gaps start when the "
            "hub's firmware scans its radio channels each night. The firmware has its "
            "own repository, where a report describes the same nightly drop.",
            "The same nightly drop, traced to the channel scan of the firmware.",
        ],
        "secret": [
            "How to connect the hub to a second Wi-Fi network as a fallback.",
            "The hub keeps one network in `wifi.ssid`. A second one goes into "
            "`wifi.fallback`, which the hub tries when the first one is out of reach.",
        ],
        "personal": [
            "The installation guide still names version 1.2 of the hub.",
            "The current release is 1.4, and its first step changed: the hub now asks "
            "for the network on its own screen.",
        ],
        "reply": "The log shows `already subscribed, skipping` right after the "
        "reconnect, which confirms the cause above. A fix would end the subscription "
        "in `on_disconnect()`, so that `start()` subscribes again.",
        "still_missing": ["The version of the dashboard app, under Settings → About."],
        "answer": "Only the chart. `ChartFeed.start()` subscribes once and returns "
        "early while `subscribed` is set, and nothing clears it after a reconnect. The "
        "current value subscribes in `ValueFeed`, which starts anew each time, so it "
        "recovers. A fix would clear `subscribed` in `on_disconnect()`; no test covers "
        "a reconnect yet.",
    },
    "de": {
        "summary": "Nachdem sich der Sensor wieder verbunden hat, bleibt das "
        "Feuchtigkeitsdiagramm leer, während der aktuelle Wert weiter aktualisiert "
        "wird.",
        "analysis": "Das Diagramm abonniert den Verlaufsstream nur einmal. Nach dem "
        "Wiederverbinden kehrt `ChartFeed.start()` sofort zurück, weil `subscribed` "
        "noch gesetzt ist, und das Diagramm bekommt den neuen Stream nie. Der aktuelle "
        "Wert hat ein eigenes Abonnement und erholt sich.",
        "next_steps": [
            "Lade das Dashboard nach dem Wiederverbinden neu; dann füllt sich das "
            "Diagramm wieder.",
            "Bis ein Fix erscheint, setze `chart.refresh` auf `30s`.",
        ],
        "missing": [
            "Die Firmware-Version des Sensors.",
            "Das Log des Hubs vom Wiederverbinden, mit `log_level: debug`.",
        ],
        "related": [
            "Dasselbe Symptom nach einem Neustart des Hubs, behoben in 1.2.0.",
            "Wie das Diagramm den Verlauf eines Sensors lädt.",
        ],
        "references": [
            "Kehrt früh zurück, solange `subscribed` gesetzt ist.",
            "Wie du das Log sammelst.",
        ],
        "duplicate": [
            "Eine Schaltfläche, die den Feuchtigkeitsverlauf einer Pflanze als "
            "CSV-Datei exportiert.",
            "Das Dashboard hat noch keinen Export. `ChartFeed.history()` liefert die "
            "Punkte des Diagramms, die ein Export schreiben könnte.",
            "Wünscht denselben Export des Verlaufs als CSV-Datei.",
        ],
        "elsewhere": [
            "Jede Nacht um 3 Uhr verlieren die Sensoren für ein paar Minuten das Netz, "
            "und das Diagramm zeigt eine Lücke.",
            "Das Dashboard zeigt nur, was der Hub meldet, und die Lücken beginnen, wenn "
            "die Firmware des Hubs jede Nacht ihre Funkkanäle durchsucht. Die Firmware "
            "hat ein eigenes Repository, in dem ein Bericht denselben nächtlichen "
            "Abbruch beschreibt.",
            "Derselbe nächtliche Abbruch, zurückgeführt auf die Kanalsuche der Firmware.",
        ],
        "secret": [
            "Wie sich der Hub mit einem zweiten WLAN als Ausweichnetz verbindet.",
            "Der Hub speichert ein Netz in `wifi.ssid`. Ein zweites gehört in "
            "`wifi.fallback`; der Hub versucht es, wenn das erste außer Reichweite ist.",
        ],
        "personal": [
            "Die Installationsanleitung nennt noch Version 1.2 des Hubs.",
            "Das aktuelle Release ist 1.4, und sein erster Schritt hat sich geändert: "
            "Der Hub fragt jetzt auf seinem eigenen Bildschirm nach dem Netz.",
        ],
        "reply": "Das Log zeigt `already subscribed, skipping` direkt nach dem "
        "Wiederverbinden, was die Ursache oben bestätigt. Ein Fix würde das Abonnement "
        "in `on_disconnect()` beenden, damit `start()` neu abonniert.",
        "still_missing": ["Die Version der Dashboard-App, unter Einstellungen → Über."],
        "answer": "Nur das Diagramm. `ChartFeed.start()` abonniert einmal und kehrt "
        "früh zurück, solange `subscribed` gesetzt ist, und nach dem Wiederverbinden "
        "setzt es nichts zurück. Der aktuelle Wert abonniert in `ValueFeed`, das jedes "
        "Mal neu startet, und erholt sich deshalb. Ein Fix würde `subscribed` in "
        "`on_disconnect()` zurücksetzen; noch prüft kein Test ein Wiederverbinden.",
    },
}
# Each comment of the page: its heading, and when the assistant posts it. The
# fields in braces are the timing rules of issue_assistant.py, see timings().
KINDS = {
    "analysis": (
        "The first analysis",
        "A new issue of a reporter, not of a maintainer, gets a first analysis: what "
        "the AI understood, the likely cause with links to the lines of code, what "
        "the reporter can try, the information still missing, and related issues and "
        "discussions. Its questions add the label `needs-info`. More than "
        "{open_links} links to issues and files fold into one line, and the footer "
        "links the notice to reporters and asks for a 👍 or 👎, which the summary of "
        "the daily sweep counts.",
    ),
    "duplicate": (
        "A sure duplicate",
        "When the AI is sure that an open, older issue reports the same, the analysis "
        "says so first. The issue gets the label `possible-duplicate` and closes "
        "{duplicate_grace} days later, unless someone writes a comment or reacts with 👎.",
    ),
    "elsewhere": (
        "An issue for a related repository",
        "Where `[related]` of `config.toml` names public repositories that share "
        "users and topics with this one, the AI also searches their issues and links "
        "those that help. When the issue was filed in the wrong repository, a "
        "sentence says where it may belong, for the maintainers, who transfer it by "
        "hand: nothing moves or closes on its own.",
    ),
    "named-duplicate": (
        "A duplicate that a maintainer named",
        "A maintainer who starts a comment with `/assistant duplicate #23` sets off the "
        "same: the issue gets the label `possible-duplicate` and this notice, and "
        "closes {duplicate_grace} days later, unless someone writes a comment or reacts "
        "with 👎. No AI writes this comment.",
    ),
    "secret": (
        "A secret in the issue",
        "An issue that seems to show a token, a key or a password gets a warning at "
        "the top of its analysis. The AI never repeats the secret, and an answer with "
        "something like one is never posted.",
    ),
    "personal": (
        "Personal data in the issue",
        "An issue that seems to show personal data, such as an e-mail address or the "
        "address of a private server, gets a note on how to remove it.",
    ),
    "security": (
        "A possible vulnerability",
        "An issue that may describe a vulnerability gets only a pointer to the private "
        "reporting of the security policy: no summary, no analysis, no questions and "
        "no duplicates. The reason for its labels on the timeline is a fixed sentence "
        "instead of the words of the AI.",
    ),
    "follow-up": (
        "A follow-up",
        "When the reporter answers the questions of an analysis, in a comment or by an "
        "edit of the issue, the AI reads the answer and replies, with what is still "
        "missing. It follows up at most {follow_ups}, and never once the maintainer has "
        "joined the conversation, also when they join while the AI writes.",
    ),
    "answer": (
        "An answer to a maintainer's question",
        "A maintainer who starts a comment with `/assistant ask` and a question gets "
        "the answer of the AI, from the code, the documentation and the issue, with "
        "the files it rests on. Its text is checked and cleaned as every text of the "
        "AI, and each question gets one answer.",
    ),
    "reminder": (
        "A reminder",
        "{remind_after} days after a question of the assistant or of the maintainer, an issue "
        "without an answer, in a comment or by an edit of its text, gets the label "
        "`stale` and a reminder, which names the day it closes and links the "
        "comment with the questions. No AI writes this "
        "comment or any of those below.",
    ),
    "closed-unanswered": (
        "Closed without an answer",
        "{close_after} days after the question, on the day that the reminder named, an issue "
        "still without an answer closes as not planned.",
    ),
    "reopened": (
        "Reopened by an answer",
        "An answer of the reporter reopens an issue that closed for want of one.",
    ),
    "closed-duplicate": (
        "Closed as a duplicate",
        "{duplicate_grace} days after the notice of a sure duplicate, without a comment or a 👎, "
        "the issue closes as a duplicate of the original.",
    ),
    "fixed": (
        "Fixed by a pull request",
        "A merged pull request that names the issue with `Fixes #42` marks it "
        "`fixed-in-next-release`. The issue stays open until a release ships the fix.",
    ),
    "released": (
        "Released",
        "When a release ships the fix, the issue closes with a link to the release and "
        "is filed under the milestone of its version.",
    ),
    "reopened-release": (
        "Reopened after the release",
        "When the reporter writes within {reopen_after_release} days of the release that the problem "
        "persists, the AI reads the comment and the issue reopens. Thanks or "
        "questions keep it closed.",
    ),
    "locked": (
        "Locked long after the closing",
        "Only when `lock_closed_after_days` of `[lifecycle]` is set, here to "
        "{lock_example} days: an issue closed for longer, and at least the "
        "{reopen_after_release} days after a release, gets this line and is locked "
        "as resolved, unless it is exempt or someone unlocked it before.",
    ),
}
# The part of a page that the comments replace; on GitHub, it says where they are.
REGION = re.compile(
    r"^<!-- comments of issue_assistant\.py -->$.*?^<!-- end of the comments -->$",
    re.MULTILINE | re.DOTALL,
)
PLACEHOLDER = re.compile(r"<p>(ia-comment-[a-z-]+-(?:en|de))</p>")
# The fold that the assistant puts around many links.
FOLD = re.compile(
    r"^<details><summary>(?P<summary>[^<\n]*)</summary>\n\n(?P<body>.*?)\n\n"
    r"</details>$",
    re.MULTILINE | re.DOTALL,
)
MARKER = re.compile(r"^<!-- issue-assistant:[^\n]*-->\n?", re.MULTILINE)
ALERT = re.compile(
    r"^> \[!(?P<kind>[A-Z]+)\]\n(?P<body>(?:>.*(?:\n|$))+)", re.MULTILINE
)
# The alerts of GitHub, as the admonitions of the theme that look like them.
ALERTS = {
    "NOTE": "note",
    "TIP": "tip",
    "IMPORTANT": "info",
    "WARNING": "warning",
    "CAUTION": "danger",
}
DEMO_LINK = re.compile(
    rf'<a href="https://github\.com/{re.escape(DEMO)}[^"]*">(?P<text>.*?)</a>', re.S
)
MENTION = re.compile(
    r"(?<![\w@/.-])@(?P<login>[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)"
)
# A reference to an issue, which GitHub links; not the anchor of an address.
REFERENCE = re.compile(r"(?<![\w&/#])#(?P<number>\d+)\b")
# A # that starts a line, or an item of a list, without a blank after it: GitHub
# reads it as text, such as the #17 of a related issue, but Python-Markdown as a
# heading.
NO_HEADING = re.compile(
    r"^(?P<start>[ \t]*(?:[-*+][ \t]+|\d+\.[ \t]+)?)#(?=[^\s#])", re.M
)
# GitHub posts the comments in the name of its app github-actions. The picture of the
# app opens no lightbox: it is decoration, with no text for a link.
CARD = (
    '<div class="ia-comment"><div class="ia-comment__header">'
    f'<img class="ia-comment__avatar {NO_LIGHTBOX}" src="assets/logo.svg" alt="" '
    'width="20" height="20"><strong>github-actions</strong> '
    '<span class="ia-comment__badge">bot</span> commented</div>'
    '<div class="ia-comment__body">{body}</div></div>'
)


class Example(NamedTuple):
    """A comment of the page, in each language."""

    kind: str
    title: str
    when: str
    texts: dict[str, str]


def assistant() -> ModuleType:
    """issue_assistant.py of the commit that the website is built from."""
    module = sys.modules.get("issue_assistant")
    if module is None:
        spec = importlib.util.spec_from_file_location(
            "issue_assistant", ROOT / "issue_assistant.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        # Its dataclasses look up their module there.
        sys.modules["issue_assistant"] = module
        spec.loader.exec_module(module)
    return module


def comments(ia: ModuleType, config: Any, language: str) -> dict[str, str]:
    """Every comment of the assistant for the demo project, in one language."""
    words = WORDS[language]
    tracked = set(DEMO_FILES)

    def answer(
        kind: str, summary: str = "", analysis: str = "", **changes: Any
    ) -> dict[str, Any]:
        return {
            "language": language,
            "kind": kind,
            "summary": summary,
            "analysis": analysis,
            "next_steps": [],
            "sensitive_data": "none",
            "security_report": False,
        } | changes

    def references(items: list[dict[str, Any]]) -> list[str]:
        # As references() of issue_assistant.py makes them, with the files of the
        # demo project for those that git tracks.
        found = []
        for item, reason in zip(items, words["references"], strict=False):
            link = ia.reference_link(item, tracked, DEMO_SHA, language, config)
            if not link:
                raise ValueError(f"the demo project has nothing for {item}")
            found.append(f"{link}: {reason}")
        return found

    line = {"path": CHART, "line": 52}
    anchor = {"path": TROUBLESHOOTING, "anchor": ia.slug("The chart stays empty")}
    # As check_findings() of issue_assistant.py lists them.
    related = [
        f"#17: {words['related'][0]}",
        f"#9 ({ia.say(language, 'discussion')}): {words['related'][1]}",
    ]
    release = {
        "tag_name": "v1.4.0",
        "html_url": f"https://github.com/{DEMO}/releases/tag/v1.4.0",
    }
    return {
        "analysis": ia.render_analysis(
            answer(
                "bug",
                words["summary"],
                words["analysis"],
                next_steps=words["next_steps"],
            ),
            REPORTER,
            ia.Findings(
                related=related,
                references=references([line, anchor]),
                numbers={9, 17},
            ),
            words["missing"],
            ["connectivity"],
            config,
        ),
        "duplicate": ia.render_analysis(
            answer("enhancement", *words["duplicate"][:2]),
            REPORTER,
            ia.Findings(
                duplicate={"number": 23},
                duplicate_reason=words["duplicate"][2],
                numbers={23},
            ),
            [],
            ["area: dashboard"],
            config,
        ),
        "elsewhere": ia.render_analysis(
            answer("bug", *words["elsewhere"][:2]),
            REPORTER,
            ia.Findings(
                related=[
                    f"{ia.elsewhere_link(f'{FIRMWARE}#31')}: {words['elsewhere'][2]}"
                ],
                numbers={f"{FIRMWARE}#31"},
                belongs_in=FIRMWARE,
            ),
            [],
            ["bug", "area: sensors"],
            config,
        ),
        "named-duplicate": ia.render_duplicate(23, REPORTER, language, config),
        "secret": ia.render_analysis(
            answer("question", *words["secret"], sensitive_data="secret"),
            REPORTER,
            ia.Findings(),
            [],
            ["question", "area: sensors"],
            config,
        ),
        "personal": ia.render_analysis(
            answer("documentation", *words["personal"], sensitive_data="personal"),
            REPORTER,
            ia.Findings(),
            [],
            ["documentation"],
            config,
        ),
        "security": ia.render_analysis(
            answer("bug", security_report=True),
            REPORTER,
            ia.Findings(),
            [],
            ["bug"],
            config,
        ),
        "follow-up": ia.render_follow_up(
            {"language": language, "sensitive_data": "none", "reply": words["reply"]},
            REPORTER,
            references([line]),
            words["still_missing"],
            config,
        ),
        "answer": ia.render_answer(
            {"language": language, "answer": words["answer"]},
            MAINTAINER,
            42,
            2,
            references([line]),
            config,
        ),
        "reminder": ia.render_reminder(
            REPORTER,
            language,
            datetime(2026, 11, 2),
            f"https://github.com/{DEMO}/issues/42#issuecomment-1",
        ),
        "closed-unanswered": ia.render_closed_unanswered(REPORTER, language),
        "reopened": ia.render_reopened(REPORTER, language),
        "closed-duplicate": ia.render_closed_duplicate(23, language),
        "fixed": ia.render_fixed(43, "main", language),
        "released": ia.render_released(REPORTER, release, [43], language),
        "reopened-release": ia.render_reopened_release(REPORTER, language),
        "locked": ia.render_locked(
            language, ia.Lifecycle(lock_closed_after_days=LOCK_EXAMPLE)
        ),
    }


def examples() -> list[Example]:
    """The comments of the page, in the order of the life of an issue."""
    ia = assistant()
    texts: dict[str, dict[str, str]] = {kind: {} for kind in KINDS}
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        for path, text in DEMO_FILES.items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text(text, "utf-8")
        config = ia.Config(root=root, repository=DEMO, labels=(), project="")
        for language in LANGUAGES:
            for kind, text in comments(ia, config, language).items():
                texts[kind][language] = text
    rules = timings(ia)
    return [
        Example(kind, title, when.format(**rules), texts[kind])
        for kind, (title, when) in KINDS.items()
    ]


def timings(ia: ModuleType, lifecycle: Any = None) -> dict[str, Any]:
    """The timing rules of issue_assistant.py, for the texts of KINDS: those of
    `lifecycle`, or the defaults of [lifecycle] in config.toml."""
    lifecycle = lifecycle or ia.DEFAULT_LIFECYCLE
    follow_ups = lifecycle.max_follow_ups
    return {
        "remind_after": lifecycle.remind_after_days,
        "close_after": lifecycle.close_after_days,
        "duplicate_grace": lifecycle.duplicate_grace_days,
        "open_links": ia.MAX_OPEN_LINKS,
        "reopen_after_release": lifecycle.reopen_after_release_days,
        "follow_ups": {1: "once", 2: "twice"}.get(follow_ups, f"{follow_ups} times"),
        "lock_example": LOCK_EXAMPLE,
    }


def thanks_rows(ia: ModuleType) -> list[str]:
    """The table of how an analysis begins, for each kind of issue."""
    kinds = [
        key.removeprefix("thanks.")
        for key in ia.TEXT["en"]
        if key.startswith("thanks.")
    ]
    rows = ["| Kind | English | Deutsch |", "| --- | --- | --- |"]
    for kind in [*(kind for kind in kinds if kind != "default"), "default"]:
        name = "any other kind" if kind == "default" else f"`{kind}`"
        texts = [cell(ia.thanks(language, kind, REPORTER)) for language in LANGUAGES]
        rows.append(f"| {name} | {' | '.join(texts)} |")
    return rows


def placeholder(kind: str, language: str) -> str:
    return f"ia-comment-{kind}-{language}"


def comments_part(shown: list[Example], thanks: list[str]) -> str:
    """The Markdown of the comments, with a placeholder for each of them."""
    lines = []
    for example in shown:
        lines += [f"## {example.title}", "", example.when, ""]
        for language, name in LANGUAGES.items():
            lines += [f'=== "{name}"', "", f"    {placeholder(example.kind, language)}"]
            lines.append("")
    lines += [
        "## How an analysis begins",
        "",
        "The first sentence of an analysis thanks the reporter for the kind of issue "
        "that the AI chose, one of the labels of the group `type`:",
        "",
        *thanks,
    ]
    return "\n".join(lines)


def admonitions(text: str) -> str:
    """The text with each alert of GitHub as an admonition of the theme."""

    def admonition(match: re.Match[str]) -> str:
        kind = match["kind"]
        body = "\n".join(
            "    " + line.removeprefix(">").removeprefix(" ")
            for line in match["body"].splitlines()
        )
        return f'!!! {ALERTS.get(kind, "note")} "{kind.title()}"\n{body}\n'

    return ALERT.sub(admonition, text)


def comment_html(text: str, convert: Callable[[str], str]) -> str:
    """A comment as GitHub shows it, with convert for its Markdown.

    The links of the demo project lead nowhere, so they only look like links, as do
    the references to its issues, and a mention stands out as on GitHub; in code,
    both stay text.
    """
    markdown = NO_HEADING.sub(r"\g<start>\\#", admonitions(MARKER.sub("", text)))
    markdown = folded(markdown, convert)
    body = DEMO_LINK.sub(r'<span class="ia-link">\g<text></span>', convert(markdown))
    parts = re.split(r"(<code>.*?</code>)", body, flags=re.S)
    for index in range(0, len(parts), 2):
        text = MENTION.sub(r'<span class="ia-mention">@\g<login></span>', parts[index])
        parts[index] = REFERENCE.sub(r'<span class="ia-link">#\g<number></span>', text)
    return CARD.format(body="".join(parts))


def folded(markdown: str, convert: Callable[[str], str]) -> str:
    """The Markdown with the fold of its links as HTML around the HTML of the links:
    GitHub reads the Markdown inside the fold, Python-Markdown leaves it as it is."""
    return FOLD.sub(
        lambda match: (
            f"<details><summary>{match['summary']}</summary>\n"
            f"{convert(match['body'])}\n</details>"
        ),
        markdown,
    )


def markdown_html(text: str) -> str:
    """The HTML of the Markdown of a comment, with extensions of the website."""
    import markdown  # The tests, which run without MkDocs, pass their own.

    return markdown.markdown(text, extensions=["admonition", "pymdownx.superfences"])


def gallery(convert: Callable[[str], str]) -> tuple[str, dict[str, str]]:
    """The Markdown of the comments and the HTML of each of them."""
    shown = examples()
    part = comments_part(shown, thanks_rows(assistant()))
    cards = {
        placeholder(example.kind, language): comment_html(text, convert)
        for example in shown
        for language, text in example.texts.items()
    }
    return part, cards


def with_gallery(markdown: str, convert: Callable[[str], str]) -> str:
    """The page with the comments in place of the part that asks for them, which
    keeps the HTML of each comment for the page's HTML."""
    if not REGION.search(markdown):
        return markdown
    part, cards = gallery(convert)
    GALLERY.update(cards)
    return REGION.sub(lambda _: part, markdown, count=1)


# The comments of the current build, for their page.
GALLERY: dict[str, str] = {}


def with_comments(html: str) -> str:
    """The page with each comment in place of its placeholder."""
    return PLACEHOLDER.sub(lambda match: GALLERY[match[1]], html)


# The guide was one page until these pages took its parts, in this order. A link to
# one of its anchors, such as guide.html#settings, leads to the page that has the
# anchor now, so that links from before keep working.
GUIDE = "guide.md"
FORMER_GUIDE = (
    "how-it-works.md",
    "getting-started.md",
    "configuration.md",
    "reference.md",
)
# The address and the anchors of each of those pages, of the current build.
ANCHORS: dict[str, tuple[str, list[str]]] = {}


def anchors(toc: Any) -> list[str]:
    """The anchors of the headings of a table of contents, the nested ones too."""
    found: list[str] = []
    for item in toc:
        found += [item.id, *anchors(item.children)]
    return found


def remember_anchors(page: Any) -> None:
    """Keep the anchors of a page that has a part of the former guide."""
    if page.file.src_uri in FORMER_GUIDE:
        ANCHORS[page.file.src_uri] = (page.file.url, anchors(page.toc))


def forwarding(url: str, own: list[str]) -> str:
    """The script that leads an anchor of the former guide at url to the page that
    has it now; an anchor of the page itself stays."""
    folder = posixpath.dirname(url) or "."
    targets: dict[str, str] = {}
    for source in FORMER_GUIDE:
        address, found = ANCHORS.get(source, ("", []))
        for anchor in found:
            if anchor not in own:
                targets.setdefault(anchor, posixpath.relpath(address, folder))
    if not targets:
        return ""
    # Anchors are slugs and addresses paths of pages, so nothing ends the script.
    data = json.dumps(targets, sort_keys=True, separators=(",", ":"))
    return (
        f"<script>(function(){{var targets={data},"
        "anchor=decodeURIComponent(location.hash.slice(1));"
        "if(Object.prototype.hasOwnProperty.call(targets,anchor))"
        "location.replace(targets[anchor]+location.hash)})()</script>"
    )


def with_forwarding(output: str, page: Any) -> str:
    """The overview of the guide with the script that forwards its former anchors."""
    if page.file.src_uri != GUIDE:
        return output
    script = forwarding(page.file.url, anchors(page.toc))
    return output.replace("</body>", f"{script}</body>", 1)


# The events of MkDocs.


def on_page_markdown(markdown: str, page: Any, config: Any, files: Any) -> str:
    described(markdown, page.meta)
    return with_gallery(action_tables_of(markdown), markdown_html)


@event_priority(50)
def on_page_content(html: str, page: Any, config: Any, files: Any) -> str:
    remember_anchors(page)
    return localized(with_comments(html), page.file.url, Path(config["docs_dir"]))


def on_post_page(output: str, page: Any, config: Any) -> str:
    output = early_theme(scrolling(output))
    if page is None:
        return output
    if page.is_homepage and page.meta.get("title"):
        output = homepage_title(output, page.meta["title"], config["site_name"])
    return with_forwarding(output, page)
