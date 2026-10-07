# Governance

The issue assistant is maintained by Dennis Otto.

## Maintainers and access

| Person | Role | Access |
| --- | --- | --- |
| [@Dennis-Otto](https://github.com/Dennis-Otto) | Maintainer | Repository administration, releases and security advisories |

## Decisions

Decisions are discussed in public issues and pull requests whenever they contain no security-sensitive information. They prioritize the safety of the repositories that use the action: the AI only reads, every write is checked, and no secret leaves the job that needs it. The maintainer has final responsibility for releases, repository access, security responses and project direction.

## Reviews

Every change reaches `main` through a pull request that passes all required checks: the tests with a coverage gate on Python 3.12 and 3.14, the action's smoke test, Ruff, mypy, actionlint, CodeQL, dependency review and the secret scan.

## Continuity

If the maintainer can no longer maintain the project, the preferred outcome is a transparent handover to a trusted active contributor, announced in the repository. Until then, the repository should be archived rather than presented as actively maintained.

## Security

Potential vulnerabilities follow [SECURITY.md](SECURITY.md) and are handled privately until a fix and a coordinated disclosure are ready.
