#!/usr/bin/env bash
set -euo pipefail

TOOL="all"
MODE="copy"
FORCE="0"
CODEX_SKILL_ROOT="${HOME}/.codex/skills"
CLAUDE_SKILL_ROOT="${HOME}/.claude/skills"

usage() {
  cat <<'USAGE'
Usage:
  install-agent-skills.sh [--tool codex|claude-code|all] [--mode copy|symlink] [--force]
  install-agent-skills.sh --tool codex --mode copy
  install-agent-skills.sh --tool all --mode symlink --force

Options:
  --tool <name>              codex, claude-code, or all. Default: all.
  --mode <name>              copy or symlink. Default: copy.
  --codex-skill-root <dir>   Override Codex skill root. Default: $HOME/.codex/skills.
  --claude-skill-root <dir>  Override Claude Code skill root. Default: $HOME/.claude/skills.
  --force                    Replace existing installed package directories.
  -h, --help                 Show this help.

The installer copies or symlinks only:
  - longform-novel-codex/
  - longform-novel-claude/

Each package contains its own synchronized references/ directory. Directories
without this engine's ownership metadata are never removed by this installer.

It does not copy novel projects, manuscripts, runtime databases, model caches,
environment files, or API keys.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tool)
      TOOL="${2:-}"
      shift 2
      ;;
    --mode)
      MODE="${2:-}"
      shift 2
      ;;
    --codex-skill-root)
      CODEX_SKILL_ROOT="${2:-}"
      shift 2
      ;;
    --claude-skill-root)
      CLAUDE_SKILL_ROOT="${2:-}"
      shift 2
      ;;
    --force)
      FORCE="1"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

case "$TOOL" in
  codex|claude-code|all) ;;
  *)
    echo "Unsupported --tool value: $TOOL" >&2
    exit 2
    ;;
esac

case "$MODE" in
  copy|symlink) ;;
  *)
    echo "Unsupported --mode value: $MODE" >&2
    exit 2
    ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"

find_python() {
  if [[ -n "${PYTHON:-}" ]]; then
    command -v "$PYTHON" >/dev/null 2>&1 && return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    PYTHON="python"
    return 0
  fi
  echo "Python is required to normalize install paths." >&2
  exit 2
}

find_python

normalize_input_path() {
  local raw="$1"
  if printf '%s' "$raw" | grep -Eq '^[A-Za-z]:[\\/]'; then
    if command -v wslpath >/dev/null 2>&1; then
      wslpath -u "$raw"
      return 0
    fi
    if command -v cygpath >/dev/null 2>&1; then
      cygpath -u "$raw"
      return 0
    fi
  fi
  printf '%s\n' "$raw"
}

abs_path() {
  local raw="$1"
  if [[ -z "$raw" ]]; then
    echo ""
    return 0
  fi
  local normalized
  normalized="$(normalize_input_path "$raw")"
  "$PYTHON" - "$normalized" <<'PY'
import os
import sys
path = os.path.expanduser(os.path.expandvars(sys.argv[1]))
print(os.path.abspath(path))
PY
}

is_child_path() {
  local child="$1"
  local parent="$2"
  "$PYTHON" - "$child" "$parent" <<'PY'
import os
import sys
child = os.path.abspath(sys.argv[1])
parent = os.path.abspath(sys.argv[2])
try:
    print("yes" if os.path.commonpath([child, parent]) == parent and child != parent else "no")
except ValueError:
    print("no")
PY
}

assert_not_dangerous_path() {
  local path="$1"
  local role="$2"
  local full
  full="$(abs_path "$path")"

  if [[ -z "$full" ]]; then
    echo "$role path must not be empty." >&2
    exit 4
  fi
  if [[ "$full" == "/" ]]; then
    echo "$role path is the filesystem root and is not allowed: $full" >&2
    exit 4
  fi
  if [[ "$full" == "$(abs_path "$HOME")" ]]; then
    echo "$role path is the user home directory and is not allowed: $full" >&2
    exit 4
  fi
  if [[ "$full" == "$(abs_path "$REPO_ROOT")" ]]; then
    echo "$role path is the repository root and is not allowed: $full" >&2
    exit 4
  fi
}

assert_safe_skill_root() {
  local root="$1"
  assert_not_dangerous_path "$root" "Skill root"
}

assert_safe_install_target() {
  local target="$1"
  local root="$2"
  local target_full
  local root_full
  target_full="$(abs_path "$target")"
  root_full="$(abs_path "$root")"

  assert_not_dangerous_path "$target_full" "Install target"

  if [[ "$target_full" == "$root_full" ]]; then
    echo "Install target must not be the skill root itself: $target_full" >&2
    exit 4
  fi

  if [[ "$(is_child_path "$target_full" "$root_full")" != "yes" ]]; then
    echo "Install target must stay inside the selected skill root." >&2
    echo "target=$target_full" >&2
    echo "root=$root_full" >&2
    exit 4
  fi
}

remove_existing_target() {
  local target="$1"
  local root="$2"

  assert_safe_install_target "$target" "$root"
  if [[ ! -e "$target" && ! -L "$target" ]]; then
    return 0
  fi

  if [[ "$FORCE" != "1" ]]; then
    echo "Target already exists: $target. Re-run with --force to replace it." >&2
    exit 3
  fi

  rm -rf -- "$target"
}

copy_directory_contents() {
  local source="$1"
  local target="$2"

  mkdir -p "$target"
  cp -R "$source"/. "$target"/
}

install_package_directory() {
  local label="$1"
  local source="$2"
  local target="$3"
  local root="$4"

  local source_full
  local target_full
  local root_full
  source_full="$(abs_path "$source")"
  target_full="$(abs_path "$target")"
  root_full="$(abs_path "$root")"

  if [[ ! -d "$source_full" ]]; then
    echo "Source package not found: $source_full" >&2
    exit 2
  fi

  assert_safe_skill_root "$root_full"
  assert_safe_install_target "$target_full" "$root_full"
  mkdir -p "$root_full"

  if [[ "$MODE" == "copy" ]]; then
    if [[ -L "$target_full" ]]; then
      remove_existing_target "$target_full" "$root_full"
    fi
    copy_directory_contents "$source_full" "$target_full"
  else
    remove_existing_target "$target_full" "$root_full"
    ln -s "$source_full" "$target_full"
  fi

  INSTALLED_LINES+=("$label"$'\t'"$MODE"$'\t'"$target_full")
}

install_tool() {
  local tool="$1"
  local skill_root="$2"
  local skill_name="$3"
  local source_dir="$4"
  local label="$5"

  install_package_directory "$label" "$source_dir" "$skill_root/$skill_name" "$skill_root"
}

INSTALLED_LINES=()

if [[ "$TOOL" == "codex" || "$TOOL" == "all" ]]; then
  install_tool \
    "codex" \
    "$CODEX_SKILL_ROOT" \
    "longform-novel-codex" \
    "$REPO_ROOT/longform-novel-codex" \
    "Codex skill"
fi

if [[ "$TOOL" == "claude-code" || "$TOOL" == "all" ]]; then
  install_tool \
    "claude-code" \
    "$CLAUDE_SKILL_ROOT" \
    "longform-novel-claude" \
    "$REPO_ROOT/longform-novel-claude" \
    "Claude Code skill"
fi

echo "OK: longform-novel-engine agent skills installed"
echo "tool=$TOOL"
echo "mode=$MODE"
for installed_line in "${INSTALLED_LINES[@]}"; do
  IFS=$'\t' read -r label installed_mode target <<< "$installed_line"
  echo "- $label: $target"
done

cat <<'TXT'

Next steps:
  1. Restart Codex / Claude Code so skill discovery refreshes.
  2. Run: python scripts/validate_skills.py
  3. Start production with: longform-engine production next project.yaml
TXT
