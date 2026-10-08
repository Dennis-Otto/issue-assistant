"""The pictures of the documentation and the hooks of its website."""

import importlib.util
import re
from pathlib import Path
from types import SimpleNamespace

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

_spec = importlib.util.spec_from_file_location(
    "mkdocs_hooks", ROOT / "scripts" / "mkdocs_hooks.py"
)
assert _spec and _spec.loader
hooks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hooks)


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


def test_every_picture_comes_light_and_dark_and_is_shown():
    shown = "\n".join(document.read_text("utf-8") for document in DOCUMENTS)
    for picture in IMAGES.iterdir():
        match = re.fullmatch(r"([a-z-]+)-(light|dark)\.(png|webp)", picture.name)
        assert match, f"{picture.name} isn't named name-light or name-dark"
        other = "dark" if match[2] == "light" else "light"
        assert (IMAGES / f"{match[1]}-{other}.{match[3]}").is_file()
        assert f"images/{picture.name}" in shown, f"no page shows {picture.name}"


def test_the_website_shows_its_own_copies_of_the_pictures():
    html = f'<img src="{RAW}docs/images/a-light.png" alt="A">'
    assert (
        hooks.localized(html, "guide.html") == '<img src="images/a-light.png" alt="A">'
    )
    assert (
        hooks.localized(html, "decisions/0001-record-decisions.html")
        == '<img src="../images/a-light.png" alt="A">'
    )


def test_other_addresses_stay_as_they_are():
    html = (
        f'<img src="{RAW}.github/social-preview.png" alt="A">'
        f'<a href="{RAW}docs/images/a-light.png">the picture</a>'
    )
    assert hooks.localized(html, "guide.html") == html


def test_the_switch_between_light_and_dark_chooses_the_picture():
    html = PICTURE.format(
        dark=f"{RAW}docs/images/a-dark.png", light=f"{RAW}docs/images/a-light.png"
    )
    assert hooks.localized(html, "index.html") == (
        '<img src="images/a-light.png#only-light" alt="A demo">'
        '<img src="images/a-dark.png#only-dark" alt="A demo">'
    )


def test_the_hook_changes_the_html_of_each_page():
    page = SimpleNamespace(file=SimpleNamespace(url="security.html"))
    html = PICTURE.format(dark="images/b-dark.png", light="images/b-light.png")
    assert hooks.on_page_content(html, page, None, None) == (
        '<img src="images/b-light.png#only-light" alt="A demo">'
        '<img src="images/b-dark.png#only-dark" alt="A demo">'
    )


def test_the_hook_knows_every_picture_of_the_documentation():
    for document in DOCUMENTS:
        text = document.read_text("utf-8")
        assert text.count("<picture>") == len(hooks.PICTURE.findall(text)), (
            document.name
        )
