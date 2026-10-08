# The AI only reads; a job without AI writes

- Status: accepted
- Date: 2026-10-07

## Context

Issues come from anyone, so an issue may try to steer the AI that analyzes it: to run commands, reach other sites, read secrets or post what the attacker wants.

## Options

1. One job that runs the AI with a token that may comment and label.
2. Two jobs: the AI reads in one, with a read-only token, behind an egress firewall and with reading tools alone; a second job without AI checks its answer and writes.

## Decision

Option 2. The `analyze` job runs the AI with `Read`, `Grep` and `Glob` alone on the egress-firewall runner. Its answer is JSON with a fixed schema. The `apply` job never sees the secret of the AI: it checks the answer against the schema, the labels and the issues that exist, refuses anything that looks like a token, turns mentions into code and drops images, HTML and foreign links before it writes.

## Consequences

A steered AI can at worst propose a wrong label or a wrong text, which the checks narrow down. Every rule that keeps the AI read-only or checks its answer has a test, and the property tests and the fuzzing of the cleaning judge it with an independent CommonMark parser.
