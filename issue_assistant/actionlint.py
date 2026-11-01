"""Reading YAML, and editing actionlint's settings without losing their comments."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from .errors import AssistantError, SettingsError
from .paths import ACTIONLINT, WORKFLOWS

# actionlint 1.7.12, the newest release, doesn't know the queue of a concurrency
# group yet, which issue-assistant.yml has; install lets it ignore that message.
QUEUE_MESSAGE = 'unexpected key "queue" for "concurrency" section'


def load_yaml(path: Path, where: str = "") -> Any:
    """A YAML file as data, with PyYAML or with yq, which GitHub's runners have.

    A file that isn't YAML is a SettingsError that names it as `where`.
    """
    try:
        import yaml
    except ImportError:
        try:
            result = subprocess.run(
                ["yq", "-o=json", ".", str(path)],
                capture_output=True,
                text=True,
                check=True,
            )
        except FileNotFoundError:
            raise AssistantError(
                "Reading the workflows needs PyYAML or yq; install one of them, "
                "for example with: pip install pyyaml"
            ) from None
        except subprocess.CalledProcessError as error:
            reason = error.stderr.strip()
        else:
            return json.loads(result.stdout)
    else:
        try:
            return yaml.safe_load(path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, UnicodeDecodeError) as error:
            reason = str(error)
    raise SettingsError([f"{where or path} is no valid YAML: {reason}"])


def yaml_of(text: str) -> Any:
    """YAML text as data, read as load_yaml reads a file; None if it isn't YAML."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "settings.yaml"
        path.write_text(text, encoding="utf-8")
        try:
            return load_yaml(path)
        except SettingsError:
            return None


def items_at(data: Any, keys: tuple[str, ...]) -> list[Any]:
    """The list that the mappings of YAML data hold at `keys`; empty if none."""
    for key in keys:
        data = data.get(key) if isinstance(data, dict) else None
    return data if isinstance(data, list) else []


def with_listed(data: Any, keys: tuple[str, ...], item: str) -> Any:
    """YAML data with `item` first in the list at `keys`, which is added if missing;
    None where something else than a mapping or a list is in the way."""
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return None
    data = deepcopy(data)
    node = data
    for key in keys[:-1]:
        if node.get(key) is None:
            node[key] = {}
        node = node[key]
        if not isinstance(node, dict):
            return None
    items = node.get(keys[-1])
    if items is not None and not isinstance(items, list):
        return None
    node[keys[-1]] = [item, *(items or [])]
    return data


def indentation(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def content_lines(lines: list[str], block: range) -> list[int]:
    """The lines of `block` that hold more than blanks or a comment."""
    return [
        number
        for number in block
        if lines[number].strip() and not lines[number].lstrip().startswith("#")
    ]


def with_item(text: str, keys: tuple[str, ...], item: str) -> str | None:
    """YAML text with `item` first in the list at `keys`, adding the keys it lacks.

    The edit adds lines below the deepest of the keys that the text has, or puts the
    item into the brackets of a list such as `labels: []`, so that every comment and
    the order of the keys stay. Its result is read back: None when that isn't the
    data with the item added, for YAML that the edit doesn't fit, such as a list of
    the top level or a mapping in braces.
    """
    expected = with_listed(yaml_of(text), keys, item)
    if expected is None:
        return None
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    # The key found last, its line and its match, and the lines of its block, which
    # are indented more deeply; at first the whole text, the block of the top level.
    found, line, match = 0, -1, None
    block = range(len(lines))
    for key in keys:
        content = content_lines(lines, block)
        if not content:
            break
        inner = indentation(lines[content[0]])
        name = re.escape(key)
        pattern = re.compile(
            rf" *(?:{name}|'{name}'|\"{name}\"):[ \t]*(?P<flow>\[[^\]]*\])?"
            r"[ \t]*(?:#.*)?\n"
        )
        hit = next(
            (
                (number, matched)
                for number in content
                if indentation(lines[number]) == inner
                and (matched := pattern.fullmatch(lines[number]))
            ),
            None,
        )
        if hit is None:
            break
        (line, match), found = hit, found + 1
        end = line + 1
        while end < len(lines) and (
            not lines[end].strip()
            or lines[end].lstrip().startswith("#")
            or indentation(lines[end]) > inner
        ):
            end += 1
        block = range(line + 1, end)
    scalar = (
        item if re.fullmatch(r"[\w.-]+", item) else "'" + item.replace("'", "''") + "'"
    )
    if found == len(keys) and match and match["flow"]:
        inside = match["flow"][1:-1].strip()
        start, stop = match.span("flow")
        lines[line] = (
            lines[line][:start]
            + f"[{scalar}{', ' + inside if inside else ''}]"
            + lines[line][stop:]
        )
    else:
        content = content_lines(lines, block)
        after = content_lines(lines, range(block.stop, len(lines)))
        if line < 0:
            indent, position = 0, len(lines)
        elif content:
            indent, position = indentation(lines[content[0]]), line + 1
        elif (
            found == len(keys)
            and after
            and indentation(lines[after[0]]) == indentation(lines[line])
            and lines[after[0]].lstrip().startswith("- ")
        ):
            # The items of a list at the indentation of its key, as many
            # formatters write them.
            indent, position = indentation(lines[line]), line + 1
        else:
            indent, position = indentation(lines[line]) + 2, line + 1
        added = [
            " " * (indent + 2 * step) + f"{key}:\n"
            for step, key in enumerate(keys[found:])
        ]
        added.append(" " * (indent + 2 * len(added)) + f"- {scalar}\n")
        lines[position:position] = added
    result = "".join(lines)
    return result if yaml_of(result) == expected else None


def add_to_actionlint(root: Path) -> list[str]:
    """Let actionlint, where a repository runs it with settings, ignore the queue of
    issue-assistant.yml, which it doesn't know yet. The runner of the AI is in the
    reusable workflow, which actionlint doesn't read in a repository that calls it."""
    path = root / ACTIONLINT
    if not path.is_file():
        return []
    done = []
    for keys, item, what in (
        (
            ("paths", str(WORKFLOWS / "issue-assistant.yml"), "ignore"),
            QUEUE_MESSAGE,
            "let actionlint ignore the queue of issue-assistant.yml",
        ),
    ):
        text = path.read_text(encoding="utf-8")
        reason = "its YAML is too unusual to edit safely"
        try:
            if item in items_at(load_yaml(path, str(ACTIONLINT)), keys):
                continue
            edited = with_item(text, keys, item)
        except AssistantError as error:
            # Without PyYAML or yq, or with settings that are no YAML, install
            # writes the rest of the set-up and says what is left to do here.
            edited, reason = None, str(error)
        if edited is None:
            done.append(
                f"{ACTIONLINT}: add {item!r} to the list {' > '.join(keys)}; {reason}"
            )
            continue
        path.write_text(edited, encoding="utf-8", newline="\n")
        done.append(f"{what} in {ACTIONLINT}")
    return done
