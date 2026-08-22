# Omarchy Keybindings Plugin (`meviusisback.keybinds`)

[![Omarchy Linux](https://img.shields.io/badge/Omarchy-Hyprland%20Plugin-blue)](https://omarchy.org)
[![Quickshell](https://img.shields.io/badge/Frontend-Quickshell%20%2F%20QML-orange)](https://quickshell.outfoxxed.me/)
[![Python 3.10+](https://img.shields.io/badge/Backend-Python%203-green)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-purple.svg)](LICENSE)

A modern, intuitive graphical frontend plugin for **[Omarchy Linux](https://omarchy.org)** to view, search, modify, and create Hyprland keybindings with live interactive key recording, smart free key recommendations, collision detection, and safe Lua synchronization.

---

## ✨ Features

- **🚀 On-Demand Launch**: Summon the floating manager anytime via `omarchy-shell shell summon meviusisback.keybinds` (or bind it to a key) — no top status bar clutter.
- **🔤 Alphabetical Sorting**: All active keybindings and catalog presets are strictly sorted in case-insensitive alphabetical order by action name.
- **⭐ Dedicated Modified & Custom Section**: Easily review and manage all personal shortcut overrides and custom keybindings in one place with 1-click **↺ Reset to Default** and **🗑️ Delete**.
- **💡 Smart Free Key Recommendations**: When creating or editing an action, the modal automatically scans for 100% free, unassigned shortcuts matching the action's first letter (e.g. `SUPER + S`, `SUPER + SHIFT + S` for *Spotify*) and offers 1-click suggestion chips.
- **⌨️ Multi-Method Key Recorder**:
  - Live keyboard capture: click to record and press any combination.
  - Modifier toggle pills: toggle <kbd>SUPER</kbd>, <kbd>CTRL</kbd>, <kbd>ALT</kbd>, or <kbd>SHIFT</kbd> with ease.
  - Quick-key chips: instantly select common special keys (<kbd>RETURN</kbd>, <kbd>SPACE</kbd>, <kbd>ESCAPE</kbd>, <kbd>TAB</kbd>, <kbd>F1-F12</kbd>).
- **⚠️ Collision Detection & 1-Click Rebind**: Detects shortcut collisions in real-time, displays detailed conflict cards, and offers 1-click rebind options for colliding actions.
- **📜 Actions Catalog**: Browse pre-configured Hyprland / Omarchy window management, workspace, media, and system presets ready to bind.
- **🖱️ Mouse Scroll Sync**: Automatically respects your configured scroll sensitivity multiplier (`scroll_factor`) and direction (`natural_scroll`) from the [Mouse Settings](https://github.com/meviusisback/mouse-settings) plugin (`meviusisback.mouse-settings`).
- **🔍 Record-to-Find Shortcut Lookup**: Click **Record to Find** next to the search bar and press any key chord on your keyboard to instantly locate its assigned action (or reveal that it's unassigned with a 1-click **+ Create Keybinding** shortcut).
- **🔄 Safe Lua Sync & Live Reload**: Writes directly to `~/.config/hypr/bindings.lua` (with automatic `.bak` backups) and invokes `hyprctl reload` for instantaneous application without session restarts.

---

## 🚀 Installation

Install directly from the marketplace with the Omarchy CLI (it clones and links the plugin into `~/.config/omarchy/plugins/`):
```bash
omarchy plugin add https://github.com/meviusisback/keybinds-plugin --enable
```

The plugin is then available via `omarchy-shell shell summon meviusisback.keybinds`.

---

## 📖 Usage

### From the Graphical Desktop
1. Summon the plugin via `omarchy-shell shell summon meviusisback.keybinds` (or your configured launcher/keybinding).
2. Search by shortcut (e.g. `SUPER + W`), action name (e.g. `Terminal`), category, or command.

### From the Terminal
Launch the GUI directly:
```bash
omarchy-keybinds
```
Or summon via the shell IPC:
```bash
omarchy-shell shell summon meviusisback.keybinds '{}'
```

---

## 🛠️ CLI Command Reference

The plugin includes a full-featured CLI backend engine:

```bash
# List all active keybindings, presets, conflicts, and mouse settings in JSON
omarchy-keybinds list

# Create or modify a shortcut
omarchy-keybinds set "<KEY>" "<DESCRIPTION>" "<COMMAND>" "[ACTION_LUA]" "[OLD_KEY]"
# Example:
omarchy-keybinds set "SUPER + SHIFT + B" "My Browser" "omarchy-launch-browser"

# Reset a modified shortcut back to Omarchy default
omarchy-keybinds reset "SUPER + SHIFT + B"

# Disable / unbind a shortcut
omarchy-keybinds disable "SUPER + W"

# Re-enable a previously disabled default shortcut
omarchy-keybinds enable "SUPER + W"

# Reload Hyprland configuration
omarchy-keybinds reload
```

---

## ⚙️ Configuration & Lua Sync

The plugin manages personal overrides in `~/.config/hypr/bindings.lua`:

- **Custom / Rebound Keybindings**: Generated with standard `o.bind(...)` directives.
- **Disabled Keybindings**: Generated with `hl.unbind(...)` directives.
- **Safety Backups**: A backup (`bindings.lua.bak`) is created automatically before any write operation.
- **Comment Preservation**: Personal comments and manual custom blocks in `bindings.lua` are preserved.

---

## 🧪 Testing

Automated unit tests cover key normalization, Lua parser safety, comment handling, unbind detection, and conflict evaluation:

```bash
python3 -m unittest discover -s tests -p "test_*.py"
```

---

---

## 🗑️ Removal

```bash
# Remove the plugin from Omarchy (keeps your personal ~/.config/hypr/bindings.lua untouched)
omarchy-shell shell hide meviusisback.keybinds >/dev/null 2>&1 || true
omarchy-shell shell removePlugin meviusisback.keybinds >/dev/null 2>&1 || true
rm -f ~/.config/omarchy/plugins/meviusisback.keybinds
```

---

## 🔒 Security & Privacy

- **Local-only**: The plugin runs entirely on your machine. It has **no network access**, no telemetry, and reads only your Hyprland keybinding configuration (`~/.config/hypr/bindings.lua` and the Omarchy default bindings).
- **No keylogging**: The interactive recorder captures keystrokes only in-memory to display the pressed chord; nothing is persisted to disk or transmitted.
- **Safe Lua writes**: All key chords, descriptions, and commands are validated and escaped before being written to `bindings.lua`. Invalid chords (containing characters that could break out of a Lua literal) are rejected, and string values are escaped so a crafted binding description cannot inject arbitrary Lua.
- **Automatic backups**: Every write first copies the existing `bindings.lua` to `bindings.lua.bak` and applies the change atomically via an atomic rename.

## 🏗️ Architecture

```
keybinds-plugin/
├── manifest.json                 # Omarchy plugin manifest (declares kind: ["panel"], keepLoaded: true)
├── KeybindsPanel.qml             # Main GUI window with search, category tabs, and active key list
├── EditKeybindDialog.qml         # Modal dialog for creating/editing shortcuts with smart free recommendations
├── KeyRecorder.qml               # Interactive keyboard event catcher, modifier pills, and live conflict check
├── KeyBadge.qml                  # Visual keyboard chord badge renderer
├── bin/
│   └── omarchy-keybinds          # CLI wrapper script
├── backend/
│   └── keybinds_manager.py       # Python engine for parsing defaults, user Lua config, conflicts, and mouse sync
├── tests/
│   └── test_backend.py           # Unit tests for parser and backend operations
└── README.md                     # Documentation
```

---

## 📄 License

Distributed under the **MIT License**. See `LICENSE` for details.
