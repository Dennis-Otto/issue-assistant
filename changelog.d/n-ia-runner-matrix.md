### Bug fixes

- On a Python older than 3.12, or without `python3`, as in a job of your own on `ubuntu-22.04` or on a self-hosted runner, the action now stops in its first step with an error that names the version it found and how to fix it, instead of a traceback or a run that no test covers.

### Changes

- The action is now tested on `ubuntu-26.04`, which `ubuntu-latest` means since 2026-10-19, and on `ubuntu-24.04-arm`, beside `ubuntu-24.04`. The README names these supported runners.
