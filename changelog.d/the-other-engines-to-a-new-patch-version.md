### Bug fixes

- The Copilot engine works with version 1.0.103 of its command line, which removed the flag `--no-remote` and stops at it. In its place the analysis now passes `--sync-session=off`, so that a run on a runner stays out of the history of the Copilot account. Remote control takes a command or a flag of its own to start, and the analysis passes neither.
