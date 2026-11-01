"""Labels as code: .github/labels.toml on GitHub, and the routing of the issue forms."""

from __future__ import annotations

from .constants import LOGIN
from .github import GitHub
from .paths import LABELS_FILE
from .settings import Config
from .values import command, did_you_mean


def sync_labels(github: GitHub, config: Config) -> list[str]:
    """Create and update the labels of labels.toml; never delete one.

    GitHub ignores the case of label names, so a label that it has in another case,
    such as Bug for bug, is renamed; creating it would fail.
    """
    existing = {label["name"].casefold(): label for label in github.labels()}
    done = []
    for label in config.labels:
        current = existing.get(label.name.casefold())
        if current is None:
            created = github.create_label(label)
            done.append(f"{'created' if created else 'updated'} {label.name}")
        elif current["name"] != label.name:
            github.update_label(label, current["name"])
            done.append(f"renamed {current['name']} to {label.name}")
        elif (current.get("color") or "").lower() != label.color or (
            current.get("description") or ""
        ) != label.description:
            github.update_label(label)
            done.append(f"updated {label.name}")
    configured = {label.name.casefold() for label in config.labels}
    for name in sorted(
        label["name"] for key, label in existing.items() if key not in configured
    ):
        command("notice", f"Label {name} is not in {LABELS_FILE}; it stays.")
    for problem in check_routing(github, config):
        command("warning", problem)
    return done


def check_routing(github: GitHub, config: Config) -> list[str]:
    """What GitHub can't do of labels.toml: assign an owner of an area, or give
    issues the issue type of a kind. `check` knows only the spelling of both."""
    problems = []
    areas: dict[str, list[str]] = {}
    for label in config.labels:
        if label.group == "area":
            for owner in label.owners:
                areas.setdefault(owner, []).append(label.name)
    for owner, names in areas.items():
        if LOGIN.fullmatch(owner) and not github.assignable(owner):
            problems.append(
                f"{LABELS_FILE}: {owner}, the owner of {', '.join(names)}, can't be "
                f"assigned to issues of {config.repository}; GitHub assigns only "
                "people with access to it"
            )
    wanted = list(
        dict.fromkeys(
            label.issue_type
            for label in config.labels
            if label.group == "type" and label.issue_type
        )
    )
    if not wanted:
        return problems
    types = github.issue_types()
    if not types:
        return problems + [
            f"{LABELS_FILE}: {config.repository} has no issue types, which "
            "organizations give their repositories; issue_type changes nothing"
        ]
    for name in wanted:
        if name not in types:
            problems.append(
                did_you_mean(
                    f"{LABELS_FILE}: {config.repository} has no issue type {name}",
                    name,
                    types,
                )
            )
    return problems
