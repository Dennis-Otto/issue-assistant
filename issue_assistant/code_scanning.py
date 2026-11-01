"""Findings of code scanning: an issue for each new one, and the accepted ones dismissed."""

from __future__ import annotations

from pathlib import Path

from .github import GitHub
from .paths import FINDINGS_FILE
from .settings import load_findings


def watch_findings(github: GitHub, root: Path) -> tuple[list[str], int]:
    """Dismiss the accepted findings and count the others, which stay open.

    The summary of a public repository's run is public, while code scanning alerts
    are only shown to maintainers: the lines name an open alert by its number and
    link only, never its rule, file or message. An accepted finding is named, since
    .github/findings.toml names it anyway.
    """
    accepted = load_findings(root)
    alerts = github.open_alerts()
    if alerts is None:
        return ["Code scanning isn't set up; there is nothing to check."], 0
    done, left = [], []
    for alert in sorted(alerts, key=lambda item: item["number"]):
        acceptance = next((item for item in accepted if item.covers(alert)), None)
        if acceptance is None:
            left.append(alert)
            continue
        github.dismiss_alert(
            alert["number"],
            acceptance.reason,
            f"{acceptance.comment} (accepted in {FINDINGS_FILE})",
        )
        done.append(
            f"Alert #{alert['number']} ({acceptance.tool} {acceptance.rule}) "
            f"dismissed as {acceptance.reason}."
        )
    done += [f"Alert #{alert['number']} is open: {alert['html_url']}" for alert in left]
    if left:
        done.append(
            f"Fix these findings, or accept them with a reason in {FINDINGS_FILE}."
        )
    return done, len(left)
