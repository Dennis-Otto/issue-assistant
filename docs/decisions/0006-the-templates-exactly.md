# Every repository uses the workflows exactly as the templates have them

- Status: accepted
- Date: 2026-10-07

## Context

The security of the action depends on its workflows: the permissions of each job, the runner with the egress firewall and the order of its steps. A repository that edits them can lose that without noticing.

## Options

1. Document the workflows and let each repository adapt them.
2. Install them from templates and check in every repository's CI that they are unchanged.

## Decision

Option 2: `install` writes the workflows from `templates/`, and `check` fails when a workflow differs from its template in anything but the commit hashes of its actions. The tests of this repository pin the rules of the templates.

## Consequences

A change of a template is a change for every repository that uses the action, which must run `install` again; the pull request of such a change says so.
