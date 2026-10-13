#!/usr/bin/env bash
# The checks of Issue assistant, the same ones its CI runs. Run them before every
# push:  bash scripts/check.sh
# The tools come from:  pip install --require-hashes -r requirements-dev.txt
#
# The blueprint keeps this file current: https://github.com/Dennis-Otto/repo-blueprint
# Checks of this project alone belong in scripts/check-project.sh, which runs last.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

step() {
  printf '\n== %s\n' "$1"
}

files() {
  git ls-files -z --cached --others --exclude-standard "$@"
}

step "Text files"
problems=0
while IFS= read -r -d '' file; do
  case "$file" in
    LICENSE | LICENSES/*) continue ;;
  esac
  # Leave out deleted, empty and binary files.
  if [[ ! -s "$file" ]] || ! grep -Iq . "$file"; then
    continue
  fi
  if grep -q $'\r' "$file"; then
    echo "$file: has CRLF line endings"
    problems=$((problems + 1))
  fi
  if grep -qE '[[:blank:]]$' "$file"; then
    echo "$file: has trailing whitespace"
    problems=$((problems + 1))
  fi
  if [[ -n "$(tail -c 1 "$file")" ]]; then
    echo "$file: lacks the line break at its end"
    problems=$((problems + 1))
  fi
done < <(files)
if ((problems > 0)); then
  echo "Fix the $problems problem(s) above; .editorconfig sets up most editors for it."
  exit 1
fi
echo "Every text file is clean."

step "Version"
# The release bot writes the version of each release into these files.
release="$(grep -oE '"\.": *"[^"]+"' .release-please-manifest.json | grep -oE '[0-9][^"]*')"
version_in() {
  local found
  found="$(grep -oE -m 1 "$2" "$1" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+[^"<]*' | head -n 1 || true)"
  if [[ "$found" != "$release" ]]; then
    echo "$1 has version ${found:-none}; the manifest of the release bot has $release."
    exit 1
  fi
}
version_in version.txt '^[0-9][^[:space:]]*'
echo "Every file has version $release."

step "Git hooks"
# Git runs a hook only when its file is executable, which git records as the mode
# 100755. A file added on Windows, which has no such mode, gets 100644, and git on
# Linux and macOS, in the dev container and in Codespaces then skips the hook.
mapfile -t hooks < <(git ls-files --stage -- .githooks | grep -v '^100755 ' | cut -f 2-)
if ((${#hooks[@]} > 0)); then
  printf '%s: is not executable, so git skips it\n' "${hooks[@]}"
  echo "Make it executable and commit that: git update-index --chmod=+x ${hooks[*]}"
  exit 1
fi
echo "Every hook in .githooks/ is executable."

step "Shell scripts"
mapfile -d '' scripts < <(files '*.sh')
if ! command -v shellcheck >/dev/null 2>&1; then
  echo "shellcheck is not installed here; the CI runs it."
elif ((${#scripts[@]} > 0)); then
  shellcheck "${scripts[@]}"
  echo "shellcheck found nothing in ${#scripts[@]} script(s)."
fi

step "Ruff"
ruff format --check --diff .
ruff check .

step "Mypy"
mypy

step "Tests, with every line and branch covered"
coverage run -m pytest
coverage report

step "Website"
# The website of the Docs workflow, built strictly as the check docs builds it, so
# that a link or an anchor that leads nowhere fails here already. Its tools are those
# of the preview in .venv-docs, which CONTRIBUTING.md sets up, or those of an activated
# virtual environment. A MkDocs from elsewhere on the PATH lacks the theme and the
# plugins of the website as a rule, so the step leaves it alone.
if [[ ! -f mkdocs.yml ]] || [[ ! -f docs/index.md && ! -f docs/README.md ]]; then
  echo "No website here: it needs mkdocs.yml and a home page, docs/index.md."
else
  candidates=(.venv-docs/bin/mkdocs .venv-docs/Scripts/mkdocs.exe)
  if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    candidates+=("$VIRTUAL_ENV/bin/mkdocs" "$VIRTUAL_ENV/Scripts/mkdocs.exe")
  fi
  mkdocs=""
  for candidate in "${candidates[@]}"; do
    if [[ -x "$candidate" ]]; then
      mkdocs="$candidate"
      break
    fi
  done
  if [[ -z "$mkdocs" ]]; then
    echo "MkDocs is not installed here; the CI builds the website."
  else
    # Into a folder of its own, which goes in any case, and with the log only when
    # the build fails. Without the warning about MkDocs 2.0, as in the Docs workflow.
    (
      site="$(mktemp -d)"
      trap 'rm -rf "$site"' EXIT
      if ! log="$(NO_MKDOCS_2_WARNING=true "$mkdocs" build --strict --site-dir "$site" 2>&1)"; then
        printf '%s\n' "$log"
        echo "The website fails to build strictly with $mkdocs; the log above says why."
        echo "Tools that are out of date or incomplete install again as CONTRIBUTING.md shows."
        exit 1
      fi
    )
    echo "The website builds strictly."
  fi
fi

if [[ -f scripts/check-project.sh ]]; then
  step "Checks of this project"
  bash scripts/check-project.sh
fi

printf '\nEvery check passed.\n'
