#!/usr/bin/env bash
set -euo pipefail

REPO_URL="git@github.com:KonstantinUshenin/nornickel-2026-parser.git"
TARGET_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$TARGET_DIR"

if [[ -d .git ]]; then
  echo "Repository already cloned in: $TARGET_DIR"
  git remote -v
  exit 0
fi

if [[ -n "$(find . -mindepth 1 -maxdepth 1 ! -name 'clone.sh' -print -quit)" ]]; then
  echo "Directory is not empty (except clone.sh). Clone into an empty folder." >&2
  exit 1
fi

echo "Cloning $REPO_URL into $TARGET_DIR ..."
git clone "$REPO_URL" .

echo "Done."
