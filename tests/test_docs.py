"""The pictures of the documentation, the hooks of its website, how the project
presents itself on GitHub and its Marketplace, and what its documentation says."""

import importlib.util
import re
import tomllib
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "docs" / "images"
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


def test_every_picture_is_a_webp_that_comes_light_and_dark_and_is_shown():
    shown = "\n".join(document.read_text("utf-8") for document in DOCUMENTS)
    for picture in IMAGES.iterdir():
        match = re.fullmatch(r"([a-z-]+)-(light|dark)\.webp", picture.name)
        assert match, f"{picture.name} isn't named name-light.webp or name-dark.webp"
        other = "dark" if match[2] == "light" else "light"
        assert (IMAGES / f"{match[1]}-{other}.webp").is_file()
        assert hooks.size(picture) == hooks.size(IMAGES / f"{match[1]}-{other}.webp")
        assert f"images/{picture.name}" in shown, f"no page shows {picture.name}"


def test_only_the_heroes_move_and_the_still_pictures_are_lossless():
    for picture in IMAGES.iterdir():
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


def test_diagrams_scroll_sideways_on_small_screens(docs: Path):
    diagram = '<pre class="mermaid"><code>flowchart TB\n  a --&gt; b</code></pre>'
    html = f"<p>Before</p>\n{diagram}\n<pre><code>code</code></pre>"
    assert hooks.localized(html, "architecture.html", docs) == (
        f'<p>Before</p>\n<div class="diagram" tabindex="0">{diagram}</div>\n'
        "<pre><code>code</code></pre>"
    )
    settings = yaml.safe_load((ROOT / "mkdocs.yml").read_text("utf-8"))
    assert "stylesheets/diagrams.css" in settings["extra_css"]
    style = (ROOT / "docs" / "stylesheets" / "diagrams.css").read_text("utf-8")
    assert ".diagram" in style and "min-width" in style and "overflow-x" in style


def test_the_hook_changes_the_html_of_each_page(docs: Path):
    page = SimpleNamespace(file=SimpleNamespace(url="security.html"))
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
        count = text.count("<picture>")
        assert count == len(hooks.PICTURE.findall(text)), document.name
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


def test_the_menu_leads_to_the_questions_and_the_comparison():
    nav = yaml.safe_load((ROOT / "mkdocs.yml").read_text("utf-8"))["nav"]
    pages = {title: page for item in nav for title, page in item.items()}
    assert pages["FAQ"] == "faq.md" and pages["Compare"] == "comparison.md"
    assert all((ROOT / "docs" / page).is_file() for page in pages.values())
    faq = (ROOT / "docs" / "faq.md").read_text("utf-8")
    assert '--8<-- "README.md:faq"' in faq
    assert '--8<-- "README.md:troubleshooting"' in faq


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
