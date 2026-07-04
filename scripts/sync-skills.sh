#! /usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Sync hand-authored skills from .agents/skills into .claude/skills and .cursor/skills.

.agents/skills is the source of truth. Hand-authored skill directories are linked
as relative symlinks into .claude/skills and .cursor/skills. The library-skills
tool skill is copied (not symlinked) into .claude/skills so git can track its
files. Package-provided skills are reconciled first via library-skills, then
hand-authored links are restored so library-skills cleanup does not leave .claude
empty.

Usage:
  scripts/sync-skills.sh [--global] [--dry-run] [--skip-library-skills]

Options:
  --global                 Also sync ~/.agents/skills into ~/.claude/skills and ~/.cursor/skills
  --dry-run                Print actions without changing files
  --skip-library-skills    Only mirror hand-authored skills; skip library-skills
EOF
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GLOBAL=0
DRY_RUN=0
SKIP_LIBRARY_SKILLS=0

for arg in "$@"; do
  case "$arg" in
    --global) GLOBAL=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --skip-library-skills) SKIP_LIBRARY_SKILLS=1 ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $arg" >&2
      usage >&2
      exit 1
      ;;
  esac
done

run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '[dry-run] '
    printf '%q ' "$@"
    printf '\n'
  else
    "$@"
  fi
}

is_copied_skill() {
  case "$1" in
    library-skills) return 0 ;;
    *) return 1 ;;
  esac
}

copy_skill_dir() {
  local source="$1"
  local dest="$2"

  if [ -L "$dest" ]; then
    run rm "$dest"
  fi

  run mkdir -p "$dest"
  run rsync -a --delete "${source}/" "${dest}/"
  echo "copied: $dest"
}

link_skill_tree() {
  local agents_skills="$1"
  local target_skills="$2"
  local rel_prefix="$3"
  local copy_instead_of_link="${4:-}"

  mkdir -p "$target_skills"

  for skill in "$agents_skills"/*; do
    [ -e "$skill" ] || continue

    local name
    name="$(basename "$skill")"

    # Package skills are managed by library-skills, not mirrored manually.
    if [ -L "$skill" ]; then
      continue
    fi

    if [ ! -d "$skill" ] || [ ! -f "$skill/SKILL.md" ]; then
      continue
    fi

    local dest="$target_skills/$name"

    if [ "$copy_instead_of_link" = copy ] && is_copied_skill "$name"; then
      copy_skill_dir "$skill" "$dest"
      continue
    fi

    local link_target="${rel_prefix}${name}"

    if [ -L "$dest" ]; then
      local current
      current="$(readlink "$dest")"
      if [ "$current" = "$link_target" ]; then
        echo "ok: $dest"
        continue
      fi
      run rm "$dest"
    elif [ -e "$dest" ]; then
      run rm -rf "$dest"
    fi

    run ln -s "$link_target" "$dest"
    echo "linked: $dest -> $link_target"
  done
}

sync_tree() {
  local agents_skills="$1"
  local claude_skills="$2"
  local cursor_skills="$3"
  local rel_prefix="$4"

  echo "syncing skills from $agents_skills"
  link_skill_tree "$agents_skills" "$claude_skills" "$rel_prefix" copy
  link_skill_tree "$agents_skills" "$cursor_skills" "$rel_prefix"
}

reconcile_library_skills() {
  if [ "$SKIP_LIBRARY_SKILLS" -eq 1 ]; then
    echo "skipping library-skills"
    return 0
  fi

  if [ "$DRY_RUN" -eq 1 ]; then
    echo "[dry-run] library-skills --all --claude --yes"
    return 0
  fi

  if command -v uvx >/dev/null 2>&1; then
    (cd "$ROOT" && uvx library-skills --all --claude --yes)
  elif command -v npx >/dev/null 2>&1; then
    (cd "$ROOT" && npx library-skills --all --claude --yes)
  else
    echo "warning: neither uvx nor npx found; skipping library-skills" >&2
  fi
}

cd "$ROOT"

reconcile_library_skills

sync_tree \
  "$ROOT/.agents/skills" \
  "$ROOT/.claude/skills" \
  "$ROOT/.cursor/skills" \
  "../../.agents/skills/"

if [ "$GLOBAL" -eq 1 ]; then
  sync_tree \
    "$HOME/.agents/skills" \
    "$HOME/.claude/skills" \
    "$HOME/.cursor/skills" \
    "../../.agents/skills/"
fi

echo "skills sync complete"
