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

if [[ -f scripts/check-project.sh ]]; then
  step "Checks of this project"
  bash scripts/check-project.sh
fi

printf '\nEvery check passed.\n'
