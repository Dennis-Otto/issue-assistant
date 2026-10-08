#!/usr/bin/env bash
# Renders the pictures of the documentation into docs/images, light and dark: the
# issue of a demo project from its opening to the release as an animation, a first
# analysis, what the checks make of a steered answer, a dry run and a failing
# Findings run. scripts/images/render.py builds them from demo data with the texts of
# issue_assistant.py itself, so that they show what the assistant posts; run this
# after a change of those texts or of the pictures.
#
# It needs Docker: the headless Chromium of the image of the social preview renders
# them, with the tools of requirements-images.txt.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# The image of the social preview, which Renovate keeps current.
image="$(sed -n 's/^FROM //p' .github/social-preview/Dockerfile)"
# Git Bash on Windows: keep the container's paths and mount the Windows path.
export MSYS_NO_PATHCONV=1
root="$(pwd -W 2>/dev/null || pwd)"

# The pictures belong to whoever runs this, not to the root user of the container.
docker run --rm --user "$(id -u):$(id -g)" --env HOME=/tmp \
  --volume "$root:/work" --workdir /work "$image" sh -c '
  pip install --quiet --user --no-warn-script-location --disable-pip-version-check \
    --break-system-packages --require-hashes -r requirements-images.txt &&
  python3 scripts/images/render.py'
