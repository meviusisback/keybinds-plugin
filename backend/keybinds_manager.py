#!/usr/bin/env python3
"""
Keybindings Manager Backend for Omarchy Hyprland
Provides parsing, catalog discovery, conflict detection, mouse scroll integration, and safe Lua config manipulation.
"""

import os
import sys
import re
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
import tempfile
import stat
import glob
import configparser

DEFAULT_BINDINGS_DIR = os.environ.get("OMARCHY_PATH", "/usr/share/omarchy") + "/default/hypr/bindings"
USER_BINDINGS_PATH = os.path.expanduser("~/.config/hypr/bindings.lua")
CACHE_DIR = os.path.expanduser("~/.cache/omarchy")

# Standard modifier weights for consistent ordering
MODIFIER_ORDER = {"SUPER": 1, "SHIFT": 2, "CTRL": 3, "CONTROL": 3, "ALT": 4}
# Reject any chord containing characters that could break out of a Lua literal or command context.
UNSAFE_KEY_RE = re.compile(r'["\'\\()\n\r\t]')
MAX_KEY_LEN = 64
MAX_TEXT_LEN = 200
MAX_CMD_LEN = 2000
MAX_FILE_BYTES = 65_536  # 64 KiB cap on any single config file read
MAX_DIR_FILES = 256  # ceiling on enumerated default-binding files
MAX_TOTAL_READ = 2_097_152  # 2 MiB cumulative cap across the default-bindings dir

MENU_CONFIG_PATH = os.path.expanduser("~/.config/omarchy/extensions/omarchy-menu.jsonc")
MENU_ENTRY_KEY = "setup.keybindings.gui"
MENU_ENTRY_VALUE = {
    "icon": "\uf464",
    "label": "Keybindings Manager",
    "action": "omarchy-shell shell summon meviusisback.keybinds",
}
_JSONC_LINE_COMMENT_RE = re.compile(r'//.*$')
_JSONC_BLOCK_COMMENT_RE = re.compile(r'/\*.*?\*/', re.DOTALL)

def sanitize_lua_str(s: str, max_len: int = MAX_TEXT_LEN) -> str:
    """Escape a string for safe embedding inside a Lua double-quoted literal."""
    s = (s or "").replace("\r", " ").replace("\n", " ")
    if len(s) > max_len:
        s = s[:max_len]
    return s.replace("\\", "\\\\").replace('"', '\\"')


# Pre-compiled regex for the o.bind() header: matches the function call
# up to and including the opening of the 3rd argument.
_BIND_HEADER_RE = re.compile(
    r"o\.bind(?:_toggle)?\s*\(\s*[\"']([^\"']+)[\"']\s*,\s*"
    r"(?:[\"']([^\"']+)[\"']|nil)\s*,\s*"
)


def _parse_bind_line(line: str):
    """Extract (raw_key, desc, action, opts) from an o.bind() or o.bind_toggle() line.

    Uses parenthesis/brace depth tracking instead of a single regex to
    correctly handle actions containing nested parentheses and curly
    braces (e.g. ``hl.dsp.window.move({ direction = "left" })``).
    """
    m = _BIND_HEADER_RE.match(line)
    if not m:
        return None

    raw_key = m.group(1).strip()
    desc = (m.group(2) or "").strip()
    pos = m.end()

    depth_paren = 0
    depth_brace = 0
    in_string = None
    escape_next = False
    action_start = pos

    while pos < len(line):
        ch = line[pos]

        if escape_next:
            escape_next = False
            pos += 1
            continue

        if ch == '\\' and in_string:
            escape_next = True
            pos += 1
            continue

        if ch == '"' or ch == "'":
            if not in_string:
                in_string = ch
            elif in_string == ch:
                in_string = None
            pos += 1
            continue

        if in_string:
            pos += 1
            continue

        if ch == '(':
            depth_paren += 1
        elif ch == ')':
            if depth_paren > 0:
                depth_paren -= 1
            else:
                break  # closes o.bind()
        elif ch == '{':
            depth_brace += 1
        elif ch == '}':
            depth_brace -= 1
        elif ch == ',' and depth_paren == 0 and depth_brace == 0:
            break  # separator before opts

        pos += 1

    action = line[action_start:pos].strip()

    opts = None
    if pos < len(line) and line[pos] == ',':
        opts_start = pos + 1
        opts_text = line[opts_start:].rstrip()
        if opts_text.endswith(')'):
            opts_text = opts_text[:-1].strip()
        if opts_text:
            opts = opts_text

    return raw_key, desc, action, opts



PRESET_CATALOG = [
    # Window Management
    {
        "id": "win.close",
        "category": "Window Management",
        "name": "Close Window",
        "description": "Close currently focused window",
        "dispatcher": "hl.dsp.window.close()",
        "command": "hyprctl dispatch killactive",
        "default_key": "SUPER + W",
    },
    {
        "id": "win.close_all",
        "category": "Window Management",
        "name": "Close All Windows",
        "description": "Close all open windows on current workspace",
        "dispatcher": "exec",
        "command": "omarchy-hyprland-window-close-all",
        "default_key": "CTRL + ALT + DELETE",
    },
    {
        "id": "win.fullscreen",
        "category": "Window Management",
        "name": "Toggle Fullscreen",
        "description": "Toggle fullscreen state for focused window",
        "dispatcher": "hl.dsp.window.fullscreen()",
        "command": "hyprctl dispatch fullscreen",
        "default_key": "SUPER + F",
    },
    {
        "id": "win.fullwidth",
        "category": "Window Management",
        "name": "Toggle Full Width",
        "description": "Expand window across the full screen width",
        "dispatcher": 'hl.dsp.window.fullscreen({ mode = "maximized" })',
        "command": "hyprctl dispatch fullscreen 2",
        "default_key": "SUPER + ALT + F",
    },
    {
        "id": "win.floating",
        "category": "Window Management",
        "name": "Toggle Window Floating",
        "description": "Toggle between tiling and floating mode",
        "dispatcher": "hl.dsp.window.float({ action = \"toggle\" })",
        "command": "hyprctl dispatch togglefloating",
        "default_key": "SUPER + T",
    },
    {
        "id": "win.split",
        "category": "Window Management",
        "name": "Toggle Split Direction",
        "description": "Toggle window tiling split between horizontal/vertical",
        "dispatcher": "hl.dsp.layout(\"togglesplit\")",
        "command": "hyprctl dispatch layoutmsg togglesplit",
        "default_key": "SUPER + J",
    },
    {
        "id": "win.popout",
        "category": "Window Management",
        "name": "Pop Window Out (Float & Pin)",
        "description": "Float focused window and pin it across workspaces",
        "dispatcher": "exec",
        "command": "omarchy-hyprland-window-pop",
        "default_key": "SUPER + O",
    },
    {
        "id": "win.pin",
        "category": "Window Management",
        "name": "Pin Active Window",
        "description": "Pin focused window across all workspaces",
        "dispatcher": "hl.dsp.window.pin()",
        "command": "hyprctl dispatch pin",
        "default_key": "SUPER + ALT + P",
    },
    {
        "id": "win.transparency",
        "category": "Window Management",
        "name": "Toggle Window Transparency",
        "description": "Toggle transparency for active window",
        "dispatcher": "exec",
        "command": "omarchy-hyprland-window-transparency-toggle",
        "default_key": "SUPER + BACKSPACE",
    },
    {
        "id": "win.gaps",
        "category": "Window Management",
        "name": "Toggle Window Gaps",
        "description": "Toggle inner and outer workspace window gaps",
        "dispatcher": "exec",
        "command": "omarchy-hyprland-window-gaps-toggle",
        "default_key": "SUPER + SHIFT + BACKSPACE",
    },
    {
        "id": "win.focus_left",
        "category": "Window Management",
        "name": "Focus Window Left",
        "description": "Move focus to the window on the left",
        "dispatcher": 'hl.dsp.focus({ direction = "l" })',
        "command": "hyprctl dispatch movefocus l",
        "default_key": "SUPER + LEFT",
    },
    {
        "id": "win.focus_right",
        "category": "Window Management",
        "name": "Focus Window Right",
        "description": "Move focus to the window on the right",
        "dispatcher": 'hl.dsp.focus({ direction = "r" })',
        "command": "hyprctl dispatch movefocus r",
        "default_key": "SUPER + RIGHT",
    },
    {
        "id": "win.focus_up",
        "category": "Window Management",
        "name": "Focus Window Up",
        "description": "Move focus to the window above",
        "dispatcher": 'hl.dsp.focus({ direction = "u" })',
        "command": "hyprctl dispatch movefocus u",
        "default_key": "SUPER + UP",
    },
    {
        "id": "win.focus_down",
        "category": "Window Management",
        "name": "Focus Window Down",
        "description": "Move focus to the window below",
        "dispatcher": 'hl.dsp.focus({ direction = "d" })',
        "command": "hyprctl dispatch movefocus d",
        "default_key": "SUPER + DOWN",
    },
    {
        "id": "mon.focus_next",
        "category": "Window Management",
        "name": "Focus Next Monitor",
        "description": "Switch active focus to the next physical monitor",
        "dispatcher": 'hl.dsp.focus({ monitor = "+1" })',
        "command": "hyprctl dispatch focusmonitor +1",
        "default_key": "CTRL + ALT + TAB",
    },
    {
        "id": "win.move_left",
        "category": "Window Management",
        "name": "Move Window Left",
        "description": "Move active window to the left in tiling layout",
        "dispatcher": "hl.dsp.window.move({ direction = \"left\" })",
        "command": "hyprctl dispatch movewindow l",
        "default_key": "SUPER + ALT + LEFT",
    },
    {
        "id": "win.move_right",
        "category": "Window Management",
        "name": "Move Window Right",
        "description": "Move active window to the right in tiling layout",
        "dispatcher": "hl.dsp.window.move({ direction = \"right\" })",
        "command": "hyprctl dispatch movewindow r",
        "default_key": "SUPER + ALT + RIGHT",
    },
    {
        "id": "win.move_up",
        "category": "Window Management",
        "name": "Move Window Up",
        "description": "Move active window up in tiling layout",
        "dispatcher": "hl.dsp.window.move({ direction = \"up\" })",
        "command": "hyprctl dispatch movewindow u",
        "default_key": "SUPER + ALT + UP",
    },
    {
        "id": "win.move_down",
        "category": "Window Management",
        "name": "Move Window Down",
        "description": "Move active window down in tiling layout",
        "dispatcher": "hl.dsp.window.move({ direction = \"down\" })",
        "command": "hyprctl dispatch movewindow d",
        "default_key": "SUPER + ALT + DOWN",
    },

    # Workspaces
    {
        "id": "ws.next",
        "category": "Workspaces",
        "name": "Next Workspace",
        "description": "Switch to next workspace",
        "dispatcher": "hl.dsp.focus({ workspace = \"e+1\" })",
        "command": "hyprctl dispatch workspace e+1",
        "default_key": "SUPER + TAB",
    },
    {
        "id": "ws.prev",
        "category": "Workspaces",
        "name": "Previous Workspace",
        "description": "Switch to previous workspace",
        "dispatcher": "hl.dsp.focus({ workspace = \"e-1\" })",
        "command": "hyprctl dispatch workspace e-1",
        "default_key": "SUPER + SHIFT + TAB",
    },
    {
        "id": "ws.previous_history",
        "category": "Workspaces",
        "name": "Previous Visited Workspace",
        "description": "Toggle focus back to the previously active workspace",
        "dispatcher": 'hl.dsp.focus({ workspace = "previous" })',
        "command": "hyprctl dispatch workspace previous",
        "default_key": "SUPER + GRAVE",
    },
    {
        "id": "mon.move_ws_left",
        "category": "Workspaces",
        "name": "Move Workspace to Left Monitor",
        "description": "Shift current workspace onto the monitor to the left",
        "dispatcher": 'hl.dsp.workspace.move({ monitor = "l" })',
        "command": "hyprctl dispatch movecurrentworkspacetomonitor l",
        "default_key": "SUPER + SHIFT + ALT + LEFT",
    },
    {
        "id": "ws.scratchpad_toggle",
        "category": "Workspaces",
        "name": "Toggle Special Scratchpad",
        "description": "Toggle overlay special workspace (scratchpad)",
        "dispatcher": 'hl.dsp.workspace.toggle_special("scratchpad")',
        "command": "hyprctl dispatch togglespecialworkspace scratchpad",
        "default_key": "SUPER + S",
    },
    {
        "id": "ws.scratchpad_moveto",
        "category": "Workspaces",
        "name": "Move to Scratchpad",
        "description": "Move focused window into special scratchpad",
        "dispatcher": 'hl.dsp.window.move({ workspace = "special:scratchpad", follow = false })',
        "command": "hyprctl dispatch movetoworkspace special:scratchpad",
        "default_key": "SUPER + ALT + S",
    },

    # Menus & Omarchy Shell
    {
        "id": "menu.root",
        "category": "Menus & System",
        "name": "Omarchy Menu",
        "description": "Summon the primary Omarchy launcher menu",
        "dispatcher": "exec",
        "command": "omarchy-menu toggle",
        "default_key": "SUPER + SPACE",
    },
    {
        "id": "menu.apps",
        "category": "Menus & System",
        "name": "Apps Menu",
        "description": "Summon the applications list menu",
        "dispatcher": "exec",
        "command": "omarchy-menu toggle apps",
        "default_key": "SUPER + ALT + SPACE",
    },
    {
        "id": "menu.system",
        "category": "Menus & System",
        "name": "System / Power Menu",
        "description": "Summon the system power/logout menu",
        "dispatcher": "exec",
        "command": "omarchy-menu toggle system",
        "default_key": "SUPER + ESCAPE",
    },
    {
        "id": "menu.keybindings",
        "category": "Menus & System",
        "name": "Keybindings Helper",
        "description": "Open keybindings quick selector",
        "dispatcher": "exec",
        "command": "omarchy-menu-keybindings",
        "default_key": "SUPER + K",
    },
    {
        "id": "shell.emojis",
        "category": "Menus & System",
        "name": "Emoji Picker",
        "description": "Open full-screen emoji selector overlay",
        "dispatcher": "exec",
        "command": "omarchy-shell shell toggle omarchy.emojis",
        "default_key": "SUPER + CTRL + E",
    },
    {
        "id": "shell.clipboard",
        "category": "Menus & System",
        "name": "Clipboard Manager",
        "description": "Open clipboard history overlay",
        "dispatcher": "exec",
        "command": "omarchy-shell shell toggle omarchy.clipboard",
        "default_key": "SUPER + CTRL + V",
    },
    {
        "id": "shell.theme",
        "category": "Menus & System",
        "name": "Theme Menu",
        "description": "Summon the Omarchy theme switcher",
        "dispatcher": "exec",
        "command": "omarchy-menu toggle theme",
        "default_key": "SUPER + SHIFT + CTRL + SPACE",
    },
    {
        "id": "shell.background",
        "category": "Menus & System",
        "name": "Background Switcher",
        "description": "Summon wallpaper selector overlay",
        "dispatcher": "exec",
        "command": "omarchy-menu toggle background",
        "default_key": "SUPER + CTRL + SPACE",
    },
    {
        "id": "shell.toggle_bar",
        "category": "Menus & System",
        "name": "Toggle Menu Bar",
        "description": "Show or hide the top status bar",
        "dispatcher": "exec",
        "command": "omarchy-toggle-bar",
        "default_key": "SUPER + SHIFT + SPACE",
    },
    {
        "id": "sys.lock",
        "category": "Menus & System",
        "name": "Lock System",
        "description": "Trigger session screen lock",
        "dispatcher": "exec",
        "command": "omarchy-system-lock",
        "default_key": "SUPER + CTRL + L",
    },
    {
        "id": "sys.screensaver",
        "category": "Menus & System",
        "name": "Screensaver",
        "description": "Start the Omarchy screensaver",
        "dispatcher": "exec",
        "command": "omarchy-launch-screensaver force",
        "default_key": "SUPER + ALT + L",
    },

    # Applications
    {
        "id": "app.terminal",
        "category": "Applications",
        "name": "Terminal",
        "description": "Terminal",
        "dispatcher": "{ omarchy = \"terminal\" }",
        "command": "omarchy-launch-terminal",
        "default_key": "SUPER + RETURN",
    },
    {
        "id": "app.browser",
        "category": "Applications",
        "name": "Browser",
        "description": "Browser",
        "dispatcher": "{ omarchy = \"browser\" }",
        "command": "omarchy-launch-browser",
        "default_key": "SUPER + SHIFT + RETURN",
    },
    {
        "id": "app.file_manager",
        "category": "Applications",
        "name": "File Manager",
        "description": "File manager",
        "dispatcher": "{ omarchy = \"nautilus\" }",
        "command": "omarchy-launch-nautilus",
        "default_key": "SUPER + SHIFT + F",
    },
    {
        "id": "app.file_manager_cwd",
        "category": "Applications",
        "name": "File Manager (Active CWD)",
        "description": "File manager (cwd)",
        "dispatcher": "{ omarchy = \"nautilus-cwd\" }",
        "command": "omarchy-launch-nautilus-cwd",
        "default_key": "SUPER + ALT + SHIFT + F",
    },
    {
        "id": "app.editor",
        "category": "Applications",
        "name": "Code Editor",
        "description": "Editor",
        "dispatcher": "{ omarchy = \"editor\" }",
        "command": "omarchy-launch-editor",
        "default_key": "SUPER + SHIFT + N",
    },
    {
        "id": "app.docker",
        "category": "Applications",
        "name": "Docker",
        "description": "Docker",
        "dispatcher": "{ tui = \"lazydocker\" }",
        "command": "lazydocker",
        "default_key": "SUPER + SHIFT + D",
    },
    {
        "id": "app.calculator",
        "category": "Applications",
        "name": "Calculator",
        "description": "Calculator",
        "dispatcher": "exec",
        "command": "omacalc",
        "default_key": "SUPER + CTRL + Q",
    },

    # Media & Screen
    {
        "id": "media.volume_up",
        "category": "Media & Audio",
        "name": "Volume Up",
        "description": "Raise audio output volume",
        "dispatcher": "exec",
        "command": "omarchy-audio-output-volume raise",
        "default_key": "XF86AudioRaiseVolume",
    },
    {
        "id": "media.volume_down",
        "category": "Media & Audio",
        "name": "Volume Down",
        "description": "Lower audio output volume",
        "dispatcher": "exec",
        "command": "omarchy-audio-output-volume lower",
        "default_key": "XF86AudioLowerVolume",
    },
    {
        "id": "media.mute",
        "category": "Media & Audio",
        "name": "Mute Audio",
        "description": "Toggle audio output mute",
        "dispatcher": "exec",
        "command": "omarchy-audio-output-volume mute-toggle",
        "default_key": "XF86AudioMute",
    },
    {
        "id": "media.mic_mute",
        "category": "Media & Audio",
        "name": "Mute Microphone",
        "description": "Toggle audio microphone mute",
        "dispatcher": "exec",
        "command": "omarchy-audio-input-mute",
        "default_key": "XF86AudioMicMute",
    },
    {
        "id": "media.screenshot",
        "category": "Media & Audio",
        "name": "Capture Screenshot",
        "description": "Capture screen or interactive slurp region",
        "dispatcher": "exec",
        "command": "omarchy-capture-screenshot",
        "default_key": "PRINT",
    },
    {
        "id": "media.screenrecord",
        "category": "Media & Audio",
        "name": "Toggle Screenrecording",
        "description": "Start or stop screenrecording",
        "dispatcher": "exec",
        "command": "omarchy-capture-screenrecording --stop-recording || omarchy-menu toggle trigger.capture.screenrecord",
        "default_key": "ALT + PRINT",
    },
    {
        "id": "media.color_picker",
        "category": "Media & Audio",
        "name": "Color Picker",
        "description": "Pick color under cursor to clipboard",
        "dispatcher": "exec",
        "command": "pkill hyprpicker || hyprpicker -a",
        "default_key": "SUPER + PRINT",
    },
    {
        "id": "media.ocr_text",
        "category": "Media & Audio",
        "name": "OCR Text from Screen",
        "description": "Extract text from selected screen area",
        "dispatcher": "exec",
        "command": "omarchy-capture-text",
        "default_key": "SUPER + CTRL + PRINT",
    },
    # Workspaces 1-10 Switching & Window Movement
    *[
        {
            "id": f"ws.switch_{i}",
            "category": "Workspaces",
            "name": f"Switch to Workspace {i}",
            "description": f"Switch active focus to workspace {i}",
            "dispatcher": f'hl.dsp.focus({{ workspace = "{i}" }})',
            "command": f"hyprctl dispatch workspace {i}",
            "default_key": f"SUPER + {i % 10}",
        }
        for i in range(1, 11)
    ],
    *[
        {
            "id": f"ws.move_{i}",
            "category": "Workspaces",
            "name": f"Move Window to Workspace {i}",
            "description": f"Move active window to workspace {i}",
            "dispatcher": f'hl.dsp.window.move({{ workspace = "{i}" }})',
            "command": f"hyprctl dispatch movetoworkspace {i}",
            "default_key": f"SUPER + SHIFT + {i % 10}",
        }
        for i in range(1, 11)
    ],
    *[
        {
            "id": f"ws.movesilent_{i}",
            "category": "Workspaces",
            "name": f"Move Window to Workspace {i} (Silent)",
            "description": f"Move active window to workspace {i} without switching focus",
            "dispatcher": f'hl.dsp.window.move({{ workspace = "{i}", follow = false }})',
            "command": f"hyprctl dispatch movetoworkspacesilent {i}",
            "default_key": f"SUPER + SHIFT + ALT + {i % 10}",
        }
        for i in range(1, 11)
    ],
    # Window Swapping
    {
        "id": "win.swap_left",
        "category": "Window Management",
        "name": "Swap Window Left",
        "description": "Swap active window with the window to the left",
        "dispatcher": 'hl.dsp.window.swap({ direction = "l" })',
        "command": "hyprctl dispatch swapwindow l",
        "default_key": "SUPER + SHIFT + LEFT",
    },
    {
        "id": "win.swap_right",
        "category": "Window Management",
        "name": "Swap Window Right",
        "description": "Swap active window with the window to the right",
        "dispatcher": 'hl.dsp.window.swap({ direction = "r" })',
        "command": "hyprctl dispatch swapwindow r",
        "default_key": "SUPER + SHIFT + RIGHT",
    },
    {
        "id": "win.swap_up",
        "category": "Window Management",
        "name": "Swap Window Up",
        "description": "Swap active window with the window above",
        "dispatcher": 'hl.dsp.window.swap({ direction = "u" })',
        "command": "hyprctl dispatch swapwindow u",
        "default_key": "SUPER + SHIFT + UP",
    },
    {
        "id": "win.swap_down",
        "category": "Window Management",
        "name": "Swap Window Down",
        "description": "Swap active window with the window below",
        "dispatcher": 'hl.dsp.window.swap({ direction = "d" })',
        "command": "hyprctl dispatch swapwindow d",
        "default_key": "SUPER + SHIFT + DOWN",
    },
    # Media Playback Controls
    {
        "id": "media.play_pause",
        "category": "Media & Audio",
        "name": "Play / Pause Media",
        "description": "Toggle media playback",
        "dispatcher": "exec",
        "command": "omarchy-shell media playPause",
        "default_key": "XF86AudioPlay",
    },
    {
        "id": "media.next_track",
        "category": "Media & Audio",
        "name": "Next Track",
        "description": "Skip to next media track",
        "dispatcher": "exec",
        "command": "omarchy-shell media next",
        "default_key": "XF86AudioNext",
    },
    {
        "id": "media.prev_track",
        "category": "Media & Audio",
        "name": "Previous Track",
        "description": "Return to previous media track",
        "dispatcher": "exec",
        "command": "omarchy-shell media previous",
        "default_key": "XF86AudioPrev",
    },
    # Display Brightness
    {
        "id": "media.brightness_up",
        "category": "Media & Audio",
        "name": "Raise Brightness",
        "description": "Increase display brightness by 5%",
        "dispatcher": "exec",
        "command": "omarchy-brightness-display +5%",
        "default_key": "XF86MonBrightnessUp",
    },
    {
        "id": "media.brightness_down",
        "category": "Media & Audio",
        "name": "Lower Brightness",
        "description": "Decrease display brightness by 5%",
        "dispatcher": "exec",
        "command": "omarchy-brightness-display 5%-",
        "default_key": "XF86MonBrightnessDown",
    },
]

_INSTALLED_APPS_CACHE = None

def get_installed_apps():
    """Scan standard XDG application directories for launchable applications.
    Returns a sorted list of unique applications: [{'name': ..., 'exec': ..., 'icon': ..., 'comment': ..., 'terminal': bool}]
    """
    global _INSTALLED_APPS_CACHE
    if _INSTALLED_APPS_CACHE is not None:
        return _INSTALLED_APPS_CACHE

    apps = []
    seen = set()
    dirs = [
        os.path.expanduser("~/.local/share/applications"),
        "/usr/share/applications",
        "/usr/local/share/applications",
    ]

    for app_dir in dirs:
        if not os.path.isdir(app_dir):
            continue
        for desktop_file in sorted(glob.glob(os.path.join(app_dir, "*.desktop"))):
            cp = configparser.RawConfigParser()
            try:
                cp.read(desktop_file, encoding="utf-8")
                if not cp.has_section("Desktop Entry"):
                    continue
                entry = cp["Desktop Entry"]
                if entry.get("Type", "Application") != "Application":
                    continue
                if entry.get("NoDisplay", "false").lower() == "true":
                    continue
                if entry.get("Hidden", "false").lower() == "true":
                    continue

                name = entry.get("Name", "").strip()
                exec_cmd = entry.get("Exec", "").strip()
                if not name or not exec_cmd:
                    continue

                # Strip field codes like %u, %f, %F, %U, --uri=%u, and trailing --
                clean_exec = re.sub(r'--\w+=%[a-zA-Z%]', '', exec_cmd)
                clean_exec = re.sub(r'["\']?%[a-zA-Z%]["\']?', '', clean_exec)
                clean_exec = re.sub(r'\s+--\s*$', '', clean_exec.strip())
                clean_exec = " ".join(clean_exec.split())
                if not clean_exec:
                    clean_exec = exec_cmd

                key = name.lower()
                if key in seen:
                    continue
                seen.add(key)

                apps.append({
                    "name": name,
                    "exec": clean_exec,
                    "icon": entry.get("Icon", "").strip(),
                    "comment": entry.get("Comment", "").strip(),
                    "terminal": entry.get("Terminal", "false").lower() == "true",
                })
            except Exception:
                continue

    apps.sort(key=lambda x: x["name"].lower())
    _INSTALLED_APPS_CACHE = apps
    return apps



def normalize_key_chord(key_chord: str) -> str:
    """Normalize a key combination string to standard 'MOD1 + MOD2 + KEY' format."""
    if not key_chord:
        return ""

    chord = key_chord.strip()
    trailing_key = None
    if chord.endswith("++"):
        chord = chord[:-1]
        trailing_key = "+"
    elif chord.endswith("+") and chord != "+":
        trailing_key = "+"
        chord = chord[:-1]

    raw_parts = [p.strip() for p in chord.split("+") if p.strip()]
    if trailing_key:
        raw_parts.append(trailing_key)
    if not raw_parts:
        return ""

    mods = []
    main_key = ""

    for part in raw_parts:
        upper = part.upper()
        if upper in ("SUPER", "WIN", "LOGO", "SUPER_L", "SUPER_R", "META", "MOD4"):
            if "SUPER" not in mods:
                mods.append("SUPER")
        elif upper in ("SHIFT", "SHIFT_L", "SHIFT_R"):
            if "SHIFT" not in mods:
                mods.append("SHIFT")
        elif upper in ("CTRL", "CONTROL", "CONTROL_L", "CONTROL_R"):
            if "CTRL" not in mods:
                mods.append("CTRL")
        elif upper in ("ALT", "ALT_L", "ALT_R", "MOD1"):
            if "ALT" not in mods:
                mods.append("ALT")
        else:
            main_key = part

    mods.sort(key=lambda m: MODIFIER_ORDER.get(m, 99))

    SYMBOL_MAP = {
        ",": "comma",
        ".": "period",
        "/": "slash",
        "-": "minus",
        "=": "equal",
        "[": "bracketleft",
        "]": "bracketright",
        ";": "semicolon",
        "'": "apostrophe",
        "\\": "backslash",
        "`": "grave",
        "+": "plus",
    }

    if main_key:
        upper_k = main_key.upper()
        lower_k = main_key.lower()
        if upper_k in ("RETURN", "ENTER", "SPACE", "ESCAPE", "TAB", "BACKSPACE", "DELETE", "PRINT"):
            main_key = upper_k
        elif upper_k in ("LEFT", "RIGHT", "UP", "DOWN"):
            main_key = upper_k
        elif upper_k.startswith("F") and upper_k[1:].isdigit():
            main_key = upper_k
        elif main_key in SYMBOL_MAP:
            main_key = SYMBOL_MAP[main_key]
        elif lower_k in SYMBOL_MAP.values():
            main_key = lower_k
        elif len(main_key) == 1:
            main_key = main_key.upper()

    if mods and main_key:
        out = f"{' + '.join(mods)} + {main_key}"
    elif mods and not main_key:
        out = " + ".join(mods)
    else:
        out = main_key
    if len(out) > MAX_KEY_LEN or UNSAFE_KEY_RE.search(out or ""):
        return ""
    return out



def get_mouse_scroll_settings():
    """Retrieve active Hyprland mouse scroll configuration (scroll_factor & natural_scroll)."""
    settings = {"natural_scroll": False, "scroll_factor": 1.0}
    try:
        res = subprocess.run(["hyprctl", "getoption", "input:scroll_factor", "-j"], capture_output=True, text=True, timeout=10)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            if "float" in data:
                settings["scroll_factor"] = float(data["float"])
    except Exception:
        pass

    try:
        res = subprocess.run(["hyprctl", "getoption", "input:natural_scroll", "-j"], capture_output=True, text=True, timeout=10)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            if "bool" in data:
                settings["natural_scroll"] = bool(data["bool"])
    except Exception:
        pass

    return settings


def parse_default_bindings():
    """Parse all default keybindings shipped with Omarchy."""
    bindings = []
    if not os.path.isdir(DEFAULT_BINDINGS_DIR):
        return bindings

    file_count = 0
    total_read = 0
    for fname in sorted(os.listdir(DEFAULT_BINDINGS_DIR)):
        if file_count >= MAX_DIR_FILES or total_read > MAX_TOTAL_READ:
            break
        file_count += 1
        if not fname.endswith(".lua"):
            continue
        filepath = os.path.join(DEFAULT_BINDINGS_DIR, fname)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read(MAX_FILE_BYTES)
            total_read += len(content.encode("utf-8"))
        except Exception:
            continue

        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith('--'):
                continue

            parsed = _parse_bind_line(stripped)
            if not parsed:
                continue

            raw_key, desc, action_raw, opts_raw = parsed

            norm_key = normalize_key_chord(raw_key)
            if stripped.startswith("o.bind_toggle"):
                clean_target = action_raw.strip('"\'')
                action_raw = f'"omarchy-toggle-{clean_target}"'
            if not desc:
                desc = action_raw.strip('"\'')

            cat = "General"
            base = fname.replace(".lua", "")
            if base == "applications":
                cat = "Applications"
            elif base == "tiling":
                cat = "Window Management"
            elif base == "media":
                cat = "Media & Audio"
            elif base == "utilities":
                cat = "Menus & System"
            elif base == "clipboard":
                cat = "Clipboard"
            elif base == "voxtype":
                cat = "AI & Voice"

            is_release = opts_raw and ("on_release = true" in opts_raw or "on_release=true" in opts_raw)

            bindings.append({
                "source": "default",
                "file": fname,
                "key": norm_key,
                "raw_key": raw_key,
                "description": desc,
                "action": action_raw,
                "category": cat,
                "is_mouse": "mouse" in raw_key.lower() or "mouse" in (opts_raw or ""),
                "is_release": is_release,
            })

    return bindings


def parse_user_bindings():
    """
    Parse user overrides from ~/.config/hypr/bindings.lua.
    CRITICAL: Skips all commented-out lines (starting with --) to avoid false unbind detection.
    """
    result = {
        "raw_content": "",
        "unbinds": set(),
        "binds": [],
        "mouse_binds": [],
    }

    if not os.path.isfile(USER_BINDINGS_PATH):
        return result

    try:
        with open(USER_BINDINGS_PATH, "r", encoding="utf-8") as f:
            content = f.read(MAX_FILE_BYTES)
            result["raw_content"] = content
    except Exception:
        return result

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue

        m_unbind = re.match(r'hl\.unbind\s*\(\s*["\']([^"\']+)["\']\s*\)', stripped)
        if m_unbind:
            raw_key = m_unbind.group(1).strip()
            result["unbinds"].add(normalize_key_chord(raw_key))
            continue

        parsed = _parse_bind_line(stripped)
        if parsed:
            raw_key, desc, action_raw, opts_raw = parsed
            norm_key = normalize_key_chord(raw_key)

            entry = {
                "source": "user",
                "key": norm_key,
                "raw_key": raw_key,
                "description": desc or action_raw.strip('"\''),
                "action": action_raw,
                "is_mouse": "mouse" in raw_key.lower() or "mouse" in (opts_raw or ""),
                "is_release": opts_raw and ("on_release = true" in opts_raw or "on_release=true" in opts_raw),
            }

            if entry["is_mouse"]:
                result["mouse_binds"].append(entry)
            else:
                result["binds"].append(entry)

    return result


def build_complete_model():
    """
    Construct the unified model of active bindings, catalog, conflicts, and mouse scroll settings.
    """
    default_binds = parse_default_bindings()
    user_state = parse_user_bindings()

    rebound_defaults_map = {}
    matched_user_bind_keys = set()

    for ub in user_state["binds"]:
        matched = next(
            (d for d in default_binds if d["description"].lower() == ub["description"].lower() or d["action"] == ub["action"]),
            None
        )
        if matched:
            rebound_defaults_map[matched["key"]] = ub
            matched_user_bind_keys.add(ub["key"])

    all_active = []

    # 1. Iterate defaults
    for d in default_binds:
        k = d["key"]

        if k in rebound_defaults_map:
            ub = rebound_defaults_map[k]
            item = dict(ub)
            item["status"] = "modified"
            item["category"] = d.get("category", "Custom")
            item["default_key"] = k
            item["default_action"] = d.get("action", "")
            all_active.append(item)
            continue

        same_key_user_bind = next((ub for ub in user_state["binds"] if ub["key"] == k and ub["key"] not in matched_user_bind_keys), None)
        if same_key_user_bind:
            item = dict(same_key_user_bind)
            item["status"] = "modified" if same_key_user_bind["description"].lower() == d["description"].lower() else "custom"
            item["category"] = d.get("category", "Custom")
            item["default_key"] = k
            item["default_action"] = d["action"]
            all_active.append(item)
            matched_user_bind_keys.add(k)
            continue

        if k in user_state["unbinds"]:
            item = dict(d)
            item["status"] = "disabled"
            item["default_key"] = k
            all_active.append(item)
            continue

        item = dict(d)
        item["status"] = "default"
        item["default_key"] = k
        all_active.append(item)

    # 2. Append custom user bindings
    for ub in user_state["binds"]:
        if ub["key"] in matched_user_bind_keys:
            continue
        item = dict(ub)
        item["status"] = "custom"
        item["category"] = "Custom"
        item["default_key"] = ""
        all_active.append(item)

    # 3. Sort Alphabetically by Description (case-insensitive)
    all_active.sort(key=lambda x: (x.get("description", "").lower(), x.get("key", "")))

    # 4. Detect Real Key Conflicts
    conflicts_dict = {}
    key_groups = {}
    for b in all_active:
        if b.get("status") == "disabled" or b.get("is_mouse") or b.get("is_release"):
            continue
        k = b["key"]
        if k not in key_groups:
            key_groups[k] = []
        key_groups[k].append(b)

    for k, group in key_groups.items():
        sources = {x.get("source") for x in group}
        files = {x.get("file") for x in group}
        is_intentional_composite = (sources == {"default"} and len(files) == 1 and files != {None})

        if len(group) > 1 and not is_intentional_composite:
            for b in group:
                b["is_conflict"] = True
                b["conflict_with"] = [other["description"] for other in group if other != b]
            conflicts_dict[k] = {
                "key": k,
                "bindings": [
                    {"description": x["description"], "action": x["action"], "status": x.get("status", "unknown")}
                    for x in group
                ],
            }
        else:
            for b in group:
                b["is_conflict"] = False

    conflicts = list(conflicts_dict.values())

    # 5. Available Catalog (sorted alphabetically by name)
    bound_descriptions = {b["description"].lower() for b in all_active if b.get("status") != "disabled"}
    catalog = []

    for preset in PRESET_CATALOG:
        is_bound = preset["name"].lower() in bound_descriptions or preset["description"].lower() in bound_descriptions
        curr_key = ""
        for b in all_active:
            if b.get("status") != "disabled" and (b["description"].lower() == preset["name"].lower() or b["description"].lower() == preset["description"].lower()):
                curr_key = b["key"]
                break

        cat_item = dict(preset)
        cat_item["is_bound"] = is_bound
        cat_item["current_key"] = curr_key
        catalog.append(cat_item)

    catalog.sort(key=lambda x: x.get("name", "").lower())

    total_modified = len([b for b in all_active if b.get("status") in ("modified", "custom")])

    return {
        "active": all_active,
        "catalog": catalog,
        "conflicts": conflicts,
        "apps": get_installed_apps(),
        "total_active": len([b for b in all_active if b.get("status") != "disabled"]),
        "total_modified": total_modified,
        "total_conflicts": len(conflicts),
        "mouse_settings": get_mouse_scroll_settings(),
        "user_bindings_file": USER_BINDINGS_PATH,
    }


def _written_bind_identity(entry):
    """Return the (description, action) pair a single o.bind line for ``entry``
    will store on disk. Scoping key removal to this exact pair lets a rebind
    drop only its own previous line, never a *different* binding that shares
    the old key (e.g. the pair it is being swapped with)."""
    desc = sanitize_lua_str(entry.get("description", ""))
    cmd = entry.get("command", "")
    action = entry.get("action", "")
    if action and (action.strip().startswith("{") or action.strip().startswith("hl.")):
        return desc, action.strip()[:MAX_CMD_LEN]
    if cmd and (cmd.strip().startswith("{") or cmd.strip().startswith("hl.")):
        return desc, cmd.strip()[:MAX_CMD_LEN]
    raw_cmd = (cmd or action).strip()
    if (raw_cmd.startswith('"') and raw_cmd.endswith('"')) or (raw_cmd.startswith("'") and raw_cmd.endswith("'")):
        raw_cmd = raw_cmd[1:-1]
    return desc, f'"{sanitize_lua_str(raw_cmd)}"'


def write_user_bindings(lines_to_add=None, unbinds_to_add=None, keys_to_remove=None, preserve_entry=None):
    """
    Safely modify ~/.config/hypr/bindings.lua.
    Preserves comments and mouse bindings, ensures clean formatted sections.

    ``preserve_entry``: when set, an existing o.bind/o.bind_toggle whose key is
    in ``keys_to_remove`` is only removed if it is *the same binding* (matching
    description OR action, mirroring build_complete_model's identity convention)
    as this entry. Any other binding sharing the key is left intact (needed for
    swaps, where one binding's old key is another binding's freshly written key,
    and for in-place action edits, where the description is unchanged).
    """
    cfg_dir = os.path.dirname(USER_BINDINGS_PATH)
    preserve_identity = _written_bind_identity(preserve_entry) if preserve_entry else None
    os.makedirs(cfg_dir, exist_ok=True)

    existing_content = ""
    # Open the source directly (no isfile() pre-check -> no TOCTOU window in
    # which a FIFO could be swapped in to block the open indefinitely).
    try:
        src_fd = os.open(USER_BINDINGS_PATH, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        src_fd = None
    except OSError:
        raise ValueError(f"refusing to operate on non-regular/symlink path: {USER_BINDINGS_PATH}")
    if src_fd is not None:
        try:
            if not stat.S_ISREG(os.fstat(src_fd).st_mode):
                os.close(src_fd)
                raise ValueError(f"refusing non-regular file: {USER_BINDINGS_PATH}")
            with os.fdopen(src_fd, "r", encoding="utf-8") as f:
                existing_content = f.read(MAX_FILE_BYTES)
        except BaseException:
            raise
        backup_path = USER_BINDINGS_PATH + ".bak"
        # Write the backup through a unique temp file and atomically replace,
        # instead of O_TRUNC on the predictable .bak path. This avoids
        # truncating an existing inode, so a planted hard link / symlink at
        # that path cannot destroy another file's contents.
        bak_fd, bak_tmp = tempfile.mkstemp(dir=cfg_dir, prefix=".bindings_", suffix=".bak.tmp")
        try:
            with os.fdopen(bak_fd, "w", encoding="utf-8") as bak_f:
                bak_f.write(existing_content)
                bak_f.flush()
                os.fsync(bak_f.fileno())
            os.replace(bak_tmp, backup_path)
        except BaseException:
            if os.path.exists(bak_tmp):
                try:
                    os.unlink(bak_tmp)
                except OSError:
                    pass
            raise

    current_lines = existing_content.splitlines() if existing_content else []

    if not current_lines:
        current_lines = [
            "-- Personal keybinding overrides for Omarchy Hyprland",
            "-- Managed graphically by Omarchy Keybindings Plugin",
            "",
        ]

    clean_keys = set(normalize_key_chord(k) for k in (keys_to_remove or []))

    new_lines = []

    for line in current_lines:
        stripped = line.strip()

        if stripped.startswith("--"):
            new_lines.append(line)
            continue

        is_targeted = False
        if clean_keys:
            m_unbind = re.match(r'hl\.unbind\s*\(\s*["\']([^"\']+)["\']\s*\)', stripped)
            if m_unbind and normalize_key_chord(m_unbind.group(1)) in clean_keys:
                is_targeted = True

            m_bind = re.match(r'o\.bind(?:_toggle)?\s*\(\s*["\']([^"\']+)["\']', stripped)
            if m_bind and normalize_key_chord(m_bind.group(1)) in clean_keys:
                if preserve_identity is not None:
                    _parsed = _parse_bind_line(stripped)
                    if _parsed:
                        norm_parsed_action = (_parsed[2] or "").strip().strip('"\'')
                        norm_pres_action = preserve_identity[1].strip().strip('"\'')
                        if ((_parsed[1] or "").strip().lower() == preserve_identity[0].lower()
                                or norm_parsed_action == norm_pres_action
                                or normalize_key_chord(m_bind.group(1)) in [normalize_key_chord(e.get("key", "")) for e in (lines_to_add or [])]):
                            is_targeted = True
                    # Else: a different binding sharing this key (swap partner) -> keep.
                else:
                    is_targeted = True

        if not is_targeted:
            new_lines.append(line)

    if unbinds_to_add:
        for k in unbinds_to_add:
            norm_k = normalize_key_chord(k)
            line_str = f'hl.unbind("{norm_k}")'
            if line_str not in new_lines:
                new_lines.append(line_str)

    if lines_to_add:
        for entry in lines_to_add:
            key = normalize_key_chord(entry.get("key", ""))
            desc = sanitize_lua_str(entry.get("description", ""))
            cmd = entry.get("command", "")
            action = entry.get("action", "")

            if action and (action.strip().startswith("{") or action.strip().startswith("hl.")):
                new_lines.append(f'o.bind("{key}", "{desc}", {action.strip()[:MAX_CMD_LEN]})')
            elif cmd and (cmd.strip().startswith("{") or cmd.strip().startswith("hl.")):
                new_lines.append(f'o.bind("{key}", "{desc}", {cmd.strip()[:MAX_CMD_LEN]})')
            else:
                raw_cmd = (cmd or action).strip()
                if (raw_cmd.startswith('"') and raw_cmd.endswith('"')) or (raw_cmd.startswith("'") and raw_cmd.endswith("'")):
                    raw_cmd = raw_cmd[1:-1]
                escaped_cmd = sanitize_lua_str(raw_cmd, max_len=MAX_CMD_LEN)
                new_lines.append(f'o.bind("{key}", "{desc}", "{escaped_cmd}")')


    final_output = []
    prev_blank = False
    for line in new_lines:
        if not line.strip():
            if not prev_blank:
                final_output.append("")
                prev_blank = True
        else:
            final_output.append(line)
            prev_blank = False

    fd, temp_file = tempfile.mkstemp(dir=cfg_dir, prefix=".bindings_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(final_output) + "\n")
            f.flush()
            os.fsync(f.fileno())
        if os.path.islink(temp_file):
            os.unlink(temp_file)
            raise ValueError("temp write path is a symlink")
        os.replace(temp_file, USER_BINDINGS_PATH)
    except BaseException:
        if os.path.exists(temp_file):
            try:
                os.unlink(temp_file)
            except BaseException:
                pass
        raise


def reload_hyprland():
    """Reload Hyprland configuration and check for errors."""
    try:
        res = subprocess.run(["hyprctl", "reload"], capture_output=True, text=True, timeout=10)
        err_res = subprocess.run(["hyprctl", "configerrors"], capture_output=True, text=True, timeout=10)
        return {
            "success": res.returncode == 0,
            "reload_output": res.stdout.strip(),
            "config_errors": err_res.stdout.strip() if err_res.returncode == 0 else "",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def set_keybinding(key: str, description: str, command: str, action: str = "", old_key: str = "", override_conflicts: bool = True):
    """Set, modify, or replace a keybinding."""
    norm_key = normalize_key_chord(key)
    norm_old_key = normalize_key_chord(old_key) if old_key else ""

    if not norm_key:
        return {"success": False, "error": "Invalid key combination"}

    default_binds = parse_default_bindings()
    keys_to_clean = [norm_key]
    unbinds_to_add = []

    if norm_old_key and norm_old_key != norm_key:
        keys_to_clean.append(norm_old_key)
        if any(d["key"] == norm_old_key for d in default_binds):
            unbinds_to_add.append(norm_old_key)

    if any(d["key"] == norm_key for d in default_binds) or override_conflicts:
        unbinds_to_add.append(norm_key)

    binds = [{
        "key": norm_key,
        "description": description or command,
        "command": command,
        "action": action,
    }]

    write_user_bindings(lines_to_add=binds, unbinds_to_add=unbinds_to_add, keys_to_remove=keys_to_clean,
                        preserve_entry=binds[0])
    reload_status = reload_hyprland()

    return {
        "success": reload_status.get("success", False),
        "key": norm_key,
        "old_key": norm_old_key,
        "description": description,
        "reload": reload_status,
    }


def reset_keybinding(key: str, default_key: str = ""):
    """Reset a keybinding back to default Omarchy configuration."""
    norm_key = normalize_key_chord(key)
    norm_def = normalize_key_chord(default_key) if default_key else ""

    keys_to_remove = [norm_key]
    if norm_def and norm_def != norm_key:
        keys_to_remove.append(norm_def)

    default_binds = parse_default_bindings()
    user_state = parse_user_bindings()

    for ub in user_state["binds"]:
        if ub["key"] == norm_key:
            matched_default = next(
                (d for d in default_binds if d["description"].lower() == ub["description"].lower() or d["action"] == ub["action"]),
                None
            )
            if matched_default and matched_default["key"] not in keys_to_remove:
                keys_to_remove.append(matched_default["key"])

    write_user_bindings(keys_to_remove=keys_to_remove)
    reload_status = reload_hyprland()
    return {"success": reload_status.get("success", False), "key": norm_key, "reload": reload_status}


def disable_keybinding(key: str):
    """Disable/unbind a keybinding."""
    norm_key = normalize_key_chord(key)
    write_user_bindings(unbinds_to_add=[norm_key], keys_to_remove=[norm_key])
    reload_status = reload_hyprland()
    return {"success": reload_status.get("success", False), "key": norm_key, "reload": reload_status}


def enable_keybinding(key: str):
    """Re-enable a previously disabled default keybinding by removing its unbind."""
    norm_key = normalize_key_chord(key)
    write_user_bindings(keys_to_remove=[norm_key])
    reload_status = reload_hyprland()
    return {"success": reload_status.get("success", False), "key": norm_key, "reload": reload_status}


def _strip_jsonc_comments(text: str) -> str:
    """Remove JSONC (JSON with Comments) style comments."""
    text = _JSONC_BLOCK_COMMENT_RE.sub('', text)
    lines = []
    for line in text.splitlines():
        lines.append(_JSONC_LINE_COMMENT_RE.sub('', line))
    return '\n'.join(lines)


def _safe_read_regular_file(path: str):
    """Open path with O_NOFOLLOW | O_NONBLOCK, verify S_ISREG, return contents or None."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError:
        raise ValueError(f"refusing to operate on non-regular/symlink path: {path}")
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"refusing non-regular file: {path}")
        with os.fdopen(fd, "r", encoding="utf-8") as f:
            return f.read(MAX_FILE_BYTES)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def _safe_atomic_write_file(path: str, content: str):
    """Write content to path atomically via a temp file in the same directory."""
    dir_name = os.path.dirname(path) or "."
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dir_name, prefix=".menu_", suffix=".jsonc.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


def check_menu_entry():
    """Return whether the launcher menu entry already exists."""
    contents = _safe_read_regular_file(MENU_CONFIG_PATH)
    if contents is None:
        return {"exists": False}
    try:
        stripped = _strip_jsonc_comments(contents)
        data = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        data = {}
    return {"exists": MENU_ENTRY_KEY in data}


def install_menu_entry():
    """Safely add the launcher menu entry without destroying existing entries."""
    contents = _safe_read_regular_file(MENU_CONFIG_PATH)
    data = {}
    if contents is not None:
        try:
            stripped = _strip_jsonc_comments(contents)
            data = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            data = {}
    if MENU_ENTRY_KEY in data:
        return {"success": True, "message": "Menu entry already exists."}
    data[MENU_ENTRY_KEY] = dict(MENU_ENTRY_VALUE)
    out = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    _safe_atomic_write_file(MENU_CONFIG_PATH, out)
    return {"success": True, "message": "Added Keybindings Manager to launcher."}


def remove_menu_entry():
    """Safely remove the launcher menu entry if present."""
    contents = _safe_read_regular_file(MENU_CONFIG_PATH)
    if contents is None:
        return {"success": True, "message": "Config file not found — nothing to remove."}
    try:
        stripped = _strip_jsonc_comments(contents)
        data = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        data = {}
    if MENU_ENTRY_KEY not in data:
        return {"success": True, "message": "Menu entry not present — nothing to remove."}
    del data[MENU_ENTRY_KEY]
    out = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    _safe_atomic_write_file(MENU_CONFIG_PATH, out)
    return {"success": True, "message": "Removed Keybindings Manager from launcher."}


def main():
    if len(sys.argv) < 2:
        print(json.dumps(build_complete_model(), indent=2))
        return

    cmd = sys.argv[1]

    if cmd == "list":
        print(json.dumps(build_complete_model()))

    elif cmd == "set":
        if len(sys.argv) < 5:
            print(json.dumps({"success": False, "error": "Usage: set <key> <desc> <command> [action] [old_key]"}))
            sys.exit(1)
        key = sys.argv[2]
        desc = sys.argv[3]
        command = sys.argv[4]
        action = sys.argv[5] if len(sys.argv) > 5 else ""
        old_key = sys.argv[6] if len(sys.argv) > 6 else ""
        res = set_keybinding(key, desc, command, action, old_key)
        print(json.dumps(res))

    elif cmd == "reset":
        if len(sys.argv) < 3:
            print(json.dumps({"success": False, "error": "Usage: reset <key> [default_key]"}))
            sys.exit(1)
        key = sys.argv[2]
        def_key = sys.argv[3] if len(sys.argv) > 3 else ""
        res = reset_keybinding(key, def_key)
        print(json.dumps(res))

    elif cmd == "disable":
        if len(sys.argv) < 3:
            print(json.dumps({"success": False, "error": "Usage: disable <key>"}))
            sys.exit(1)
        key = sys.argv[2]
        res = disable_keybinding(key)
        print(json.dumps(res))

    elif cmd == "enable":
        if len(sys.argv) < 3:
            print(json.dumps({"success": False, "error": "Usage: enable <key>"}))
            sys.exit(1)
        key = sys.argv[2]
        res = enable_keybinding(key)
        print(json.dumps(res))

    elif cmd == "reload":
        res = reload_hyprland()
        print(json.dumps(res))

    elif cmd == "menu-check":
        res = check_menu_entry()
        print(json.dumps(res))

    elif cmd == "menu-install":
        res = install_menu_entry()
        print(json.dumps(res))

    elif cmd == "menu-remove":
        res = remove_menu_entry()
        print(json.dumps(res))

    elif cmd == "apps":
        print(json.dumps(get_installed_apps()))

    else:
        print(json.dumps({"success": False, "error": f"Unknown command {cmd}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
