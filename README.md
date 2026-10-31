<div align="center">

# Keybindings

**A graphical frontend for Omarchy keybindings: view, search, modify and create Hyprland shortcuts with live key recording, smart free-key recommendations, collision detection and safe Lua sync.**

[![License: MIT](https://img.shields.io/github/license/meviusisback/keybinds-plugin)](LICENSE)
![Linux](https://img.shields.io/badge/linux-supported-1793d1)

![Keybindings manager panel](preview.png)
![Keybindings feature overview](infographic.png)

</div>

## Why

Editing Hyprland keybindings by hand means memorizing chords, guessing free combinations and hoping your Lua still parses. This plugin replaces that loop with a visual manager: see every binding sorted and searchable, record a new chord by pressing it, get told about collisions before they bite, and let the plugin write `bindings.lua` safely — with a backup every time and a live reload, no session restart.

## Features

- **On-demand launch** — summon the floating manager anytime via `omarchy-shell shell summon meviusisback.keybinds` (or bind it to a key); as a `panel` plugin, nothing sits in the top bar until summoned.
- **Alphabetical sorting** — all active keybindings and catalog presets, strictly case-insensitive by action name.
- **Dedicated Modified & Custom section** — review all personal shortcut overrides and custom keybindings in one place, with 1-click **↺ Reset to Default** and **🗑️ Delete**.
- **Smart free-key recommendations** — when creating or editing an action, the modal scans for 100% free, unassigned shortcuts matching the action's first letter (e.g. `SUPER + S`, `SUPER + SHIFT + S` for Spotify) with 1-click suggestion chips.
- **Multi-method key recorder** — live keyboard capture (click to record, press any combination); modifier toggle pills (<kbd>SUPER</kbd>, <kbd>CTRL</kbd>, <kbd>ALT</kbd>, <kbd>SHIFT</kbd>); quick-key chips for special keys (<kbd>RETURN</kbd>, <kbd>SPACE</kbd>, <kbd>ESCAPE</kbd>, <kbd>TAB</kbd>, <kbd>F1-F12</kbd>).
- **Collision detection & 1-click rebind** — shortcut conflicts surface in real time as detailed cards with 1-click rebind options for the colliding actions.
- **Actions catalog** — pre-configured Hyprland / Omarchy window-management, workspace, media and system presets ready to bind.
- **Mouse scroll sync** — respects your scroll sensitivity multiplier (`scroll_factor`) and direction (`natural_scroll`) from the [Mouse Settings](https://github.com/meviusisback/mouse-settings) plugin (`meviusisback.mouse-settings`).
- **Record-to-find lookup** — press **Record to Find** next to the search bar and hit any key chord to locate its assigned action instantly (or get a 1-click **+ Create Keybinding** when it's unassigned).
- **Safe Lua sync & live reload** — writes directly to `~/.config/hypr/bindings.lua` (with automatic `.bak` backups) and invokes `hyprctl reload` for instantaneous application without session restarts.

## Install

```bash
omarchy plugin add https://github.com/meviusisback/keybinds-plugin --enable
```

On first launch, the plugin offers to add a **Keybindings Manager** entry to your Omarchy launcher (Setup → Keybindings → Keybindings Manager). It asks once per session — accept or dismiss — and you can toggle it anytime from the **📋 Add to Launcher** / **✓ In Launcher** button in the header bar.

## Usage

From the desktop: summon with `omarchy-shell shell summon meviusisback.keybinds` (or your launcher/keybinding), then search by shortcut (e.g. `SUPER + W`), action name (e.g. `Terminal`), category or command.

From the terminal — the `bin/` directory is **not** on `PATH` by default (Omarchy only puts `/usr/share/omarchy/bin` there). Either call the wrapper by its installed path:

```bash
~/.config/omarchy/plugins/meviusisback.keybinds/bin/omarchy-keybinds
```

or link it once into your personal bin (the wrapper resolves symlinks correctly):

```bash
mkdir -p ~/.local/bin
ln -s ~/.config/omarchy/plugins/meviusisback.keybinds/bin/omarchy-keybinds ~/.local/bin/omarchy-keybinds
omarchy-keybinds   # launch the GUI directly
```

Or summon via the shell IPC:

```bash
omarchy-shell shell summon meviusisback.keybinds
```

Note: the raw IPC exits 0 even when the shell refuses it (it answers `ok` / `unknown` — e.g. while the plugin is disabled). The wrapper turns a refusal into a visible error with the matching `omarchy plugin enable` hint, instead of exiting 0 silently.

CLI reference (full backend engine):

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

## Requirements

- Omarchy Linux with Hyprland (bindings live in `~/.config/hypr/bindings.lua`; reload via `hyprctl`)
- Quickshell / QML shell for the graphical frontend
- Python 3.10+ (backend engine, standard library)

## Configuration

The plugin manages personal overrides in `~/.config/hypr/bindings.lua`:

- **Custom / rebound keybindings** — generated with standard `o.bind(...)` directives.
- **Disabled keybindings** — generated with `hl.unbind(...)` directives.
- **Safety backups** — a backup (`bindings.lua.bak`) is created automatically before any write operation.
- **Comment preservation** — personal comments and manual custom blocks in `bindings.lua` are preserved.

## How it works

The Python engine (`backend/keybinds_manager.py`) parses the Omarchy default bindings plus your `bindings.lua`, evaluates conflicts and mouse-scroll sync, and writes changes back with validated, escaped values via atomic rename (invalid chords that could break out of a Lua literal are rejected). The QML panels (`KeybindsPanel`, `EditKeybindDialog`, `KeyRecorder`, `KeyBadge`) are the frontend. Local-only: no network access, no telemetry, only your keybinding files are read — the interactive recorder captures keystrokes in memory just to display the pressed chord; nothing is persisted or transmitted.

Verify with `python3 -m unittest discover -s tests -p "test_*.py"` — 14 tests covering key normalization, Lua parser safety, comment handling, unbind detection and conflict evaluation.

## Removal

```bash
omarchy plugin remove meviusisback.keybinds
```

Keeps your personal `~/.config/hypr/bindings.lua` untouched.

## License

MIT — see [LICENSE](LICENSE).
