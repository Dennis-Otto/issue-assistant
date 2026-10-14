# install pins the commit of its own checkout

- Status: accepted
- Date: 2026-10-14

## Context

`install` writes the templates of the checkout that it runs from and pins the action in them, with `--ref <commit-hash> --version <tag>`. People looked up the hash by hand, and `git ls-remote` lists the object of an annotated tag first, `refs/tags/v1.2.2` and only then `refs/tags/v1.2.2^{}`, the commit. `install` took any 40 hex digits, so a repository could end up with a pin that GitHub can't resolve as an action, or with the templates of one release under the pin of another, which `check` of the pinned release then refused. [Decision 0006](0006-the-templates-exactly.md) needs the templates and the pin of the same release.

## Options

1. Keep both options required and only check that `--ref` is a commit. The lookup by hand stays, and so does a pin of another release than the templates.
2. Ask GitHub for the commit of the tag. It needs the network and the GitHub CLI for a command that otherwise works offline, and it still can't tell whether the checkout holds the templates of that tag.
3. Take both from the checkout: its commit, and its tag of a release. A `--ref` that is given must be that commit, and a `--version` that the checkout has as a tag must point to it. Outside a git checkout, such as an unpacked package of a release, both are given as before.

## Decision

Option 3. A clone of a release tag, `git clone --depth 1 --branch vX.Y.Z`, is all that `install` needs, and the commit that it pins is the one whose templates it writes. The object of a tag is refused with the commit to use instead, and a checkout off a release tag is refused with the command that clones one.

## Consequences

The set-up starts with one command, which also takes the latest release by its tag, and an update runs the same command again. A checkout of `main` installs only with `--ref` of its own commit and a `--version` that names no other commit, as the Action test does. `install --setup` builds on this: once the files are written, it creates the environment `issue-assistant` for the default branch and asks for the engine's secret through the GitHub CLI.
