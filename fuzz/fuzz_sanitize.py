"""Coverage-guided fuzzing of the cleaning of the engine's text, with Atheris.

The property tests in tests/test_properties.py try random text; this fuzzer follows
the branches of the code to find text that the cleaning lets through as a mention,
a reference, a foreign link or HTML, in a comment or in one line of the log, the job
summary or the timeline. Both judge the result with tests/oracle.py.

    pip install --require-hashes -r requirements-fuzz.txt
    python fuzz/fuzz_sanitize.py -max_total_time=60

A finding stops the fuzzer with the input that caused it; the workflow fuzz.yml runs
it on every change of the cleaning and longer every week.
"""

import sys
from pathlib import Path

import atheris

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

with atheris.instrument_imports():
    import issue_assistant as assistant
    from oracle import CONFIG, problems


def test_one_input(data: bytes) -> None:
    provider = atheris.FuzzedDataProvider(data)
    numbers = {
        provider.ConsumeIntInRange(1, 20)
        for _ in range(provider.ConsumeIntInRange(0, 3))
    }
    limit = provider.ConsumeIntInRange(1, 300)
    text = provider.ConsumeUnicodeNoSurrogates(2000)
    output = assistant.sanitize(text, CONFIG, numbers)
    found = problems(output, numbers)
    if found:
        raise AssertionError(f"{found} in {output!r} from {text!r}")
    # A line stands alone as a rationale, and after other text in the log.
    line = assistant.plain(text, CONFIG, limit)
    found = problems(line, set()) + problems(f"- Reason: {line}", set())
    if len(line) > limit or assistant.CONTROL.search(line):
        found.append(f"a line longer than {limit} or with a control character")
    if found:
        raise AssertionError(f"{found} in {line!r} from {text!r}")


def main() -> None:
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
