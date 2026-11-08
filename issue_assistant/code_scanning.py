"""Findings of code scanning: the accepted ones dismissed, and each other one reported
once, by the first run that finds it open."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .github import GitHub
from .paths import FINDINGS_FILE, FINDINGS_RECORD
from .settings import load_findings

# The words of a line about an open alert, by which the command annotates it: an
# error for one that fails the run, a warning for one that an earlier run reported.
ALERT_OPEN = " is open: "
ALERT_STILL_OPEN = " is still open: "
# The line about a record that isn't one, also a warning.
BROKEN_RECORD = (
    f"{FINDINGS_RECORD} holds no record of the alerts that earlier runs reported, so "
    "every open alert counts as new."
)


def reported_alerts(root: Path) -> tuple[dict[int, str], list[str]]:
    """The alerts that earlier runs reported, by number, with the time each opened,
    and a line about a record that is broken.

    The record comes from the cache of the default branch, which every workflow there
    can write. A record that isn't one counts as none, so it reports every open alert
    again and can never hide one.
    """
    path = root / FINDINGS_RECORD
    if not path.is_file():
        return {}, []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if isinstance(data, dict) and all(
        key.isdecimal() and isinstance(value, str) for key, value in data.items()
    ):
        return {int(key): value for key, value in data.items()}, []
    return {}, [BROKEN_RECORD]


def alert_opened(alert: dict[str, Any]) -> str:
    """When the alert opened: GitHub sets updated_at when an alert opens, opens
    again, is fixed or is dismissed, and leaves it while every scan finds it open."""
    return str(alert.get("updated_at") or "")


def keep_reported(root: Path, alerts: list[dict[str, Any]]) -> None:
    """Write the record of the alerts that are open now, which the workflow keeps in
    its cache under the hash of the file: one key for each record."""
    path = root / FINDINGS_RECORD
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {str(alert["number"]): alert_opened(alert) for alert in alerts}
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")


def watch_findings(github: GitHub, root: Path) -> tuple[list[str], int]:
    """Dismiss the accepted findings, report each other open alert once, and say how
    many opened since the record of FINDINGS_RECORD, which fail the run.

    The workflow runs after every scan and every morning, and GitHub mails the
    maintainer about every failed run: an alert that stays open fails the first run
    that finds it, and again only once it opens again, with a later time. The runs
    after it name it as still open. Only a run that finds a new alert writes the
    record, of the alerts open then, and a dry run never does, as it writes nothing.

    The summary of a public repository's run is public, while code scanning alerts
    are only shown to maintainers: the lines name an open alert by its number and
    link only, never its rule, file or message. An accepted finding is named, since
    .github/findings.toml names it anyway.
    """
    accepted = load_findings(root)
    alerts = github.open_alerts()
    if alerts is None:
        return ["Code scanning isn't set up; there is nothing to check."], 0
    reported, done = reported_alerts(root)
    left = []
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
    new = [
        alert for alert in left if reported.get(alert["number"]) != alert_opened(alert)
    ]
    done += [
        f"Alert #{alert['number']}{ALERT_OPEN}{alert['html_url']}" for alert in new
    ]
    done += [
        f"Alert #{alert['number']}{ALERT_STILL_OPEN}{alert['html_url']}"
        for alert in left
        if alert not in new
    ]
    if len(new) < len(left):
        done.append(
            "An open alert fails only the first run that finds it, and again once it "
            "opens again; until it is fixed or accepted, the runs after it name it here."
        )
    if left:
        done.append(
            f"Fix these findings, or accept them with a reason in {FINDINGS_FILE}."
        )
    if new and not github.dry_run:
        keep_reported(root, left)
    return done, len(new)
