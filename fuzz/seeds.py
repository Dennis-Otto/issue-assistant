"""The dictionary and the seed inputs of a fuzz target, on libFuzzer's command line.

A target such as fuzz/fuzz_render.py has its dictionary of Markdown in
fuzz/fuzz_render.dict and its seed inputs, the texts that once got past the cleaning,
in fuzz/corpus/fuzz_render/. libFuzzer reads both from the start, unless the command
line names a dictionary or seed inputs of its own, so the Fuzzing workflow and a run
by hand use them alike. The seeds are read, never written: libFuzzer keeps what it
finds in the folder that the command line names, if any.
"""

from pathlib import Path

FUZZ = Path(__file__).resolve().parent


def with_seeds(argv: list[str], target: str) -> list[str]:
    """The command line with the target's dictionary and seed inputs added."""
    arguments = list(argv)
    dictionary = FUZZ / f"{target}.dict"
    if dictionary.is_file() and not any(arg.startswith("-dict=") for arg in argv):
        arguments.append(f"-dict={dictionary}")
    corpus = FUZZ / "corpus" / target
    seeds = sorted(str(path) for path in corpus.glob("*") if path.is_file())
    named = {Path(arg).resolve() for arg in argv[1:] if not arg.startswith("-")}
    if (
        seeds
        and corpus.resolve() not in named
        and not any(arg.startswith("-seed_inputs=") for arg in argv)
    ):
        arguments.append("-seed_inputs=" + ",".join(seeds))
    return arguments
