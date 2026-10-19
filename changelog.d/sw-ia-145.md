### Fixed

- The Labels workflow no longer fails when another workflow created a label a moment before, such as the settings bot of Repo Blueprint, which the same push starts when it changes `.github/labels.toml`, as the first push of a new repository always does. `sync-labels` then gives the label the color and the description of `labels.toml`, as any label that exists, and `/assistant mute` does the same for its label.
