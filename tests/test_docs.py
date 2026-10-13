"""The pictures of the documentation, the hooks of its website, how the project
presents itself on GitHub and its Marketplace, and what its documentation says."""

import dataclasses
import importlib.util
import inspect
import json
import re
import sys
import textwrap
import tomllib
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import issue_assistant as assistant

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "docs" / "images"
# The screenshots and the animation of docs/images; scripts/diagrams.sh draws and
# checks the pictures of the diagrams in docs/images/diagrams.
SCREENSHOTS = sorted(path for path in IMAGES.iterdir() if path.name != "diagrams")
RAW = "https://raw.githubusercontent.com/Dennis-Otto/issue-assistant/main/"
DOCUMENTS = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
PICTURE = (
    "<picture>\n"
    '  <source media="(prefers-color-scheme: dark)" srcset="{dark}">\n'
    '  <img src="{light}" alt="A demo">\n'
    "</picture>"
)
# The variants of the animation at the top of the README and of the home page, in the
# order of their <source>s: the first one whose condition holds is shown.
HERO = [
    (
        "(prefers-reduced-motion: reduce) and (max-width: 600px) and "
        "(prefers-color-scheme: dark)",
        "hero-mobile-still-dark.webp",
    ),
    (
        "(prefers-reduced-motion: reduce) and (max-width: 600px)",
        "hero-mobile-still-light.webp",
    ),
    (
        "(prefers-reduced-motion: reduce) and (prefers-color-scheme: dark)",
        "hero-still-dark.webp",
    ),
    ("(prefers-reduced-motion: reduce)", "hero-still-light.webp"),
    (
        "(max-width: 600px) and (prefers-color-scheme: dark)",
        "hero-mobile-dark.webp",
    ),
    ("(max-width: 600px)", "hero-mobile-light.webp"),
    ("(prefers-color-scheme: dark)", "hero-dark.webp"),
]

_spec = importlib.util.spec_from_file_location(
    "mkdocs_hooks", ROOT / "scripts" / "mkdocs_hooks.py"
)
assert _spec and _spec.loader
hooks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hooks)


# The headers of pictures, as far as the hook reads them.


def png(width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
    )


def webp(chunk: bytes, data: bytes) -> bytes:
    size = len(data).to_bytes(4, "little")
    return (
        b"RIFF" + (12 + len(data)).to_bytes(4, "little") + b"WEBP" + chunk + size + data
    )


def extended(width: int, height: int) -> bytes:
    """A WebP with the chunk VP8X, as an animation has it."""
    canvas = (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    return webp(b"VP8X", b"\x12\x00\x00\x00" + canvas)


def lossless(width: int, height: int) -> bytes:
    bits = (width - 1) | (height - 1) << 14 | 1 << 28
    return webp(b"VP8L", b"\x2f" + bits.to_bytes(4, "little"))


def lossy(width: int, height: int, scale: int = 0) -> bytes:
    sizes = (width | scale << 14).to_bytes(2, "little") + (
        height | scale << 14
    ).to_bytes(2, "little")
    return webp(b"VP8 ", b"\x50\x09\x00\x9d\x01\x2a" + sizes)


@pytest.fixture
def docs(tmp_path: Path) -> Path:
    """A folder of pages with the pictures that the tests of the hook show."""
    (tmp_path / "images").mkdir()
    for name, header in {
        "a-light.png": png(1600, 900),
        "a-dark.png": png(1600, 900),
        "b-light.webp": lossless(1200, 800),
        "b-dark.webp": lossless(1200, 800),
        "hero-light.webp": extended(1395, 1251),
        "hero-dark.webp": extended(1395, 1251),
        "hero-mobile-light.webp": extended(860, 1700),
        "hero-mobile-dark.webp": extended(860, 1700),
        "hero-still-light.webp": lossless(1395, 1251),
        "hero-still-dark.webp": lossless(1395, 1251),
        "hero-mobile-still-light.webp": lossless(860, 1700),
        "hero-mobile-still-dark.webp": lossless(860, 1700),
    }.items():
        (tmp_path / "images" / name).write_bytes(header)
    return tmp_path


def sources(text: str) -> list[list[tuple[str, str]]]:
    """The <source>s of each picture of a page: their conditions and their files."""
    return [
        [
            (source["media"], source["srcset"].rsplit("/", 1)[-1])
            for source in hooks.SOURCE.finditer(picture["sources"])
        ]
        for picture in hooks.PICTURE.finditer(text)
    ]


def hero(prefix: str) -> str:
    """The hero of the README, with its files in the folder at prefix."""
    lines = "".join(
        f'  <source media="{media}" srcset="{prefix}{name}">\n' for media, name in HERO
    )
    return f'<picture>\n{lines}  <img src="{prefix}hero-light.webp" alt="A demo">\n</picture>'


# The pictures themselves.


def test_every_picture_that_the_documentation_shows_on_main_exists():
    # The link check skips these addresses, since a pull request can't show them yet.
    shown = [
        (document.name, match[1])
        for document in DOCUMENTS
        for match in re.finditer(
            rf'{re.escape(RAW)}([^")\s]+)', document.read_text("utf-8")
        )
    ]
    assert shown
    for name, path in shown:
        assert (ROOT / path).is_file(), f"{name} shows {path}, which doesn't exist"


def test_every_page_that_the_documentation_links_on_main_exists():
    # The link check skips these addresses too, since a pull request that adds a page
    # can't link it yet. The README links them in full, since the Marketplace shows it.
    linked = [
        (document.name, match[2])
        for document in DOCUMENTS
        for match in re.finditer(
            r"https://(github\.com/Dennis-Otto/issue-assistant/blob|"
            r"raw\.githubusercontent\.com/Dennis-Otto/issue-assistant)/main/"
            r"(docs/[^#?\")\s]+\.md)",
            document.read_text("utf-8"),
        )
    ]
    assert ("README.md", "docs/comparison.md") in linked
    for name, path in linked:
        assert (ROOT / path).is_file(), f"{name} links {path}, which doesn't exist"


def test_the_readme_links_only_full_addresses():
    # The Marketplace shows the README on a page of its own, where a relative link leads
    # nowhere.
    readme = (ROOT / "README.md").read_text("utf-8")
    relative = re.findall(r"\]\((?!https?://|mailto:|#)([^)]+)\)", readme)
    assert not relative, f"the README links {relative} relatively"


def test_every_picture_is_a_webp_that_comes_light_and_dark_and_is_shown():
    shown = "\n".join(document.read_text("utf-8") for document in DOCUMENTS)
    for picture in SCREENSHOTS:
        match = re.fullmatch(r"([a-z-]+)-(light|dark)\.webp", picture.name)
        assert match, f"{picture.name} isn't named name-light.webp or name-dark.webp"
        other = "dark" if match[2] == "light" else "light"
        assert (IMAGES / f"{match[1]}-{other}.webp").is_file()
        assert hooks.size(picture) == hooks.size(IMAGES / f"{match[1]}-{other}.webp")
        assert f"images/{picture.name}" in shown, f"no page shows {picture.name}"


def test_only_the_heroes_move_and_the_still_pictures_are_lossless():
    for picture in SCREENSHOTS:
        data = picture.read_bytes()
        assert data[:4] == b"RIFF" and data[8:12] == b"WEBP", picture.name
        animated = data[12:16] == b"VP8X" and data[20] & 0x02
        if re.fullmatch(r"hero(-mobile)?-(light|dark)\.webp", picture.name):
            assert animated, f"{picture.name} is no animation"
        else:
            assert not animated, f"{picture.name} moves"
            # Lossless WebP keeps every pixel of the screenshot, as a PNG does.
            assert b"VP8L" in data[12:40] and b"VP8 " not in data, picture.name


def test_phones_get_a_portrait_hero_and_less_motion_a_still_one():
    for theme in ("light", "dark"):
        width, height = hooks.size(IMAGES / f"hero-{theme}.webp")
        assert width > height
        assert hooks.size(IMAGES / f"hero-still-{theme}.webp") == (width, height)
        width, height = hooks.size(IMAGES / f"hero-mobile-{theme}.webp")
        assert height > width
        assert hooks.size(IMAGES / f"hero-mobile-still-{theme}.webp") == (
            width,
            height,
        )


def test_the_readme_and_the_home_page_show_every_variant_of_the_hero():
    for document in (ROOT / "README.md", ROOT / "docs" / "index.md"):
        assert sources(document.read_text("utf-8"))[0] == HERO, document.name


# The hooks of the website.


def test_the_website_runs_the_hooks_of_the_blueprint_too():
    # The hooks of mkdocs.yml replace those of .github/mkdocs-blueprint.yml, whose
    # hook tries a failed download again: without it, one slow answer of the server
    # of a font fails the strict build of a pull request.
    blueprint = (ROOT / ".github" / "mkdocs-blueprint.yml").read_text("utf-8")
    # Read as text: the file has tags of Python that a safe loader refuses.
    block = re.search(r"^hooks:\n((?:  - .+\n)+)", blueprint, re.MULTILINE)
    assert block
    inherited = re.findall(r"^  - (.+)$", block[1], re.MULTILINE)
    assert inherited == [".github/mkdocs_blueprint.py"]
    own = yaml.safe_load((ROOT / "mkdocs.yml").read_text("utf-8"))["hooks"]
    assert own == [*inherited, "scripts/mkdocs_hooks.py"]
    assert all((ROOT / hook).is_file() for hook in own)


def test_the_website_shows_its_own_copies_of_the_pictures(docs: Path):
    html = f'<img src="{RAW}docs/images/a-light.png" alt="A">'
    assert (
        hooks.localized(html, "guide.html", docs)
        == '<img src="images/a-light.png" alt="A">'
    )
    assert (
        hooks.localized(html, "decisions/0001-record-decisions.html", docs)
        == '<img src="../images/a-light.png" alt="A">'
    )


def test_other_addresses_stay_as_they_are(docs: Path):
    html = (
        f'<img src="{RAW}.github/social-preview.png" alt="A">'
        f'<a href="{RAW}docs/images/a-light.png">the picture</a>'
    )
    assert hooks.localized(html, "guide.html", docs) == html


def test_the_switch_between_light_and_dark_chooses_the_picture(docs: Path):
    html = PICTURE.format(
        dark=f"{RAW}docs/images/a-dark.png", light=f"{RAW}docs/images/a-light.png"
    )
    # Lazy: the browser doesn't load the copy that the theme hides. The size keeps
    # the space for the picture while it loads.
    assert hooks.localized(html, "index.html", docs) == (
        '<img src="images/a-light.png#only-light" alt="A demo" loading="lazy" '
        'decoding="async" width="1600" height="900">'
        '<img src="images/a-dark.png#only-dark" alt="A demo" loading="lazy" '
        'decoding="async" width="1600" height="900">'
    )
    html = PICTURE.format(dark="../images/b-dark.webp", light="../images/b-light.webp")
    assert hooks.localized(html, "decisions/0001-record-decisions.html", docs) == (
        '<img src="../images/b-light.webp#only-light" alt="A demo" loading="lazy" '
        'decoding="async" width="1200" height="800">'
        '<img src="../images/b-dark.webp#only-dark" alt="A demo" loading="lazy" '
        'decoding="async" width="1200" height="800">'
    )


def test_each_theme_keeps_the_variants_of_its_picture(docs: Path):
    def copy(theme: str) -> str:
        def source(media: str, name: str, width: int, height: int) -> str:
            return (
                f'<source media="{media}" srcset="images/{name}-{theme}.webp" '
                f'width="{width}" height="{height}">'
            )

        return (
            "<picture>"
            + source(
                "(prefers-reduced-motion: reduce) and (max-width: 600px)",
                "hero-mobile-still",
                860,
                1700,
            )
            + source("(prefers-reduced-motion: reduce)", "hero-still", 1395, 1251)
            + source("(max-width: 600px)", "hero-mobile", 860, 1700)
            + f'<img src="images/hero-{theme}.webp#only-{theme}" alt="A demo" '
            'loading="lazy" decoding="async" width="1395" height="1251" '
            'class="off-glb"></picture>'
        )

    assert hooks.localized(hero(f"{RAW}docs/images/"), "index.html", docs) == (
        copy("light") + copy("dark")
    )
    assert hooks.localized(hero("images/"), "index.html", docs) == (
        copy("light") + copy("dark")
    )


def test_what_the_documentation_sets_itself_stays(docs: Path):
    html = (
        "<picture>"
        '<source media="(prefers-color-scheme: dark)" srcset="images/a-dark.png">'
        '<source media="(max-width: 600px)" srcset="images/b-light.webp">'
        '<img src="images/a-light.png" alt="A" width="800" loading="eager" class="wide">'
        "</picture>"
    )
    assert hooks.localized(html, "index.html", docs) == (
        '<picture><source media="(max-width: 600px)" srcset="images/b-light.webp" '
        'width="1200" height="800"><img src="images/a-light.png#only-light" alt="A" '
        'width="800" loading="eager" class="wide off-glb" decoding="async"></picture>'
        '<img src="images/a-dark.png#only-dark" alt="A" width="800" loading="eager" '
        'class="wide" decoding="async">'
    )


def test_a_picture_without_a_dark_variant_stays_as_it_is(docs: Path):
    html = (
        "<picture>"
        '<source media="(max-width: 600px)" srcset="images/b-light.webp">'
        '<img src="images/a-light.png" alt="A">'
        "</picture>"
    )
    assert hooks.localized(html, "index.html", docs) == html


def test_a_picture_on_another_server_has_no_size_to_read(docs: Path):
    html = PICTURE.format(
        dark="https://example.com/a-dark.png", light="https://example.com/a-light.png"
    )
    assert hooks.localized(html, "index.html", docs) == (
        '<img src="https://example.com/a-light.png#only-light" alt="A demo" '
        'loading="lazy" decoding="async">'
        '<img src="https://example.com/a-dark.png#only-dark" alt="A demo" '
        'loading="lazy" decoding="async">'
    )


def test_the_size_of_a_picture_comes_from_the_header_of_its_file(tmp_path: Path):
    for header, expected in (
        (png(1768, 2210), (1768, 2210)),
        (extended(1395, 1251), (1395, 1251)),
        (extended(16384, 1), (16384, 1)),
        (lossless(860, 1700), (860, 1700)),
        (lossless(16384, 16384), (16384, 16384)),
        # The two bits of scaling above the width and the height are no part of them.
        (lossy(640, 480, scale=3), (640, 480)),
    ):
        path = tmp_path / "picture"
        path.write_bytes(header)
        assert hooks.size(path) == expected


def test_a_picture_that_is_neither_png_nor_webp_fails_the_build(tmp_path: Path):
    for header in (b"GIF89a\x10\x00\x10\x00" + bytes(20), webp(b"ALPH", bytes(10))):
        path = tmp_path / "picture.gif"
        path.write_bytes(header)
        with pytest.raises(ValueError, match="neither a PNG nor a WebP"):
            hooks.size(path)


def test_a_missing_picture_fails_the_build(docs: Path):
    html = PICTURE.format(dark="images/c-dark.png", light="images/c-light.png")
    with pytest.raises(FileNotFoundError):
        hooks.localized(html, "index.html", docs)


def test_a_source_that_the_hook_cannot_read_fails_the_build(docs: Path):
    # Each would be lost in both copies of the picture.
    for source in (
        '<source type="image/avif" srcset="images/a-light.avif">',
        '<source srcset="images/b-light.webp" media="(max-width: 600px)">',
    ):
        html = (
            "<picture>"
            '<source media="(prefers-color-scheme: dark)" srcset="images/a-dark.png">'
            f'{source}<img src="images/a-light.png" alt="A">'
            "</picture>"
        )
        with pytest.raises(ValueError, match="unsupported <source>"):
            hooks.localized(html, "index.html", docs)


def test_diagrams_scroll_sideways_on_small_screens():
    diagram = '<pre class="mermaid"><code>flowchart TB\n  a --&gt; b</code></pre>'
    html = f"<p>Before</p>\n{diagram}\n<pre><code>code</code></pre>"
    # On the finished page, since the hook of the blueprint draws a diagram only
    # after the hooks of the content.
    assert hooks.on_post_page(html, None, None) == (
        f'<p>Before</p>\n<div class="diagram" tabindex="0">{diagram}</div>\n'
        "<pre><code>code</code></pre>"
    )
    settings = yaml.safe_load((ROOT / "mkdocs.yml").read_text("utf-8"))
    assert "stylesheets/diagrams.css" in settings["extra_css"]
    style = (ROOT / "docs" / "stylesheets" / "diagrams.css").read_text("utf-8")
    assert ".diagram" in style and "min-width" in style and "overflow-x" in style


def test_the_pictures_of_a_diagram_stay_for_the_hook_of_the_blueprint(docs: Path):
    # The folder of the fixture has no picture of a diagram: the hook reads no size.
    # A page gives the path from its folder, and a part of the README from the root.
    for folder in ("images/diagrams/", "../images/diagrams/", "docs/images/diagrams/"):
        html = PICTURE.format(dark=f"{folder}a-b.dark.svg", light=f"{folder}a-b.svg")
        assert hooks.localized(html, "index.html", docs) == html


def test_the_website_draws_each_diagram_live_and_lets_it_scroll(tmp_path: Path):
    # The hooks in the order of MkDocs: by its priority, the content hook of this
    # project before that of the blueprint, which draws a diagram from its source in
    # docs/diagrams/ in place of its pictures, and the finished page last. The part of
    # the README gives the path from the root, which leads nowhere from the page.
    (tmp_path / "diagrams").mkdir()
    (tmp_path / "diagrams" / "a-b.mmd").write_text("flowchart TB\n  a --> b\n", "utf-8")
    spec = importlib.util.spec_from_file_location(
        "mkdocs_blueprint", ROOT / ".github" / "mkdocs_blueprint.py"
    )
    assert spec and spec.loader
    blueprint = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(blueprint)
    page = SimpleNamespace(
        file=SimpleNamespace(url="architecture.html", src_uri="architecture.md")
    )
    folder = "docs/images/diagrams/"
    html = PICTURE.format(dark=f"{folder}a-b.dark.svg", light=f"{folder}a-b.svg")
    content = hooks.on_page_content(
        f"<p>{html}</p>", page, {"docs_dir": str(tmp_path)}, None
    )
    content = blueprint.on_page_content(
        content, page, SimpleNamespace(docs_dir=str(tmp_path)), None
    )
    assert hooks.on_post_page(content, None, None) == (
        '<div class="diagram" tabindex="0"><pre class="mermaid"><code>flowchart TB\n'
        "  a --&gt; b\n</code></pre></div>"
    )


def test_the_hook_changes_the_html_of_each_page(docs: Path):
    page = SimpleNamespace(
        file=SimpleNamespace(url="security.html", src_uri="security.md")
    )
    html = PICTURE.format(dark="images/b-dark.webp", light="images/b-light.webp")
    assert hooks.on_page_content(html, page, {"docs_dir": str(docs)}, None) == (
        '<img src="images/b-light.webp#only-light" alt="A demo" loading="lazy" '
        'decoding="async" width="1200" height="800">'
        '<img src="images/b-dark.webp#only-dark" alt="A demo" loading="lazy" '
        'decoding="async" width="1200" height="800">'
    )


def test_the_theme_of_the_system_applies_before_the_pictures_load():
    # Material sets the theme of a first visit only when its script has loaded, at
    # the end of the page; until then the light copies would show, and load.
    page = (
        '<body data-md-color-scheme="default"><header><form><input '
        'data-md-color-media="(prefers-color-scheme: dark)"></form></header>'
        "<main><img></main><header>a heading</header></body>"
    )
    assert hooks.on_post_page(page, None, None) == page.replace(
        "</header>", "</header>" + hooks.SCHEME, 1
    )
    # A theme chosen with the switch stays the stored one.
    assert hooks.SCHEME.startswith('<script>try{if(!__md_get("__palette"))')
    assert "matchMedia" in hooks.SCHEME and hooks.SCHEME.endswith("</script>")
    assert hooks.on_post_page("<p>No header</p>", None, None) == "<p>No header</p>"


def test_the_hook_knows_every_picture_of_the_documentation():
    for document in DOCUMENTS:
        text = document.read_text("utf-8")
        # The guide includes the parts of the README.
        url = (
            "guide.html"
            if document.name == "README.md"
            else document.relative_to(ROOT / "docs").with_suffix(".html").as_posix()
        )
        html = hooks.localized(text, url, ROOT / "docs")
        assert text.count("<picture>") == len(hooks.PICTURE.findall(text)), url
        # The hook of the blueprint draws each diagram live from its pictures, which
        # stay as they are until then.
        figures = drawn(text)
        assert all(picture in html for _, picture in figures), url
        count = text.count("<picture>") - len(figures)
        assert html.count("#only-light") == html.count("#only-dark") == count
        assert html.count('loading="lazy"') == 2 * count
        assert html.count(RAW + "docs/") == 0, document.name


# How the project presents itself: its listing on the GitHub Marketplace, the README
# that becomes the page of the listing, and the description and the topics of the
# repository, which GitHub's search and the search engines read.

# The colors that the Marketplace has for the badge of an action.
MARKETPLACE_COLORS = {
    "white",
    "black",
    "gray-dark",
    "yellow",
    "blue",
    "green",
    "orange",
    "red",
    "purple",
}


def load_toml(path: str) -> dict:
    return tomllib.loads((ROOT / path).read_text("utf-8"))


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> dict:
    """The settings of the repository as the settings bot applies them, read by the
    bot's own code: those of repository.toml with this project's own on top."""
    monkeypatch.syspath_prepend(str(ROOT / ".github"))
    from blueprint.settings import Settings

    return Settings.load(ROOT / ".github" / "repository.toml").repository


def tagline(text: str) -> str:
    """The sentence in bold that a page opens with, below its heading."""
    match = re.search(r"^\*\*([^*\n]+)\*\*", text, re.MULTILINE)
    assert match, "no tagline"
    return match[1]


def test_the_action_has_a_name_of_its_own_on_the_marketplace():
    action = yaml.safe_load((ROOT / "action.yml").read_text("utf-8"))
    # The Marketplace lists an action only under a name that no other action has, at
    # the address that the name gives. issue-assistant belongs to workflowkit's
    # "Issue Assistant"; this address was free on 2026-10-12.
    address = re.sub(r"[^a-z0-9]+", "-", action["name"].lower()).strip("-")
    assert address == "read-only-issue-assistant"
    # The violet of the hero and of the social preview.
    assert action["branding"]["color"] in MARKETPLACE_COLORS
    assert action["branding"]["color"] == "purple"
    # The word that maintainers search for, within what the listing shows.
    assert "triage" in action["description"] and len(action["description"]) <= 125


def test_the_readme_says_what_why_and_how_before_the_animation():
    # The page of the listing on the Marketplace is the README, and the animation
    # fills the screen: what comes before it is what a visitor sees first.
    text = (ROOT / "README.md").read_text("utf-8")
    top = text[: text.index("<picture>")]
    assert top.startswith("# Issue assistant\n\n**")
    assert tagline(top).startswith("Safe AI issue triage")
    assert len(re.findall(r"^- \*\*", top, re.MULTILINE)) == 3
    (commands,) = re.findall(r"^```sh\n(.*?)^```$", top, re.MULTILINE | re.DOTALL)
    # The commands install the latest release with its commit hash and its tag.
    assert '--branch "$tag"' in commands and '--version "$tag"' in commands
    # They clone into a new folder, and install only once that clone has succeeded: a
    # folder left from an earlier run would install its old templates under the new
    # tag, and without a tag the clone fails.
    assert 'dir="$(mktemp -d)"' in commands and "/tmp/" not in commands
    assert re.search(
        r'git clone [^\n]* "\$dir" &&\n\s+python3 "\$dir/issue_assistant\.py" install --ref',
        commands,
    )
    assert "(#set-up-a-repository)" in top


def test_the_website_and_the_repository_describe_the_project_alike(settings):
    readme = (ROOT / "README.md").read_text("utf-8")
    home = (ROOT / "docs" / "index.md").read_text("utf-8")
    assert tagline(home) == tagline(readme)
    website = yaml.safe_load((ROOT / "mkdocs.yml").read_text("utf-8"))
    description = settings["description"]
    assert website["site_description"] == description
    assert description.startswith(tagline(readme).rstrip("."))
    # GitHub takes a description of at most 350 characters.
    assert len(description) <= 350


def test_the_issue_chooser_leads_to_the_documentation_and_the_discussions(settings):
    chooser = yaml.safe_load(
        (ROOT / ".github" / "ISSUE_TEMPLATE" / "config.yml").read_text("utf-8")
    )
    # Without blank issues, a question that is neither a bug nor a feature request
    # needs a way out of the chooser: the discussions, as SUPPORT.md says.
    assert chooser["blank_issues_enabled"] is False
    links = {link["name"]: link["url"] for link in chooser["contact_links"]}
    website = yaml.safe_load((ROOT / "mkdocs.yml").read_text("utf-8"))
    assert links["Documentation"] == website["site_url"] == settings["homepage"]
    discussions = f"{website['repo_url']}/discussions"
    assert settings["has_discussions"] and links["Question or idea"] == discussions
    assert f"({discussions})" in (ROOT / "SUPPORT.md").read_text("utf-8")
    # The links of the blueprint's chooser come first, with its names, as in every
    # repository made from the blueprint; those of this project follow.
    assert list(links)[:3] == [
        "Documentation",
        "Security vulnerability",
        "Question or idea",
    ]


def test_the_repository_has_topics_that_github_takes(settings):
    topics = settings["topics"]
    # GitHub takes at most 20 topics, each of lowercase letters, digits and hyphens.
    assert len(topics) <= 20
    for topic in topics:
        assert re.fullmatch(r"[a-z0-9][a-z0-9-]{0,49}", topic), topic
    # The project's file only adds topics. One that should go leaves the answers of
    # the blueprint, with copier update --data topics=..., so that repository.toml
    # follows them.
    extra = load_toml(".github/repository.project.toml")["repository"]["topics"]
    base = load_toml(".github/repository.toml")["repository"]["topics"]
    assert not set(extra) & set(base) and len(set(extra)) == len(extra)
    assert set(topics) == set(base) | set(extra)
    assert {"triage", "claude-code", "github-action", "prompt-injection"} <= set(topics)


# What the documentation says: every part of the README on the website, the same
# users everywhere, and claims that a reader can check.

# The repositories whose issues the assistant looks after, besides its own.
USERS = {
    "ha-autodarts",
    "paperless-unified-search",
    "paperless-sync",
    "repo-blueprint",
    "dennis-otto.github.io",
}


def readme_part(name: str) -> str:
    """The part of the README between the markers that the website includes."""
    text = (ROOT / "README.md").read_text("utf-8")
    match = re.search(
        rf"^<!-- --8<-- \[start:{name}\] -->\n(.*?)^<!-- --8<-- \[end:{name}\] -->$",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match, f"the README has no part {name}"
    return match[1]


def users(text: str, start: str) -> set[str]:
    """The repositories that the line starting with start links."""
    (line,) = [line for line in text.splitlines() if line.startswith(start)]
    return set(re.findall(r"\(https://github\.com/Dennis-Otto/([\w.-]+)\)", line))


def test_every_part_of_the_readme_is_on_the_website_once():
    text = (ROOT / "README.md").read_text("utf-8")
    starts = re.findall(r"^<!-- --8<-- \[start:([a-z-]+)\] -->$", text, re.MULTILINE)
    ends = re.findall(r"^<!-- --8<-- \[end:([a-z-]+)\] -->$", text, re.MULTILINE)
    assert sorted(starts) == sorted(ends) and len(set(starts)) == len(starts)
    assert {"requirements", "faq", "troubleshooting", "compare"} <= set(starts)
    included = [
        match
        for page in sorted((ROOT / "docs").rglob("*.md"))
        for match in re.findall(
            r'^--8<-- "README\.md:([a-z-]+)"$', page.read_text("utf-8"), re.MULTILINE
        )
    ]
    assert sorted(included) == sorted(starts)


def menu() -> dict[str, str]:
    """The pages of the menu of the website, by their title, also those in sections."""

    def walk(items: list[dict[str, object]]) -> dict[str, str]:
        found: dict[str, str] = {}
        for item in items:
            for title, target in item.items():
                if isinstance(target, list):
                    found |= walk(target)
                else:
                    found[title] = str(target)
        return found

    return walk(yaml.safe_load((ROOT / "mkdocs.yml").read_text("utf-8"))["nav"])


def test_the_menu_leads_to_the_questions_and_the_comparison():
    pages = menu()
    assert pages["Help"] == "faq.md" and pages["Compare"] == "comparison.md"
    assert all((ROOT / "docs" / page).is_file() for page in pages.values())
    faq = (ROOT / "docs" / "faq.md").read_text("utf-8")
    assert '--8<-- "README.md:faq"' in faq
    assert '--8<-- "README.md:troubleshooting"' in faq


def test_the_guide_has_a_page_for_each_topic():
    # A visitor lands on what they look for, and the menu beside a page shows the
    # other pages of the guide, not one page of 12,000 pixels.
    nav = yaml.safe_load((ROOT / "mkdocs.yml").read_text("utf-8"))["nav"]
    (guide,) = [item["Guide"] for item in nav if "Guide" in item]
    assert [next(iter(item.values())) for item in guide] == [
        "guide.md",
        "how-it-works.md",
        "configuration.md",
        "reference.md",
        "comments.md",
    ]
    assert menu()["Get started"] == "getting-started.md"
    parts = {
        "getting-started.md": ["requirements", "set-up"],
        "how-it-works.md": ["how-it-works", "security"],
        "configuration.md": ["settings", "findings"],
        "reference.md": ["commands", "engines", "try-a-prompt"],
        "faq.md": ["faq", "troubleshooting"],
        "index.md": ["quick-start"],
    }
    for page, names in parts.items():
        text = (ROOT / "docs" / page).read_text("utf-8")
        assert re.findall(r'^--8<-- "README\.md:([a-z-]+)"$', text, re.M) == names
    # The overview of the guide includes nothing and leads to each of its pages.
    overview = (ROOT / "docs" / "guide.md").read_text("utf-8")
    assert "--8<--" not in overview
    for page in guide[1:]:
        assert f"]({next(iter(page.values()))})" in overview
    # Every page is in the menu, but the records of the decisions, which their
    # list links.
    pages = set(menu().values())
    for path in (ROOT / "docs").rglob("*.md"):
        page = path.relative_to(ROOT / "docs").as_posix()
        assert page in pages or re.fullmatch(r"decisions/\d{4}-[a-z0-9-]+\.md", page)


def test_every_page_names_the_same_users():
    readme = (ROOT / "README.md").read_text("utf-8")
    home = (ROOT / "docs" / "index.md").read_text("utf-8")
    roadmap = (ROOT / "docs" / "roadmap.md").read_text("utf-8")
    assert users(readme, "**Used by**") == USERS
    assert users(home, "**Used by**") == USERS
    assert users(roadmap, "- **The repositories that use it.**") == USERS


def test_the_requirements_say_that_the_firewall_runner_is_a_preview():
    workflow = yaml.safe_load(
        (
            ROOT / "templates" / ".github" / "workflows" / "issue-assistant.yml"
        ).read_text("utf-8")
    )
    runner = workflow["jobs"]["analyze"]["runs-on"]
    requirements = readme_part("requirements")
    assert f"`{runner}`" in requirements
    assert "technical preview" in requirements and "Linux only" in requirements
    assert (
        "(https://github.com/github-early-access/actions-native-egress-firewall)"
        in requirements
    )


def test_every_test_that_the_documentation_names_exists():
    # The security design pins each countermeasure to its tests; a renamed test
    # would leave the claim without its evidence.
    tests = "\n".join(
        path.read_text("utf-8") for path in (ROOT / "tests").glob("test_*.py")
    )
    defined = set(re.findall(r"^def (test_\w+)", tests, re.MULTILINE))
    texts = [
        document.read_text("utf-8")
        for document in (*DOCUMENTS, ROOT / "CONTRIBUTING.md")
    ]
    named = {name for text in texts for name in re.findall(r"`(test_\w+)`", text)}
    assert (
        "test_the_prompt_keeps_the_text_of_issues_apart_from_its_instructions" in named
    )
    assert not named - defined, sorted(named - defined)
    files = {
        path
        for text in texts
        for path in re.findall(r"`((?:tests|fuzz)/[\w/.]+\.py)`", text)
    }
    assert files and all((ROOT / path).is_file() for path in files), files


def test_the_comparison_is_dated_and_every_cell_has_a_source():
    (checked,) = re.findall(r"Checked on (\d{4}-\d{2}-\d{2})", readme_part("compare"))
    assert date.fromisoformat(checked) <= date.today()
    page = (ROOT / "docs" / "comparison.md").read_text("utf-8")
    rows = [
        [cell.strip() for cell in line.strip("|").split("|")]
        for line in page.splitlines()
        if line.startswith("| ") and not line.startswith("| ---")
    ]
    columns, *body = rows
    assert columns[1] == "Issue assistant" and len(body) >= 10
    for row in body:
        assert len(row) == len(columns), row[0]
        # The column of the issue assistant is what this documentation describes;
        # every other cell names the source that it was checked against.
        for column, cell in zip(columns[2:], row[2:], strict=True):
            assert re.search(r"\[\^[\w-]+\]", cell), (row[0], column)
    referenced = set(re.findall(r"\[\^([\w-]+)\](?!:)", page))
    sources = dict(re.findall(r"^\[\^([\w-]+)\]: (.+)$", page, re.MULTILINE))
    assert referenced == set(sources)
    assert all("](https://" in source for source in sources.values())


# The diagrams: Mermaid in docs/diagrams/, which scripts/diagrams.sh draws as the
# pictures that the Markdown shows, for the GitHub app too, and which the website
# draws live; a ```mermaid block that the diagram bot hasn't drawn yet counts as well.
# A page of the website shows a diagram of the README by including the part of the
# README around it, so that each diagram is written once.

FIGURES = [ROOT / "README.md", ROOT / "CONTRIBUTING.md", *DOCUMENTS[1:]]
SOURCES = ROOT / "docs" / "diagrams"


def diagrams(text: str) -> list[tuple[int, str]]:
    """The Mermaid blocks of a Markdown text: the line where each starts, and its
    source."""
    return [
        (text.count("\n", 0, match.start()) + 1, match[1])
        for match in re.finditer(
            r"^```mermaid\n(.*?)^```$", text, re.MULTILINE | re.DOTALL
        )
    ]


def drawn(text: str) -> list[tuple[str, str]]:
    """The pictures of diagrams in a Markdown text, as scripts/diagrams.sh writes
    them: the name of the source of each, and the whole <picture>."""
    return [
        (re.search(r'images/diagrams/([\w.-]+)\.svg"', match["image"])[1], match[0])
        for match in hooks.PICTURE.finditer(text)
        if hooks.DRAWN.search(match["image"])
    ]


def every_diagram() -> list[tuple[str, str]]:
    """Every diagram of the documentation, by its source in docs/diagrams/ or the
    file and line of its block, with its Mermaid."""
    sources = [
        (path.relative_to(ROOT).as_posix(), path.read_text("utf-8"))
        for path in sorted(SOURCES.glob("*.mmd"))
    ]
    return sources + [
        (f"{document.relative_to(ROOT).as_posix()}:{line}", source)
        for document in FIGURES
        for line, source in diagrams(document.read_text("utf-8"))
    ]


def statements(source: str) -> list[str]:
    """The lines of a diagram that say something: no front matter, no comment."""
    source = re.sub(r"\A---\n.*?\n---\n", "", source, flags=re.DOTALL)
    lines = [line.strip() for line in source.splitlines()]
    return [line for line in lines if line and not line.startswith("%%")]


def test_the_diagrams_are_found_where_they_are():
    text = (
        "Before\n\n```mermaid\n---\nconfig:\n  look: classic\n---\nflowchart TB\n"
        "  %% a comment\n  a --> b\n```\n\n```python\nprint()\n```\n"
    )
    ((line, source),) = diagrams(text)
    assert line == 3
    assert statements(source) == ["flowchart TB", "a --> b"]
    assert len(every_diagram()) >= 20
    picture = PICTURE.format(
        dark="docs/images/diagrams/a-b.dark.svg", light="docs/images/diagrams/a-b.svg"
    )
    screenshot = PICTURE.format(dark="images/a-dark.png", light="images/a-light.png")
    assert drawn(f"{picture}\n\n{screenshot}\n") == [("a-b", picture)]


def test_every_diagram_says_what_it_shows():
    # A screen reader reads the title and the description of a diagram, whose words
    # it can't follow along the arrows, and the description says what the arrows say.
    titles = []
    for place, source in every_diagram():
        lines = statements(source)
        title = [
            line[len("accTitle: ") :] for line in lines if line.startswith("accTitle: ")
        ]
        described = [
            line[len("accDescr: ") :] for line in lines if line.startswith("accDescr: ")
        ]
        assert len(title) == 1 and len(described) == 1, (
            f"{place}: add a line accTitle: and a line accDescr: below its first line"
        )
        assert len(described[0]) > 2 * len(title[0]), f"{place}: describe it in full"
        titles.append(title[0])
    assert len(set(titles)) == len(titles), "two diagrams have the same title"


def test_every_diagram_runs_from_top_to_bottom():
    # From left to right, a diagram grows wider than the page and shrinks to fit it,
    # to text of 9 pixels; from top to bottom it keeps its size and grows longer,
    # which the page scrolls, on a phone too.
    for place, source in every_diagram():
        kind, *lines = statements(source)
        assert kind in {"flowchart TB", "sequenceDiagram"}, (
            f"{place}: draw {kind} as flowchart TB"
        )
        sideways = [line for line in lines if re.fullmatch(r"direction \w+", line)]
        assert set(sideways) <= {"direction TB"}, f"{place}: {sideways}"


def test_every_box_of_a_diagram_has_a_border_that_stands_out():
    # The borders of the blueprint's diagrams: 2 pixels keep a tinted box apart from
    # the page, light and dark.
    for place, source in every_diagram():
        for line in statements(source):
            if line.startswith("classDef "):
                assert re.search(r"[ ,]stroke-width:2px(,|$)", line), (
                    f"{place}: end {line!r} with ,stroke-width:2px"
                )


def test_each_diagram_is_written_once():
    # A copy drifts away from its original. A page of the website includes the part
    # of the README with the diagram instead, such as workflows-diagram.
    shown = every_diagram()
    sources = [source.strip() for _, source in shown]
    copies = [
        place
        for (place, _), source in zip(shown, sources, strict=True)
        if sources.count(source) > 1
    ]
    assert not copies, f"the same diagram is at {copies}"
    pictures = [
        (document.relative_to(ROOT).as_posix(), name)
        for document in FIGURES
        for name, _ in drawn(document.read_text("utf-8"))
    ]
    names = [name for _, name in pictures]
    copies = [place for place, name in pictures if names.count(name) > 1]
    assert not copies, f"{copies} show the same diagram"
    readme = (ROOT / "README.md").read_text("utf-8")
    markers = re.findall(r"^<!-- --8<-- \[start:([a-z-]+)\] -->$", readme, re.MULTILINE)
    parts = [readme_part(marker) for marker in markers]
    figures = [(f"README.md:{line}", source) for line, source in diagrams(readme)]
    figures += [(f"the picture of {name}", picture) for name, picture in drawn(readme)]
    for place, figure in figures:
        assert any(figure in part for part in parts), (
            f"{place} is in no part of the README that the website includes"
        )
    for page, marker in [
        ("architecture.md", "workflows-diagram"),
        ("security.md", "trust-boundaries-diagram"),
    ]:
        text = (ROOT / "docs" / page).read_text("utf-8")
        assert f'--8<-- "README.md:{marker}"' in text.splitlines()
        part = readme_part(marker)
        assert len(diagrams(part)) + len(drawn(part)) == 1, marker


# The pages of the website that the code makes or completes: every comment of the
# assistant, the inputs and outputs of the action, and every key of the settings.


def fake_html(text: str) -> str:
    """The Markdown of a comment, marked, for the tests that run without MkDocs."""
    return f"<converted>{text}</converted>"


def page_meta(path: Path) -> tuple[dict, str]:
    """The front matter of a page and its Markdown."""
    text = path.read_text("utf-8")
    match = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        return {}, text
    return yaml.safe_load(match[1]), text[match.end() :]


def all_texts(language: str) -> str:
    """Every comment of the page in one language, and the table of how they begin."""
    shown = hooks.examples()
    return "\n".join(
        [example.texts[language] for example in shown] + hooks.thanks_rows(assistant)
    )


def test_every_text_of_the_assistant_is_on_the_comments_page():
    # Each text of TEXT, with its values filled in, is part of a comment of the page,
    # so that a new text that no example shows fails here.
    for language in hooks.LANGUAGES:
        shown = all_texts(language)
        for key, template in assistant.TEXT[language].items():
            for fragment in re.split(r"\{[a-z]+\}", template):
                assert fragment.strip() in shown, (language, key, fragment)


def test_the_comments_page_shows_every_kind_of_comment(monkeypatch):
    # Each function that writes a comment makes one example, in both languages.
    writers = [
        name
        for name, function in inspect.getmembers(assistant, inspect.isfunction)
        if name.startswith("render_")
        and name != "render_marker"
        and "render_marker(" in inspect.getsource(function)
    ]
    called: set[str] = set()
    for name in writers:
        original = getattr(assistant, name)

        def record(*args, _name=name, _original=original, **kwargs):
            called.add(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(assistant, name, record)
    shown = hooks.examples()
    assert called == set(writers) and len(writers) == 9
    assert [example.kind for example in shown] == list(hooks.KINDS)
    for example in shown:
        for language in hooks.LANGUAGES:
            text = example.texts[language]
            kind = {
                "duplicate": "analysis",
                "secret": "analysis",
                "personal": "analysis",
                "security": "analysis",
                "reopened-release": "reopened",
            }.get(example.kind, example.kind)
            assert text.startswith(f"<!-- issue-assistant:{kind}"), example.kind
            if "lang=" in text.splitlines()[0]:
                assert f"lang={language}" in text.splitlines()[0]
    german = {example.kind: example.texts["de"] for example in shown}
    assert "Danke für deine Meldung" in german["analysis"]
    assert "Zeile 52" in german["analysis"] and "line 52" not in german["analysis"]
    assert "2. November 2026" in german["reminder"]
    # The AI never repeats a secret, and a possible vulnerability gets no analysis.
    english = {example.kind: example.texts["en"] for example in shown}
    assert "[!WARNING]" in english["secret"] and "[!NOTE]" in english["personal"]
    assert (
        "### Summary" not in english["security"] and "[!CAUTION]" in english["security"]
    )


def test_the_hook_loads_the_assistant_of_the_commit_it_builds(monkeypatch):
    # MkDocs runs the hook without the import of the tests: it loads
    # issue_assistant.py itself, under the name that its dataclasses look up.
    monkeypatch.delitem(sys.modules, "issue_assistant")
    loaded = hooks.assistant()
    assert loaded is not assistant and sys.modules["issue_assistant"] is loaded
    assert Path(loaded.__file__) == hooks.ROOT / "issue_assistant.py"
    assert loaded.TEXT == assistant.TEXT
    # And loads it once.
    assert hooks.assistant() is loaded


def test_a_reference_that_the_demo_project_lacks_fails_the_build(monkeypatch):
    monkeypatch.setattr(assistant, "reference_link", lambda *_: "")
    with pytest.raises(ValueError, match="the demo project has nothing for"):
        hooks.examples()


def test_the_comments_page_takes_its_timing_rules_from_the_assistant(monkeypatch):
    when = {example.kind: example.when for example in hooks.examples()}
    assert when["reminder"].startswith("15 days after a question")
    assert when["closed-unanswered"].startswith("30 days after the question")
    assert "closes 3 days later" in when["duplicate"]
    assert when["closed-duplicate"].startswith("3 days after the notice")
    assert "at most twice," in when["follow-up"]
    assert "within 30 days of the release" in when["reopened-release"]
    # Other rules change the page, with no text of its own to keep current.
    monkeypatch.setattr(assistant, "REMIND_AFTER", timedelta(days=10))
    monkeypatch.setattr(assistant, "CLOSE_AFTER", timedelta(days=20))
    monkeypatch.setattr(assistant, "DUPLICATE_GRACE", timedelta(days=5))
    monkeypatch.setattr(assistant, "REOPEN_AFTER_RELEASE", timedelta(days=60))
    for follow_ups, words in ((1, "once"), (3, "3 times")):
        monkeypatch.setattr(assistant, "MAX_FOLLOW_UPS", follow_ups)
        rules = hooks.timings(assistant)
        assert rules["follow_ups"] == words
    when = {kind: text.format(**rules) for kind, (_, text) in hooks.KINDS.items()}
    assert when["reminder"].startswith("10 days after a question")
    assert when["closed-unanswered"].startswith("20 days after the question")
    assert "closes 5 days later" in when["duplicate"]
    assert when["closed-duplicate"].startswith("5 days after the notice")
    assert "at most 3 times," in when["follow-up"]
    assert "within 60 days of the release" in when["reopened-release"]


def test_a_comment_looks_as_github_shows_it():
    text = (
        "<!-- issue-assistant:analysis lang=en questions=1 engine=claude -->\n\n"
        "Thanks for the report, @alex-demo!\n\n"
        "> [!WARNING]\n> Remove the token.\n\n"
        "### Related\n\n- #17: The same, see `#3` and `@not-a-mention`.\n"
        "- [`chart.py`, line 52](https://github.com/demo-org/plant-monitor/blob/x/"
        "chart.py#L52): returns early.\n\n"
        "#5 is a reference.\n\n# A heading\n"
    )
    seen: list[str] = []
    html = hooks.comment_html(text, lambda markdown: seen.append(markdown) or "")
    assert html.startswith('<div class="ia-comment">')
    (markdown,) = seen
    assert "issue-assistant:analysis" not in markdown
    # GitHub's alert as the admonition of the theme with the same look.
    assert '!!! warning "Warning"\n    Remove the token.\n' in markdown
    # A # without a blank after it starts no heading on GitHub, and one with does.
    assert "\n- \\#17: " in markdown and "\n\\#5 is a reference" in markdown
    assert "\n### Related\n" in markdown and markdown.endswith("\n# A heading\n")
    converted = hooks.comment_html(
        text,
        lambda markdown: (
            '<p>Thanks, @alex-demo! <a href="https://github.com/demo-org/plant-monitor/'
            'blob/x/chart.py#L52"><code>chart.py</code>, line 52</a> and #17, '
            "<code>#3 @not-a-mention</code>, a@b.example, "
            '<a href="https://example.com/#9">#9</a>.</p>'
        ),
    )
    assert '<span class="ia-mention">@alex-demo</span>' in converted
    assert '<span class="ia-link"><code>chart.py</code>, line 52</span>' in converted
    assert '<span class="ia-link">#17</span>' in converted
    assert "<code>#3 @not-a-mention</code>" in converted
    assert "a@b.example" in converted and 'href="https://example.com/#9"' in converted
    # The picture of the app opens no lightbox, which would be a link without text.
    assert f'class="ia-comment__avatar {hooks.NO_LIGHTBOX}"' in converted
    assert 'alt=""' in converted


def test_every_alert_of_github_becomes_an_admonition():
    for kind, admonition in hooks.ALERTS.items():
        text = f"Before.\n\n> [!{kind}]\n> One line.\n> Another line.\n\nAfter.\n"
        assert hooks.admonitions(text) == (
            f'Before.\n\n!!! {admonition} "{kind.title()}"\n    One line.\n'
            "    Another line.\n\nAfter.\n"
        )
    # An alert at the end of the text, without a line break after it.
    assert hooks.admonitions("> [!NOTE]\n> Last.") == '!!! note "Note"\n    Last.\n'
    # One that GitHub may add later looks like a note.
    assert hooks.admonitions("> [!LATER]\n> Text.\n").startswith('!!! note "Later"')


def test_the_comments_take_the_place_that_the_page_keeps_for_them():
    page = (ROOT / "docs" / "comments.md").read_text("utf-8")
    hooks.GALLERY.clear()
    markdown = hooks.with_gallery(page, fake_html)
    # On GitHub, the page says where the comments are; on the website, they are there.
    assert "The comments themselves are on the" in page
    assert "The comments themselves are on the" not in markdown
    assert markdown.startswith(page[: page.index("<!-- comments of")])
    for kind in hooks.KINDS:
        assert f"\n## {hooks.KINDS[kind][0]}\n" in markdown
        for language, name in hooks.LANGUAGES.items():
            key = hooks.placeholder(kind, language)
            assert f'=== "{name}"\n\n    {key}\n' in markdown
            assert hooks.GALLERY[key].startswith('<div class="ia-comment">')
    assert "| `bug` | Thanks for the report, @alex-demo! |" in markdown
    assert markdown.rstrip().endswith(
        "| any other kind | Thanks for opening this issue, @alex-demo! | "
        "Danke für dein Issue, @alex-demo! |"
    )
    # The HTML of the page gets the HTML of each comment for its placeholder.
    key = hooks.placeholder("fixed", "de")
    assert hooks.with_comments(f"<h2>Fixed</h2>\n<p>{key}</p>") == (
        f"<h2>Fixed</h2>\n{hooks.GALLERY[key]}"
    )
    assert "Behoben durch" in hooks.GALLERY[key]
    # A page without the place stays as it is.
    assert hooks.with_gallery("# Other\n", fake_html) == "# Other\n"
    assert hooks.with_comments("<p>ia-comment</p>") == "<p>ia-comment</p>"


def test_the_markdown_of_a_comment_becomes_the_html_that_github_shows():
    # With Python-Markdown and the extensions of the website, as MkDocs builds it.
    (analysis,) = [
        example for example in hooks.examples() if example.kind == "analysis"
    ]
    html = hooks.comment_html(analysis.texts["en"], hooks.markdown_html)
    assert "<h3>Summary</h3>" in html and "<h1>" not in html
    assert '<li><span class="ia-link">#17</span>: The same symptom' in html
    assert "<sub>🤖 Automated first analysis by Claude" in html


def test_every_input_and_output_of_the_action_is_in_the_reference():
    action = yaml.safe_load((ROOT / "action.yml").read_text("utf-8"))
    page = (ROOT / "docs" / "reference.md").read_text("utf-8")
    markdown = hooks.action_tables(page, action)
    assert "<!-- action.yml" not in markdown
    rows = re.findall(r"^\| `([\w-]+)` \|(.*)\|$", markdown, re.MULTILINE)
    named = [name for name, _ in rows]
    assert named == [*action["inputs"], *action["outputs"]]
    # The inputs come first; the outputs have names of inputs, such as issue, too.
    cells = dict(rows[: len(action["inputs"])])
    assert cells["command"].split("|")[0].strip() == "yes"
    assert cells["dry-run"].split("|")[1].strip() == "`false`"
    assert cells["issue"].split("|")[1].strip() == "none"
    assert "`${{ github.token }}`" in cells["github-token"]
    # The bars of a description would split its cell.
    assert hooks.cell("a | b\n c") == "a \\| b c"
    # Only a page that asks for the tables reads action.yml.
    assert hooks.action_tables_of("# Other\n") == "# Other\n"
    assert hooks.action_tables_of(page) == markdown


def reference_table(heading: str) -> dict[str, dict[str, str]]:
    """The rows of the table under a heading of the configuration, by their key."""
    text = (ROOT / "docs" / "configuration.md").read_text("utf-8")
    part = text.split(f"\n### {heading}\n", 1)[1].split("\n### ", 1)[0]
    lines = [line for line in part.splitlines() if line.startswith("| ")]
    columns = [cell.strip() for cell in lines[0].strip("|").split("|")]
    rows = {}
    for line in lines[2:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        row = dict(zip(columns, cells, strict=True))
        rows[row["Key"].strip("`")] = row
    return rows


def default(cell: str) -> object:
    """The value of a default written as TOML, such as `["Other"]`."""
    (literal,) = re.findall(r"^`([^`]*)`$", cell)
    return tomllib.loads(f"value = {literal}")["value"]


def test_the_reference_of_the_labels_and_the_findings_has_every_key():
    for heading, kind in (
        ("labels.toml", assistant.Label),
        ("findings.toml", assistant.Acceptance),
    ):
        rows = reference_table(heading)
        fields = {field.name: field for field in dataclasses.fields(kind)}
        assert list(rows) == list(fields), heading
        for key, row in rows.items():
            if fields[key].default is dataclasses.MISSING:
                assert row["Default"] == "required", key
            else:
                expected = fields[key].default
                assert default(row["Default"]) == (
                    list(expected) if isinstance(expected, tuple) else expected
                ), key
    groups = reference_table("labels.toml")["group"]
    assert all(f"`{group}`" in groups["What it does"] for group in assistant.GROUPS)
    reasons = reference_table("findings.toml")["reason"]["What `check` makes sure of"]
    assert all(f"`{reason}`" in reasons for reason in assistant.DISMISS_REASONS)
    page = (ROOT / "docs" / "configuration.md").read_text("utf-8")
    assert all(f"`{label}`" in page for label in assistant.LIFECYCLE)


# The keys of config.toml, as the reference names them, and what they set.
SETTINGS = {
    "[engine] name": ("engine", "other"),
    "[engine] model": ("model", "sonnet"),
    "[engine] max_per_day": ("max_per_day", 5),
    "[engine] max_per_author_per_day": ("max_per_author_per_day", 1),
    "[engine] release_replies": ("release_replies", 0),
    "[links] hosts": ("hosts", ["docs.example.com"]),
    "[forms] area_field": ("area_field", "Component"),
    "[forms] unmapped_options": ("unmapped_options", ["None"]),
    "[transparency] files": ("notices", ["README.md"]),
    "[releases] close_with_release": ("releases", False),
}


def plain(value: object) -> object:
    """A value of the settings as TOML writes it."""
    if isinstance(value, frozenset):
        return sorted(value)
    return list(value) if isinstance(value, tuple) else value


def test_the_reference_of_config_toml_has_every_key_with_its_default(root: Path):
    rows = reference_table("config.toml")
    assert list(rows) == list(SETTINGS)
    settings = root / ".github" / "issue-assistant" / "config.toml"
    settings.unlink()
    defaults = assistant.load_config(root, "owner/name")
    for key, row in rows.items():
        assert default(row["Default"]) == plain(getattr(defaults, SETTINGS[key][0]))
    # Each key changes what the assistant reads.
    sections: dict[str, dict[str, object]] = {}
    for key, (_, value) in SETTINGS.items():
        section, name = re.fullmatch(r"\[(\w+)\] (\w+)", key).groups()  # type: ignore[union-attr]
        sections.setdefault(section, {})[name] = value

    def toml(value: object) -> str:
        if isinstance(value, bool):
            return str(value).lower()
        if isinstance(value, int):
            return str(value)
        if isinstance(value, list):
            return "[" + ", ".join(f'"{item}"' for item in value) + "]"
        return f'"{value}"'

    settings.write_text(
        "".join(
            f"[{section}]\n"
            + "".join(f"{name} = {toml(value)}\n" for name, value in values.items())
            for section, values in sections.items()
        ),
        "utf-8",
    )
    loaded = assistant.load_config(root, "owner/name")
    for key, (attribute, value) in SETTINGS.items():
        assert plain(getattr(loaded, attribute)) == value, key
    # And the assistant knows no key that the reference leaves out.
    assert {
        f"[{section}] {name}": (key.attribute, key.kind)
        for section, keys in assistant.CONFIG_KEYS.items()
        for name, key in keys.items()
    } == {
        key: (attribute, list if isinstance(value, list) else type(value))
        for key, (attribute, value) in SETTINGS.items()
    }


def test_the_documentation_names_the_engines_and_their_models_of_the_assistant():
    # An engine or its default model changes in ENGINES alone; the reference and
    # the README must follow, or name what the assistant no longer runs.
    rows = reference_table("config.toml")
    readme = (ROOT / "README.md").read_text("utf-8")
    engines = assistant.ENGINES
    known = rows["[engine] name"]["What `check` makes sure of"]
    assert re.findall(r"`([^`]+)`", known.split(":", 1)[1]) == sorted(engines)
    for engine in engines.values():
        default = f"`{engine.model}` for {engine.name}"
        assert default in rows["[engine] model"]["What it does"], engine
        assert default in readme, engine
        disclosed = rows["[transparency] files"]["What `check` makes sure of"]
        assert f"{engine.name} and {engine.vendor}" in disclosed, engine
    model = engines[assistant.DEFAULT_ENGINE].model
    assert f'# model = "{model}"' in readme


def test_the_starters_have_only_keys_of_the_reference():
    starter = tomllib.loads(
        (ROOT / "templates/.github/issue-assistant/config.toml").read_text("utf-8")
    )
    documented = set(SETTINGS)
    for section, values in starter.items():
        for name in values:
            assert f"[{section}] {name}" in documented
    labels = reference_table("labels.toml")
    for label in tomllib.loads(
        (ROOT / "templates/.github/labels.toml").read_text("utf-8")
    )["label"]:
        assert set(label) <= set(labels)
    accepted = reference_table("findings.toml")
    for item in tomllib.loads(
        (ROOT / "templates/.github/findings.toml").read_text("utf-8")
    ).get("accept", []):
        assert set(item) <= set(accepted)


# How the website presents itself: a description of its own for each page, the title
# of the home page and its preview card, and the colours of the brand.


def heading(anchor: str, *children: SimpleNamespace) -> SimpleNamespace:
    """A heading of the table of contents of a page, as MkDocs makes it."""
    return SimpleNamespace(id=anchor, children=list(children))


def page_of(source: str, toc: list[SimpleNamespace]) -> SimpleNamespace:
    url = source.removesuffix(".md") + ".html"
    return SimpleNamespace(
        is_homepage=False,
        meta={},
        file=SimpleNamespace(src_uri=source, url=url),
        toc=toc,
    )


def test_an_anchor_of_the_former_guide_leads_to_the_page_that_has_it(monkeypatch):
    # The guide was one page: a link to guide.html#settings leads to the settings
    # on the page Configuration now.
    monkeypatch.setattr(hooks, "ANCHORS", {})
    for source, toc in {
        "configuration.md": [heading("settings", heading("engine")), heading("guide")],
        # Earlier in the former guide, so its anchor wins.
        "how-it-works.md": [heading("how-it-works"), heading("engine")],
        # Never part of the former guide.
        "security.md": [heading("threats")],
    }.items():
        hooks.on_page_content("", page_of(source, toc), {"docs_dir": "docs"}, None)
    assert set(hooks.ANCHORS) == {"configuration.md", "how-it-works.md"}
    guide = page_of("guide.md", [heading("guide")])
    output = hooks.on_post_page("<body><p>The guide</p></body>", guide, None)
    data = re.search(r"<script>\(function\(\)\{var targets=(\{[^}]*\}),", output)
    assert data and output.endswith("</script></body>")
    # The anchors of the overview itself stay where they are.
    assert json.loads(data[1]) == {
        "engine": "how-it-works.html",
        "how-it-works": "how-it-works.html",
        "settings": "configuration.html",
    }
    assert "location.replace(targets[anchor]+location.hash)" in output
    # The paths are relative to the overview.
    assert '"settings":"../configuration.html"' in hooks.forwarding("a/guide.html", [])
    # Other pages get no script, and nor does the guide without anchors to forward.
    faq = page_of("faq.md", [heading("faq")])
    assert hooks.on_post_page("<body></body>", faq, None) == "<body></body>"
    hooks.ANCHORS.clear()
    assert hooks.on_post_page("<body></body>", guide, None) == "<body></body>"


def test_the_former_guide_is_on_the_pages_that_forward_its_anchors():
    # Each part of the README that the guide had is on one of those pages.
    for page in hooks.FORMER_GUIDE:
        assert (ROOT / "docs" / page).is_file()
    assert set(hooks.FORMER_GUIDE) < set(menu().values())
    assert menu()["Overview"] == hooks.GUIDE


def test_every_page_has_a_description_of_its_own():
    descriptions: dict[str, str] = {}
    for path in sorted((ROOT / "docs").rglob("*.md")):
        meta, markdown = page_meta(path)
        hooks.described(markdown, meta)
        page = path.relative_to(ROOT / "docs").as_posix()
        assert meta.get("description"), f"{page} has no description"
        descriptions[page] = meta["description"]
    site = yaml.safe_load((ROOT / "mkdocs.yml").read_text("utf-8"))["site_description"]
    for page, description in descriptions.items():
        # What search engines show of it, without the description of the website.
        assert 50 <= len(description) <= hooks.DESCRIPTION, (page, description)
        assert description != site
    repeated = {
        d for d in descriptions.values() if list(descriptions.values()).count(d) > 1
    }
    assert not repeated, repeated
    # A record of a decision is described by its context.
    assert descriptions["decisions/0002-the-ai-only-reads.md"].startswith(
        "Issues come from anyone"
    )


def test_every_page_has_a_title_that_names_it_alone():
    # The tab of the browser and the card of a shared link take the title of the
    # front matter, else the one of the menu, where "Overview" names no page.
    vague = {"Home", "Index", "Introduction", "Overview"}
    for title, page in menu().items():
        meta, _ = page_meta(ROOT / "docs" / page)
        assert meta.get("title", title) not in vague, (page, title)
    assert page_meta(ROOT / "docs" / "guide.md")[0]["title"] == "Guide"


def test_a_description_comes_from_the_first_paragraph_of_prose():
    markdown = (
        "# Title\n\n[← README](https://example.com) · [Other](other.md)\n\n"
        '- Status: accepted\n\n1. A list.\n\n| A | table |\n\n--8<-- "README.md:x"\n\n'
        "The **first** paragraph, with [a link](a.md) and `code`.\nIts second line. "
        "Another sentence that makes it long enough to need cutting somewhere along "
        "the way, since descriptions are short. And one more.\n\nThe second paragraph."
    )
    assert hooks.lead(markdown) == (
        "The first paragraph, with a link and code. Its second line. Another sentence "
        "that makes it long enough to need cutting somewhere along the way, since "
        "descriptions are short. And one more."
    )
    # Whole sentences, as many as fit, when they say enough.
    sentence = "A sentence of forty characters, or so. "
    assert len(sentence) == 39
    assert hooks.shortened("Short. And short.") == "Short. And short."
    assert hooks.shortened(sentence * 5) == (sentence * 4).strip()
    # Too little before the next sentence, or one sentence that is too long: the
    # words up to the limit.
    for text in ("Too little. " + "word, " * 40, "word, " * 40):
        cut = hooks.shortened(text)
        assert len(cut) <= hooks.DESCRIPTION and cut.endswith("word…"), cut
        assert len(cut) > hooks.DESCRIPTION - 10
    # A first word that is longer than the limit, such as an address, and words
    # that all lead to the next one: the characters up to the limit.
    address = "https://example.com/" + "a" * 200 + " and more."
    assert hooks.shortened(address) == address[: hooks.DESCRIPTION - 1] + "…"
    leading = "And the " * 30
    assert hooks.shortened(leading) == leading[: hooks.DESCRIPTION - 1].rstrip() + "…"
    # Never after a word that leads to the next one.
    assert hooks.shortened("Brief. " + "much more and " * 20) == (
        "Brief. " + "much more and " * 10 + "much more…"
    )
    assert hooks.lead("# Only a heading\n") == ""
    meta: dict[str, str] = {}
    hooks.described("# Only a heading\n", meta)
    assert meta == {}
    meta = {"description": "Its own."}
    hooks.described(markdown, meta)
    assert meta == {"description": "Its own."}


def test_the_hook_describes_each_page_and_fills_in_what_it_asks_for():
    page = SimpleNamespace(meta={})
    markdown = (
        "# Reference\n\nThe commands and the action.\n\n<!-- action.yml: outputs -->\n"
    )
    result = hooks.on_page_markdown(markdown, page, None, None)
    assert page.meta == {"description": "The commands and the action."}
    assert "| `prompt` | The prompt for the engine |" in result


def test_the_home_page_has_a_title_with_its_words_and_a_card_with_its_name():
    meta, _ = page_meta(ROOT / "docs" / "index.md")
    website = yaml.safe_load((ROOT / "mkdocs.yml").read_text("utf-8"))
    name = website["site_name"]
    assert meta["title"].startswith(f"{name}: ") and "triage" in meta["title"]
    assert meta["social"]["cards_layout_options"]["title"] == name
    assert "triage" in meta["description"] and len(meta["description"]) <= 160
    # The theme adds the name of the website to the title, which names it already.
    output = f"<head><title>{meta['title']} - {name}</title></head><header></header>"
    home = SimpleNamespace(
        is_homepage=True, meta=meta, file=SimpleNamespace(src_uri="index.md")
    )
    assert hooks.on_post_page(output, home, website).startswith(
        f"<head><title>{meta['title']}</title></head><header></header>"
    )
    other = SimpleNamespace(
        is_homepage=False, meta={"title": "Other"}, file=SimpleNamespace(src_uri="o.md")
    )
    assert "<title>Other - Issue assistant</title>" in hooks.on_post_page(
        "<title>Other - Issue assistant</title>", other, website
    )
    # The buttons lead to the set-up, to a real analysis and to the latest release.
    text = (ROOT / "docs" / "index.md").read_text("utf-8")
    buttons = re.findall(r"^\[([^\]]+)\]\(([^)]+)\)\{ \.md-button", text, re.MULTILINE)
    assert [label for label, _ in buttons] == [
        "Get started",
        "See it live",
        "Latest release",
    ]
    assert buttons[0][1] == "getting-started.md"
    assert buttons[1][1] in (ROOT / "README.md").read_text("utf-8")


def colours(css: str, selector: str) -> dict[str, str]:
    """The custom properties that a rule of the stylesheet sets."""
    block = css.split(selector + " {", 1)[1].split("}", 1)[0]
    return dict(re.findall(r"(--[\w-]+): (#[0-9a-f]{6,8});", block))


def luminance(colour: str) -> float:
    channels = [int(colour[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(foreground: str, background: str) -> float:
    """The contrast of two colours as WCAG defines it, with the alpha of the
    foreground, such as that of the paler text of the tabs, mixed in."""
    if len(foreground) == 9:
        alpha = int(foreground[7:], 16) / 255
        mixed = [
            round(
                int(foreground[i : i + 2], 16) * alpha
                + int(background[i : i + 2], 16) * (1 - alpha)
            )
            for i in (1, 3, 5)
        ]
        foreground = "#" + "".join(f"{value:02x}" for value in mixed)
    lighter, darker = sorted(
        (luminance(foreground), luminance(background)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


# The background of the dark theme of Material, as the browser shows it, and that of
# a code block in each theme.
SLATE = "#1e2129"
CODE = "#f5f5f5"
SLATE_CODE = "#272a35"
# The colour of the text of the light theme.
TEXT = "#212121"


def test_the_colours_of_the_brand_are_readable():
    css = (ROOT / "docs" / "stylesheets" / "brand.css").read_text("utf-8")
    light = colours(css, ":root > *")
    dark = colours(
        css, '[data-md-color-scheme="slate"][data-md-color-primary="custom"]'
    )
    accent = colours(
        css, '[data-md-color-scheme="slate"][data-md-color-accent="custom"]'
    )
    primary = light["--md-primary-fg-color"]
    # The header, its tabs and the filled buttons: white, and paler white, on violet.
    assert contrast(light["--md-primary-bg-color"], primary) >= 7
    assert contrast(light["--md-primary-bg-color--light"], primary) >= 4.5
    # Links, the violet itself, on white; under the pointer, the accent.
    assert contrast(primary, "#ffffff") >= 7
    assert contrast(light["--md-accent-fg-color"], "#ffffff") >= 4.5
    assert contrast(light["--md-accent-bg-color"], light["--md-accent-fg-color"]) >= 4.5
    # Links and buttons without a fill on the dark theme, and under the pointer.
    assert contrast(dark["--md-typeset-a-color"], SLATE) >= 4.5
    assert contrast(accent["--md-accent-fg-color"], SLATE) >= 4.5
    assert (
        contrast(accent["--md-accent-bg-color"], accent["--md-accent-fg-color"]) >= 4.5
    )
    assert ".md-button:not(.md-button--primary, :hover, :focus)" in css
    # Links in the text are underlined, since their colour alone sets them apart
    # from the text around them by less than the 3:1 of WCAG.
    assert contrast(primary, TEXT) < 3
    assert re.search(r"\) a:not\([^)]*\) \{\n  text-decoration: underline;", css)
    # The greys of code in the light theme and its violet in the dark one.
    code = colours(css, '[data-md-color-scheme="default"]')
    assert len(code) == 5
    assert all(contrast(colour, CODE) >= 4.5 for colour in code.values())
    dark_code = colours(css, '[data-md-color-scheme="slate"]')
    assert len(dark_code) == 2
    assert all(contrast(colour, SLATE_CODE) >= 4.5 for colour in dark_code.values())
    # The cards of shared links have the violet, with white text.
    website = yaml.safe_load((ROOT / "mkdocs.yml").read_text("utf-8"))
    card = website["plugins"]["social"]["cards_layout_options"]
    assert card["background_color"] == primary
    assert contrast(card["color"], card["background_color"]) >= 7


def test_the_website_wears_the_brand():
    website = yaml.safe_load((ROOT / "mkdocs.yml").read_text("utf-8"))
    theme = website["theme"]
    assert theme["logo"] == theme["favicon"] == "assets/logo.svg"
    logo = (ROOT / "docs" / theme["logo"]).read_text("utf-8")
    # The speech bubble of the social preview.
    bubble = re.search(r'<path d="(M18 14h60[^"]+)"', logo)
    assert bubble and bubble[1] in (ROOT / ".github" / "social-preview.html").read_text(
        "utf-8"
    )
    # The palette is that of the blueprint, with the colours of the brand.
    blueprint = (ROOT / ".github" / "mkdocs-blueprint.yml").read_text("utf-8")
    # Read as text: the file has tags of Python that a safe loader refuses.
    block = re.search(r"^  palette:\n((?:    .+\n)+)", blueprint, re.MULTILINE)
    assert block
    inherited = yaml.safe_load(textwrap.dedent(block[1]))
    assert [
        {
            key: value
            for key, value in scheme.items()
            if key not in {"primary", "accent"}
        }
        for scheme in theme["palette"]
    ] == inherited
    assert all(
        scheme["primary"] == scheme["accent"] == "custom" for scheme in theme["palette"]
    )
    assert website["extra_css"][0] == "stylesheets/brand.css"
    assert all((ROOT / "docs" / sheet).is_file() for sheet in website["extra_css"])


def test_the_hidden_copy_of_a_picture_has_no_link():
    # The hook puts a copy of a picture on the page for each theme, and the theme
    # hides the other one; the link of the lightbox around it goes with it, so that
    # a keyboard never stops on a link with nothing to see or read.
    html = hooks.localized(
        f'<picture><source media="{hooks.DARK}" srcset="d.webp"><img src="l.webp" '
        'alt="A" width="1" height="1"></picture>',
        "page.html",
        ROOT / "docs",
    )
    assert 'src="l.webp#only-light"' in html and 'src="d.webp#only-dark"' in html
    css = (ROOT / "docs" / "stylesheets" / "pictures.css").read_text("utf-8")
    for scheme, hidden in (("default", "dark"), ("slate", "light")):
        assert (
            f'[data-md-color-scheme="{scheme}"] .md-typeset '
            f'.glightbox:has(> img[src$="#only-{hidden}"])'
        ) in css
    assert "display: none;" in css
    website = yaml.safe_load((ROOT / "mkdocs.yml").read_text("utf-8"))
    assert "stylesheets/pictures.css" in website["extra_css"]
