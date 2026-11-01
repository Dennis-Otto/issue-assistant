# Run on the standard library of Python alone

- Status: accepted, amended on 2026-11-01 by the package below
- Date: 2026-10-07

## Context

The action runs in the workflows of every repository that uses it, with access to their issues. Every third-party package it installed would be code that runs there and could be compromised.

## Options

1. Use packages from PyPI, pinned by hash.
2. Use the standard library of the Python of GitHub's runners alone.

## Decision

Option 2: `issue_assistant.py` imports nothing outside the standard library of Python 3.12. Only the tests, the property tests and the fuzzing use packages, hash-pinned in their requirements.

## Consequences

The action installs nothing and has no supply chain of its own beyond the actions it pins. Some code, such as the requests to GitHub, is written by hand.

## Amendment: a package of modules (2026-11-01)

The standard library alone stays. What changes is the form of the code: `issue_assistant.py` had grown to 11,600 lines and 480 functions. Reviews and blame covered the whole file, mypy and coverage reported on one unit, mutmut built one mutants file of many megabytes, and a new contributor had no map.

### Options

1. Keep one module, with better headings.
2. A package of modules that import each other freely, with imports inside functions where two need each other.
3. A package in layers, where each module imports only from those before it.

### Decision

Option 3. The code is the package `issue_assistant/`, with a module for each part, and [a map of them](../architecture/modules.md). `tests/test_package.py` checks the layers, that only the standard library is imported (and PyYAML where `check` finds it, with `yq` otherwise), and that no module grows past about 1,500 lines or function past 150.

- **The action** runs `PYTHONPATH="$GITHUB_ACTION_PATH${PYTHONPATH:+:$PYTHONPATH}" python3 -P -B -m issue_assistant "$COMMAND"` in the repository it looks after. `-m` alone would put that checkout first in the path of imports, where a `json.py` of a pull request would run with the token of the job; `-P` keeps it out, and a test starts the action in such a checkout. `-B` leaves the folder of the action as its commit has it, as the script did. A `PYTHONPATH` of the caller still counts, as it did for the script, behind the tool, and an unset one adds no empty entry, which Python would read as the current folder. The checkouts of the tool list `/issue_assistant/`.
- **`__init__.py` hands on every name** with `from .module import *`, so the tests, the fuzzers and the scripts stay as they were, with `assistant.render_analysis`; the code of the modules never goes through it.
- **A test that replaces a name** does so in the module that looks it up, since each module holds what it imports under a name of its own.

### Consequences

A review and a blame see one part, and a new module is a line in `__init__.py` and in the map. A name used by a new module may have to move down a layer, which keeps the layers honest. Whoever ran `python3 issue_assistant.py` runs `python3 -m issue_assistant`, with `PYTHONPATH` at the checkout of the tool from another folder.
