"""Render the pictures of the documentation in docs/images.

pages.py builds the page of each picture, from the demo project and the texts of
the package issue_assistant itself; this script takes the pictures of them in a browser.
Each picture comes light and dark, for the theme of the reader, as WebP: the still
ones lossless, as sharp as a PNG at less than a third of its size. The animation of
the life of an issue also comes upright for phones, and each of the two as a still
picture for readers who asked for less motion. Last, it writes
scripts/images/manifest.json, which test_the_pictures_show_what_the_assistant_posts
compares with the pages of the commit.

scripts/render-images.sh runs this in the browser image of the social preview; run
it after a change of the texts of the assistant or of the pictures, as that test
asks.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from glob import glob

import pages
from PIL import Image
from playwright.sync_api import Browser, Page, sync_playwright

OUTPUT = pages.ROOT / "docs" / "images"
THEMES = ("light", "dark")
# The headless Chromium of the image, whatever version of Playwright drives it.
BROWSER = "/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"
# The page of the animation, which HEROES takes in each of its layouts.
HERO = "hero"

# Each frame: the step, where the timeline scrolls to, and how long it stays.
SEQUENCE = (
    (1, "top", 2600),
    (2, "new", 2600),
    (3, "new", 3600),
    (3, "bottom", 3800),
    (4, "new", 3400),
    (5, "new", 3800),
    (6, "new", 5200),
)
# New entries fade in over these frames, each with its opacity and how long it stays.
FADE = ((0.3, 110), (0.65, 110))
# The still pictures have twice the pixels of their size on the page.
SCALE = 2
# Each layout of the animation: its name, the width of the page that it is taken on,
# and its scale, half again the pixels of its size on the page. On the narrow page,
# style.css stands it upright for phones.
HEROES = (("hero", 1000, 1.5), ("hero-mobile", 430, 1.5))
# The frame of the animation that its still picture shows: the first analysis.
POSTER = (3, "new")


# Taking the pictures.


def capture(page: Page) -> Image.Image:
    shot = page.locator(".shot").screenshot(omit_background=True, animations="disabled")
    return Image.open(io.BytesIO(shot)).convert("RGBA")


def load(page: Page, source: str) -> None:
    page.set_content(source, wait_until="networkidle")
    page.evaluate("document.fonts.ready.then(() => true)")


def save(picture: Image.Image, name: str, theme: str) -> None:
    """A still picture, as lossless WebP with the strongest compression."""
    path = OUTPUT / f"{name}-{theme}.webp"
    picture.save(path, lossless=True, quality=100, method=6)
    print(
        f"rendered {path.relative_to(pages.ROOT)} ({path.stat().st_size // 1024} KiB)"
    )


def still(page: Page, source: str, name: str, theme: str) -> None:
    load(page, source)
    save(capture(page), name, theme)


def offset(page: Page, step: int, mode: str) -> float:
    """How far the timeline of the animation scrolls for a step."""
    pixels: float = page.evaluate("([step, mode]) => target(step, mode)", [step, mode])
    return pixels


def show(page: Page, step: int, progress: float, pixels: float) -> None:
    page.evaluate(
        "([step, progress, offset]) => show(step, progress, offset)",
        [step, progress, pixels],
    )


def frames(page: Page) -> Iterator[tuple[Image.Image, int]]:
    """The frames of the animation and how long each one stays."""
    offsets = [offset(page, step, mode) for step, mode, _ in SEQUENCE]
    for index, ((step, _, hold), pixels) in enumerate(
        zip(SEQUENCE, offsets, strict=True)
    ):
        if index and step != SEQUENCE[index - 1][0]:
            for opacity, pause in FADE:
                show(page, step, opacity, pixels)
                yield capture(page), pause
        show(page, step, 1, pixels)
        yield capture(page), hold


def animation(page: Page, source: str, name: str, theme: str) -> None:
    """The animation, and its still picture for readers who asked for less motion."""
    load(page, source)
    taken, durations = zip(*frames(page), strict=True)
    # Every frame gets the size of the largest one, on a transparent background.
    size = (max(frame.width for frame in taken), max(frame.height for frame in taken))
    pictures = []
    for frame in taken:
        padded = Image.new("RGBA", size)
        padded.paste(frame, (0, 0))
        pictures.append(padded)
    path = OUTPUT / f"{name}-{theme}.webp"
    pictures[0].save(
        path,
        save_all=True,
        append_images=pictures[1:],
        duration=list(durations),
        loop=0,
        quality=85,
        method=6,
        minimize_size=True,
    )
    print(
        f"rendered {path.relative_to(pages.ROOT)} "
        f"({path.stat().st_size // 1024} KiB, {len(pictures)} frames)"
    )
    step, mode = POSTER
    show(page, step, 1, offset(page, step, mode))
    frame = Image.new("RGBA", size)
    frame.paste(capture(page), (0, 0))
    save(frame, f"{name}-still", theme)


def new_page(browser: Browser, width: int, scale: float, theme: str) -> Page:
    return browser.new_page(
        viewport={"width": width, "height": 1400},
        device_scale_factor=scale,
        color_scheme=theme,  # type: ignore[arg-type]
    )


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    sources = pages.sources()
    stills = {name: source for name, source in sources.items() if name != HERO}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=glob(BROWSER)[0])
        for theme in THEMES:
            page = new_page(browser, 1000, SCALE, theme)
            for name, source in stills.items():
                still(page, source, name, theme)
            page.close()
            for name, width, scale in HEROES:
                page = new_page(browser, width, scale, theme)
                animation(page, sources[HERO], name, theme)
                page.close()
        browser.close()
    # Only once every picture is taken: what they were rendered from.
    pages.MANIFEST.write_text(
        pages.manifest_text(sources, pages.CAMERA.read_text("utf-8")), "utf-8"
    )
    print(f"wrote {pages.MANIFEST.relative_to(pages.ROOT)}")


if __name__ == "__main__":
    main()
