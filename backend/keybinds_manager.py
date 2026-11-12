#!/usr/bin/env python3
"""
Keybindings Manager Backend for Omarchy Hyprland
Provides parsing, catalog discovery, conflict detection, mouse scroll integration, and safe Lua config manipulation.
"""

import os
import errno
import sys
import re
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
import tempfile
import stat
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
MAX_APP_FILES = 1_024  # ceiling on enumerated .desktop entries
MAX_APP_TOTAL_READ = 4_194_304  # 4 MiB cumulative cap across the application dirs

MENU_CONFIG_PATH = os.path.expanduser("~/.config/omarchy/extensions/omarchy-menu.jsonc")
MENU_ENTRY_KEY = "setup.keybindings.gui"
MENU_ENTRY_VALUE = {
    "icon": "\uf464",
    "label": "Keybindings Manager",
    "action": "omarchy-shell shell summon meviusisback.keybinds",
}

def sanitize_lua_str(s: str, max_len: int = MAX_TEXT_LEN) -> str:
    """Escape a string for safe embedding inside a Lua double-quoted literal."""
    s = (s or "").replace("\r", " ").replace("\n", " ")
    if len(s) > max_len:
        s = s[:max_len]
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _lua_single_expression_problem(expr: str) -> str:
    """Return why ``expr`` cannot be one Lua expression, or "" when it can.

    A raw dispatcher is written between ``o.bind("<key>", "<desc>", `` and the
    closing ``)``, so anything that ends the statement early, opens a second
    one, or leaves a bracket or string open makes the whole bindings.lua fail to
    compile - and Hyprland then drops every user override in the file. The check
    therefore rejects comments (a trailing ``--`` comments out the closing paren),
    a trailing escape, a top-level ``;`` or ``,`` (a second expression smuggled in
    as o.bind's fourth argument is evaluated at load time), and requires the
    outermost call or table to close exactly at the end of the text.
    """
    if "\n" in expr or "\r" in expr:
        return "newlines are not allowed in a raw dispatcher"
    if len(expr) > MAX_CMD_LEN:
        return f"a raw dispatcher may not exceed {MAX_CMD_LEN} characters"
    closers = {")": "(", "]": "[", "}": "{"}
    depth = []
    quote = None
    long_close = None
    opened_any = False
    i, n = 0, len(expr)
    while i < n:
        ch = expr[i]
        if long_close is not None:
            if expr.startswith(long_close, i):
                i += len(long_close)
                long_close = None
                continue
            i += 1
            continue
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch == "-" and expr.startswith("--", i):
            return "comments are not allowed in a raw dispatcher"
        if ch == "[":
            # Lua long-bracket string: [[ ... ]] or [==[ ... ]==]
            j = i + 1
            level = 0
            while j < n and expr[j] == "=":
                level += 1
                j += 1
            if j < n and expr[j] == "[":
                long_close = "]" + "=" * level + "]"
                i = j + 1
                continue
        if ch in ("'", '"'):
            quote = ch
        elif ch in "([{":
            if not depth:
                opened_any = True
            depth.append(ch)
        elif ch in ")]}":
            if not depth or depth[-1] != closers[ch]:
                return f"unbalanced brackets: unexpected {ch!r}"
            depth.pop()
            if not depth and i != n - 1:
                return "a second expression follows the closed call"
        elif ch in ",;" and not depth:
            return f"a second expression ({ch!r}) is not allowed"
        elif ch == "\\":
            return "a trailing escape is not allowed"
        i += 1
    if quote:
        return "unterminated string literal"
    if long_close:
        return "unterminated long string"
    if depth:
        return f"unbalanced brackets: unclosed {depth[-1]!r}"
    if not opened_any:
        return "a raw dispatcher must be a call or a table"
    return ""


def _is_raw_dispatcher(action: str) -> bool:
    """True for the two shapes Omarchy stores as code, not as a command string.

    ``hl.*`` / ``{...}`` are the plain dispatchers; ``o.<name>(...)`` covers the
    shortcut helpers Omarchy's own defaults use (``o.launch_terminal()``), which
    must stay code or editing such a binding would replace the call with a
    literal command string and silently break the shortcut.
    """
    stripped = (action or "").strip()
    return (stripped.startswith("{") or stripped.startswith("hl.")
            or re.match(r"^o\.[A-Za-z_][A-Za-z0-9_.]*\s*\(", stripped) is not None)


_BIND_HEAD_RE = re.compile(r"o\.bind(?:_toggle)?\s*\(")


def _read_lua_string(text: str, pos: int):
    """Read one Lua string literal (or ``nil``) starting at ``pos``.

    Returns ``(value, next_pos, ok)`` and decodes escapes, so a description the
    plugin wrote with ``\\"`` comes back as the user's original text. The old
    ``[^"']+`` header regex stopped at any quote and made every such binding
    invisible to the plugin.
    """
    while pos < len(text) and text[pos].isspace():
        pos += 1
    if text.startswith("nil", pos):
        return None, pos + 3, True
    if pos >= len(text) or text[pos] not in "\"'":
        return None, pos, False
    quote = text[pos]
    pos += 1
    out = []
    while pos < len(text):
        ch = text[pos]
        if ch == "\\" and pos + 1 < len(text):
            out.append(text[pos + 1])
            pos += 2
            continue
        if ch == quote:
            return "".join(out), pos + 1, True
        out.append(ch)
        pos += 1
    return None, pos, False  # unterminated literal


def _parse_bind_line(line: str):
    """Extract (raw_key, desc, action, opts) from an o.bind() or o.bind_toggle() line.

    Uses parenthesis/brace depth tracking instead of a single regex to
    correctly handle actions containing nested parentheses and curly
    braces (e.g. ``hl.dsp.window.move({ direction = "left" })``).
    """
    m = _BIND_HEAD_RE.match(line)
    if not m:
        return None

    pos = m.end()
    key_value, pos, ok = _read_lua_string(line, pos)
    if not ok or key_value is None:
        return None
    while pos < len(line) and line[pos].isspace():
        pos += 1
    if pos >= len(line) or line[pos] != ",":
        return None
    pos += 1
    desc_value, pos, ok = _read_lua_string(line, pos)
    if not ok:
        return None
    while pos < len(line) and line[pos].isspace():
        pos += 1
    if pos >= len(line) or line[pos] != ",":
        return None
    pos += 1
    while pos < len(line) and line[pos].isspace():
        pos += 1

    raw_key = key_value.strip()
    desc = (desc_value or "").strip()

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

        # Long strings ([[...]], [==[...]==]) may contain parens, braces, commas
        # and quotes; skip their body so they cannot unbalance the scan.
        m_long = re.match(r"\[(=*)\[", line[pos:]) if ch == "[" else None
        if m_long:
            close = "]" + m_long.group(1) + "]"
            pos += m_long.end()
            while pos < len(line) and not line.startswith(close, pos):
                pos += 1
            pos += len(close) if pos < len(line) else 0
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


def _lua_units(lines):
    """Group physical lines into logical Lua units.

    Returns ``[(physical_lines, text, is_comment)]``. ``text`` is the unit with
    comments removed and newlines flattened to spaces (string contents are
    preserved), so callers match statements without being fooled by ``--[[ ]]``
    blocks or trailing ``--`` comments. Lines are grouped while a
    paren/brace/bracket or a string is still open, so a hand-formatted
    multi-line ``o.bind`` is one unit and is replaced or removed as a whole
    instead of leaving a dangling continuation line behind.
    """
    units = []
    long_close = None   # terminator of an open long bracket (comment or string)
    is_long_str = False  # True: long string (code), False: long comment
    in_string = None    # quote character of an open short string
    escape = False
    depth = 0
    raw = []
    code = []
    has_code = False

    def flush():
        nonlocal raw, code, has_code
        text = re.sub(r"[ \t]*\n[ \t]*", " ", "".join(code)).strip()
        units.append((raw, text, not has_code))
        raw, code, has_code = [], [], False

    for line in lines:
        raw.append(line)
        i = 0
        while i < len(line):
            ch = line[i]
            if long_close is not None:
                if line.startswith(long_close, i):
                    if is_long_str:
                        # Keep the closing delimiter: dropping it turned a
                        # long-string action into unquoted text on reparse.
                        code.append(long_close)
                    i += len(long_close)
                    long_close = None
                    is_long_str = False
                else:
                    if is_long_str:
                        code.append(ch)
                    i += 1
                continue
            if in_string is not None:
                code.append(ch)
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == in_string:
                    in_string = None
                i += 1
                continue
            if ch == "-" and line.startswith("--", i):
                m = re.match(r"--\[(=*)\[", line[i:])
                if m:
                    long_close = "]" + m.group(1) + "]"
                    is_long_str = False
                    i += m.end()
                else:
                    break  # line comment: the rest of the physical line is comment
                continue
            m = re.match(r"\[(=*)\[", line[i:]) if ch == "[" else None
            if m:
                code.append(line[i:i + m.end()])  # keep [[ / [==[ as source
                long_close = "]" + m.group(1) + "]"
                is_long_str = True
                has_code = True
                i += m.end()
                continue
            code.append(ch)
            if not ch.isspace():
                has_code = True
            if ch in "\"'":
                in_string = ch
                escape = False
            elif ch in "({[":
                depth += 1
            elif ch in ")}]":
                depth = max(0, depth - 1)
            i += 1
        code.append(
            "\n" if depth or in_string is not None or long_close is not None else " ")
        # A trailing backslash escapes the newline it ends, not the first
        # character of the next physical line.
        escape = False
        if not depth and in_string is None and long_close is None:
            flush()
    if raw:
        flush()
    return units


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


def _read_bounded_regular_file(path: str, max_bytes: int = MAX_FILE_BYTES):
    """Read at most ``max_bytes`` from ``path``, never blocking on a FIFO.

    Unlike the write path this follows symlinks on purpose (dotfile-managed
    configs and packaged ``.desktop`` links must stay readable), but O_NONBLOCK
    plus the S_ISREG fstat close the FIFO-blocking and device-target holes. The
    cap is byte-exact: an oversized file reads ``max_bytes + 1`` bytes and is
    refused, so a caller can never act on a silently truncated read.
    Returns None for missing, non-regular or oversized paths.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return None
    f = None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        f = os.fdopen(fd, "rb")
        raw = f.read(max_bytes + 1)
    finally:
        if f is not None:
            f.close()
        else:
            try:
                os.close(fd)
            except OSError:
                pass
    if len(raw) > max_bytes:
        return None
    return raw.decode("utf-8")


def get_installed_apps(dirs=None):
    """Scan standard XDG application directories for launchable applications.
    Returns a sorted list of unique applications: [{'name': ..., 'exec': ..., 'icon': ..., 'comment': ..., 'terminal': bool}]

    Every entry is read through the bounded reader (size cap, no FIFO block) and
    the scan itself is capped, so a planted or corrupt ``.desktop`` cannot turn
    the panel's model build into an unbounded read.
    """
    global _INSTALLED_APPS_CACHE
    if dirs is None and _INSTALLED_APPS_CACHE is not None:
        return _INSTALLED_APPS_CACHE

    apps = []
    seen = set()
    dirs_is_default = dirs is None
    if dirs is None:
        dirs = [
            os.path.expanduser("~/.local/share/applications"),
            "/usr/share/applications",
            "/usr/local/share/applications",
        ]

    file_count = 0
    total_read = 0
    for app_dir in dirs:
        if not os.path.isdir(app_dir):
            continue
        # scandir stops at MAX_APP_FILES: glob would materialise (and sort) the
        # whole directory before either cap was consulted.
        try:
            with os.scandir(app_dir) as entries:
                names = []
                for entry in entries:
                    if entry.name.endswith(".desktop"):
                        names.append(entry.path)
                        if len(names) >= MAX_APP_FILES:
                            break
        except OSError:
            continue
        for desktop_file in sorted(names):
            if total_read > MAX_APP_TOTAL_READ:
                break
            file_count += 1
            try:
                contents = _read_bounded_regular_file(desktop_file)
            except (OSError, UnicodeDecodeError):
                continue
            if contents is None:
                continue
            total_read += len(contents.encode("utf-8"))
            cp = configparser.RawConfigParser()
            try:
                cp.read_string(contents)
                if not cp.has_section("Desktop Entry"):
                    continue
                entry = cp["Desktop Entry"]
                if entry.get("Type", "Application") != "Application":
                    continue
                if entry.get("NoDisplay", "false").lower() == "true":
                    continue
                if entry.get("Hidden", "false").lower() == "true":
                    continue

                # Cap every field that reaches the panel: StdioCollector has no
                # byte ceiling, so a hostile .desktop could otherwise push a
                # multi-megabyte payload that the GUI thread must parse.
                name = entry.get("Name", "").strip()[:MAX_TEXT_LEN]
                exec_cmd = entry.get("Exec", "").strip()[:MAX_CMD_LEN]
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
                    "icon": entry.get("Icon", "").strip()[:MAX_TEXT_LEN],
                    "comment": entry.get("Comment", "").strip()[:MAX_TEXT_LEN],
                    "terminal": entry.get("Terminal", "false").lower() == "true",
                })
            except Exception:
                continue

    apps.sort(key=lambda x: x["name"].lower())
    if dirs_is_default:
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

    Reads through the bounded, non-blocking reader (byte-exact 64 KiB cap, no
    FIFO block, symlinks followed for dotfile-managed configs). Parsing works on
    logical Lua units, so commented-out text inside ``--[[ ]]`` blocks is never
    mistaken for a live unbind/bind.
    """
    result = {
        "raw_content": "",
        "unbinds": set(),
        "binds": [],
        "mouse_binds": [],
    }

    try:
        content = _read_bounded_regular_file(USER_BINDINGS_PATH)
    except (OSError, UnicodeDecodeError):
        return result
    if content is None:
        return result
    result["raw_content"] = content

    for _lines, text, is_comment in _lua_units(_split_lines(content)[0]):
        if is_comment or not text:
            continue

        m_unbind = re.match(r'hl\.unbind\s*\(\s*["\']([^"\']+)["\']\s*\)', text)
        if m_unbind:
            raw_key = m_unbind.group(1).strip()
            result["unbinds"].add(normalize_key_chord(raw_key))
            continue

        parsed = _parse_bind_line(text)
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


def _decode_lua_string(text: str) -> str:
    """Decode the escapes ``sanitize_lua_str`` adds (``\\\\`` and ``\\"``).

    Only those two are decoded: they are the ones the writer produces, and a
    round-trip must be byte-stable. Other Lua escapes (``\\n``) are left as
    written so nothing invents a newline the file did not have.
    """
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text) and text[i + 1] in '\\"':
            out.append(text[i + 1])
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _split_lines(content: str):
    """Split bindings.lua on LF only, preserving other separators verbatim.

    ``str.splitlines()`` also splits on CR, form feed, U+0085/U+2028/U+2029;
    rejoining with LF silently rewrote those bytes and could break a literal
    that contained one. Returns ``(lines, newline)`` with CRLF detected so a
    rewrite keeps the file's convention.
    """
    if not content:
        return [], "\n"
    newline = "\r\n" if "\r\n" in content else "\n"
    lines = [line[:-1] if line.endswith("\r") else line for line in content.split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    return lines, newline


def _written_bind_identity(entry):
    """Return the (description, action) pair a single o.bind line for ``entry``
    will store on disk. Scoping key removal to this exact pair lets a rebind
    drop only its own previous line, never a *different* binding that shares
    the old key (e.g. the pair it is being swapped with)."""
    # Decoded (flattened, length-capped) description: this is what a parsed
    # line yields, so identity comparisons stay on the same side of the
    # escaping the writer applies.
    desc = (entry.get("description") or "").replace("\r", " ").replace("\n", " ")[:MAX_TEXT_LEN]
    cmd = entry.get("command", "")
    action = entry.get("action", "")
    if action and (action.strip().startswith("{") or action.strip().startswith("hl.")):
        return desc, action.strip()[:MAX_CMD_LEN]
    raw_cmd = (cmd or action).strip()
    if (raw_cmd.startswith('"') and raw_cmd.endswith('"')) or (raw_cmd.startswith("'") and raw_cmd.endswith("'")):
        raw_cmd = _decode_lua_string(raw_cmd[1:-1])
    return desc, f'"{sanitize_lua_str(raw_cmd)}"'


def _resolve_bindings_write_path(path: str):
    """Resolve a (possibly symlinked) bindings path to the real target.

    Returns (resolved_path, exists). Follows symlinks so dotfile-managed
    configs keep their link intact, but every read/write happens on the
    resolved regular file. Dangling links and loops become ValueError.
    """
    resolved = os.path.realpath(path)
    try:
        fd = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        if os.path.lexists(path):
            raise ValueError(f"dangling symlink, refusing to edit: {path}")
        return resolved, False
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.ENOTDIR):
            raise ValueError(f"cannot resolve symlink path, refusing to edit: {path}")
        raise ValueError(f"refusing to operate on non-regular path: {path}")
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"refusing non-regular file: {path}")
    finally:
        os.close(fd)
    return resolved, True


def write_user_bindings(lines_to_add=None, unbinds_to_add=None, keys_to_remove=None,
                        preserve_entry=None, previous_entry=None, override_keys=None):
    """
    Safely modify ~/.config/hypr/bindings.lua.
    Preserves comments and mouse bindings, ensures clean formatted sections.

    ``preserve_entry`` / ``previous_entry``: an existing o.bind/o.bind_toggle
    whose key is in ``keys_to_remove`` is removed only if it is *the same
    binding* — its description OR action matches the entry being written
    (``preserve_entry``) or the binding it replaces (``previous_entry``). Any
    other binding sharing the key is left intact: that is what keeps a swap
    partner and a second binding on the same chord alive.

    ``override_keys``: keys whose existing bind lines are removed regardless of
    identity. ``set_keybinding`` uses it for a key that is not being edited in
    place, where binding a new action over an occupied chord is an override.
    """
    cfg_dir = os.path.dirname(USER_BINDINGS_PATH)
    preserve_identity = _written_bind_identity(preserve_entry) if preserve_entry else None
    previous_identity = _written_bind_identity(previous_entry) if previous_entry else None
    identities = [i for i in (previous_identity, preserve_identity) if i is not None]
    overrides = set(normalize_key_chord(k) for k in (override_keys or []))
    os.makedirs(cfg_dir, exist_ok=True)

    resolved_path, exists = _resolve_bindings_write_path(USER_BINDINGS_PATH)
    target_dir = os.path.dirname(resolved_path) or cfg_dir
    os.makedirs(target_dir, exist_ok=True)

    existing_content = ""
    # Open the resolved target directly (no isfile() pre-check -> no TOCTOU
    # window in which a FIFO could be swapped in to block the open).
    if exists:
        try:
            src_fd = os.open(resolved_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            src_fd = None
        except OSError as e:
            if e.errno == errno.ELOOP:
                raise ValueError(f"cannot resolve symlink path, refusing to edit: {USER_BINDINGS_PATH}")
            raise ValueError(f"refusing to operate on non-regular path: {USER_BINDINGS_PATH}")
        if src_fd is not None:
            try:
                if not stat.S_ISREG(os.fstat(src_fd).st_mode):
                    raise ValueError(f"refusing non-regular file: {USER_BINDINGS_PATH}")
                with os.fdopen(src_fd, "rb") as f:
                    raw = f.read(MAX_FILE_BYTES + 1)
                if len(raw) > MAX_FILE_BYTES:
                    # Byte-exact cap like the menu path (round-2 filesystem
                    # review): a text-mode read(n) counts CHARACTERS, so a
                    # >64 KiB file used to be silently truncated here — the tail
                    # lost in BOTH the rewrite and the .bak. Refuse instead of
                    # destroying data.
                    raise ValueError(
                        f"file exceeds the {MAX_FILE_BYTES}-byte cap, refusing to edit: {USER_BINDINGS_PATH}")
                existing_content = raw.decode("utf-8")
            except BaseException:
                try:
                    os.close(src_fd)
                except OSError:
                    pass
                raise
            backup_path = resolved_path + ".bak"
            # Write the backup through a unique temp file and atomically replace,
            # instead of O_TRUNC on the predictable .bak path. This avoids
            # truncating an existing inode, so a planted hard link / symlink at
            # that path cannot destroy another file's contents. The .bak lands
            # beside the resolved target so dotfile-managed links keep working.
            bak_fd, bak_tmp = tempfile.mkstemp(dir=target_dir, prefix=".bindings_", suffix=".bak.tmp")
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

    current_lines, newline = _split_lines(existing_content)

    if not current_lines:
        current_lines = [
            "-- Personal keybinding overrides for Omarchy Hyprland",
            "-- Managed graphically by Omarchy Keybindings Plugin",
            "",
        ]

    clean_keys = set(normalize_key_chord(k) for k in (keys_to_remove or []))

    new_lines = []

    # Work on logical statements, not physical lines: a hand-formatted
    # multi-line o.bind must be replaced or removed as a whole, and text inside
    # a --[[ ]] block is a comment even though its lines do not start with --.
    for unit_lines, text, is_comment in _lua_units(current_lines):
        if is_comment or not text:
            new_lines.extend(unit_lines)
            continue

        is_targeted = False
        if clean_keys:
            m_unbind = re.match(r'hl\.unbind\s*\(\s*["\']([^"\']+)["\']\s*\)', text)
            if m_unbind and normalize_key_chord(m_unbind.group(1)) in clean_keys:
                is_targeted = True

            m_bind = re.match(r'o\.bind(?:_toggle)?\s*\(\s*["\']([^"\']+)["\']', text)
            if m_bind and normalize_key_chord(m_bind.group(1)) in clean_keys:
                bind_key = normalize_key_chord(m_bind.group(1))
                if bind_key in overrides:
                    is_targeted = True
                elif identities:
                    _parsed = _parse_bind_line(text)
                    if _parsed:
                        parsed_desc = (_parsed[1] or "").strip().lower()
                        parsed_action = (_parsed[2] or "").strip().strip('"\'')
                        for ident in identities:
                            # Both halves, not either: a rename that lands on the
                            # sibling's description (or command) used to match
                            # that sibling by the OR and delete it.
                            if (parsed_desc == ident[0].strip().lower()
                                    and parsed_action == ident[1].strip().strip('"\'')):
                                is_targeted = True
                                break
                    # Else: a different binding sharing this key (swap partner,
                    # or a second binding on the same chord) -> keep it.
                else:
                    is_targeted = True

        if not is_targeted:
            new_lines.extend(unit_lines)

    if unbinds_to_add:
        # Place unbinds at the top of the body, not at the end: Hyprland's
        # unbind() removes every bind on the chord registered before it, and the
        # user file is loaded after Omarchy's defaults. At the top it shadows the
        # default while every user binding (a sibling on the same chord included)
        # is registered afterwards and survives.
        unbind_lines = []
        for k in unbinds_to_add:
            norm_k = normalize_key_chord(k)
            line_str = f'hl.unbind("{norm_k}")'
            if line_str not in new_lines and line_str not in unbind_lines:
                unbind_lines.append(line_str)
        # Ask the Lua scanner where the leading comments end. Walking physical
        # lines that start with "--" stops on the SECOND line of a --[[ ]] block
        # and put the unbind inside the comment, where it never ran: the default
        # stayed bound and the write still reported success.
        insert_at = 0
        consumed = 0
        for unit_lines, unit_text, unit_is_comment in _lua_units(new_lines):
            if unit_is_comment or not unit_text:
                consumed += len(unit_lines)
                insert_at = consumed
                continue
            break
        for offset, line_str in enumerate(unbind_lines):
            new_lines.insert(insert_at + offset, line_str)

    if lines_to_add:
        for entry in lines_to_add:
            key = normalize_key_chord(entry.get("key", ""))
            desc = sanitize_lua_str(entry.get("description", ""))
            cmd = entry.get("command", "")
            action = entry.get("action", "")

            # Raw Lua comes ONLY from the explicit dispatcher field, never from
            # `command`: the command can be an untrusted .desktop Exec, and
            # deciding "this is code" from a prefix in that data let `Exec=hl.…`
            # inject statements into the file Hyprland executes.
            if _is_raw_dispatcher(action):
                problem = _lua_single_expression_problem(action.strip())
                if problem:
                    raise ValueError(f"refusing to write the raw dispatcher: {problem}")
                new_lines.append(f'o.bind("{key}", "{desc}", {action.strip()})')
            else:
                raw_cmd = (cmd or action).strip()
                if (raw_cmd.startswith('"') and raw_cmd.endswith('"')) or (raw_cmd.startswith("'") and raw_cmd.endswith("'")):
                    raw_cmd = _decode_lua_string(raw_cmd[1:-1])
                escaped_cmd = sanitize_lua_str(raw_cmd, max_len=MAX_CMD_LEN)
                new_lines.append(f'o.bind("{key}", "{desc}", "{escaped_cmd}")')

    # Fail closed: prove the intended change is really in the output. A file
    # whose statements cannot be isolated (an unbalanced bracket above the
    # binds, a hand-formatted multi-statement line) used to be written back
    # unchanged while the caller reported success.
    def _parsed_binds(lines):
        parsed_binds = []
        for _unit, text, is_comment in _lua_units(lines):
            if is_comment or not text:
                continue
            parsed = _parse_bind_line(text)
            if parsed:
                parsed_binds.append((normalize_key_chord(parsed[0]), parsed))
        return parsed_binds

    written_binds = _parsed_binds(new_lines)
    for entry in (lines_to_add or []):
        want = normalize_key_chord(entry.get("key", ""))
        if want and not any(k == want for k, _ in written_binds):
            raise ValueError(
                "could not isolate the statement to edit in the bindings file "
                "(unbalanced brackets or a hand-formatted bind); nothing was written")
    if previous_identity is not None and previous_identity != preserve_identity:
        for written_key, parsed in written_binds:
            if written_key not in clean_keys:
                continue
            written_desc = (parsed[1] or "").strip().lower()
            written_action = (parsed[2] or "").strip().strip("\"'")
            if (written_desc == previous_identity[0].strip().lower()
                    and written_action == previous_identity[1].strip().strip("\"'")):
                raise ValueError(
                    "could not replace the existing binding "
                    "(its statement cannot be isolated); nothing was written")

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

    fd, temp_file = tempfile.mkstemp(dir=target_dir, prefix=".bindings_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(newline.join(final_output) + newline)
            f.flush()
            os.fsync(f.fileno())
        if os.path.islink(temp_file):
            os.unlink(temp_file)
            raise ValueError("temp write path is a symlink")
        os.replace(temp_file, resolved_path)
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


def set_keybinding(key: str, description: str, command: str, action: str = "", old_key: str = "",
                   override_conflicts: bool = True,
                   old_description: str = "", old_command: str = "", old_action: str = ""):
    """Set, modify, or replace a keybinding.

    ``old_description`` / ``old_command`` / ``old_action`` describe the binding
    being edited in place. They are what identifies the exact line to replace
    when several bindings share the chord (a rename changes both the
    description and the command, so key alone cannot find it).
    """
    norm_key = normalize_key_chord(key)
    norm_old_key = normalize_key_chord(old_key) if old_key else ""

    if not norm_key:
        return {"success": False, "error": "Invalid key combination"}

    # A dispatcher-looking command is almost always a lost argument; writing it
    # as a quoted shell command would silently break the binding. Raw Lua is
    # accepted only from the explicit dispatcher slot.
    if not action.strip() and _is_raw_dispatcher(command.strip()):
        return {
            "success": False,
            "error": "dispatcher-looking command: pass it as the action argument "
                     "(set <key> <desc> <command> <action>)",
        }
    if _is_raw_dispatcher(action):
        problem = _lua_single_expression_problem(action.strip())
        if problem:
            return {"success": False, "error": f"invalid raw Lua dispatcher: {problem}"}

    default_binds = parse_default_bindings()
    keys_to_clean = [norm_key]
    unbinds_to_add = []

    if norm_old_key and norm_old_key != norm_key:
        keys_to_clean.append(norm_old_key)
        if any(d["key"] == norm_old_key for d in default_binds):
            unbinds_to_add.append(norm_old_key)

    # hl.unbind(<chord>) removes every binding on that chord at the Hyprland
    # level, so it is emitted only to shadow an Omarchy default. Emitting it on
    # every save silently killed the sibling binding this code takes care to
    # preserve in the file, which made the panel show bindings that no longer
    # existed.
    if any(d["key"] == norm_key for d in default_binds):
        unbinds_to_add.append(norm_key)

    binds = [{
        "key": norm_key,
        "description": description or command,
        "command": command,
        "action": action,
    }]

    # Editing a binding in place replaces *that* binding only: a second user
    # binding on the same chord must survive. Any other key is an override.
    in_place = bool(norm_old_key) and norm_old_key == norm_key
    previous_entry = None
    if in_place:
        same_key = [b for b in parse_user_bindings()["binds"] if b["key"] == norm_key]
        if old_description or old_command or old_action:
            previous_entry = {
                "description": old_description,
                "command": old_command,
                "action": old_action,
            }
        elif len(same_key) == 1:
            previous_entry = same_key[0]
        elif len(same_key) > 1:
            return {
                "success": False,
                "error": "several bindings share this chord: pass the previous "
                         "description to say which one is being replaced",
            }

    try:
        write_user_bindings(lines_to_add=binds, unbinds_to_add=unbinds_to_add, keys_to_remove=keys_to_clean,
                            preserve_entry=binds[0], previous_entry=previous_entry,
                            override_keys=[] if (in_place or not override_conflicts) else [norm_key])
    except ValueError as e:
        return {"success": False, "error": str(e)}
    reload_status = reload_hyprland()

    return {
        "success": True,
        "key": norm_key,
        "old_key": norm_old_key,
        "description": description,
        "reload": reload_status,
        "reload_success": bool(reload_status.get("success", False)),
    }


def reset_keybinding(key: str, default_key: str = ""):
    """Reset a keybinding back to default Omarchy configuration."""
    norm_key = normalize_key_chord(key)
    norm_def = normalize_key_chord(default_key) if default_key else ""

    if not norm_key:
        return {"success": False, "error": "Invalid key combination"}

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

    try:
        write_user_bindings(keys_to_remove=keys_to_remove)
    except ValueError as e:
        return {"success": False, "error": str(e)}
    reload_status = reload_hyprland()
    # The write is what success means; a failed reload is reported separately so
    # the panel cannot say "not saved" about a change that landed.
    return {"success": True, "key": norm_key, "reload": reload_status,
            "reload_success": bool(reload_status.get("success", False))}


def disable_keybinding(key: str):
    """Disable/unbind a keybinding."""
    norm_key = normalize_key_chord(key)
    if not norm_key:
        return {"success": False, "error": "Invalid key combination"}
    try:
        write_user_bindings(unbinds_to_add=[norm_key], keys_to_remove=[norm_key])
    except ValueError as e:
        return {"success": False, "error": str(e)}
    reload_status = reload_hyprland()
    return {"success": True, "key": norm_key, "reload": reload_status,
            "reload_success": bool(reload_status.get("success", False))}


def enable_keybinding(key: str):
    """Re-enable a previously disabled default keybinding by removing its unbind."""
    norm_key = normalize_key_chord(key)
    if not norm_key:
        return {"success": False, "error": "Invalid key combination"}
    try:
        write_user_bindings(keys_to_remove=[norm_key])
    except ValueError as e:
        return {"success": False, "error": str(e)}
    reload_status = reload_hyprland()
    return {"success": True, "key": norm_key, "reload": reload_status,
            "reload_success": bool(reload_status.get("success", False))}


def _strip_jsonc_comments(text: str) -> str:
    """Remove JSONC comments, never touching the inside of a quoted string.

    Single-pass state machine: `//` and `/*…*/` are only comments in NORMAL
    state, so URLs (https://…), Windows paths (C:\\…) and comment-looking
    text inside string values survive intact. A LINE_COMMENT ends at newline
    OR EOF (a file ending in `// note` is valid JSONC); an unterminated string
    or block comment raises ValueError — malformed input is a parse failure the
    caller must refuse, never "accidentally parseable" output.
    """
    out = []
    i = 0
    n = len(text)
    state = "normal"  # normal | string | line_comment | block_comment
    while i < n:
        c = text[i]
        if state == "normal":
            if c == '"':
                state = "string"
                out.append(c)
            elif c == "/" and i + 1 < n and text[i + 1] == "/":
                state = "line_comment"
                i += 1
            elif c == "/" and i + 1 < n and text[i + 1] == "*":
                state = "block_comment"
                i += 1
            else:
                out.append(c)
        elif state == "string":
            if c == "\\" and i + 1 < n:
                out.append(c)
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                state = "normal"
                out.append(c)
            else:
                out.append(c)
        elif state == "line_comment":
            if c == "\n":
                state = "normal"
                out.append(c)
        else:  # block_comment
            if c == "*" and i + 1 < n and text[i + 1] == "/":
                state = "normal"
                i += 1
        i += 1
    if state == "string":
        raise ValueError("unterminated string in JSONC input")
    if state == "block_comment":
        raise ValueError("unterminated block comment in JSONC input")
    return "".join(out)


def _parse_menu_config(contents: str) -> dict:
    """Strip JSONC comments string-aware and parse as a JSON object.

    Raises ValueError on anything that cannot be parsed with confidence — the
    menu entry writers must refuse instead of treating a parse error as an
    empty object (marketplace finding #13661: that fallback rewrote the file
    with only this plugin's entry, destroying every other launcher entry).
    """
    try:
        stripped = _strip_jsonc_comments(contents)
        data = json.loads(stripped)
    except (json.JSONDecodeError, ValueError) as e:
        raise ValueError(f"cannot parse {MENU_CONFIG_PATH}: {e}")
    if not isinstance(data, dict):
        raise ValueError(
            f"cannot parse {MENU_CONFIG_PATH}: top level is not a JSON object")
    return data


def _menu_refusal(exc: BaseException) -> dict:
    """Failure result that always carries both keys the QML toasts read."""
    text = f"Refusing to touch {MENU_CONFIG_PATH}: {exc}. No changes were made."
    return {"success": False, "message": text, "error": text}


def _safe_read_regular_file(path: str):
    """Open path with O_NOFOLLOW | O_NONBLOCK, verify S_ISREG, return contents or None.

    Fails closed on symlinks and non-regular files. Callers that need to follow
    dotfile symlinks (bindings.lua) must resolve and write to the target path
    explicitly so the link is preserved.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError:
        raise ValueError(f"refusing to operate on non-regular/symlink path: {path}")
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"refusing non-regular file: {path}")
        with os.fdopen(fd, "rb") as f:
            raw = f.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            # Byte-exact: read in BINARY so the cap counts UTF-8 bytes, not
            # decoded characters (a text-mode read(n) returns n CHARACTERS and
            # would accept up to ~260 KiB of 4-byte glyphs — three reviewers
            # flagged this). The +1 byte distinguishes an exact-fit file
            # (accepted) from an oversized one; no read that passes can be
            # truncated, so nothing is ever parsed-and-rewritten half a file.
            raise ValueError(
                f"file exceeds the {MAX_FILE_BYTES}-byte cap, refusing to edit: {path}")
        # UnicodeDecodeError (a ValueError) on non-UTF-8 content propagates to
        # the menu-* wrappers, which refuse without writing.
        return raw.decode("utf-8")
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
    """Return whether the launcher menu entry already exists.

    Always returns JSON: any read/parse problem answers
    `{exists: False, success: False, message/error}` instead of raising
    (an exception here used to leave stdout empty and the QML silently
    skipped the first-run dialog).
    """
    try:
        contents = _safe_read_regular_file(MENU_CONFIG_PATH)
        if contents is None:
            return {"exists": False, "success": True}
        data = _parse_menu_config(contents)
        return {"exists": MENU_ENTRY_KEY in data, "success": True}
    except Exception as e:  # noqa: BLE001 — every menu path must answer JSON
        res = _menu_refusal(e)
        res["exists"] = False
        return res


def install_menu_entry():
    """Safely add the launcher menu entry without destroying existing entries.

    Fail closed: if the existing file cannot be parsed with confidence, or the
    write fails, nothing is written and a refusal is returned — the previous
    parse-error fallback (`data = {}`) rewrote the file with only this entry.
    """
    try:
        contents = _safe_read_regular_file(MENU_CONFIG_PATH)
        data = {} if contents is None else _parse_menu_config(contents)
        if MENU_ENTRY_KEY in data:
            return {"success": True, "message": "Menu entry already exists."}
        data[MENU_ENTRY_KEY] = dict(MENU_ENTRY_VALUE)
        out = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        _safe_atomic_write_file(MENU_CONFIG_PATH, out)
        return {"success": True, "message": "Added Keybindings Manager to launcher."}
    except Exception as e:  # noqa: BLE001 — every menu path must answer JSON
        return _menu_refusal(e)


def remove_menu_entry():
    """Safely remove the launcher menu entry if present.

    Fail closed like install: an unparseable file is refused, never rewritten.
    """
    try:
        contents = _safe_read_regular_file(MENU_CONFIG_PATH)
        if contents is None:
            return {"success": True, "message": "Config file not found — nothing to remove."}
        data = _parse_menu_config(contents)
        if MENU_ENTRY_KEY not in data:
            return {"success": True, "message": "Menu entry not present — nothing to remove."}
        del data[MENU_ENTRY_KEY]
        out = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        _safe_atomic_write_file(MENU_CONFIG_PATH, out)
        return {"success": True, "message": "Removed Keybindings Manager from launcher."}
    except Exception as e:  # noqa: BLE001 — every menu path must answer JSON
        return _menu_refusal(e)


def main():
    if len(sys.argv) < 2:
        print(json.dumps(build_complete_model(), indent=2))
        return

    cmd = sys.argv[1]

    if cmd == "list":
        print(json.dumps(build_complete_model()))

    elif cmd == "set":
        if len(sys.argv) < 5:
            print(json.dumps({"success": False, "error": "Usage: set <key> <desc> <command> [action] [old_key] [old_desc] [old_command] [old_action]"}))
            sys.exit(1)
        key = sys.argv[2]
        desc = sys.argv[3]
        command = sys.argv[4]
        action = sys.argv[5] if len(sys.argv) > 5 else ""
        old_key = sys.argv[6] if len(sys.argv) > 6 else ""
        old_desc = sys.argv[7] if len(sys.argv) > 7 else ""
        old_command = sys.argv[8] if len(sys.argv) > 8 else ""
        old_action = sys.argv[9] if len(sys.argv) > 9 else ""
        try:
            res = set_keybinding(key, desc, command, action, old_key,
                                 old_description=old_desc, old_command=old_command,
                                 old_action=old_action)
        except Exception as e:  # noqa: BLE001 — always emit JSON for the UI
            res = {"success": False, "error": str(e)}
        print(json.dumps(res))

    elif cmd == "reset":
        if len(sys.argv) < 3:
            print(json.dumps({"success": False, "error": "Usage: reset <key> [default_key]"}))
            sys.exit(1)
        key = sys.argv[2]
        def_key = sys.argv[3] if len(sys.argv) > 3 else ""
        try:
            res = reset_keybinding(key, def_key)
        except Exception as e:  # noqa: BLE001 — always emit JSON for the UI
            res = {"success": False, "error": str(e)}
        print(json.dumps(res))

    elif cmd == "disable":
        if len(sys.argv) < 3:
            print(json.dumps({"success": False, "error": "Usage: disable <key>"}))
            sys.exit(1)
        key = sys.argv[2]
        try:
            res = disable_keybinding(key)
        except Exception as e:  # noqa: BLE001 — always emit JSON for the UI
            res = {"success": False, "error": str(e)}
        print(json.dumps(res))

    elif cmd == "enable":
        if len(sys.argv) < 3:
            print(json.dumps({"success": False, "error": "Usage: enable <key>"}))
            sys.exit(1)
        key = sys.argv[2]
        try:
            res = enable_keybinding(key)
        except Exception as e:  # noqa: BLE001 — always emit JSON for the UI
            res = {"success": False, "error": str(e)}
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
