#!/usr/bin/env bash
# Renders the pictures of this repository in place, for the screenshot bot. After a
# change of main that touches a file of .github/screenshots-paths.txt, after every
# release and every week, the Screenshots workflow runs this script and opens a pull
# request with each picture whose pixels changed; one whose pixels stayed the same
# keeps its file. To see what it would change, run it, then
# `python3 .github/blueprint.py screenshots restore`, which gives every picture that
# looks the same its committed file back and lists the others. It needs Pillow:
# `python3 -m pip install --require-hashes -r .github/screenshots-requirements.txt`.
#
# It renders the social preview, .github/social-preview.png, and the pictures of the
# documentation in docs/images. A change of the texts of the assistant or of the
# pages of the pictures re-renders them in its own pull request, since
# test_the_pictures_show_what_the_assistant_posts fails until then; the bot renders
# them anew after what no test sees, a new version of the browser, of Pillow or of
# the fonts.
#
# It needs Docker. The blueprint brought it once; it belongs to this project.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

bash scripts/social-preview.sh
bash scripts/render-images.sh
