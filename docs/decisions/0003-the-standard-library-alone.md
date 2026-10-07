# Run on the standard library of Python alone

- Status: accepted
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
