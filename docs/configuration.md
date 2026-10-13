---
description: The settings of the issue assistant in labels.toml, config.toml and findings.toml, with every key, its type, its default and what the check makes sure of.
---

# Configuration

A repository sets up the issue assistant in three files of `.github/`, which `install` writes as starters when they are missing: the labels, the settings, and the findings of code scanning that it accepts. They change through pull requests like the code, and `check` makes sure of them in the CI.

--8<-- "README.md:settings"

--8<-- "README.md:findings"

## Every key

Each key of the three files, its type, its default and what `check` makes sure of. A key that says *required* has no default.

### labels.toml

Each `[[label]]` of `.github/labels.toml` is a label of the repository:

| Key | Type | Default | What it does | What `check` makes sure of |
| --- | --- | --- | --- | --- |
| `name` | string | required | The name of the label | No other label has it |
| `color` | string | required | Its colour, six hexadecimal digits without `#` | Six digits, such as `d73a4a` |
| `description` | string | required | What the label means; GitHub shows it with the label | 1 to 100 characters |
| `group` | string | required | `type`, `area`, `topic`, `lifecycle`, `decision`, `release` or `dependabot`. The AI chooses one label of the group `type`, the kind of the issue, and any of `area` and `topic` | One of these groups, and at least one label of the group `type` |
| `form_options` | list of strings | `[]` | The answers of the area field of the issue forms that choose the label | Only kinds, areas and topics have them; each is an option of a form and belongs to one label alone |
| `ask` | boolean | `true` | For a kind: whether the AI may ask its reporters for information | Nothing |

The group `lifecycle` needs the seven labels that the assistant sets: `needs-triage`, `needs-info`, `stale`, `possible-duplicate`, `duplicate`, `invalid` and `fixed-in-next-release`, the last one only while `close_with_release` is `true`.

### config.toml

`.github/issue-assistant/config.toml` is optional; without it, every key has its default.

| Key | Type | Default | What it does | What `check` makes sure of |
| --- | --- | --- | --- | --- |
| `[engine] name` | string | `"claude"` | The AI engine that writes the analysis | An engine of the assistant: `claude` |
| `[engine] model` | string | `""` | The model; empty for the engine's default, `claude-opus-5-5` for Claude | A model name: a letter or a digit, then letters, digits and `._:@/[]-` |
| `[links] hosts` | list of strings | `[]` | The hosts that links in the comments may lead to, besides the repository itself | Each is a host name, such as `docs.example.com` |
| `[forms] area_field` | string | `"Area"` | The dropdown of the issue forms whose answer chooses an area label | Each of its options belongs to one label, or to `unmapped_options` |
| `[forms] unmapped_options` | list of strings | `["Other"]` | Answers of that dropdown that choose no label | Nothing |
| `[transparency] files` | list of strings | `["SUPPORT.md"]` | The files that tell reporters which AI reads their issue | Each exists and names the engine and its vendor: Claude and Anthropic |
| `[releases] close_with_release` | boolean | `true` | Whether a fixed issue stays open until a release ships the fix; `false` for a repository without releases | It is `true` or `false` |

### findings.toml

Each `[[accept]]` of `.github/findings.toml` accepts the alerts of one rule of code scanning:

| Key | Type | Default | What it does | What `check` makes sure of |
| --- | --- | --- | --- | --- |
| `tool` | string | required | The tool, as code scanning names it, in any case | It isn't empty |
| `rule` | string | required | The rule, such as `py/unused-import` | It isn't empty |
| `reason` | string | required | The reason that GitHub records for the dismissal | `false positive`, `won't fix` or `used in tests` |
| `comment` | string | required | Why, in words, shown with the dismissed alert | 1 to 200 characters |
| `path` | string | `""` | Only alerts in files under this path; empty for every file | Nothing |
