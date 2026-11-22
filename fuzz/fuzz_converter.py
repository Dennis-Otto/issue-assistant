"""Coverage-guided fuzzing of the converter of way D with Atheris: no HTML is slow.

The converter (issue_assistant/converter.py, #520) is to read GitHub's HTML of every
text that anyone can write, and GitHub's HTML of a text can be twice as long as the
text. This fuzzer builds HTML of up to twice GitHub's 65,536 characters from a
fuzzed start, a fuzzed piece repeated many times and a fuzzed end, the shape of the
texts that make a reading grow faster than its text, and fails when converting one
takes longer than half a second, or when the Markdown outgrows the bound of its
length. The dictionary fuzz/fuzz_converter.dict gives it the tags, attributes and
classes of the closed list.

    pip install --require-hashes -r requirements-fuzz.txt
    python fuzz/fuzz_converter.py -max_total_time=60

A finding stops the fuzzer with the input that caused it; the workflow fuzz.yml runs
it on every change and longer every week.
"""

import sys
import time
from pathlib import Path

import atheris

from seeds import with_seeds

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

with atheris.instrument_imports():
    import issue_assistant as assistant

# The most processor time that one conversion may take; one in linear time needs a
# small part of it, also with the fuzzer's instrumentation.
BUDGET = 0.5
LONGEST_HTML = 2 * assistant.MAX_TEXT


def html_from(provider: atheris.FuzzedDataProvider) -> tuple[str, str]:
    """HTML of up to LONGEST_HTML characters, and how it was built, for the report."""
    start = provider.ConsumeUnicodeNoSurrogates(provider.ConsumeIntInRange(0, 64))
    piece = provider.ConsumeUnicodeNoSurrogates(provider.ConsumeIntInRange(1, 32))
    end = provider.ConsumeUnicodeNoSurrogates(64)
    room = LONGEST_HTML - len(start) - len(end)
    count = provider.ConsumeIntInRange(0, room // max(len(piece), 1))
    return start + piece * count + end, f"{start!r} + {piece!r} * {count} + {end!r}"


def test_one_input(data: bytes) -> None:
    provider = atheris.FuzzedDataProvider(data)
    html, built = html_from(provider)
    started = time.process_time()
    result = assistant.markdown_of(html, html)
    seconds = time.process_time() - started
    if seconds > BUDGET:
        raise AssertionError(f"markdown_of took {seconds:.2f} s for {built}")
    if len(result.text) > assistant.LONGEST + len(assistant.CUT_NOTE) + 1:
        raise AssertionError(f"markdown_of wrote {len(result.text)} for {built}")


def main() -> None:
    # A reading that grows with the square of its text can take minutes on one
    # input, and BUDGET is checked only after a call returns: unless the command
    # line sets another limit, libFuzzer stops an input after five seconds.
    argv = with_seeds(sys.argv, "fuzz_converter")
    timeout = any(arg.startswith("-timeout=") for arg in argv)
    atheris.Setup(argv if timeout else [*argv, "-timeout=5"], test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
