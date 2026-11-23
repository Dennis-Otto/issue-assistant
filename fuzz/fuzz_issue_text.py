"""Coverage-guided fuzzing of the reading of issues with Atheris: no text is slow.

Anyone can open an issue, and every analysis reads the text of every issue of the
repository. A text that kept one of the patterns busy for minutes would stall each
analysis until its job timed out. This fuzzer builds texts of up to GitHub's 65,536
characters from a fuzzed start, a fuzzed piece repeated many times and a fuzzed
end, the shape of the texts that make patterns backtrack. It fails when a reading of
one of them grows faster than its text, as the timing tests judge a reading, or
takes longer than CEILING. The dictionary fuzz/fuzz_issue_text.dict gives it the
pieces whose reading takes the most steps, such as the pipes of a table, the fences
that GitHub draws, the dollar signs of math and the references of characters.

    pip install --require-hashes -r requirements-fuzz.txt
    python fuzz/fuzz_issue_text.py -max_total_time=60

A finding stops the fuzzer with the input that caused it; the workflow fuzz.yml runs
it on every change and longer every week.
"""

import sys
import time
from collections.abc import Callable
from pathlib import Path

import atheris

from seeds import with_seeds

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

with atheris.instrument_imports():
    import issue_assistant as assistant
    from support import too_slow

# A reading of up to this much processor time passes at once: one in linear time
# needs a part of it, also with the fuzzer's instrumentation.
BUDGET = 0.5
# A slower one passes only on its growth, as the timing tests judge a reading: it
# fails when the whole text takes GROWTH times as long as the text with an eighth of
# its repetitions, or longer than CEILING. A fixed half second measured the machine,
# not the reading: quotes before a void tag, then a short row of a void tag again
# and again (test_the_fuzz_target_judges_a_slow_reading_by_its_growth of
# tests/test_assistant.py), is linear, but a reading of it took 0.8 to 1.13 seconds
# under the instrumentation in a container of four processors, and up to 1.35
# seconds in the tries of its growth, and runners of GitHub were found 1.65 times
# as slow as such a machine (#528, #543): about 2.2 seconds. The ceiling leaves room
# above that.
CEILING = 3
# libFuzzer counts the whole call of an input in wall time, not processor time: five
# readings, each with its first call and, above BUDGET, the tries of the growth, the
# whole three times more and the eighth three times, about 4.4 times a call. At the
# ceiling that is 5 times 4.4 times 3 seconds, about 66 seconds of processor time,
# and a busy runner takes longer in wall time than in processor time: the timeout
# leaves nearly twice that, so that an input in linear time ends with the message of
# the growth rather than a bare timeout. That text took 17 seconds in all. The sixth
# reading, rendering_estimate, takes a few hundredths of a second on a whole text,
# far below BUDGET, so it adds no tries.
TIMEOUT = 120
USER = {"login": "reporter", "type": "User"}
CREATED = "2026-10-13T00:00:00Z"


def text_from(provider: atheris.FuzzedDataProvider) -> tuple[str, str, int, str]:
    """The start, the piece, how often it repeats and the end of a text of up to
    MAX_TEXT characters."""
    start = provider.ConsumeUnicodeNoSurrogates(provider.ConsumeIntInRange(0, 64))
    piece = provider.ConsumeUnicodeNoSurrogates(provider.ConsumeIntInRange(1, 16))
    end = provider.ConsumeUnicodeNoSurrogates(64)
    room = assistant.MAX_TEXT - len(start) - len(end)
    count = provider.ConsumeIntInRange(0, room // max(len(piece), 1))
    return start, piece, count, end


def readings(text: str) -> list[tuple[str, Callable[[], object]]]:
    """The readings of the text as the body of an issue and of one comment, each a
    call of its own."""
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
    return [
        ("form_fields", lambda: assistant.form_fields(text)),
        ("excerpt", lambda: assistant.excerpt(text, assistant.MAX_TEXT // 4)),
        ("render_issue", lambda: assistant.render_issue(issue, comments)),
        ("issue_language", lambda: assistant.issue_language(issue, comments)),
        (
            "attachments_of",
            lambda: assistant.attachments_of(issue, comments, "owner/repository"),
        ),
        (
            "rendering_estimate",
            lambda: assistant.rendering_estimate(text, "owner/repository"),
        ),
    ]


def test_one_input(data: bytes) -> None:
    provider = atheris.FuzzedDataProvider(data)
    start, piece, count, end = text_from(provider)
    built = f"{start!r} + {piece!r} * {count} + {end!r}"
    eighths = readings(start + piece * (count // 8) + end)
    for (name, read), (_, eighth) in zip(
        readings(start + piece * count + end), eighths, strict=True
    ):
        started = time.process_time()
        read()
        seconds = time.process_time() - started
        problem = too_slow(seconds, count, eighth, read, BUDGET, CEILING)
        if problem:
            raise AssertionError(f"{name} {problem} for {built}")


def main() -> None:
    # A pattern that backtracks can take hours on one input, and the time is judged
    # only after a call returns: unless the command line sets another limit,
    # libFuzzer stops an input after TIMEOUT seconds and saves it.
    argv = with_seeds(sys.argv, "fuzz_issue_text")
    timeout = any(arg.startswith("-timeout=") for arg in argv)
    atheris.Setup(argv if timeout else [*argv, f"-timeout={TIMEOUT}"], test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
