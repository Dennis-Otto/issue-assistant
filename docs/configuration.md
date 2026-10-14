---
description: The settings of the issue assistant in labels.toml, config.toml and findings.toml, with every key, its type, its default and what the check makes sure of.
---

# Configuration

A repository sets up the issue assistant in three files of `.github/`, which `install` writes as starters when they are missing: the labels, the settings, and the findings of code scanning that it accepts. They change through pull requests like the code, and `check` makes sure of them in the CI.

--8<-- "README.md:settings"

--8<-- "README.md:findings"

## Every key

Each key of the three files, its type, its default and what `check` makes sure of. A key that says *required* has no default. Besides, `check` makes sure that every value has the type of its key, and fails on every key that these tables don't have, naming the key that was probably meant, such as `form_options` for `form_option`.

### labels.toml

Each `[[label]]` of `.github/labels.toml` is a label of the repository:

| Key | Type | Default | What it does | What `check` makes sure of |
| --- | --- | --- | --- | --- |
| `name` | string | required | The name of the label | 1 to 50 characters, as GitHub allows, and no other label has it, in any case: GitHub takes `bug` and `Bug` for one label |
| `color` | string | required | Its colour, six hexadecimal digits without `#` | Six digits, such as `d73a4a` |
| `description` | string | required | What the label means; GitHub shows it with the label | 1 to 100 characters |
| `group` | string | required | `type`, `area`, `topic`, `lifecycle`, `decision`, `release` or `dependabot`. The AI chooses one label of the group `type`, the kind of the issue, and any of `area` and `topic` | One of these groups, and at least one label of the group `type` |
| `form_options` | list of strings | `[]` | The answers of the area field of the issue forms that choose the label; of a dropdown with `multiple: true`, each chosen answer adds its label | Only kinds, areas and topics have them; each is an option of a form and belongs to one label alone |
| `ask` | boolean | `true` | For a kind: whether the AI may ask its reporters for information | It is `true` or `false` |

The group `lifecycle` needs the seven labels that the assistant sets: `needs-triage`, `needs-info`, `stale`, `possible-duplicate`, `duplicate`, `invalid` and `fixed-in-next-release`, the last one only while `close_with_release` is `true`.

### config.toml

`.github/issue-assistant/config.toml` is optional; without it, every key has its default.

| Key | Type | Default | What it does | What `check` makes sure of |
| --- | --- | --- | --- | --- |
| `[engine] name` | string | `"claude"` | The AI engine that writes the analysis | An engine of the assistant: `claude` |
| `[engine] model` | string | `""` | The model; empty for the engine's default, `claude-opus-5-5` for Claude | A model name: a letter or a digit, then letters, digits and `._:@/[]-` |
| `[engine] max_per_day` | integer | `20` | The most [AI runs](how-it-works.md#limits-of-the-ai-runs) that people who don't maintain the repository start in 24 hours: analyses of new issues, follow-ups and replies after a release; `0` lets only maintainers start the AI, by hand | A whole number, 0 or more |
| `[engine] max_per_author_per_day` | integer | `3` | The most analyses of one reporter's new issues in 24 hours | A whole number, 0 or more |
| `[engine] release_replies` | integer | `2` | How many comments of the reporter the AI reads after each release that closed their issue | A whole number, 0 or more |
| `[context] max_issues` | integer | `1000` | How many closed issues an analysis and a follow-up read besides every open one: those that changed last; see [what the AI reads](how-it-works.md#what-the-ai-reads) | A whole number, 0 or more |
| `[context] max_discussions` | integer | `1000` | How many discussions an analysis and a follow-up read: those that changed last; `0` reads none | A whole number, 0 or more |
| `[links] hosts` | list of strings | `[]` | The hosts that links in the comments may lead to, besides the repository itself | Each is a host name, such as `docs.example.com` |
| `[forms] area_field` | string | `"Area"` | The dropdown of the issue forms whose answer chooses an area label | Each of its options belongs to one label, or to `unmapped_options` |
| `[forms] unmapped_options` | list of strings | `["Other"]` | Answers of that dropdown that choose no label | A list of strings |
| `[transparency] files` | list of strings | `["SUPPORT.md"]` | The files that tell reporters which AI reads their issue; the footer of every analysis and follow-up links the first, at its heading *What happens with your issue* when it has one | Each exists and names the engine and its vendor: Claude and Anthropic |
| `[releases] close_with_release` | boolean | `true` | Whether a fixed issue stays open until a release ships the fix; `false` for a repository without releases | It is `true` or `false` |

### findings.toml

Each `[[accept]]` of `.github/findings.toml` accepts the alerts of one rule of code scanning:

| Key | Type | Default | What it does | What `check` makes sure of |
| --- | --- | --- | --- | --- |
| `tool` | string | required | The tool, as code scanning names it, in any case | It isn't empty |
| `rule` | string | required | The rule, such as `py/unused-import` | It isn't empty |
| `reason` | string | required | The reason that GitHub records for the dismissal | `false positive`, `won't fix` or `used in tests` |
| `comment` | string | required | Why, in words, shown with the dismissed alert | 1 to 200 characters |
| `path` | string | `""` | Only alerts in this file, or in the files of this folder, such as `vendor` for `vendor/board.js` but not `vendored/board.js`; empty for every file | Nothing; a key that the table doesn't have, such as `paths`, stops the findings command too, since the acceptance would cover every file |
