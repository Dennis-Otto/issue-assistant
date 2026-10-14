"""A stand-in for gh, GitHub's command line tool, for the tests of the GitHub class.

The tests start it with Python's real subprocess.run and the options of the
assistant, as `python fake_gh.py ARGUMENTS`, where the assistant runs
`gh ARGUMENTS`; tests/support.py's fake_gh sets it up.

Each call adds a line to the file that FAKE_GH_LOG names: a JSON object with the
arguments and the text read from standard input, which gh reads only for
`--input -`, else null. The answer is the next one of the JSON list in the file
FAKE_GH_ANSWERS, an object with the stdout, the stderr and the exit status; the
last one repeats.
"""

import json
import os
import sys
from pathlib import Path


def main() -> int:
    arguments = sys.argv[1:]
    log = Path(os.environ["FAKE_GH_LOG"])
    reads = any(
        pair == ("--input", "-") for pair in zip(arguments, arguments[1:], strict=False)
    )
    # As bytes, so that the test sees the encoding of the assistant, not this one's.
    text = sys.stdin.buffer.read().decode("utf-8") if reads else None
    earlier = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"arguments": arguments, "input": text}) + "\n")
    answers = json.loads(Path(os.environ["FAKE_GH_ANSWERS"]).read_text("utf-8"))
    answer = answers[min(len(earlier), len(answers) - 1)]
    sys.stdout.buffer.write(answer.get("stdout", "").encode("utf-8"))
    sys.stderr.buffer.write(answer.get("stderr", "").encode("utf-8"))
    return int(answer.get("status", 0))


if __name__ == "__main__":
    sys.exit(main())
