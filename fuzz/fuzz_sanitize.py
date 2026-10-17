"""Coverage-guided fuzzing of the cleaning of the engine's text, with Atheris.

The property tests in tests/test_properties.py try random text; this fuzzer follows
the branches of the code to find text that the cleaning lets through as a mention,
a reference, a foreign link or HTML, in a comment or in one line of the log, the job
summary or the timeline. Both judge the result with tests/oracle.py.

    pip install --require-hashes -r requirements-fuzz.txt
    python fuzz/fuzz_sanitize.py -max_total_time=60

An input is three bytes and then the text, as UTF-8: the first two bytes say which
of the issues #1 to #16 were checked, a bit each, and, read backwards, which of the
issues #1 to #16 of the related repository owner/server, so that the cleaning also
makes a checked owner/server#N a link; the third says how long a line of the log may
be, 1 to 256 characters. The dictionary fuzz/fuzz_sanitize.dict and the
seeds in fuzz/corpus/fuzz_sanitize/, the texts that once got past the cleaning, give
it the words of Markdown from the start. A finding stops the fuzzer with the input
that caused it; the workflow fuzz.yml runs it on every change and longer every week.
"""

import dataclasses
import sys
from pathlib import Path

import atheris

from seeds import with_seeds

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

with atheris.instrument_imports():
    import issue_assistant as assistant
    from oracle import problems
    from support import CONFIG

# A related repository, whose checked issues the cleaning makes links.
RELATED = "owner/server"
SETTINGS = dataclasses.replace(CONFIG, related_repositories=(RELATED,))


def test_one_input(data: bytes) -> None:
    head = data[:3].ljust(3, b"\0")
    mask = int.from_bytes(head[:2], "little")
    checked = [number for number in range(1, 17) if mask >> (number - 1) & 1]
    numbers: set[int | str] = set(checked)
    numbers |= {f"{RELATED}#{17 - number}" for number in checked}
    limit = 1 + head[2]
    text = data[3:].decode("utf-8", "replace")
    output = assistant.sanitize(text, SETTINGS, numbers)
    found = problems(output, numbers, SETTINGS)
    if found:
        raise AssertionError(f"{found} in {output!r} from {text!r}")
    # A line stands alone as a rationale, and after other text in the log.
    line = assistant.plain(text, SETTINGS, limit)
    found = problems(line, set(), SETTINGS)
    found += problems(f"- Reason: {line}", set(), SETTINGS)
    if len(line) > limit or assistant.CONTROL.search(line):
        found.append(f"a line longer than {limit} or with a control character")
    if found:
        raise AssertionError(f"{found} in {line!r} from {text!r}")


def main() -> None:
    atheris.Setup(with_seeds(sys.argv, "fuzz_sanitize"), test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
