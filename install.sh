#!/bin/bash
# vaultnotes installer for macOS. Idempotent — rerun any time.

set -euo pipefail

# When run via `curl … | bash`, stdin is the script itself, which breaks
# `read` and any interactive child (gh auth login, vaultnotes init).
# Reconnect stdin to the terminal so prompts work.
if [ ! -t 0 ] && [ -r /dev/tty ]; then
  exec < /dev/tty
fi

say() { printf '\n── %s\n' "$*"; }

say "Checking Obsidian vault"
default_vault="$HOME/Documents/Obsidian Vault"
read -r -p "Vault path [$default_vault]: " vault || true
vault="${vault:-$default_vault}"
if [ ! -d "$vault" ]; then
  echo
  echo "No vault found at: $vault"
  echo "Install Obsidian from https://obsidian.md, create a vault there,"
  echo "and rerun this installer."
  exit 1
fi

if ! command -v brew >/dev/null 2>&1; then
  say "Installing Homebrew"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi

say "Installing core tools (python, pipx, git, gh)"
brew install python pipx git gh || true
pipx ensurepath >/dev/null 2>&1 || true

# Make pipx bins visible in this shell for subsequent commands.
export PATH="$HOME/.local/bin:$PATH"

VAULTNOTES_PKG="git+https://github.com/MaleenKidiwela/vaultnotes.git"
if ! command -v vaultnotes >/dev/null 2>&1; then
  say "Installing vaultnotes"
  pipx install "$VAULTNOTES_PKG"
else
  say "Upgrading vaultnotes"
  pipx install --force "$VAULTNOTES_PKG"
fi

if ! gh auth status >/dev/null 2>&1; then
  say "Authenticating with GitHub"
  gh auth login
fi

say "Launching vaultnotes init"
vaultnotes init
