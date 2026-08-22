#!/bin/bash
set -e

PLUGIN_ID="meviusisback.keybinds"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_TARGET="$HOME/.config/omarchy/plugins/$PLUGIN_ID"
DESKTOP_DIR="$HOME/.local/share/applications"

echo "==> Installing Omarchy Keybindings Plugin..."

# 1. Make scripts executable
chmod +x "$REPO_DIR/backend/keybinds_manager.py"
chmod +x "$REPO_DIR/bin/omarchy-keybinds"

# 2. Link plugin into ~/.config/omarchy/plugins/
mkdir -p "$HOME/.config/omarchy/plugins"
if [[ -e "$PLUGIN_TARGET" && ! -L "$PLUGIN_TARGET" ]]; then
  echo "Backing up existing non-symlink plugin at $PLUGIN_TARGET"
  mv "$PLUGIN_TARGET" "${PLUGIN_TARGET}.bak"
fi
ln -sfn "$REPO_DIR" "$PLUGIN_TARGET"
echo "✓ Plugin linked to $PLUGIN_TARGET"

# 3. Install desktop entry for Apps menu
mkdir -p "$DESKTOP_DIR"
cp "$REPO_DIR/meviusisback.keybinds.desktop" "$DESKTOP_DIR/meviusisback.keybinds.desktop"
echo "✓ Desktop entry installed to $DESKTOP_DIR/meviusisback.keybinds.desktop"

# 4. Refresh plugin state safely (dismiss any open instance before rescan to avoid hot-reload crash)
if which omarchy-shell >/dev/null 2>&1; then
  omarchy-shell shell hide "$PLUGIN_ID" >/dev/null 2>&1 || true
  omarchy-shell shell rescanPlugins >/dev/null 2>&1 || true
  echo "✓ Refreshed Omarchy shell plugin state"
fi

echo "==> Omarchy Keybindings Plugin installed successfully!"
echo "    Launch from the Apps menu or run: omarchy-shell shell summon meviusisback.keybinds '{}'"
