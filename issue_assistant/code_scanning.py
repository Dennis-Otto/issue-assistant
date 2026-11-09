"""Findings of code scanning: the accepted ones dismissed, and each other one reported
once, by the first run that finds it open; after the scan of a pull request of the
repository itself, the accepted ones of the pull request dismissed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .github import GitHub
from .paths import FINDINGS_FILE, FINDINGS_RECORD
from .settings import Acceptance, load_findings

# The words of a line about an open alert, by which the command annotates it: an
# error for one that fails the run, a warning for one that an earlier run reported.
ALERT_OPEN = " is open: "
ALERT_STILL_OPEN = " is still open: "
# The line of a run whose repository has no code scanning.
NO_CODE_SCANNING = "Code scanning isn't set up; there is nothing to check."
# The line of a run after a scan for which GitHub names no pull request of the
# repository itself, such as one of a fork's.
NO_PULL_REQUEST = (
    "GitHub names no pull request of this repository for the scan: nothing to do."
)
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


def dismiss_accepted(
    github: GitHub, accepted: tuple[Acceptance, ...], alerts: list[dict[str, Any]]
) -> tuple[list[str], list[dict[str, Any]]]:
    """Dismiss each alert that an entry of FINDINGS_FILE covers, with the entry's
    reason and comment: the lines about them, and the alerts that stay open, both by
    the numbers of the alerts."""
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
    return done, left


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
        return [NO_CODE_SCANNING], 0
    reported, done = reported_alerts(root)
    dismissed, left = dismiss_accepted(github, accepted, alerts)
    done += dismissed
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


def scanned_pull_requests(event: str, payload: dict[str, Any]) -> list[int] | None:
    """The pull requests whose scan started the run, which it looks after in place
    of the default branch; None for a run of the default branch.

    The Findings workflow runs every morning, by hand, after a push of FINDINGS_FILE
    and after each scan of code scanning, of the default branch and of a pull
    request (event `workflow_run`). Only a pull request of the repository itself
    counts, which someone with write access pushed: after the scan of a fork's, or
    of another branch without one, the list is empty, and the run does nothing.
    """
    if event != "workflow_run":
        return None
    scan = payload.get("workflow_run") or {}
    repository = payload.get("repository") or {}
    head = (scan.get("head_repository") or {}).get("full_name")
    own = head is not None and head == repository.get("full_name")
    if scan.get("event") != "pull_request":
        default = scan.get("head_branch") == repository.get("default_branch")
        return None if own and default else []
    if not own:
        return []
    return sorted({int(item["number"]) for item in scan.get("pull_requests") or []})


def accept_in_pull_requests(
    github: GitHub, root: Path, numbers: list[int]
) -> list[str]:
    """Dismiss the alerts of these pull requests that FINDINGS_FILE accepts, and
    name the others; none of them fails the run.

    GitHub posts each alert that the scan of a pull request finds in its changes as
    a review thread, and the ruleset of the default branch may require every thread
    to be resolved before the merge. A pull request that only moves a finding that
    the repository accepts, such as a false positive on a line that now has another
    number, makes a new alert that the runs of the default branch only see after the
    merge, so its thread blocked the merge until a person resolved it. Dismissing the
    alert resolves its thread, and the alert stays dismissed after the merge.

    FINDINGS_FILE is that of the default branch, the checkout of the run, never one
    that the pull request changes. The check of code scanning of the pull request
    shows the other alerts there, so they fail no run, and their lines name each by
    its number and link only, as on the default branch.
    """
    if not numbers:
        return [NO_PULL_REQUEST]
    accepted = load_findings(root)
    done, left = [], []
    for number in numbers:
        alerts = github.open_alerts(pull_request=number)
        if alerts is None:
            return [NO_CODE_SCANNING]
        dismissed, others = dismiss_accepted(github, accepted, alerts)
        done += dismissed
        left += [
            f"Alert #{alert['number']} is open in pull request #{number}: "
            f"{alert['html_url']}"
            for alert in others
        ]
    if left:
        left.append(
            "The check of code scanning shows these alerts in the pull request: fix "
            f"them there, or accept them with a reason in {FINDINGS_FILE} of the "
            "default branch, and the next scan of the pull request dismisses them."
        )
    return done + left
