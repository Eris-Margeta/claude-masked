#!/usr/bin/env bash
set -euo pipefail

BIN_DIR="${CLAUDE_MASKED_BIN_DIR:-$HOME/.local/bin}"
SHIM_SRC="$(cd "$(dirname "$0")" && pwd)/bin/claude"
TARGET="$BIN_DIR/claude"
WRAPPER="$BIN_DIR/claude-masked"

restore() {
  local dest="$1"
  local backup="$2"
  [[ -e "$dest" || -L "$dest" ]] || return 0
  local resolved
  resolved="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$dest" 2>/dev/null || true)"
  local shim
  shim="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$SHIM_SRC" 2>/dev/null || true)"
  if [[ "$resolved" != "$shim" ]] && ! grep -q "claude-masked" "$dest" 2>/dev/null; then
    echo "did not touch $dest (not claude-masked)"
    return 0
  fi
  rm -f "$dest"
  if [[ -e "$backup" || -L "$backup" ]]; then
    mv "$backup" "$dest"
    echo "restored $dest from $backup"
  else
    echo "removed masked $dest; no backup"
  fi
}

rm -f "$WRAPPER"
restore "$TARGET" "$BIN_DIR/claude-anthropic"
restore /opt/homebrew/bin/claude "$BIN_DIR/claude-anthropic-homebrew"
restore /usr/local/bin/claude "$BIN_DIR/claude-anthropic-usrlocal"
