"""Coverage-guided fuzzing of the reading of issues with Atheris: no text is slow.

Anyone can open an issue, and every analysis reads the text of every issue of the
repository. A text that kept one of the patterns busy for minutes would stall each
analysis until its job timed out. This fuzzer builds texts of up to GitHub's 65,536
characters from a fuzzed start, a fuzzed piece repeated many times and a fuzzed
end, the shape of the texts that make patterns backtrack, and fails when reading
one of them takes longer than half a second.

    pip install --require-hashes -r requirements-fuzz.txt
    python fuzz/fuzz_issue_text.py -max_total_time=60

A finding stops the fuzzer with the input that caused it; the workflow fuzz.yml runs
it on every change and longer every week.
"""

import sys
import time
from pathlib import Path

import atheris

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

with atheris.instrument_imports():
    import issue_assistant as assistant

# The most processor time that one reading may take; a reading in linear time
# needs a small part of it, also with the fuzzer's instrumentation.
BUDGET = 0.5
USER = {"login": "reporter", "type": "User"}
CREATED = "2026-10-13T00:00:00Z"


def text_from(provider: atheris.FuzzedDataProvider) -> tuple[str, str]:
    """A text of up to MAX_TEXT characters, and how it was built, for the report."""
    start = provider.ConsumeUnicodeNoSurrogates(provider.ConsumeIntInRange(0, 64))
    piece = provider.ConsumeUnicodeNoSurrogates(provider.ConsumeIntInRange(1, 16))
    end = provider.ConsumeUnicodeNoSurrogates(64)
    room = assistant.MAX_TEXT - len(start) - len(end)
    count = provider.ConsumeIntInRange(0, room // max(len(piece), 1))
    return start + piece * count + end, f"{start!r} + {piece!r} * {count} + {end!r}"


def test_one_input(data: bytes) -> None:
    provider = atheris.FuzzedDataProvider(data)
    text, built = text_from(provider)
    issue = {
        "number": 1,
        "title": "Issue",
        "user": USER,
        "state": "open",
        "labels": [],
        "created_at": CREATED,
        "body": text,
    }
    comments = [{"id": 2, "user": USER, "created_at": CREATED, "body": text}]
    for name, read in (
        ("form_fields", lambda: assistant.form_fields(text)),
        ("excerpt", lambda: assistant.excerpt(text, assistant.MAX_TEXT // 4)),
        ("render_issue", lambda: assistant.render_issue(issue, comments)),
        ("issue_language", lambda: assistant.issue_language(issue, comments)),
    ):
        started = time.process_time()
        read()
        seconds = time.process_time() - started
        if seconds > BUDGET:
            raise AssertionError(f"{name} took {seconds:.2f} s for {built}")


def main() -> None:
    # A pattern that backtracks can take hours on one input, and BUDGET is checked
    # only after a call returns: unless the command line sets another limit,
    # libFuzzer stops an input after five seconds and saves it.
    timeout = any(arg.startswith("-timeout=") for arg in sys.argv)
    atheris.Setup(sys.argv if timeout else [*sys.argv, "-timeout=5"], test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
