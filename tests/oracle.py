"""What GitHub would make of a comment, judged by a parser of its own.

A CommonMark parser, independent of the assistant's rules, decides what is code and
what is text; in the text, the rules of GitHub decide what becomes a mention, a
reference or a link. The property tests and the fuzzer judge the cleaned text of
the engine with it.
"""

from pathlib import Path

from markdown_it import MarkdownIt

import issue_assistant as assistant

PARSER = MarkdownIt("commonmark")
CONFIG = assistant.Config(
    root=Path("."),
    repository="owner/project",
    labels=(assistant.Label("bug", "d73a4a", "Bug", "type"),),
    project="A project.",
    hosts=frozenset({"docs.example.com"}),
)


def rendered(markdown: str) -> tuple[list[str], list[str], list[str]]:
    """The text outside code, the link targets and the HTML of rendered Markdown."""
    texts: list[str] = []
    links: list[str] = []
    html: list[str] = []

    def walk(tokens):
        for token in tokens:
            if token.type in {"code_inline", "fence", "code_block"}:
                continue
            if token.type in {"html_inline", "html_block", "image"}:
                html.append(token.content)
            if token.type == "link_open":
                links.append(str(token.attrGet("href")))
            if token.type == "text":
                texts.append(token.content)
            walk(token.children or [])

    walk(PARSER.parse(markdown))
    return texts, links, html


def linked(text: str) -> tuple[list[str], str]:
    """The addresses that GitHub links in the text, and the text without them.

    GitHub links an address only at the start or after a space or one of * _ ~ (,
    and a # or @ inside it is part of the address.
    """
    links: list[str] = []

    def drop(match, url):
        start = match.start()
        before = match.string[start - 1] if start else " "
        if before not in " \t\n*_~(":
            return match[0]
        links.append(url)
        return " "

    text = assistant.BARE_URL.sub(lambda m: drop(m, m[0]), text)
    text = assistant.WWW_URL.sub(lambda m: drop(m, f"https://{m[0]}"), text)
    return links, text


def problems(output: str, numbers: set[int]) -> list[str]:
    """What in the cleaned text would mention, reference, link or show HTML."""
    found = []
    texts, links, html = rendered(output)
    for part in texts:
        addresses, part = linked(part)
        found += [
            f"link to {url}"
            for url in addresses
            if not assistant.allowed_url(url, CONFIG)
        ]
        found += [f"mention {m[0]}" for m in assistant.MENTION.finditer(part)]
        found += [f"reference {m[0]}" for m in assistant.CROSS_REFERENCE.finditer(part)]
        found += [
            f"reference {m[0]}"
            for m in assistant.ISSUE_REFERENCE.finditer(part)
            if int(m[1]) not in numbers
        ]
    found += [
        f"link to {url}" for url in links if not assistant.allowed_url(url, CONFIG)
    ]
    found += [f"HTML {item!r}" for item in html]
    if "\0" in output or "\r" in output:
        found.append("a NUL or carriage return")
    return found
