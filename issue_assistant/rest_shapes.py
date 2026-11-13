"""GraphQL's answers in the form of the REST API, which the assistant reads."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def as_rest(
    node: dict[str, Any], owns: Callable[[dict[str, Any]], bool]
) -> dict[str, Any]:
    """An issue of GraphQL in the form of the REST API, which the assistant reads,
    with `closed_by`, the number of the pull request that fixed it, if one did.

    That is the last merged pull request of this repository that names the issue,
    such as `Fixes #12`, else the pull request that closed it last. A release
    closes the issues that its pull requests fix (`close_with_release`), so the
    closing is often the bot's and not the pull request's."""
    closer = None
    if node["state"] == "CLOSED":
        fixes = [
            pull["number"]
            for pull in node["closedByPullRequestsReferences"]["nodes"]
            if pull["merged"] and owns(pull)
        ]
        events = node["timelineItems"]["nodes"]
        closed = ((events[-1] if events else {}).get("closer") or {}).get("number")
        closer = fixes[-1] if fixes else closed
    return {
        "number": node["number"],
        "title": node["title"],
        "body": node["body"],
        "user": actor(node["author"]),
        "state": node["state"].lower(),
        "state_reason": (node["stateReason"] or "").lower() or None,
        "labels": (node["labels"] or {"nodes": []})["nodes"],
        "created_at": node["createdAt"],
        "closed_at": node["closedAt"],
        "comments": node["comments"]["totalCount"],
        "closed_by": closer,
    }


def closed_as_rest(
    node: dict[str, Any], owns: Callable[[dict[str, Any]], bool]
) -> dict[str, Any]:
    """A closed issue of GraphQL for `evaluate`, in the form of the REST API, with
    `duplicate_of`: the issue of this repository that it was closed as a duplicate
    of, else the one it was marked as a duplicate of last, unless someone unmarked
    it, with its number and when it was opened; None for any other issue."""
    events = node["timelineItems"]["nodes"]
    marked = (events[-1] if events else {}).get("canonical") or {}
    original = node["duplicateOf"] or (marked if "number" in marked else None)
    author = node["author"] or {}
    return {
        "number": node["number"],
        "title": node["title"],
        "body": node["body"],
        # A deleted account is GitHub's ghost.
        "user": {
            "login": author.get("login") or "ghost",
            "type": "Bot" if author.get("__typename") == "Bot" else "User",
        },
        "author_association": node["authorAssociation"],
        "state": "closed",
        "state_reason": (node["stateReason"] or "").lower() or None,
        "labels": (node["labels"] or {"nodes": []})["nodes"],
        "created_at": node["createdAt"],
        "closed_at": node["closedAt"],
        "duplicate_of": {
            "number": original["number"],
            "created_at": original["createdAt"],
        }
        if original and owns(original)
        else None,
    }


def actor(node: dict[str, Any] | None) -> dict[str, str]:
    """An author of GraphQL as the REST API names it: a bot by its login and [bot],
    as github-actions[bot], of the type Bot, and a deleted account as ghost."""
    if not node:
        return {"login": "ghost", "type": "User"}
    if node.get("__typename") == "Bot":
        return {"login": f"{node['login']}[bot]", "type": "Bot"}
    return {"login": node["login"], "type": "User"}


def post_as_rest(node: dict[str, Any]) -> dict[str, Any]:
    """A discussion, a comment or a reply of GraphQL: who wrote it, in which role,
    when, and its text, as the REST API names them."""
    return {
        "user": actor(node["author"]),
        "author_association": node["authorAssociation"],
        "created_at": node["createdAt"],
        "body": node.get("body"),
    }


def discussion_as_rest(node: dict[str, Any]) -> dict[str, Any]:
    """A discussion of GitHub.discussion() in the form of the REST API, with its
    comments, each with its replies, and their number."""
    comments = node["comments"]
    return {
        "node_id": node["id"],
        "number": node["number"],
        "title": node["title"],
        **post_as_rest(node),
        "state": "closed" if node["closed"] else "open",
        "locked": node["locked"],
        "answered": bool(node["isAnswered"]),
        "category": {
            "name": node["category"]["name"],
            "is_answerable": node["category"]["isAnswerable"],
        },
        "comment_count": comments["totalCount"],
        "comments": [
            {
                **post_as_rest(comment),
                "replies": [
                    post_as_rest(reply) for reply in comment["replies"]["nodes"]
                ],
            }
            for comment in comments["nodes"]
        ],
    }
