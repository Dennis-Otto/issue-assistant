# Security policy

## Supported versions

Security fixes are provided for the latest release. Repositories that use the action pin its commit hash; merge the update pull request of Dependabot or Renovate for a new release to receive a fix.

## Reporting a vulnerability

Please do not open a public issue, discussion or pull request for a suspected vulnerability. Use GitHub's private vulnerability reporting for this repository:

<https://github.com/Dennis-Otto/issue-assistant/security/advisories/new>

Include the affected release, the repository set-up, reproduction steps and the potential impact. Reports in English or German are welcome.

Of particular interest are ways in which the text of an issue or a comment makes the AI read outside the checkout, run anything, reach the network, or get text past the checks of `apply`, for example a secret, a mention or a link to a foreign site.

## What happens next

| Step | Target |
| --- | --- |
| Acknowledgement of the report | within 7 days |
| First assessment, including whether the report is accepted | within 14 days |
| Fix released for a confirmed vulnerability | as fast as possible, at the latest within 90 days |
| Public disclosure | when the fixed release is available, in a GitHub security advisory and the release notes |

If a fix needs longer, for example because the cause lies in an upstream project, you receive an update at least every 14 days. Reporters are credited in the advisory and the release notes unless they prefer to stay anonymous.

## Scope

In scope are the action (`action.yml`, `issue_assistant.py`), its prompts, the workflow and firewall templates, and the CI and release workflows of this repository.

Out of scope, and reported to their own projects instead: the Claude Code action and Claude Code (<https://github.com/anthropics/claude-code-action/security>), GitHub Actions and its egress firewall (<https://bounty.github.com>).

## Secrets

The action stores no secrets. A repository keeps the engine's token as a secret of its environment `issue-assistant`, which only the default branch may use; only the job that runs the engine sees it, and the job that writes to GitHub never does.

## How the project keeps itself secure

- Every pull request and every push to `main` runs CodeQL, a Gitleaks secret scan and, for changed dependencies, a review against known vulnerabilities. OpenSSF Scorecard checks the practices of the repository every week.
- Actions are pinned to commit hashes, tokens get the least permissions they need, and Renovate keeps actions, dependencies and images current, with the updates that fix a vulnerability at once.
- OSV-Scanner checks every lock file against the OSV database of known vulnerabilities, on every pull request and every week.
- Harden-Runner records the network traffic of every job of the workflows, so that a connection that doesn't belong there shows.
- Releases carry an SBOM as SPDX and as CycloneDX, the licenses of their third-party components (`THIRD_PARTY_NOTICES.md`), an OpenVEX document of the advisories that the project accepts with their reasons, and signed build provenance, are immutable once published, and are verified as their users can after every release and every week.

## Findings of code scanning

CodeQL and OpenSSF Scorecard report their findings in the repository's Security tab. The Findings workflow of the [issue assistant](https://github.com/Dennis-Otto/issue-assistant#findings) dismisses the findings that `.github/findings.toml` accepts, each with its reason, and fails while any other finding is open. It names an open finding only by the number and link of its alert, which only maintainers can open; nothing about a possible vulnerability becomes a public issue.
