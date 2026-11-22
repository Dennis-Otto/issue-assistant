"""Coverage-guided fuzzing of the converter of way D with Atheris: no HTML is slow.

The converter (issue_assistant/converter.py, #520) is to read GitHub's HTML of every
text that anyone can write, and GitHub's HTML of a text can be twice as long as the
text. This fuzzer builds HTML of up to twice GitHub's 65,536 characters from a
fuzzed start, a fuzzed piece repeated many times and a fuzzed end, the shape of the
texts that make a reading grow faster than its text. It fails when the converting of
one grows faster than its text, as the timing tests judge a reading, or when the
Markdown outgrows the bound of its length. The dictionary fuzz/fuzz_converter.dict
gives it the tags, attributes and classes of the closed list.

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
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

with atheris.instrument_imports():
    import issue_assistant as assistant
    from support import too_slow

# A conversion of up to this much processor time passes at once: one in linear time
# needs a part of it, also with the fuzzer's instrumentation.
BUDGET = 0.5
# A slower one passes only on its growth, as the timing tests judge a reading: it
# fails when the whole text takes GROWTH times as long as the text with an eighth of
# its repetitions, or longer than CEILING. A fixed half second measured the runner,
# not the converter: dense but linear HTML, such as the rows of a table, takes 0.28
# seconds under the instrumentation on a machine of a maintainer and about 0.47 on a
# slow runner of GitHub, which failed an input of 0.51 seconds by chance (#528, as
# #500 and #504 found for the timing tests). The promise is linear time. The ceiling
# fits the timeout of TIMEOUT seconds that main() gives libFuzzer, which counts the
# whole call in wall time, not processor time: the growth calls the whole three more
# times and the eighth three times, about 4.4 seconds of processor time for a first
# call of a second, and a busy runner can take longer in wall time than in processor
# time: the timeout leaves more than twice that, so an input of up to a second ends
# with the message of the growth rather than a bare timeout. CEILING of the tests (2 s) would need
# twice the timeout.
CEILING = 1
TIMEOUT = 10
LONGEST_HTML = 2 * assistant.MAX_TEXT


def html_from(provider: atheris.FuzzedDataProvider) -> tuple[str, str, int, str]:
    """The start, the piece, how often it repeats and the end of HTML of up to
    LONGEST_HTML characters."""
    start = provider.ConsumeUnicodeNoSurrogates(provider.ConsumeIntInRange(0, 64))
    piece = provider.ConsumeUnicodeNoSurrogates(provider.ConsumeIntInRange(1, 32))
    end = provider.ConsumeUnicodeNoSurrogates(64)
    room = LONGEST_HTML - len(start) - len(end)
    count = provider.ConsumeIntInRange(0, room // max(len(piece), 1))
    return start, piece, count, end


def converting(html: str):
    """A call of the converter on the HTML."""
    return lambda: assistant.markdown_of(html, html)


def test_one_input(data: bytes) -> None:
    provider = atheris.FuzzedDataProvider(data)
    start, piece, count, end = html_from(provider)
    html = start + piece * count + end
    built = f"{start!r} + {piece!r} * {count} + {end!r}"
    started = time.process_time()
    result = assistant.markdown_of(html, html)
    seconds = time.process_time() - started
    eighth = converting(start + piece * (count // 8) + end)
    problem = too_slow(seconds, count, eighth, converting(html), BUDGET, CEILING)
    if problem:
        raise AssertionError(f"markdown_of {problem} for {built}")
    if len(result.text) > assistant.LONGEST + len(assistant.CUT_NOTE) + 1:
        raise AssertionError(f"markdown_of wrote {len(result.text)} for {built}")


def main() -> None:
    # A reading that grows with the square of its text can take minutes on one
    # input, and the time is judged only after a call returns: unless the command
    # line sets another limit, libFuzzer stops an input after TIMEOUT seconds.
    argv = with_seeds(sys.argv, "fuzz_converter")
    timeout = any(arg.startswith("-timeout=") for arg in argv)
    atheris.Setup(argv if timeout else [*argv, f"-timeout={TIMEOUT}"], test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
