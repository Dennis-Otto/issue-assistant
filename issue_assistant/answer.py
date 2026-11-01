"""The engine's answer: its JSON schema, and the checks that it passes on its way in."""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Iterable
from typing import Any

from .cleaning import tracked_files
from .constants import NO_PRIORITY
from .errors import AssistantError
from .issues import Visible
from .languages import ANSWER_LANGUAGES, LOCALES
from .settings import Config, maintainers, siblings
from .values import command, text

SECRET = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    # Not the end of a word, as in task-runner-configuration-for-tests.
    r"|(?<![A-Za-z0-9])sk-ant-[A-Za-z0-9_-]{8,}"
    r"|(?<![A-Za-z0-9])sk-(?:proj-)?[A-Za-z0-9_-]{20,}"
    r"|xox[abposr]-[A-Za-z0-9-]{10,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    # Webhooks, whose address alone lets anyone post as their owner: up to the secret
    # at its end, which placeholders such as services/YOUR/WEBHOOK/URL lack.
    r"|hooks\.slack\.com/(?:services|triggers|workflows)/(?:[A-Za-z0-9]+/)+"
    r"[A-Za-z0-9]{20,}"
    r"|\.webhook\.office\.com/webhookb2/[\w@-]+/IncomingWebhook/[0-9a-f]{20,}/"
    r"[0-9a-f-]{20,}"
)
LONG_TOKEN = re.compile(r"(?<![\w+/=-])[\w+/=-]{40,}(?![\w+/=-])")
# A link to a repository on GitHub, whose owner and name people choose, up to the
# path of a file or folder in it after the commit or branch. A name or a branch of 32
# or more characters is checked like any other text, so that no key hides as one; a
# commit is lower-case hex, which never mixes case.
GITHUB_REPOSITORY = re.compile(
    r"https://github\.com/[A-Za-z0-9-]{1,39}/[\w.-]{1,31}(?![\w.-])"
    r"(?:/(?:blob|tree)/(?:[0-9a-f]{40}|[\w.-]{1,31})(?![\w.-]))?"
)
LETTER_PAIR = re.compile(r"(?=[A-Za-z]{2})")
CASE_CHANGE = re.compile(r"(?=[a-z][A-Z]|[A-Z][a-z])")
LOWER_CASE = re.compile(r"[a-z]+")


def strings(limit: int, items: int) -> dict[str, Any]:
    return {"type": "array", "items": text(limit), "maxItems": items}


def choice(values: list[str]) -> dict[str, Any]:
    return {"type": "string", "enum": values}


# What an issue shows that it shouldn't: nothing, personal data such as an e-mail
# address, or a secret that must be replaced.
SENSITIVE = ["none", "personal", "secret"]
# What a discussion is: a question, which the answer settles, a bug or a request for
# a feature, which belong in an issue, or spam, which gets no answer.
DISCUSSION_KINDS = ["question", "bug", "feature", "spam"]


def record(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


# The longest summary for the maintainers in their language, which an answer in
# another language folds below its text; see for_maintainers.
MAINTAINER_SUMMARY = 700

# The longest reason why an issue could suit someone new to the project.
NEWCOMER_REASON = 200


def many(values: list[str], items: int) -> dict[str, Any]:
    if not values:
        return {"type": "array", "maxItems": 0}
    return {"type": "array", "items": choice(values), "maxItems": items}


def answer_schema(mode: str, config: Config) -> dict[str, Any]:
    """The JSON schema of the engine's answer.

    Engines that can enforce a schema get it; `apply` checks every answer against it.
    """
    if mode == "maintainer-reply":
        return record(
            {"waiting_for_reporter": {"type": "boolean"}, "reason": text(280)}
        )
    if mode == "release-reply":
        return record(
            {
                "language": choice(ANSWER_LANGUAGES),
                "problem_persists": {"type": "boolean"},
                "reason": text(280),
            }
        )
    reference = {
        "type": "object",
        "additionalProperties": False,
        "required": ["path", "reason"],
        "properties": {
            "path": text(200),
            "line": {"type": "integer", "minimum": 1},
            "anchor": text(120),
            "reason": text(200),
        },
    }
    if mode == "maintainer-question":
        return record(
            {
                "language": choice(ANSWER_LANGUAGES),
                "answer": text(3000),
                "references": {"type": "array", "items": reference, "maxItems": 6},
            }
        )
    if mode == "discussion-answer":
        return record(
            {
                "language": choice(ANSWER_LANGUAGES),
                "kind": choice(DISCUSSION_KINDS),
                "answer": text(3000),
                "maintainer_summary": text(MAINTAINER_SUMMARY),
                "missing_information": strings(400, 5),
                "references": {"type": "array", "items": reference, "maxItems": 6},
                "sensitive_data": choice(SENSITIVE),
                "security_report": {"type": "boolean"},
            }
        )
    common = {
        "language": choice(ANSWER_LANGUAGES),
        "kind": choice(config.group("type") + ["spam"]),
        "areas": many(config.group("area"), 3),
        "topics": many(config.group("topic"), 2),
        "label_rationale": text(280),
    }
    if priorities := config.group("priority"):
        common |= {
            "priority": choice([*priorities, NO_PRIORITY]),
            "priority_rationale": text(280),
        }
    if mode == "follow-up":
        return record(
            common
            | {
                "reply": text(1500),
                "maintainer_summary": text(MAINTAINER_SUMMARY),
                "missing_information": strings(400, 5),
                "references": {"type": "array", "items": reference, "maxItems": 6},
                "sensitive_data": choice(SENSITIVE),
            }
        )
    related = {
        "kind": choice(["issue", "discussion"]),
        "number": {"type": "integer", "minimum": 1},
        "reason": text(280),
    }
    # Where there are related repositories, a related issue can be one of theirs,
    # and the issue can belong in one of them.
    belongs: dict[str, Any] = {}
    if names := siblings(config):
        repositories = choice([config.repository, *names])
        related = {"repository": repositories, **related}
        belongs = {"belongs_in": repositories}
    triage = record(
        common
        | {
            "summary": text(700),
            "maintainer_summary": text(MAINTAINER_SUMMARY),
            "analysis": text(2500),
            "next_steps": strings(400, 5),
            "missing_information": strings(400, 5),
            "duplicates": {
                "type": "array",
                "maxItems": 3,
                "items": record(
                    {
                        "number": {"type": "integer", "minimum": 1},
                        "confidence": choice(["high", "medium"]),
                        "reason": text(280),
                    }
                ),
            },
            "related": {"type": "array", "maxItems": 5, "items": record(related)},
            **belongs,
            "references": {"type": "array", "items": reference, "maxItems": 6},
            "sensitive_data": choice(SENSITIVE),
            "security_report": {"type": "boolean"},
        }
    )
    # Only a repository with labels to offer to newcomers is asked. An answer may
    # leave the field out, which says no, and parse_answer drops one that breaks the
    # schema: a hint that nobody sees must never cost an analysis.
    if config.group("contribution"):
        triage["properties"]["newcomer"] = record(
            {"fit": {"type": "boolean"}, "reason": text(NEWCOMER_REASON)}
        )
    return triage


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """The schema for an engine that wants every field of an object required, as the
    structured outputs of some APIs do: a field that the answer may leave out may be
    null instead, which parse_answer drops again, and every list names its items."""
    result = dict(schema)
    if "properties" in schema:
        result["properties"] = {
            key: strict_schema(value)
            if key in schema["required"]
            else {"anyOf": [strict_schema(value), {"type": "null"}]}
            for key, value in schema["properties"].items()
        }
        result["required"] = list(schema["properties"])
    if schema.get("type") == "array":
        # The items of a list that must stay empty, such as the areas of a
        # repository that has none.
        result["items"] = strict_schema(schema.get("items", {"type": "string"}))
    return result


def schema_json(mode: str, config: Config, *, strict: bool = False) -> str:
    """The schema of the answer as one line of JSON, for the engine's command line;
    `strict` gives its strict form.

    The workflow puts it between single quotes, which an apostrophe in a label name,
    as in "area: user's guide", would end. As the escape \\u0027, the apostrophe ends
    nothing and reads back as the same JSON.
    """
    schema = answer_schema(mode, config)
    text = json.dumps(
        strict_schema(schema) if strict else schema, separators=(",", ":")
    )
    return text.replace("'", "\\u0027")


# The names of the fields of the schema; any other key is the engine's.
FIELD_NAME = re.compile(r"[a-z_]{1,40}")


def key_path(path: str, key: str) -> str:
    """Where a field of the answer is, for a message in the log.

    A key that the engine made up shows as a short JSON string, so that it can't add
    a line or a long text to the message.
    """
    if FIELD_NAME.fullmatch(key):
        return f"{path}.{key}"
    return f"{path}[{json.dumps(key if len(key) <= 40 else key[:40] + '...')}]"


def validate(schema: dict[str, Any], value: Any, path: str = "answer") -> list[str]:
    """The ways a value breaks the schema, for the subset of JSON schema used here."""
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            return [f"{path} is not an object"]
        problems = [
            f"{path}.{key} is missing" for key in schema["required"] if key not in value
        ]
        problems += [
            f"{key_path(path, key)} is not expected"
            for key in value
            if key not in schema["properties"]
        ]
        for key, item in value.items():
            if key in schema["properties"]:
                problems += validate(schema["properties"][key], item, f"{path}.{key}")
        return problems
    if kind == "array":
        if not isinstance(value, list):
            return [f"{path} is not a list"]
        problems = []
        if len(value) > schema.get("maxItems", len(value)):
            problems.append(f"{path} has more than {schema['maxItems']} items")
        if "items" in schema:
            for index, item in enumerate(value):
                problems += validate(schema["items"], item, f"{path}[{index}]")
        return problems
    if kind == "string":
        if not isinstance(value, str):
            return [f"{path} is not text"]
        if "enum" in schema and value not in schema["enum"]:
            return [f"{path} is not one of {', '.join(schema['enum'])}"]
        if len(value) > schema.get("maxLength", len(value)):
            return [f"{path} is longer than {schema['maxLength']} characters"]
        return []
    if kind == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            return [f"{path} is not a whole number"]
        if value < schema.get("minimum", value):
            return [f"{path} is below {schema['minimum']}"]
        return []
    if not isinstance(value, bool):
        return [f"{path} is not true or false"]
    return []


def all_text(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from all_text(item)
    elif isinstance(value, list):
        for item in value:
            yield from all_text(item)


def reads_like_noise(token: str) -> bool:
    """Whether the letters of a token are mixed like those of a random key.

    Words, in paths and in the names of code, change between lower and upper case
    only where a word starts, and run on in lower case for several letters. The
    letters of a random key change case at about every second pair, and run on in
    lower case for two letters on average.
    """
    pairs = len(LETTER_PAIR.findall(token))
    changes = len(CASE_CHANGE.findall(token))
    runs = LOWER_CASE.findall(token)
    # At least one change in five pairs, and 3.5 letters per run on average or fewer.
    return 5 * changes >= pairs and 2 * sum(map(len, runs)) <= 7 * len(runs)


def names_a_file(token: str, paths: str) -> bool:
    """Whether the token is a part of one of the paths, one per line, that begins and
    ends at a slash or a dot.

    A token ends before the dot of an extension, as in `File.java`, and starts after
    the dot of a folder, as in `.github/`.
    """
    part = re.escape(token.strip("/"))
    return bool(re.search(rf"(?<![^\n./]){part}(?![^\n./])", paths))


def looks_secret(text: str, files: Collection[str] = ()) -> bool:
    """Whether the text holds something that looks like a token or a key.

    A known kind of token counts wherever it is. Any other run of 40 or more letters,
    digits and `+/=_-` counts when it mixes lower case, upper case and digits, and
    either has the `+` or `=` of Base64, or its letters read like noise. It doesn't
    count when it is a part of the path of one of `files`, the files of the
    repository, which are public. In a link to GitHub, the owner and the name of the
    repository, and the commit or branch of a file, are never keys.
    """
    if SECRET.search(text):
        return True
    paths = "\n".join(files)
    return any(
        re.search("[a-z]", token)
        and re.search("[A-Z]", token)
        and re.search("[0-9]", token)
        and ("+" in token or "=" in token or reads_like_noise(token))
        and not names_a_file(token, paths)
        for token in LONG_TOKEN.findall(GITHUB_REPOSITORY.sub(" ", text))
    )


def answer_json(raw: str) -> Any:
    """The JSON of the engine's answer. An engine that can't enforce the schema may
    wrap it in a code fence, or write a sentence before or after it."""
    text = raw.strip()
    if fenced := re.fullmatch(r"```(?:json)?[ \t]*\n(.*)\n```", text, re.DOTALL):
        text = fenced[1]
    try:
        return json.loads(text)
    except ValueError as error:
        problem = error
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(text[start : end + 1])
        except ValueError:
            pass
    raise AssistantError(f"The engine's answer is no JSON: {problem}") from problem


def without_nulls(schema: dict[str, Any], value: Any) -> Any:
    """The answer without the null of a field that it may leave out, which an engine
    with the strict schema writes; a null in a required field stays, for `validate`
    to refuse."""
    if schema.get("type") == "object" and isinstance(value, dict):
        fields = schema["properties"]
        return {
            key: without_nulls(fields[key], item) if key in fields else item
            for key, item in value.items()
            if item is not None or key in schema["required"]
        }
    if schema.get("type") == "array" and isinstance(value, list) and "items" in schema:
        return [without_nulls(schema["items"], item) for item in value]
    return value


def parse_answer(mode: str, raw: str, config: Config) -> dict[str, Any]:
    schema = answer_schema(mode, config)
    answer = without_nulls(schema, answer_json(raw))
    # Before the checks, so that no invisible character splits a token that the
    # check would find, and nothing that GitHub doesn't show reaches it.
    visible = Visible()
    answer = visible.everywhere(answer)
    if visible.removed:
        command(
            "notice",
            f"Removed {visible.removed} invisible characters from the engine's answer.",
        )
    # An engine may not enforce the schema, and a hint that nobody sees must never
    # cost the reporter the analysis.
    newcomer = schema["properties"].get("newcomer")
    if (
        newcomer
        and isinstance(answer, dict)
        and "newcomer" in answer
        and validate(newcomer, answer["newcomer"])
    ):
        command(
            "notice",
            "Left out the engine's note on a newcomer: it breaks the schema.",
        )
        del answer["newcomer"]
    problems = validate(schema, answer)
    if problems:
        raise AssistantError(
            "The engine's answer breaks the schema: " + "; ".join(problems)
        )
    # Paths of the repository may look random and are public. Git lists them only
    # for an answer that would be refused without them.
    if any(map(looks_secret, all_text(answer))):
        files = tracked_files(config.root)
        if any(looks_secret(item, files) for item in all_text(answer)):
            raise AssistantError(
                "The engine's answer contains text that looks like a token or key; "
                "nothing was posted."
            )
    # A summary for the maintainers is for an answer that they can't read: in their
    # own language, it would only repeat the answer to them.
    own = maintainers(config)
    if answer.get("maintainer_summary", "").strip() and answer["language"] == own:
        command(
            "notice",
            "Left out the engine's summary for the maintainers: its answer is in "
            f"their language, {LOCALES[own].name}, already.",
        )
        answer["maintainer_summary"] = ""
    result: dict[str, Any] = answer
    return result
