#!/usr/bin/env bash
# Install claude-masked over every `claude` Yume might spawn.
# Yume prefers /opt/homebrew/bin/claude (absolute path) for `auth login`.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="${CLAUDE_MASKED_BIN_DIR:-$HOME/.local/bin}"
SHIM_SRC="$ROOT/bin/claude"
TARGET="$BIN_DIR/claude"
REAL_BACKUP="$BIN_DIR/claude-anthropic"
WRAPPER="$BIN_DIR/claude-masked"

mkdir -p "$BIN_DIR"

if [[ ! -f "$SHIM_SRC" ]]; then
  echo "missing shim: $SHIM_SRC" >&2
  exit 1
fi

chmod +x "$ROOT/claude_masked.py" "$SHIM_SRC" "$ROOT/uninstall.sh" "$ROOT/auth.py" 2>/dev/null || true

install_over() {
  local dest="$1"
  local backup="$2"
  [[ -e "$dest" || -L "$dest" ]] || return 0
  local resolved
  resolved="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$dest")"
  if [[ "$resolved" == "$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$SHIM_SRC")" ]]; then
    echo "already masked: $dest"
    return 0
  fi
  if grep -q "claude-masked" "$dest" 2>/dev/null; then
    echo "already masked: $dest"
    return 0
  fi
  mkdir -p "$(dirname "$backup")"
  if [[ -e "$backup" || -L "$backup" ]]; then
    echo "keeping backup $backup"
  else
    mv "$dest" "$backup"
    echo "moved $dest → $backup"
  fi
  rm -f "$dest"
  ln -sfn "$SHIM_SRC" "$dest"
  echo "masked $dest → $SHIM_SRC"
}

# Primary PATH entry.
if [[ -e "$TARGET" || -L "$TARGET" ]]; then
  install_over "$TARGET" "$REAL_BACKUP"
else
  ln -sfn "$SHIM_SRC" "$TARGET"
  echo "linked $TARGET → $SHIM_SRC"
fi
ln -sfn "$SHIM_SRC" "$WRAPPER"

# Homebrew / extra copies Yume hard-spawns (absolute path).
install_over /opt/homebrew/bin/claude "$BIN_DIR/claude-anthropic-homebrew"
install_over /usr/local/bin/claude "$BIN_DIR/claude-anthropic-usrlocal"

echo
echo "which -a claude:"
which -a claude || true
echo
echo "Real Claude Code backups:"
ls -la "$BIN_DIR"/claude-anthropic* 2>/dev/null || true
echo
echo "If Yume is open, quit it fully (Cmd+Q) and reopen."
