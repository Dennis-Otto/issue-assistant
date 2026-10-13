---
description: The commands of the issue assistant, every input and output of its GitHub Action, its AI engines, and how to try a prompt on your own computer.
---

# Reference

The commands of `issue_assistant.py`, the inputs and outputs of the action that runs them, the AI engines that it supports, and how to try a prompt on your own computer.

--8<-- "README.md:commands"

## Inputs and outputs of the action

The workflows of the templates set the inputs of the action; a workflow of your own, such as the check in the CI, sets `command`, and for a command that writes maybe `dry-run`. The tables come from `action.yml` of this version.

### Inputs

<!-- action.yml: inputs -->

### Outputs

The commands `event`, `engine` and `context` set the outputs for the steps and jobs after them.

<!-- action.yml: outputs -->

--8<-- "README.md:engines"

--8<-- "README.md:try-a-prompt"
