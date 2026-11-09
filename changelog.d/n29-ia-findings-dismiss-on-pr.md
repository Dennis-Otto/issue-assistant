### Bug fixes

- A pull request that moves a finding that `.github/findings.toml` accepts, such as a false positive on a line that now has another number, no longer waits for a person to resolve the review thread of its new alert. After each scan of a pull request of the repository itself, the Findings workflow dismisses the alerts of the pull request that `findings.toml` of the default branch accepts, which resolves their threads; the other alerts there fail no run, and a fork's pull request keeps its threads for a person. It comes with the next move of the pin, without `install`.
