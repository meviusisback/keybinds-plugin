# Omarchy Keybindings Plugin (`meviusisback.keybinds`)

A modern, intuitive graphical frontend plugin for [Omarchy Linux](https://omarchy.org) to view, search, modify, and create Hyprland keybindings with live interactive key recording, collision detection, and safe Lua synchronization.

---

## Features

- **📱 Apps Menu Integration**: Sits cleanly in the **Apps** menu as an on-demand floating tool (does not clutter the top status bar).
- **🔤 Alphabetical Ordering**: All active keybindings and preset actions are sorted alphabetically by action name.
- **⭐ Dedicated Modified & Custom Section**: Easily review and manage all personal shortcut overrides and custom keybindings in one place.
- **⌨️ Multi-Method Key Recorder**: Click to record live keyboard shortcuts, toggle modifier pills (<kbd>SUPER</kbd>, <kbd>CTRL</kbd>, <kbd>ALT</kbd>, <kbd>SHIFT</kbd>), or pick from common quick key chips.
- **⚠️ Conflict Detection & 1-Click Rebind**: Detects shortcut collisions in real-time, displays detailed collision cards, and offers 1-click rebind options.
- **📜 Actions Catalog**: Browse pre-configured Hyprland / Omarchy window management, workspace, media, and system presets ready to bind.
- **🖱️ Mouse Scroll Sync**: Automatically respects your configured scroll sensitivity multiplier (`scroll_factor`) and direction (`natural_scroll`) from the mouse settings plugin.
- **🔄 Safe Lua Sync & Live Reload**: Writes directly to `~/.config/hypr/bindings.lua` (with automatic `.bak` backups) and invokes `hyprctl reload` for instantaneous application without session restarts.

---

## Installation

Run the automated installer:
```bash
./install.sh
```

Or install manually:
```bash
mkdir -p ~/.config/omarchy/plugins ~/.local/share/applications
ln -sfn "$(pwd)" ~/.config/omarchy/plugins/meviusisback.keybinds
cp meviusisback.keybinds.desktop ~/.local/share/applications/
omarchy-shell shell rescanPlugins
```

---

## Usage

- **From Apps Menu**: Open the Omarchy menu (<kbd>SUPER</kbd> + <kbd>SPACE</kbd> or <kbd>SUPER</kbd> + <kbd>ALT</kbd> + <kbd>SPACE</kbd>) and select **Keybindings**.
- **From Terminal**:
  ```bash
  omarchy-keybinds
  ```
- **CLI Commands**:
  ```bash
  # View all keybindings as JSON
  omarchy-keybinds list

  # Set a custom shortcut
  omarchy-keybinds set "SUPER + SHIFT + B" "My Browser" "omarchy-launch-browser"

  # Reset a shortcut to Omarchy default
  omarchy-keybinds reset "SUPER + SHIFT + B"

  # Disable / unbind a shortcut
  omarchy-keybinds disable "SUPER + W"
  ```

---

## Testing

Run the automated unit tests:
```bash
python3 -m unittest discover -s tests -p "test_*.py"
```

---

## Architecture

- `manifest.json`: Plugin manifest declaring `kind: ["panel"]` and `keepLoaded: true`.
- `KeybindsPanel.qml`: Main GUI floating window with search, category filters, tab views, and active key list.
- `EditKeybindDialog.qml`: Modal dialog for creating and modifying keybindings with proper Omarchy border insets.
- `KeyRecorder.qml`: Interactive keyboard event catcher, modifier toggles, and live conflict detector.
- `KeyBadge.qml`: Visual keyboard chord badge renderer.
- `backend/keybinds_manager.py`: Python engine for parsing system defaults, managing user overrides, conflict scanning, and Hyprland sync.
- `tests/test_backend.py`: Unit tests for parser and manager operations.
- `bin/omarchy-keybinds`: CLI launcher and helper script.
