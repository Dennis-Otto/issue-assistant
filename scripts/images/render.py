"""Render the pictures of the documentation in docs/images.

Every picture shows a demo project, demo-org/plant-monitor, with made-up people,
issues, pull requests and alerts. The texts of the assistant in them come from
issue_assistant.py itself: its commands run against the fake GitHub of the tests,
so that the pictures show what the assistant posts. Each picture comes light and
dark, for the theme of the reader.

scripts/render-images.sh runs this in the browser image of the social preview; run
it after a change of the texts of the assistant or of the pictures.
"""

from __future__ import annotations

import colorsys
import contextlib
import html
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from glob import glob
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt
from PIL import Image
from playwright.sync_api import Page, sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import issue_assistant as assistant  # noqa: E402
import support  # noqa: E402

OUTPUT = ROOT / "docs" / "images"
THEMES = ("light", "dark")
# The headless Chromium of the image, whatever version of Playwright drives it.
BROWSER = "/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"

# The demo project and its people.
REPOSITORY = "demo-org/plant-monitor"
REPORTER = "alex-demo"
MAINTAINER = "sam-demo"
NUMBER = 42
RELATED = 17
PULL = 43
PULL_TITLE = "Resubscribe the chart after a reconnect"
TAG = "v1.4.0"
SHA = "4f1c2b7d9e0a3c5b6d8e1f2a4b6c8d0e2f4a6b8c"
TITLE = "The moisture chart stays empty after the sensor reconnects"

AREAS = """
[[label]]
name = "area: dashboard"
color = "bfdadc"
description = "The dashboard and its charts"
group = "area"
form_options = ["Dashboard"]

[[label]]
name = "area: sensors"
color = "bfdadc"
description = "The sensors and their connection"
group = "area"
form_options = ["Sensors"]

[[label]]
name = "connectivity"
color = "5319e7"
description = "Wi-Fi, reconnects and sensors that go offline"
group = "topic"
"""

CHART = '''"""The moisture chart of the dashboard."""

from __future__ import annotations

from dataclasses import dataclass, field

from plant_monitor.hub import Hub, Stream


@dataclass
class Point:
    time: float
    moisture: float


@dataclass
class ChartFeed:
    """Feeds the chart with the history of one sensor."""

    hub: Hub
    sensor: str
    points: list[Point] = field(default_factory=list)
    subscribed: bool = False
    stream: Stream | None = None

    def history(self, hours: int = 24) -> list[Point]:
        return [point for point in self.points if point.time >= -hours * 3600]

    def add(self, time: float, moisture: float) -> None:
        self.points.append(Point(time, moisture))

    def clear(self) -> None:
        self.points.clear()

    def on_disconnect(self) -> None:
        # The stream ends with the connection; the flag stays as it was.
        self.stream = None

    def on_reconnect(self) -> None:
        self.start()

    def start(self) -> None:
        """Subscribe to the history stream of the sensor."""
        if self.subscribed:
            return
        self.stream = self.hub.subscribe(self.sensor, self.add)
        self.subscribed = True

    def stop(self) -> None:
        if self.stream:
            self.stream.close()
        self.subscribed = False
'''

TROUBLESHOOTING = """# Troubleshooting

## The chart stays empty

Collect the log of the hub with `log_level: debug` and attach it to the issue.
"""

FINDINGS = """[[accept]]
tool = "Scorecard"
rule = "CodeReviewID"
reason = "won't fix"
comment = "One maintainer: nobody else can approve a pull request."
"""

BODY = """### What happened?

After the sensor loses its Wi-Fi for a moment and reconnects, the moisture chart on the dashboard stays empty. The current value keeps updating.

### Area

Dashboard

### Version

1.3.2
"""

ANSWER_TEXT = """Thanks, that helps! Firmware 2.1.0. The log of the reconnect:

```text
12:04:31 sensor-1 disconnected
12:04:43 sensor-1 reconnected after 12 s
12:04:43 chart: already subscribed, skipping
```
"""

# The demo project as a git checkout: the assistant links only files that git tracks.
FILES = {
    ".github/labels.toml": (ROOT / "templates/.github/labels.toml").read_text("utf-8")
    + AREAS,
    ".github/issue-assistant/project.md": "Plant monitor, a demo project: sensors "
    "measure the moisture of plants, and a dashboard shows it.\n",
    ".github/issue-assistant/config.toml": '[engine]\nname = "claude"\n',
    ".github/findings.toml": FINDINGS,
    "src/plant_monitor/chart.py": CHART,
    "docs/troubleshooting.md": TROUBLESHOOTING,
}
EARLY_RETURN = CHART.splitlines().index("        if self.subscribed:") + 1

ANSWER = support.make_answer(
    kind="bug",
    areas=["area: dashboard"],
    topics=["connectivity"],
    label_rationale="The chart stops after the sensor reconnects.",
    summary="After the sensor reconnects, the moisture chart stays empty while the "
    "current value keeps updating.",
    analysis="The chart subscribes to the history stream once. After a reconnect, "
    "`ChartFeed.start()` returns early because `subscribed` is still true, so the "
    "chart never gets the new stream. The current value has a subscription of its "
    "own and recovers.",
    next_steps=[
        "Reload the dashboard after a reconnect; the chart fills again.",
        "Until a fix ships, set `chart.refresh` to `30s`.",
    ],
    missing_information=[
        "The firmware version of the sensor.",
        "The log of the hub from the reconnect, with `log_level: debug`.",
    ],
    related=[
        {
            "kind": "issue",
            "number": RELATED,
            "reason": "The same symptom after a restart of the hub, fixed in 1.2.0.",
        }
    ],
    references=[
        {
            "path": "src/plant_monitor/chart.py",
            "line": EARLY_RETURN,
            "reason": "Returns early while `subscribed` is still set.",
        },
        {
            "path": "docs/troubleshooting.md",
            "anchor": assistant.slug("The chart stays empty"),
            "reason": "How to collect the log.",
        },
    ],
)


def demo_repository(folder: Path) -> assistant.Config:
    for path, text in FILES.items():
        (folder / path).parent.mkdir(parents=True, exist_ok=True)
        (folder / path).write_text(text, "utf-8")
    for command in (
        ["init", "-q", "-b", "main"],
        ["add", "-A"],
        ["commit", "-q", "-m", "demo"],
    ):
        subprocess.run(
            ["git", "-c", "user.name=Demo", "-c", "user.email=demo@example.com"]
            + command,
            cwd=folder,
            check=True,
        )
    return assistant.load_config(folder, REPOSITORY)


def issues() -> list[dict[str, Any]]:
    """The issue of the pictures, after the event job labelled it, and an older one."""
    return [
        support.make_issue(
            NUMBER,
            author=REPORTER,
            labels=("bug", "area: dashboard", "needs-triage"),
            body=BODY,
            title=TITLE,
        ),
        support.make_issue(
            RELATED,
            author="kim-demo",
            labels=("bug",),
            state="closed",
            state_reason="completed",
            days_ago=90,
            title="The chart stays empty after a restart of the hub",
        ),
    ]


def run(
    root: Path, github: support.FakeGitHub, argv: list[str], **env: str
) -> tuple[str, str]:
    """A command of the assistant as its workflow runs it: its log and its summary."""
    original = assistant.GitHub
    saved = dict(os.environ)
    with tempfile.TemporaryDirectory() as folder:
        summary = Path(folder) / "summary"
        os.environ.update(
            GITHUB_STEP_SUMMARY=str(summary),
            GITHUB_OUTPUT=str(Path(folder) / "output"),
            GH_REPO=REPOSITORY,
            ISSUE_ASSISTANT_ROOT=str(root),
            **env,
        )
        assistant.GitHub = lambda repository, dry_run=False: github  # type: ignore[assignment,misc]
        log = io.StringIO()
        try:
            with contextlib.redirect_stdout(log):
                assistant.main(argv)
        finally:
            assistant.GitHub = original  # type: ignore[misc]
            os.environ.clear()
            os.environ.update(saved)
        return log.getvalue(), summary.read_text("utf-8")


def alert(number: int, tool: str, rule: str, path: str) -> dict[str, Any]:
    return support.make_alert(number, tool, rule, path) | {
        "html_url": f"https://github.com/{REPOSITORY}/security/code-scanning/{number}"
    }


def content(root: Path, config: assistant.Config) -> dict[str, Any]:
    """What the assistant writes in the pictures, from its own commands."""
    found: dict[str, Any] = {}
    github = support.FakeGitHub(config, *issues(), dry_run=True)
    with tempfile.TemporaryDirectory() as folder:
        event = Path(folder) / "event.json"
        event.write_text(json.dumps({"inputs": {"issue": str(NUMBER)}}), "utf-8")
        _, found["event"] = run(
            root,
            github,
            ["event"],
            DRY_RUN="true",
            GITHUB_EVENT_NAME="workflow_dispatch",
            GITHUB_EVENT_PATH=str(event),
        )
    _, found["apply"] = run(
        root,
        github,
        ["apply", "--issue", str(NUMBER), "--mode", "triage"],
        DRY_RUN="true",
        RESULT=json.dumps(ANSWER),
        GITHUB_SHA=SHA,
        GITHUB_EVENT_NAME="workflow_dispatch",
        ENGINE="claude",
    )
    found["analysis"] = github.previews[-1]
    found["fixed"] = assistant.render_fixed(PULL, "main", "en")
    release = {
        "tag_name": TAG,
        "html_url": f"https://github.com/{REPOSITORY}/releases/tag/{TAG}",
    }
    found["released"] = assistant.render_released(REPORTER, release, [PULL], "en")
    scanning = support.FakeGitHub(config)
    scanning.alert_store = [
        alert(3, "Scorecard", "CodeReviewID", "no file"),
        alert(7, "CodeQL", "py/path-injection", "src/plant_monitor/export.py"),
    ]
    log, found["findings"] = run(root, scanning, ["findings"])
    found["annotations"] = [
        line.removeprefix("::error::")
        for line in log.splitlines()
        if line.startswith("::error::")
    ]
    return found


# Markdown as GitHub shows it.

MARKDOWN = MarkdownIt("commonmark", {"html": True}).enable("table")
UNTOUCHED = re.compile(r"(<!--.*?-->|<pre>.*?</pre>|<code>.*?</code>|<a .*?</a>)", re.S)
MENTION = re.compile(r"(?<![\w/`])@([A-Za-z0-9][A-Za-z0-9-]*)")
REFERENCE = re.compile(r"(?<![\w&])#(\d+)\b")
ADDRESS = re.compile(r"\bhttps://[^\s<]+")


def github_text(rendered: str) -> str:
    """Mentions in bold and links for issue numbers and addresses, outside code."""
    pieces = UNTOUCHED.split(rendered)
    for index in range(0, len(pieces), 2):
        piece = ADDRESS.sub(r'<a href="\g<0>">\g<0></a>', pieces[index])
        piece = MENTION.sub(r'<span class="mention">@\1</span>', piece)
        pieces[index] = REFERENCE.sub(r'<span class="reference">#\1</span>', piece)
    return "".join(pieces)


def markdown(text: str) -> str:
    return f'<div class="markdown">{github_text(MARKDOWN.render(text))}</div>'


def inline(text: str) -> str:
    return github_text(MARKDOWN.renderInline(text))


# The parts of a page.

LOGO = (
    '<svg viewBox="0 0 96 96" aria-hidden="true"><path d="M18 14h60a10 10 0 0 1 10 '
    "10v36a10 10 0 0 1-10 10H46L28 84V70H18A10 10 0 0 1 8 60V24a10 10 0 0 1 10-10z"
    '" fill="#8b5cf6"/><circle cx="48" cy="42" r="15" fill="none" stroke="#fff" '
    'stroke-width="6"/><circle cx="48" cy="42" r="4" fill="#fff"/></svg>'
)
ICONS = {
    "open": '<circle cx="8" cy="8" r="6.25" fill="none" stroke="currentColor" '
    'stroke-width="1.5"/><circle cx="8" cy="8" r="1.6" fill="currentColor"/>',
    "closed": '<circle cx="8" cy="8" r="6.25" fill="none" stroke="currentColor" '
    'stroke-width="1.5"/><path d="M5.2 8.2l1.9 1.9 3.7-3.9" fill="none" '
    'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" '
    'stroke-linejoin="round"/>',
    "tag": '<path d="M2.5 2.5h5.2l5.8 5.8-5.2 5.2-5.8-5.8z" fill="none" '
    'stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>'
    '<circle cx="5.4" cy="5.4" r="1.1" fill="currentColor"/>',
    "merge": '<g fill="none" stroke="currentColor" stroke-width="1.5">'
    '<circle cx="4.5" cy="3.5" r="1.6"/><circle cx="4.5" cy="12.5" r="1.6"/>'
    '<circle cx="11.5" cy="9" r="1.6"/><path d="M4.5 5.1v5.8M4.5 5.6c0 2.3 2.2 '
    '3.4 5.4 3.4"/></g>',
    "milestone": '<path d="M7.25 1.5v13M3 3.5h7.5l2 2-2 2H3z" fill="none" '
    'stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>',
    "repo": '<path d="M3.5 13V3a1.5 1.5 0 0 1 1.5-1.5h7.5v10H5a1.5 1.5 0 0 0-1.5 '
    '1.5 1.5 1.5 0 0 0 1.5 1.5h7.5" fill="none" stroke="currentColor" '
    'stroke-width="1.4" stroke-linejoin="round"/>',
    "code": '<path d="M5.5 4L1.75 8l3.75 4M10.5 4l3.75 4-3.75 4" fill="none" '
    'stroke="currentColor" stroke-width="1.5" stroke-linecap="round" '
    'stroke-linejoin="round"/>',
    "actions": '<circle cx="8" cy="8" r="6.25" fill="none" stroke="currentColor" '
    'stroke-width="1.5"/><path d="M6.5 5.5v5l4-2.5z" fill="currentColor"/>',
    "success": '<circle cx="8" cy="8" r="7" fill="currentColor"/><path d="M5 8.2l2 '
    '2 4-4.2" fill="none" stroke="#fff" stroke-width="1.7" stroke-linecap="round" '
    'stroke-linejoin="round"/>',
    "failure": '<circle cx="8" cy="8" r="7" fill="currentColor"/><path d="M5.6 '
    '5.6l4.8 4.8M10.4 5.6l-4.8 4.8" stroke="#fff" stroke-width="1.7" '
    'stroke-linecap="round"/>',
    "home": '<path d="M2.5 7.5L8 2.5l5.5 5M4 6.5v7h8v-7" fill="none" '
    'stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>',
    "arrow": '<path d="M2 8h11M9 4l4 4-4 4" fill="none" stroke="currentColor" '
    'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
}


def icon(name: str, size: int = 16, css: str = "") -> str:
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 16 16" class="{css}" '
        f'aria-hidden="true">{ICONS[name]}</svg>'
    )


def label(name: str, config: assistant.Config) -> str:
    """A label as GitHub shows it: in its colour, toned down in the dark theme."""
    color = next(item.color for item in config.labels if item.name == name)
    red, green, blue = (int(color[index : index + 2], 16) for index in (0, 2, 4))
    lightness = (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255
    hue, light, saturation = colorsys.rgb_to_hls(red / 255, green / 255, blue / 255)
    text = colorsys.hls_to_rgb(hue, max(light, 0.72), min(saturation, 0.9))
    dark_text = "#" + "".join(f"{round(part * 255):02x}" for part in text)
    style = (
        f"--light-background: #{color}; "
        f"--light-text: {'#1f2328' if lightness > 0.55 else '#ffffff'}; "
        f"--dark-background: rgba({red}, {green}, {blue}, 0.18); "
        f"--dark-border: rgba({red}, {green}, {blue}, 0.35); "
        f"--dark-text: {dark_text}"
    )
    return f'<span class="label" style="{style}">{html.escape(name)}</span>'


def avatar(who: str) -> str:
    if who == "bot":
        return f'<div class="avatar bot">{LOGO}</div>'
    return f'<div class="avatar {who.split("-")[0]}">{who[0].upper()}</div>'


def comment(
    who: str,
    body: str,
    *,
    verb: str = "commented",
    when: str = "2 minutes ago",
    role: str = "",
) -> str:
    name = "github-actions" if who == "bot" else who
    badge = '<span class="bot-badge">bot</span>' if who == "bot" else ""
    note = f'<span class="role">{role}</span>' if role else ""
    own = " self" if who == REPORTER and verb == "opened" else ""
    return (
        f'{avatar(who)}<div class="comment{own}"><div class="comment-head">'
        f"<b>{name}</b>{badge}<span>{verb} {when}</span>{note}</div>"
        f'<div class="body">{body}</div></div>'
    )


def event(badge: str, text: str, css: str = "") -> str:
    return f'<span class="badge {css}">{icon(badge)}</span><span>{text}</span>'


def window(address: str, inner: str, width: int = 880) -> str:
    return (
        f'<div class="window" style="width: {width}px"><div class="chrome"><i></i>'
        f'<i></i><i></i><div class="address">github.com/<b>{address}</b></div>'
        f'<span class="demo">demo</span></div>{inner}</div>'
    )


def repository(tab: str) -> str:
    tabs = "".join(
        f'<div class="tab{" on" if name == tab else ""}">{icon(symbol)}{name}{count}</div>'
        for name, symbol, count in (
            ("Code", "code", ""),
            ("Issues", "open", '<span class="count">12</span>'),
            ("Pull requests", "merge", '<span class="count">2</span>'),
            ("Actions", "actions", ""),
        )
    )
    owner, name = REPOSITORY.split("/")
    return (
        f'<div class="repo"><div class="repo-name">{icon("repo")}<a>{owner}</a>'
        f'<span>/</span><strong>{name}</strong></div><div class="tabs">{tabs}</div></div>'
    )


def document(body: str, script: str = "") -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:'
        "wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;600&display=block"
        f'"><style>{(HERE / "style.css").read_text("utf-8")}</style></head>'
        f"<body>{body}<script>{script}</script></body></html>"
    )


# The pictures.

STEPS = (
    ("Opened", "A reporter opens an issue with the <em>bug form</em>."),
    ("Labeled", "The event job labels it from the form, <em>without AI</em>."),
    (
        "Analyzed",
        "An AI that <em>only reads</em> writes the first analysis; "
        "a job without AI checks and posts it.",
    ),
    ("Answered", "The reporter answers, and the issue <em>no longer waits</em>."),
    (
        "Fixed",
        "A merged pull request with <em>Fixes #42</em> marks it fixed in the next "
        "release.",
    ),
    ("Released", "The release <em>closes it</em>, with a link to the release."),
)
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
# The animation has half again the pixels of its size on the page, the still
# pictures twice as many.
SCALES = {"animation": 1.5, "still": 2}

HERO_SCRIPT = """
const timeline = document.getElementById('timeline');
const viewport = timeline.parentElement;
const captions = JSON.parse(document.getElementById('captions').textContent);
function within(range, step) {
  const [first, last = first] = range.split('-').map(Number);
  return step >= first && step <= last;
}
window.show = (step, progress, offset) => {
  for (const item of timeline.querySelectorAll('[data-step]')) {
    const at = Number(item.dataset.step);
    item.style.display = at <= step ? '' : 'none';
    item.style.opacity = at === step ? progress : 1;
    item.classList.toggle('fresh', at === step && step > 1);
  }
  for (const item of document.querySelectorAll('[data-steps]')) {
    item.style.display = within(item.dataset.steps, step) ? '' : 'none';
  }
  document.querySelectorAll('.step').forEach((item, index) => {
    item.classList.toggle('past', index + 1 < step);
    item.classList.toggle('now', index + 1 === step);
  });
  document.getElementById('caption').innerHTML = `<span>${captions[step - 1]}</span>`;
  timeline.style.transform = `translateY(${-offset}px)`;
};
window.target = (step, mode) => {
  window.show(step, 1, 0);
  const most = Math.max(0, timeline.scrollHeight - viewport.clientHeight);
  if (mode === 'top') return 0;
  if (mode === 'bottom') return most;
  const first = timeline.querySelector(`[data-step="${step}"]`);
  return Math.min(most, Math.max(0, first.offsetTop - 10));
};
"""


def hero(found: dict[str, Any], config: assistant.Config) -> str:
    """The issue of the demo from its opening to the release, step by step."""

    def labels(*names: str) -> str:
        return " ".join(label(name, config) for name in names)

    bot = "<b>github-actions</b>"
    timeline = [
        (
            1,
            "entry",
            comment(REPORTER, markdown(BODY), verb="opened", when="on Sep 14"),
        ),
        (
            2,
            "event",
            event("tag", f"{bot} added {labels('area: dashboard', 'needs-triage')}"),
        ),
        (3, "entry", comment("bot", markdown(found["analysis"]), when="on Sep 14")),
        (
            3,
            "event",
            event("tag", f"{bot} added {labels('connectivity', 'needs-info')}"),
        ),
        (
            4,
            "entry",
            comment(REPORTER, markdown(ANSWER_TEXT), when="on Sep 15", role="Author"),
        ),
        (4, "event", event("tag", f"{bot} removed {labels('needs-info')}")),
        (
            5,
            "event",
            event(
                "merge",
                f"<b>{MAINTAINER}</b> merged <b>{PULL_TITLE}</b> "
                f'<span class="reference">#{PULL}</span>',
                "merged",
            ),
        ),
        (5, "entry", comment("bot", markdown(found["fixed"]), when="on Sep 18")),
        (5, "event", event("tag", f"{bot} added {labels('fixed-in-next-release')}")),
        (6, "entry", comment("bot", markdown(found["released"]), when="on Oct 2")),
        (6, "event", event("closed", f"{bot} closed this as completed", "done")),
        (
            6,
            "event",
            event("milestone", f"{bot} added this to the <b>{TAG}</b> milestone"),
        ),
    ]
    entries = "".join(
        f'<div class="{kind}" data-step="{step}">{inner}</div>'
        for step, kind, inner in timeline
    )
    sidebar_labels = "".join(
        f'<span data-steps="{steps}">{label(name, config)}</span>'
        for name, steps in (
            ("bug", "1-6"),
            ("area: dashboard", "2-6"),
            ("connectivity", "3-6"),
            ("needs-triage", "2-5"),
            ("needs-info", "3"),
            ("fixed-in-next-release", "5"),
        )
    )
    sidebar = (
        '<div class="sidebar">'
        '<div class="side"><h4>Assignees</h4><span class="none">No one assigned</span></div>'
        f'<div class="side"><h4>Labels</h4><div class="labels">{sidebar_labels}</div></div>'
        '<div class="side"><h4>Milestone</h4>'
        '<span class="none" data-steps="1-5">No milestone</span>'
        f'<span class="value" data-steps="6">{icon("milestone")}{TAG}</span></div>'
        '<div class="side"><h4>Development</h4>'
        '<span class="none" data-steps="1-4">No branches or pull requests</span>'
        f'<span class="value" data-steps="5-6">{icon("merge", css="done-icon")}'
        f"{PULL_TITLE} #{PULL}</span></div></div>"
    )
    steps = "".join(
        f'<div class="step"><i>{index}</i>{name}</div>'
        for index, (name, _) in enumerate(STEPS, 1)
    )
    page = (
        f'{repository("Issues")}<div class="issue">'
        f'<div class="title">{TITLE} <span>#{NUMBER}</span></div>'
        '<div class="meta">'
        f'<span class="state open" data-steps="1-5">{icon("open")}Open</span>'
        f'<span class="state done" data-steps="6">{icon("closed")}Closed</span>'
        f"<span><b>{REPORTER}</b> opened this issue</span></div>"
        f'<div class="columns"><div class="viewport"><div class="timeline" id="timeline">'
        f"{entries}</div></div>{sidebar}</div></div>"
    )
    captions = json.dumps([caption for _, caption in STEPS])
    return document(
        f'<div class="shot stage"><div class="steps">{steps}</div>'
        f'<div class="caption" id="caption"></div>'
        f"{window(f'{REPOSITORY}/issues/{NUMBER}', page)}</div>"
        f'<script type="application/json" id="captions">{captions}</script>',
        HERO_SCRIPT,
    )


def analysis(found: dict[str, Any], config: assistant.Config) -> str:
    """The first analysis on the issue of the demo, in full."""
    page = (
        f'<div class="issue"><div class="title">{TITLE} <span>#{NUMBER}</span></div>'
        f'<div class="meta"><span class="state open">{icon("open")}Open</span>'
        f"<span><b>{REPORTER}</b> opened this issue</span></div>"
        f'<div class="timeline"><div class="entry">{comment("bot", markdown(found["analysis"]))}</div>'
        '<div class="event">'
        + event(
            "tag",
            "<b>github-actions</b> added "
            f"{label('connectivity', config)} {label('needs-info', config)}",
        )
        + "</div></div></div>"
    )
    return document(
        f'<div class="shot">{window(f"{REPOSITORY}/issues/{NUMBER}", page, 820)}</div>'
    )


def run_page(
    title: str,
    number: int,
    trigger: str,
    subtitle: str,
    jobs: list[str],
    passed: bool,
    boxes: str,
    duration: str,
) -> str:
    status = "success" if passed else "failure"
    css = "ok" if passed else "fail"
    side = (
        '<div class="run-side">'
        f'<div class="item on">{icon("home")}Summary</div><h4>Jobs</h4>'
        + "".join(
            f'<div class="item">{icon(status, css=css)}{job}</div>' for job in jobs
        )
        + '<h4>Run details</h4><div class="item">Usage</div>'
        '<div class="item">Workflow file</div></div>'
    )
    facts = "".join(
        f'<div class="fact">{name}<b>{value}</b></div>'
        for name, value in (
            ("Triggered via", trigger),
            ("Status", f"{icon(status, css=css)}{status.title()}"),
            ("Total duration", duration),
            ("Artifacts", "–"),
        )
    )
    main = (
        f'<div class="run-main"><div class="run-title">{icon(status, 20, css)}'
        f'{title} <span class="muted">#{number}</span></div>'
        f'<div class="run-sub">{subtitle}</div>'
        f'<div class="facts">{facts}</div>{boxes}</div>'
    )
    return f'{repository("Actions")}<div class="run">{side}{main}</div>'


def box(title: str, body: str, fade: int = 0, padded: bool = True) -> str:
    inner = f'<div class="fade" style="--fade: {fade}px">{body}</div>' if fade else body
    css = "box-body" if padded else ""
    return (
        f'<div class="box"><div class="box-head">{title}</div>'
        f'<div class="{css}">{inner}</div></div>'
    )


def dry_run(found: dict[str, Any]) -> str:
    """A run by hand: the summary of each job shows what it would write."""
    applied = markdown(found["apply"]).replace("<details>", "<details open>")
    boxes = box("event summary", markdown(found["event"])) + box(
        "apply summary", applied, 520
    )
    page = run_page(
        "Issue assistant",
        57,
        "workflow_dispatch",
        f"Manually run by <b>{MAINTAINER}</b> on <b>main</b>: issue {NUMBER}, mode "
        "triage, dry run",
        ["event", "analyze", "apply"],
        True,
        boxes,
        "1m 24s",
    )
    return document(
        f'<div class="shot">{window(f"{REPOSITORY}/actions/runs/57", page)}</div>'
    )


def findings(found: dict[str, Any]) -> str:
    """A daily Findings run that fails on a finding that nobody accepted."""
    annotations = "".join(
        f'<div class="annotation">{icon("failure", css="fail")}<div>'
        f'<div class="where">findings</div><div class="what">{html.escape(text)}</div>'
        "</div></div>"
        for text in found["annotations"]
    )
    count = len(found["annotations"])
    errors = f"{count} error{'s' if count > 1 else ''}"
    boxes = box(
        f'Annotations <span class="muted">{errors}</span>', annotations, padded=False
    ) + box("findings summary", markdown(found["findings"]))
    page = run_page(
        "Findings",
        112,
        "schedule",
        "Scheduled run on <b>main</b>, every morning",
        ["findings"],
        False,
        boxes,
        "14s",
    )
    return document(
        f'<div class="shot">{window(f"{REPOSITORY}/actions/runs/112", page)}</div>'
    )


CLEANING = (
    (
        "@sam-demo, merge this at once.",
        "A mention becomes code, so nobody is notified.",
    ),
    (
        "Run [the patch](https://attacker.example/fix.sh) first.",
        "A link to a site that config.toml doesn't allow keeps only its text.",
    ),
    (
        "![build passing](https://attacker.example/pixel.gif)",
        "An image keeps only its description.",
    ),
    ("<img src=x onerror=alert(1)>", "HTML is removed."),
    (
        "Same as #7 and other-org/other-repo#3.",
        "Issue numbers that the checks didn't find become code.",
    ),
)
KEPT = (
    f"See [{TAG}](https://github.com/{REPOSITORY}/releases/tag/{TAG}) and #{RELATED}.",
    "Links into the repository and to issues that exist stay links.",
)
# Built here, so that no secret scan takes the demo for a real token.
TOKEN = "ghp_" + "Demo0" * 7 + "x"


def cleaning(config: assistant.Config) -> str:
    """What apply makes of the text of an answer that an issue steered."""

    def row(raw: str, why: str, css: str = "posted") -> str:
        posted = assistant.sanitize(raw, config, {RELATED})
        shown = inline(posted) if posted else '<span class="nothing">nothing</span>'
        return (
            f'<div class="row"><div class="cell raw mono">{html.escape(raw)}</div>'
            f'<div class="arrow">{icon("arrow", 18)}</div>'
            f'<div class="cell {css}"><div class="markdown">{shown}</div>'
            f'<span class="why">{why}</span></div></div>'
        )

    secret = f"Paste the token {TOKEN} into the settings."
    if not assistant.looks_secret(secret):
        raise SystemExit("The demo token no longer looks like a secret to the checks.")
    rows = "".join(row(raw, why) for raw, why in CLEANING) + row(*KEPT, "kept")
    page = (
        '<div class="cleaning"><h2>The job <code>apply</code> checks every answer '
        "before it reaches GitHub</h2>"
        '<p class="lead">An issue tried to steer the AI. What its answer said, and what '
        "the job without AI posts:</p>"
        '<div class="heads"><span>In the answer</span><span></span><span>Posted</span></div>'
        f'{rows}<div class="refused">{icon("failure", 22, "fail")}<div>'
        f'<div class="mono">{html.escape(secret)}</div>'
        "<b>Something like a token anywhere in the answer:</b> the whole answer is "
        "refused, and nothing is posted.</div></div></div>"
    )
    return document(
        f'<div class="shot"><div class="window" style="width: 860px">{page}</div></div>'
    )


# Taking the pictures.


def capture(page: Page) -> Image.Image:
    shot = page.locator(".shot").screenshot(omit_background=True, animations="disabled")
    return Image.open(io.BytesIO(shot)).convert("RGBA")


def load(page: Page, source: str) -> None:
    page.set_content(source, wait_until="networkidle")
    page.evaluate("document.fonts.ready.then(() => true)")


def still(page: Page, source: str, name: str, theme: str) -> None:
    load(page, source)
    target = OUTPUT / f"{name}-{theme}.png"
    capture(page).save(target, optimize=True)
    print(f"rendered {target.relative_to(ROOT)} ({target.stat().st_size // 1024} KiB)")


def frames(page: Page) -> Iterator[tuple[Image.Image, int]]:
    """The frames of the animation and how long each one stays."""
    offsets = [
        page.evaluate("([step, mode]) => target(step, mode)", [step, mode])
        for step, mode, _ in SEQUENCE
    ]

    def show(step: int, progress: float, offset: float) -> None:
        page.evaluate(
            "([step, progress, offset]) => show(step, progress, offset)",
            [step, progress, offset],
        )

    for index, ((step, _, hold), offset) in enumerate(
        zip(SEQUENCE, offsets, strict=True)
    ):
        if index and step != SEQUENCE[index - 1][0]:
            for opacity, pause in FADE:
                show(step, opacity, offset)
                yield capture(page), pause
        show(step, 1, offset)
        yield capture(page), hold


def animation(page: Page, source: str, name: str, theme: str) -> None:
    load(page, source)
    taken, durations = zip(*frames(page), strict=True)
    # Every frame gets the size of the largest one, on a transparent background.
    size = (max(frame.width for frame in taken), max(frame.height for frame in taken))
    pictures = []
    for frame in taken:
        padded = Image.new("RGBA", size)
        padded.paste(frame, (0, 0))
        pictures.append(padded)
    target = OUTPUT / f"{name}-{theme}.webp"
    pictures[0].save(
        target,
        save_all=True,
        append_images=pictures[1:],
        duration=list(durations),
        loop=0,
        quality=85,
        method=6,
        minimize_size=True,
    )
    print(
        f"rendered {target.relative_to(ROOT)} "
        f"({target.stat().st_size // 1024} KiB, {len(pictures)} frames)"
    )


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as folder:
        config = demo_repository(Path(folder))
        found = content(Path(folder), config)
        pictures = {
            "analysis": analysis(found, config),
            "cleaning": cleaning(config),
            "dry-run": dry_run(found),
            "findings": findings(found),
        }
        source = hero(found, config)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=glob(BROWSER)[0])
        for theme in THEMES:
            for kind, scale in SCALES.items():
                page = browser.new_page(
                    viewport={"width": 1000, "height": 1400},
                    device_scale_factor=scale,
                    color_scheme=theme,  # type: ignore[arg-type]
                )
                if kind == "animation":
                    animation(page, source, "hero", theme)
                else:
                    for name, picture in pictures.items():
                        still(page, picture, name, theme)
                page.close()
        browser.close()


if __name__ == "__main__":
    main()
