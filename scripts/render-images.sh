#!/usr/bin/env bash
# Renders the pictures of the documentation into docs/images, light and dark: the
# issue of a demo project from its opening to the release as an animation, wide and
# upright for phones and each also still, a first analysis, what the checks make of a
# steered answer, issues that try to steer the AI, a dry run and a failing Findings
# run. scripts/images/pages.py builds their pages from demo data with the texts of
# the package issue_assistant itself, so that they show what the assistant posts, and
# scripts/images/render.py takes the pictures and writes scripts/images/manifest.json.
# Run this after a change of those texts or of the pictures, and commit docs/images
# and the manifest with it: test_the_pictures_show_what_the_assistant_posts fails
# until then. scripts/screenshots.sh runs it for the screenshot bot, which renders
# the pictures anew after a new version of the tools.
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
# Chromium keeps the pixels of a page in shared memory, and the 64 MB that Docker
# gives a container by default are too few for a picture at twice its size: the
# browser crashes.
docker run --rm --shm-size=1g --user "$(id -u):$(id -g)" --env HOME=/tmp \
  --volume "$root:/work" --workdir /work "$image" sh -c '
  pip install --quiet --user --no-warn-script-location --disable-pip-version-check \
    --break-system-packages --require-hashes -r requirements-images.txt &&
  python3 scripts/images/render.py'
