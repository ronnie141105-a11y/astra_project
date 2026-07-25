#!/usr/bin/env bash
#
# clean_git_tracked_artifacts.sh
#
# Removes __pycache__/, *.pyc/*.pyo, and other build/venv artifacts from
# Git's index (tracking) without deleting them from disk, then relies on
# the updated .gitignore to keep them out going forward.
#
# This does NOT rewrite history -- it only fixes the current tip. If you
# need these files gone from *every* historical commit too (e.g. before
# open-sourcing the repo), run this first, commit, then separately use
# `git filter-repo` (preferred) or `git filter-branch` as a follow-up.
#
# Usage:
#   ./scripts/clean_git_tracked_artifacts.sh          # untrack + commit
#   ./scripts/clean_git_tracked_artifacts.sh --dry-run # show what would happen
#
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=true
fi

echo "== ASTRA: scrubbing tracked build artifacts from Git =="

# Patterns of tracked-but-should-not-be paths. Kept as an array so both
# the dry-run listing and the real `git rm` walk the same set.
PATTERNS=(
    '__pycache__'
    '*.pyc'
    '*.pyo'
    '*.pyd'
    '.pytest_cache'
    '.mypy_cache'
    '.ruff_cache'
    '*.egg-info'
    '.venv'
    'venv'
    'build'
    'dist'
    '.env'
)

# Build the list of currently tracked paths matching any pattern.
mapfile -t MATCHES < <(
    for pat in "${PATTERNS[@]}"; do
        git ls-files -z | tr '\0' '\n' | grep -E "(^|/)${pat//./\\.}($|/)" || true
    done | sort -u
)

if [[ ${#MATCHES[@]} -eq 0 ]]; then
    echo "Nothing tracked matches the artifact patterns. Nothing to do."
    exit 0
fi

echo "The following ${#MATCHES[@]} tracked path(s) will be untracked (kept on disk):"
printf '  %s\n' "${MATCHES[@]}"

if $DRY_RUN; then
    echo "(dry run -- no changes made)"
    exit 0
fi

# --cached: remove from the index only, leave the working tree files alone.
printf '%s\0' "${MATCHES[@]}" | xargs -0 git rm -r --cached --quiet --ignore-unmatch --

echo "Untracked ${#MATCHES[@]} path(s). Staging .gitignore as well (if changed)..."
git add .gitignore

echo
echo "Done. Review the staged changes with 'git status' / 'git diff --cached',"
echo "then commit, e.g.:"
echo "  git commit -m 'chore: stop tracking __pycache__/.pyc and other build artifacts'"
