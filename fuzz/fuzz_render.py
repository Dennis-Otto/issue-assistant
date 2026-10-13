"""Coverage-guided fuzzing of whole comments, with Atheris: an answer goes in, and
the comment that the assistant posts comes out, judged by tests/oracle.py.

fuzz/fuzz_sanitize.py fuzzes the cleaning of one text. This fuzzer fuzzes what
reporters see: an answer of the engine, built from its schema, passes the checks of
`apply` and becomes a first analysis or a follow-up, with every text where the
comment puts it, under a heading, in a list or behind an issue number, next to the
assistant's own words. Only those may mention the reporter, show HTML or link to the
security policy.

    pip install --require-hashes -r requirements-fuzz.txt
    python fuzz/fuzz_render.py -max_total_time=60

An input is two bytes, the choices and then the text, as UTF-8. The first byte is
the kind of comment, odd for a follow-up; the second says how many bytes of choices
follow, which choose the kind of issue, the language, how many items each list has
and the like. Without a choice left, the answer shows the text the most: every
field and list item, and no security report, whose comment shows none of it. The
text goes into every field, cut to the length that the schema allows. The
dictionary fuzz/fuzz_render.dict and the seeds in fuzz/corpus/fuzz_render/ give it
the words of Markdown and the texts that once got past the cleaning from the start.
A finding stops the fuzzer with the input that caused it.
"""

import contextlib
import dataclasses
import functools
import io
import sys
from pathlib import Path

import atheris

from seeds import with_seeds

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

with atheris.instrument_imports():
    import issue_assistant as assistant
    from support import CONFIG, answer_of, posted_comments, stray_problems

# The settings of the tests in this repository, whose files the references name.
SETTINGS = dataclasses.replace(CONFIG, root=ROOT)
FILES = ("README.md", "./docs/security.md", "issue_assistant.py")
# The files that git tracks stay the same while the fuzzer runs: one call of git
# instead of one for each input.
assistant.tracked_files = functools.cache(assistant.tracked_files)


class Choices:
    """The choices of an answer for `answer_of`, each from the next byte, and the
    text for every field."""

    def __init__(self, data: bytes, text: str) -> None:
        self.data = iter(data)
        self.words = text
        self.numbers = 0

    def next(self) -> int | None:
        return next(self.data, None)

    def text(self, key: str, limit: int) -> str:
        choice = self.next() if key == "path" else len(FILES)
        if choice is None or choice % (len(FILES) + 1) < len(FILES):
            return FILES[(choice or 0) % (len(FILES) + 1)]
        return self.words[:limit]

    def count(self, key: str, most: int) -> int:
        choice = self.next()
        return most if choice is None else choice % (most + 1)

    def one_of(self, key: str, values: list[str]) -> str:
        choice = self.next()
        return values[0] if choice is None else values[choice % len(values)]

    def number(self, key: str, least: int) -> int:
        choice = self.next()
        if choice is not None:
            return least + choice % 24
        # Issues that exist and are older than #12, the issue of the comment, one
        # after the other, so that each duplicate and related issue is shown.
        self.numbers += 1
        return least + (6 + 2 * self.numbers) % 11

    def boolean(self, key: str) -> bool:
        choice = self.next()
        return choice is not None and bool(choice & 1)

    def present(self, key: str) -> bool:
        choice = self.next()
        return choice is None or bool(choice & 1)


def test_one_input(data: bytes) -> None:
    mode = "follow-up" if data[:1] and data[0] & 1 else "triage"
    count = data[1] if len(data) > 1 else 0
    text = data[2 + count :].decode("utf-8", "replace")
    choices = Choices(data[2 : 2 + count], text)
    answer = answer_of(assistant.answer_schema(mode, SETTINGS), choices)
    # The notice of the invisible characters that the checks remove.
    with contextlib.redirect_stdout(io.StringIO()):
        bodies, numbers = posted_comments(SETTINGS, mode, answer)
    for body in bodies:
        found = stray_problems(body, numbers, SETTINGS)
        if found:
            raise AssertionError(f"{found} in {body!r} from {answer!r}")


def main() -> None:
    atheris.Setup(with_seeds(sys.argv, "fuzz_render"), test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
