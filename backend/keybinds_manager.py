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

def _trusted_binary(name: str) -> str:
    """An absolute path to ``name``, or "" when no trustworthy one exists.

    The docstring used to promise that a writable PATH entry could not choose the
    binary while returning ``which(name) or name`` - which is exactly PATH order,
    plus a bare name that execvp re-resolves. Anything the plugin executes (hyprctl
    reload, the luac compile gate) goes through here instead.
    """
    found = shutil.which(name)
    if not found or not os.path.isabs(found):
        return ""
    parent = os.path.dirname(found)
    try:
        st = os.stat(parent)
    except OSError:
        return ""
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return ""
    return found


def _hyprctl() -> str:
    """Absolute path to hyprctl, or "" (the caller then reports the reload skipped)."""
    return _trusted_binary("hyprctl")


def _omarchy_root() -> str:
    """The Omarchy install we read defaults from.

    OMARCHY_PATH is honoured only when it is absolute and the bindings directory
    really exists: the environment of whoever spawns the backend must not be able
    to point every default-binding read (whose rows are copied into edits) at an
    arbitrary tree.
    """
    cand = os.environ.get("OMARCHY_PATH", "")
    if cand and os.path.isabs(cand):
        if os.path.isdir(os.path.join(cand, "default", "hypr", "bindings")):
            return cand
    return "/usr/share/omarchy"


OMARCHY_ROOT = _omarchy_root()
DEFAULT_BINDINGS_DIR = OMARCHY_ROOT + "/default/hypr/bindings"
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
    # Drop C0 control bytes: Lua tolerates NUL but any C-string reader of
    # bindings.lua truncates at it, and nothing legitimate needs one.
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", s or "")
    s = s.replace("\r", " ").replace("\n", " ")
    if len(s) > max_len:
        s = s[:max_len]
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _hold_tail_is_shaped(tail: str) -> bool:
    """True when a hold tail is exactly: string, string, table.

    o.bind_hold's 4th-6th arguments are the stop description, the stop command and
    the options. Balance alone accepted ``"stop d", os.execute("id"), { locked =
    true }`` and wrote it verbatim - a call evaluated when Hyprland loads the file.
    """
    text = (tail or "").strip()
    parts = []
    i = 0
    depth = 0
    quote = None
    start = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth < 0:
                return False
        elif ch == "," and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
        i += 1
    parts.append(text[start:].strip())
    if len(parts) != 3 or any(not part for part in parts):
        return False
    lit = [parts[0], parts[1]]
    for part in lit:
        _v, end, ok = _read_lua_string(part, 0)
        if not ok or end != len(part):
            return False
    table = parts[2]
    if not (table.startswith("{") and table.endswith("}") and _lua_ast_balanced(table)):
        return False
    return _table_carries_no_code(table)


def _table_carries_no_code(table: str) -> bool:
    """True when an options table only holds literals.

    A call runs when Hyprland loads the file: `{ locked = true, os.execute("id") }`
    passed the old shape check, and `os.execute"id"` / `os.execute[[id]]` passed the
    round-12 one (no parentheses). Only keys, `=`, `,`, numbers, strings, `true`,
    `false` and `nil` may appear.
    """
    masked = _code_mask(table)
    code = "".join(ch if masked[i] else " " for i, ch in enumerate(table))
    if any(ch in code for ch in "();"):
        return False
    if "--" in code or "//" in code:
        return False
    # A name is a KEY only when it is followed by `=`, `,` or the closing brace. A
    # name followed by a dot, a colon or a literal is a call without parentheses
    # (`os.execute"id"`), which runs at load.
    for m in re.finditer(r"[A-Za-z_][\w]*", code):
        rest = code[m.end():].lstrip()
        if rest[:1] not in ("=", ",", "}"):
            return False
        # The RAW text decides: masking blanks a literal, so `dofile "x"` looked
        # like a key. A string, a long bracket or a table right after the name is
        # a call without parentheses, which runs when Hyprland loads the file.
        raw_rest = table[m.end():].lstrip()
        if raw_rest[:1] in ('"', "'", "[", "{"):
            return False
    return True


def _lua_ast_balanced(expr: str) -> bool:
    """Cheap bracket/quote balance check (the luac gate is the real parser)."""
    depth = 0
    quote = None
    long_close = None
    i = 0
    while i < len(expr):
        ch = expr[i]
        if long_close:
            if expr.startswith(long_close, i):
                i += len(long_close)
                long_close = None
                continue
            i += 1
            continue
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch
        elif ch == "[":
            m = re.match(r"\[(=*)\[", expr[i:])
            if m:
                long_close = "]" + m.group(1) + "]"
                i += len(m.group(0))
                continue
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth < 0:
                return False
        i += 1
    return depth == 0 and quote is None and long_close is None


def _lua_function_problem(expr: str) -> str:
    """Return why ``function ... end`` cannot be one safe Lua value.

    ``function() end) os.execute("id") -- end`` used to pass with a bare
    end-anchored regex: the first ``end`` closed o.bind early, the injected call
    became a top-level statement and the trailing comment hid the orphan paren, so
    loading bindings.lua ran it. The proof is structural and byte-level: the token
    that empties the block stack must be the outermost function's ``end``, and
    nothing may follow it - not even a literal the token stream masks, which would
    still be a second expression and a file that does not parse.
    """
    masked = _code_mask(expr)
    code = "".join(ch if masked[i] else " " for i, ch in enumerate(expr))
    if "--" in code:
        return "comments are not allowed in a raw dispatcher"
    if ";" in code:
        return "a raw dispatcher may not contain ';'"
    brackets = []
    stack = []
    opened = 0
    last = None
    pending_elseif = False
    pending_if = False
    prev_kw = ""
    end_offset = -1
    for m in re.finditer(r"[A-Za-z_]\w*|[()\[\]{}]|.", code):
        tok = m.group(0)
        if not tok.strip():
            continue
        if end_offset >= 0:
            return "a second expression follows the function"
        if tok in (",", "=") and not stack:
            return "a raw dispatcher may not continue after the function"
        if tok in "([{":
            brackets.append(tok)
        elif tok in ")]}":
            want = {")": "(", "]": "[", "}": "{"}[tok]
            if not brackets or brackets.pop() != want:
                return "a raw dispatcher must keep its brackets balanced"
        elif tok == "function":
            opened += 1
            stack.append("function")
        elif tok == "end":
            if not stack:
                return "a raw dispatcher may not close an 'end' it did not open"
            stack.pop()
            if opened and not stack:
                end_offset = m.end()
        elif tok in ("do", "repeat"):
            stack.append(tok)
        elif tok == "until":
            if not stack or stack[-1] != "repeat":
                return "a raw dispatcher may not close an 'until' it did not open"
            stack.pop()
        elif tok == "then":
            # `elseif ... then` closes with the same `end` as its `if`; a `then`
            # with no `if`/`elseif` before it is not Lua at all (`function() then
            # end end` used to pass and needed luac to catch it).
            if not pending_elseif and not pending_if:
                return "a 'then' without an 'if' is not valid Lua"
            if not pending_elseif:
                stack.append(tok)
            pending_elseif = False
            pending_if = False
        if tok == "elseif":
            pending_elseif = True
            pending_if = True
        elif tok == "if":
            pending_elseif = False
            pending_if = True
        if re.match(r"[A-Za-z_]", tok):
            prev_kw = tok
        last = tok
    if end_offset >= 0:
        if expr[end_offset:].strip():
            return "a second expression follows the function"
        return ""
    if brackets:
        return "a raw dispatcher must keep its brackets balanced"
    if opened == 0:
        return "a function action must start with 'function'"
    if stack:
        return "a function action must close with 'end'"
    if last != "end":
        return "a function action must end with 'end'"
    return ""


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
    # A raw dispatcher starts with a call or a table (the documented forms). A
    # leading long-bracket string is consumed as a literal by the scanner, so
    # "[[x]] os.execute(\"id\")" used to close nothing and pass; refuse it.
    if re.match(r"\s*\[=*\[", expr or ""):
        return "a raw dispatcher must start with a call or a table"

    if "\n" in expr or "\r" in expr:
        return "newlines are not allowed in a raw dispatcher"
    # Control bytes cannot appear in Lua source: ESC/BEL/DEL produced text luac
    # rejects, i.e. a bindings.lua Hyprland drops entire, reported as saved.
    bad = [ch for ch in expr if (ord(ch) < 32 and ch not in "\t\v\f") or ord(ch) == 127]
    if bad:
        return "control characters are not allowed in a raw dispatcher"
    if len(expr) > MAX_CMD_LEN:
        return f"a raw dispatcher may not exceed {MAX_CMD_LEN} characters"
    if re.match(r"^function\b", expr):
        return _lua_function_problem(expr)
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


_MODIFIER_NAMES = {"SUPER", "SHIFT", "CTRL", "ALT"}


def _key_is_plausible(norm_key: str) -> bool:
    """True when a normalised chord can actually bind something.

    Hyprland keys are modifiers plus a keysym (``SUPER + RETURN``, ``code:34``,
    ``mouse:272``). Anything else - a leading dash, an empty part, stray
    whitespace - would be written and reported as saved while binding nothing.
    """
    parts = [p.strip() for p in (norm_key or "").split("+")]
    if not parts or any(not p for p in parts):
        return False
    if len(parts) == 1 and parts[0].upper() in _MODIFIER_NAMES:
        # A lone modifier is not a keysym: Hyprland binds nothing, and the panel
        # used to report it saved.
        return False
    for part in parts[:-1]:
        if part.upper() not in _MODIFIER_NAMES:
            return False
    # A keysym may contain spaces and slashes (switch:on:Lid Switch,
    # switch:on:Apple SMC power/lid events, both shipped by Omarchy). Only what can
    # end the Lua literal is refused, plus a leading dash (a chord that binds
    # nothing and would be reported as saved).
    if parts[-1].startswith("-"):
        return False
    return (len(parts[-1]) <= MAX_KEY_LEN
            and not re.search(r"[\"\']|\\\\|[();\x00-\x1f\x7f]", parts[-1]))


def _registers_a_binding(action: str) -> bool:
    """True when the raw action registers another binding in CODE position.

    The anchored check missed a registration inside a table or a function body
    (`{ o.bind(...) }`), which Hyprland evaluates at load time.
    """
    return _bind_head(action or "") is not None


def _is_raw_dispatcher(action: str) -> bool:
    """True for the shapes Omarchy stores as code, not as a command string.

    ``hl.*`` / ``{...}`` are the plain dispatchers; ``o.<name>(...)`` covers the
    shortcut helpers Omarchy's own defaults use (``o.launch_terminal()``), which
    must stay code or editing such a binding would replace the call with a
    literal command string and silently break the shortcut.
    """
    stripped = (action or "").strip()
    if not stripped:
        return False
    if stripped.startswith("{"):
        return True
    # A complete string literal is data, whatever it looks like.
    if _pure_literal(stripped) is not None:
        return False
    # A call with no space before its "(" is code: hl.dsp.*, o.launch_terminal(),
    # but also Omarchy's bare helpers (send_shortcut_once("CTRL", "A")). A shell
    # command like `notify-send (x)` or `sh -c ...` does not match.
    return re.match(r"^[A-Za-z_][A-Za-z0-9_.:]*\(", stripped) is not None


def _code_mask(text: str):
    """Mask of the positions in ``text`` that are Lua code, not string content.

    Detection could not simply search the flattened unit: a bind mentioned inside
    a long string (``[[hl.unbind("SUPER + X")]]``) is data, and an anchored match
    was blind to a bind that follows another statement. Searching the code mask
    gives both: tolerant, and never fooled by a literal.
    """
    mask = [True] * len(text)
    i, n = 0, len(text)
    quote = None
    long_close = None
    while i < n:
        ch = text[i]
        if long_close is not None:
            mask[i] = False
            if text.startswith(long_close, i):
                for k in range(i, min(n, i + len(long_close))):
                    mask[k] = False
                i += len(long_close)
                long_close = None
                continue
            i += 1
            continue
        if quote is not None:
            mask[i] = False
            if ch == "\\":
                if i + 1 < n:
                    mask[i + 1] = False
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            mask[i] = False
            i += 1
            continue
        if ch == "[":
            m = re.match(r"\[(=*)\[", text[i:])
            if m:
                delimit = len(m.group(1))
                for k in range(i, min(n, i + len(m.group(0)))):
                    mask[k] = False
                long_close = "]" + "=" * delimit + "]"
                i += len(m.group(0))
                continue
        i += 1
    return mask


def _search_in_code(pattern: str, text: str):
    """First match of ``pattern`` whose start is Lua code, or None."""
    mask = _code_mask(text)
    for m in re.finditer(pattern, text):
        if m.start() < len(mask) and mask[m.start()]:
            return m
    return None


def _file_mentions_unbind(content: str, norm_key: str) -> bool:
    """True when a unit calls any unbind form for this chord (code, not text).

    ``hl["unbind"]("K")`` is valid Lua that Hyprland honours but the anchored
    parser does not recognise; "enable" must know the key is still disabled rather
    than reporting success.
    """
    for _unit, text, is_comment in _lua_units(_split_lines(content)[0]):
        if is_comment or not text:
            continue
        mask = _code_mask(text)
        # The token may sit inside a literal (`hl["unbind"]`), so test the mask at
        # the CALL PAREN — that is code in every spelling — then read the chord
        # from the call's own window.
        for m in re.finditer(r'unbind["\']?\s*\]?\s*\(', text):
            at = m.end() - 1
            if at < len(mask) and mask[at]:
                window = text[at:at + 200]
                if re.search(r'["\']' + re.escape(norm_key) + r'["\']', window):
                    return True
    return False


def _unit_is_single_statement(text: str) -> bool:
    """True when ``text`` is exactly one bind/unbind call and nothing else.

    A unit that also carries a second statement (``o.bind(...); if x then``)
    cannot be removed line-wise without taking the other statement with it: the
    caller refuses instead of corrupting the file.
    """
    # Same tolerant head as detection: `o .bind(...)` and `o["bind"](...)` are
    # legal Lua, and a strict spelling here refused to edit rows the scanner had
    # already matched (the write then certified success without removing them).
    head = re.match(r"\s*(?:" + _BIND_HEAD_RE.pattern
                    + r"|hl\s*(?:\.\s*unbind|\[\s*[\"\']unbind[\"\']\s*\])\s*\()", text)
    if not head:
        return False
    i = text.index("(", head.start())
    depth = 0
    quote = None
    while i < len(text):
        ch = text[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                rest = text[i + 1:].strip()
                return rest == "" or rest == ";"
        i += 1
    return False


_BIND_HEAD_RE = re.compile(
    r'o\s*\.\s*bind(?:_toggle|_hold)?\s*\(|o\s*\[\s*["\']bind(?:_toggle|_hold)?["\']\s*\]\s*\(')


def _bind_head(text: str):
    """First bind-call head in CODE position, or None."""
    return _search_in_code(_BIND_HEAD_RE.pattern, text or "")


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
    if pos < len(text) and text[pos] == "[":
        m = re.match(r"\[(=*)\[", text[pos:])
        if m:
            close = "]" + "=" * len(m.group(1)) + "]"
            start = pos + len(m.group(0))
            end = text.find(close, start)
            if end < 0:
                return None, len(text), False
            return text[start:end], end + len(close), True
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


def _bind_fn(text: str) -> str:
    """The call the statement uses: o.bind, o.bind_toggle or o.bind_hold."""
    m = _BIND_HEAD_RE.match((text or "").lstrip())
    head = m.group(0) if m else ""
    if "toggle" in head:
        return "o.bind_toggle"
    if "hold" in head:
        return "o.bind_hold"
    return "o.bind"


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
    fn_depth = 0
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

        if line.startswith("function", pos) and not (pos and (line[pos - 1].isalnum() or line[pos - 1] in "_.:")):
            fn_depth += 1
            pos += len("function")
            continue
        if line.startswith("end", pos) and fn_depth and not (pos and (line[pos - 1].isalnum() or line[pos - 1] == "_")):
            nxt = line[pos + 3:pos + 4]
            if not (nxt.isalnum() or nxt == "_"):
                fn_depth -= 1
                pos += 3
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
        elif ch == ',' and depth_paren == 0 and depth_brace == 0 and fn_depth == 0:
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

    for idx, part in enumerate(raw_parts):
        upper = part.upper()
        # "ALT + Alt_R" means Right-Alt on its own (Omarchy ships it for
        # push-to-talk). Folding Alt_R into the ALT modifier turned the chord into
        # the keysym-less "ALT": an edit rewrote the shortcut and 'disable'
        # reported a still-bound key as off.
        if idx == len(raw_parts) - 1 and upper in (
                "SUPER_L", "SUPER_R", "ALT_L", "ALT_R", "SHIFT_L", "SHIFT_R",
                "CONTROL_L", "CONTROL_R"):
            main_key = upper
            continue
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
    """Read the compositor's scroll factor and natural-scroll flag.

    One resolver, one short timeout, and an explicit `available` flag: the old
    version spawned hyprctl twice with a 10 s timeout each (a wedged compositor
    added 20 s to every `list`), and an empty trusted path made argv[0] = "" whose
    failure was swallowed, so the panel showed 1.0/False as if read.
    """
    settings = {"natural_scroll": False, "scroll_factor": 1.0, "available": False}
    hyprctl = _hyprctl()
    if not hyprctl:
        return settings
    for name, key in (("input:scroll_factor", "float"), ("input:natural_scroll", "bool")):
        try:
            res = subprocess.run([hyprctl, "getoption", name, "-j"],
                                 capture_output=True, text=True, timeout=2,
                                 start_new_session=True)
        except Exception:
            continue
        if res.returncode != 0:
            continue
        try:
            data = json.loads(res.stdout)
        except ValueError:
            continue
        if key in data:
            settings["scroll_factor" if key == "float" else "natural_scroll"] = (
                float(data["float"]) if key == "float" else bool(data["bool"]))
            settings["available"] = True
    return settings


def parse_default_bindings():
    """Parse all default keybindings shipped with Omarchy."""
    bindings = []
    if not os.path.isdir(DEFAULT_BINDINGS_DIR):
        return bindings

    file_count = 0
    total_read = 0
    try:
        names = sorted(os.listdir(DEFAULT_BINDINGS_DIR))
    except OSError:
        # An unreadable directory must not turn the whole model build into a
        # traceback (the panel would show an empty list and no error at all).
        return bindings
    for fname in names:
        if file_count >= MAX_DIR_FILES or total_read > MAX_TOTAL_READ:
            break
        file_count += 1
        if not fname.endswith(".lua"):
            continue
        filepath = os.path.join(DEFAULT_BINDINGS_DIR, fname)
        # Same bounded reader as every other path: a FIFO planted in this
        # directory used to block `list` forever, and an oversized file was
        # silently truncated mid-statement.
        try:
            content = _read_bounded_regular_file(filepath)
        except (OSError, UnicodeDecodeError):
            continue
        if content is None:
            continue
        total_read += len(content.encode("utf-8"))

        for _unit_lines, unit_text, unit_is_comment in _lua_units(content.splitlines()):
            if unit_is_comment or not unit_text:
                continue
            stripped = unit_text.strip()

            parsed = _parse_bind_line(stripped)
            if not parsed:
                continue

            raw_key, desc, action_raw, opts_raw = parsed
            fn_name = _bind_fn(stripped)

            norm_key = normalize_key_chord(raw_key)
            # o.bind_toggle prefixes "omarchy-toggle-" itself, so the stored third
            # argument is the toggle NAME. Rewriting it here made a re-emitted row
            # run "omarchy-toggle-omarchy-toggle-bar".
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
            elif base in ("voxtype", "dictation"):
                cat = "AI & Voice"

            is_release = opts_raw and ("on_release = true" in opts_raw or "on_release=true" in opts_raw)

            decoded_cmd, _end, _ok = _read_lua_string((action_raw or "").strip(), 0)
            bindings.append({
                "source": "default",
                "file": fname,
                "command": decoded_cmd if (_ok and decoded_cmd is not None
                                           and not _is_raw_dispatcher(action_raw)) else "",
                "key": norm_key,
                "raw_key": raw_key,
                "description": desc,
                "action": action_raw,
                "opts": opts_raw or "",
                "fn": fn_name,
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

        # `hl["unbind"]("K")` is legal Lua and Hyprland honours it; the anchored
        # spelling alone showed such a chord as still bound.
        m_unbind = _search_in_code(
            r'hl\s*(?:\.\s*unbind|\[\s*["\']unbind["\']\s*\])\s*\(', text)
        if m_unbind:
            _unbind_key, _unbind_end, _unbind_ok = _read_lua_string(text, m_unbind.end())
            if _unbind_ok and _unbind_key:
                result["unbinds"].add(normalize_key_chord(_unbind_key))
                continue

        parsed = _parse_bind_line(text)
        if parsed:
            raw_key, desc, action_raw, opts_raw = parsed
            norm_key = normalize_key_chord(raw_key)

            literal_cmd = ""
            if not _is_raw_dispatcher(action_raw):
                value, _end, ok = _read_lua_string((action_raw or "").strip(), 0)
                if ok and value is not None:
                    literal_cmd = value

            entry = {
                "source": "user",
                "key": norm_key,
                "raw_key": raw_key,
                "description": desc or action_raw.strip('"\''),
                # raw description as written (nil/empty stays empty) and the
                # decoded literal command: the editor needs both so an untouched
                # save is byte-stable and an in-place edit can identify the line.
                "description_raw": desc or "",
                "command": literal_cmd,
                "action": action_raw,
                "opts": opts_raw or "",
                "fn": _bind_fn(text),
                "is_mouse": "mouse" in raw_key.lower() or "mouse" in (opts_raw or ""),
                "is_release": opts_raw and ("on_release = true" in opts_raw or "on_release=true" in opts_raw),
            }

            if entry["is_mouse"]:
                result["mouse_binds"].append(entry)
            # A mouse binding is still a user binding: it must be matched to the
            # default row it overrides. Kept only in mouse_binds, saving one made
            # its default row read "disabled" and the panel's Enable then deleted it.
            result["binds"].append(entry)

    return result


def _chords_disabled_by_unbind():
    """Chords that Hyprland ends up with NO binding for.

    ``hl.unbind(k)`` removes the binds on ``k`` registered BEFORE it, so an unbind
    at the top shadows only the default while a user bind after it stays live. Only
    a chord whose unbind comes after its last bind (or that has no bind at all) is
    actually disabled.
    """
    try:
        content = _read_bounded_regular_file(USER_BINDINGS_PATH)
    except (OSError, UnicodeDecodeError):
        content = None
    if not content:
        return set()
    last_bind = {}
    last_unbind = {}
    for index, (_lines, text, is_comment) in enumerate(_lua_units(content.splitlines())):
        if is_comment or not text:
            continue
        m_unbind = _search_in_code(
            r'\bhl\s*(?:\.\s*unbind|\[\s*["\']unbind["\']\s*\])\s*\(', text)
        if m_unbind:
            value, _end, ok = _read_lua_string(text, m_unbind.end())
            if ok and value:
                last_unbind[normalize_key_chord(value)] = index
            continue
        parsed = _parse_bind_line(text)
        if parsed:
            last_bind[normalize_key_chord(parsed[0])] = index
    return {k for k in last_unbind
            if k not in last_bind or last_unbind[k] > last_bind[k]}


def build_complete_model():
    """
    Construct the unified model of active bindings, catalog, conflicts, and mouse scroll settings.
    """
    default_binds = parse_default_bindings()
    user_state = parse_user_bindings()

    rebound_defaults_map = {}
    _emitted_rebound = set()
    matched_user_rows = set()  # id() of the rows paired as a moved override
    matched_user_bind_keys = set()  # the chords they left, for conflict bookkeeping

    for ub in user_state["binds"]:
        # A default counts as MOVED only when it is really gone from Hyprland: its
        # own chord carries an hl.unbind AND the same action moved to this row. A
        # second chord added for an action still bound at its default chord is an
        # ADDITION, and hiding the live default row made it impossible to see,
        # disable or reset that binding.
        matched = next(
            (d for d in default_binds
             if d["action"] == ub["action"] and d["key"] in user_state["unbinds"]),
            None
        )
        if matched and matched["key"] not in rebound_defaults_map:
            # One default row can stand for one moved binding: a second user row
            # with the same action must stay visible as its own custom row instead
            # of silently disappearing from the model.
            rebound_defaults_map[matched["key"]] = (ub, matched)
            matched_user_rows.add(id(ub))
            matched_user_bind_keys.add(ub["key"])

    all_active = []

    # 1. Iterate defaults
    for d in default_binds:
        k = d["key"]

        if k in rebound_defaults_map:
            ub, matched_default = rebound_defaults_map[k]
            if k not in _emitted_rebound:
                _emitted_rebound.add(k)
                item = dict(ub)
                item["status"] = "modified"
                item["category"] = matched_default.get("category", "Custom")
                item["default_key"] = k
                # The default this row actually replaces, not whichever default
                # happens to come first on the chord (ALT+TAB carries two).
                item["default_action"] = matched_default.get("action", "")
                all_active.append(item)
            if d.get("action") != matched_default.get("action"):
                # The OTHER default on this chord is dead now: the chord carries one
                # user bind and an unbind. Keep it listed so it can be seen and
                # reset instead of vanishing from the panel.
                sibling = dict(d)
                sibling["status"] = "disabled"
                sibling["disabled_row"] = True
                sibling["default_key"] = k
                all_active.append(sibling)
            continue

        same_key_user_bind = next(
            (ub for ub in user_state["binds"]
             if ub["key"] == k and id(ub) not in matched_user_rows
             and not ub.get("_consumed_by_default")), None)
        if same_key_user_bind:
            item = dict(same_key_user_bind)
            item["status"] = "modified" if same_key_user_bind["description"].lower() == d["description"].lower() else "custom"
            item["category"] = d.get("category", "Custom")
            item["default_key"] = k
            item["default_action"] = d["action"]
            all_active.append(item)
            # Only THIS row is consumed: another user binding on the same chord is a
            # real binding (and a real conflict) and must stay in the list.
            same_key_user_bind["_consumed_by_default"] = True
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
        if ub.get("_consumed_by_default") or id(ub) in matched_user_rows:
            continue
        item = dict(ub)
        item["status"] = "custom"
        item["category"] = "Custom"
        item["default_key"] = ""
        all_active.append(item)

    # 2b. A chord carrying an hl.unbind is DISABLED in Hyprland, whatever its own
    #     statement says: Disable keeps the user's line (so Enable can bring it
    #     back), and that used to leave the row looking live with no Enable button.
    disabled_chords = _chords_disabled_by_unbind()
    for item in all_active:
        if (item.get("status") in ("modified", "custom")
                and item.get("key") in disabled_chords):
            item["disabled_from"] = item["status"]
            item["status"] = "disabled"
            item["disabled_row"] = True

    # What the row can do, decided HERE: the panel used to gate Reset on
    # status=='modified' and Delete on status=='custom', so a disabled override
    # was listed with neither button and could only be removed via Enable.
    for item in all_active:
        status = item.get("status")
        on_default_chord = bool(item.get("default_key"))
        item["can_reset"] = on_default_chord and (
            status == "modified" or bool(item.get("disabled_row")))
        item["can_delete"] = (status == "custom"
                              or (bool(item.get("disabled_row")) and not on_default_chord))
        item["can_enable"] = status == "disabled"
        item["can_disable"] = status in ("default", "modified", "custom")

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
                    {"key": x["key"], "description": x["description"], "action": x["action"],
                     "command": x.get("command", ""),
                     "description_raw": x.get("description_raw", ""),
                     "status": x.get("status", "unknown")}
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

    # Count what the Modified & Custom tab RENDERS: a disabled override is listed
    # there (with Reset/Delete), so leaving it out of the badge made the two
    # disagree (0 in the badge, 1 row in the list).
    total_modified = len([b for b in all_active
                          if b.get("status") in ("modified", "custom")
                          or b.get("disabled_row")])

    return {
        "active": all_active,
        "catalog": catalog,
        "conflicts": conflicts,
        "apps": get_installed_apps(),
        # The tab renders all_active (disabled rows included), so the badge must
        # count the same list or it reads 205 above 206 rows.
        "total_active": len(all_active),
        "total_modified": total_modified,
        "total_conflicts": len(conflicts),
        "mouse_settings": get_mouse_scroll_settings(),
        "user_bindings_file": USER_BINDINGS_PATH,
    }


def _pure_literal(text: str):
    """Decoded value when ``text`` is ONE complete Lua string literal, else None.

    The writer re-emits a stored literal verbatim so an untouched save cannot
    change the bytes. That must never interpolate caller text into code: the
    documented CLI takes old_action as an argument, and `"x") os.execute(...) --`
    was written as Lua before this check existed.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    value, end, ok = _read_lua_string(raw, 0)
    if not ok or value is None:
        return None
    if raw[end:].strip():
        return None
    return value


def _identity_value(raw) -> str:
    """A comparison value for a description or action, independent of form.

    The stored text is a Lua literal; the model hands the editor the decoded
    value. Comparing the two must not depend on which legal literal form the file
    used - a wholly quoted command, a ``[[...]]`` action or a ``\\n`` escape all
    decoded to different strings under the old quote-stripping comparison, so the
    row was not recognised and a second bind was appended on top of it.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    value, _end, ok = _read_lua_string(text, 0)
    if not (ok and value is not None):
        value = text
    return _plain_value(value)


def _plain_value(raw) -> str:
    """Normalise plain text the way the writer does, WITHOUT decoding a literal.

    A command typed in the dialog is data, not Lua: a value that starts with a
    quote means the user wants that quote, so it must not be re-parsed.
    """
    text = (raw or "").strip()
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text.replace("\r", " ").replace("\n", " ")


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
    desc = _plain_value(entry.get("description") or "")
    cmd = entry.get("command", "")
    action = entry.get("action", "")
    if entry.get("action_literal"):
        # The file already holds this exact literal and the command was not
        # edited: re-encoding it is what changed the bytes.
        return desc, _identity_value(entry["action_literal"])
    if _is_raw_dispatcher(action):
        return desc, action.strip()[:MAX_CMD_LEN]
    if cmd:
        return desc, _plain_value(cmd)
    return desc, _identity_value(action)


def _lua_compile_error(text: str) -> str:
    """First line of the Lua compiler's complaint about ``text``, or "".

    ``luac -p`` only parses (no execution), so the ``hl``/``o`` helpers being
    runtime globals is fine. No compiler installed -> "" (the shape checks above
    are all there is, which is why they stay).
    """
    luac = (_trusted_binary("luac") or _trusted_binary("luac5.4")
            or _trusted_binary("luac5.3"))
    if not luac:
        # Nothing to compare against here: the "no compiler" refusal lives in
        # _assert_lua_compiles, which knows the ORIGINAL text and can therefore
        # blame only the edit (an existing function row must not block every save).
        return ""
    if not text.strip():
        return ""
    # Beside the file it checks, like the other temp writers: a SIGKILL mid-compile
    # used to leave a copy of bindings.lua (with its commands) in the shared temp.
    _check_dir = os.path.dirname(os.path.realpath(USER_BINDINGS_PATH))
    fd, tmp = tempfile.mkstemp(
        suffix=".lua", prefix=".bindings_check_",
        dir=_check_dir if os.path.isdir(_check_dir) else None)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        try:
            res = subprocess.run([luac, "-p", tmp], capture_output=True, text=True,
                                 timeout=10)
        except Exception:
            return ""  # a broken compiler is not a reason to lose the edit
        if res.returncode != 0:
            first = (res.stderr or res.stdout or "").strip().splitlines()
            # The first line starts with our temp file's path, which means nothing
            # to the user and leaks a scratch location into a toast.
            message = first[0].replace(tmp + ":", "line ").strip() if first else ""
            return message or "luac rejected it"
        return ""
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _has_function_action(text: str) -> bool:
    """True when ``text`` holds a Lua ``function`` literal in code position.

    Masked, so a description or a command string that merely contains the word
    does not count, and nested literals (``f(function() ... end)``,
    ``{ run = (function() ... end) }``) are found - the old comma-adjacency regex
    saw only the direct ``o.bind(K, D, function()`` spelling.
    """
    flags = _code_mask(text)
    if isinstance(flags, list):
        masked = "".join(ch if code else " " for ch, code in zip(text, flags))
    else:
        masked = flags
    return bool(re.search(r"(?<![\w.:])function\b", masked))


def _assert_lua_compiles(text: str, original: str = "") -> None:
    """Refuse a write that would leave a file Hyprland cannot parse.

    Only OUR change matters: a file that was already broken stays editable (the
    plugin has always preserved such bytes verbatim), but a file that compiled
    before must still compile - otherwise one bad save makes Hyprland drop every
    user override in it.
    """
    # A function body is the one action the shape checks cannot prove (they are a
    # token stack, not a parser). With no trusted compiler, refuse to ADD one
    # rather than write a bindings.lua Hyprland drops entirely and report "saved";
    # a file that already had one stays editable, so the row can still be fixed.
    if not (_trusted_binary("luac") or _trusted_binary("luac4")
            or _trusted_binary("luac5.4") or _trusted_binary("luac5.3")):
        if _has_function_action(text) and not _has_function_action(original):
            raise ValueError(
                "this action is a Lua function and no Lua compiler (luac) was found "
                "to validate it; install lua or use a dispatcher form")
    error = _lua_compile_error(text)
    if not error:
        return
    if _lua_compile_error(original):
        return  # already broken before us; do not make the user unable to fix it
    raise ValueError(
        "refusing to write: the resulting bindings.lua would not compile ("
        + error + ")")


def _resolve_bindings_write_path(path: str):
    """Resolve a (possibly symlinked) bindings path to the real target.

    Returns (resolved_path, exists). Follows symlinks so dotfile-managed
    configs keep their link intact, but every read/write happens on the
    resolved regular file. Dangling links and loops become ValueError.
    """
    resolved = os.path.realpath(path)
    # Dotfile links are followed on purpose, but the TARGET was unbounded: a stale
    # or planted link turned any save into a rewrite of an arbitrary user-writable
    # file (probe: bindings.lua -> /var/tmp/victim.lua, rewritten, .bak dropped
    # beside it). Keep it inside the home directory.
    home = os.path.realpath(os.path.expanduser("~"))
    if home not in ("", "/") and not (resolved == home
                                      or resolved.startswith(home + os.sep)):
        raise ValueError(
            "the bindings file resolves outside your home folder "
            f"({resolved}); refusing to write there")
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
                        preserve_entry=None, previous_entry=None, override_keys=None,
                        require_absent=None, require_removal=False,
                        no_duplicate_keys=None, unbind_at_end=False,
                        remove_unbinds_only=None):
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

    ``require_absent``: keys that must have no bind line left in the output.
    The removal verbs pass their target, so "wrote nothing, said saved" cannot
    happen (they used to succeed while the statement stayed in place).

    ``require_removal``: the write must remove at least one statement (a verb that
    only deletes). Counting units catches every detection blind spot, including
    forms the anchored patterns do not even recognise.

    ``unbind_at_end``: place the emitted hl.unbind AFTER every user statement
    instead of above them. Hyprland's unbind() removes the binds registered before
    it, so this disables a chord while KEEPING the user's own lines recoverable -
    Disable followed by Enable used to destroy a hand-written command.

    ``remove_unbinds_only``: chords whose hl.unbind statements are removed while
    their o.bind statements stay (what Enable needs after the above).
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
            # The .bak is written only after the statement proofs pass (see
            # below): a refused edit must not destroy the previous known-good
            # backup while telling the user that nothing was written.

    original_content_for_backup = existing_content
    bom = "\ufeff" if existing_content.startswith("\ufeff") else ""
    if bom:
        # A BOM is only legal at byte 0. Strip it from the line list and put it
        # back on write, so an inserted unbind cannot push it into the middle of
        # the file (which made the whole file unparseable).
        existing_content = existing_content[1:]
    current_lines, newline = _split_lines(existing_content)

    if not current_lines:
        current_lines = [
            "-- Personal keybinding overrides for Omarchy Hyprland",
            "-- Managed graphically by Omarchy Keybindings Plugin",
            "",
        ]

    clean_keys = set(normalize_key_chord(k) for k in (keys_to_remove or []))
    unbind_only_keys = set(normalize_key_chord(k) for k in (remove_unbinds_only or []))

    # Which unbind units are the one Disable appended: the unbind that comes AFTER
    # the last bind on that chord. The shadow unbind a save writes ABOVE the user's
    # bind has to stay, or Enable would resurrect the Omarchy default beside the
    # user's own binding and one keypress would run both actions.
    _units = list(_lua_units(current_lines))
    _last_bind_index = -1
    for _i, (_ul, _ut, _uc) in enumerate(_units):
        if not _uc and _ut and _bind_head(_ut):
            _last_bind_index = _i
    trailing_unbind_units = set()
    for _i, (_ul, _ut, _uc) in enumerate(_units):
        if _uc or not _ut or _i <= _last_bind_index:
            continue
        _m = _search_in_code(
            r'\bhl\s*(?:\.\s*unbind|\[\s*["\']unbind["\']\s*\])\s*\(', _ut)
        if not _m:
            continue
        _value, _err, _ok = _read_lua_string(_ut, _m.end())
        if _ok and _value and normalize_key_chord(_value) in unbind_only_keys:
            trailing_unbind_units.add(_i)

    new_lines = []
    removed_positions = []

    # Work on logical statements, not physical lines: a hand-formatted
    # multi-line o.bind must be replaced or removed as a whole, and text inside
    # a --[[ ]] block is a comment even though its lines do not start with --.
    for _unit_index, (unit_lines, text, is_comment) in enumerate(_units):
        if is_comment or not text:
            new_lines.extend(unit_lines)
            continue

        is_targeted = False
        if clean_keys or unbind_only_keys:
            m_unbind = _search_in_code(
                r'\bhl\s*(?:\.\s*unbind|\[\s*["\']unbind["\']\s*\])\s*\(', text)
            unbind_key = ""
            if m_unbind:
                value, _end, ok = _read_lua_string(text, m_unbind.end())
                if ok and value is not None:
                    unbind_key = normalize_key_chord(value)
            if (unbind_key and unbind_key in unbind_only_keys
                    and _unit_index in trailing_unbind_units):
                # Enable: drop the unbind, keep the user's own binding.
                if not _unit_is_single_statement(text):
                    raise ValueError(
                        "the line holding this chord also holds another "
                        "statement; refusing to edit it (split it into its "
                        "own line first)")
                is_targeted = True
            elif unbind_key and unbind_key in clean_keys:
                if not _unit_is_single_statement(text):
                    raise ValueError(
                        "the line holding this chord also holds another "
                        "statement; refusing to edit it (split it into its "
                        "own line first)")
                is_targeted = True

            # Not anchored: a bind can follow `if ... then ` or be written as
            # `o .bind(...)`, and an anchored match made both the removal and its
            # proof blind to it (the write reported success and the chord stayed
            # bound).
            bind_key = ""
            m_bind = _bind_head(text)
            if m_bind:
                # Read the arguments with the real parser: a key written as a
                # long-bracket string (o.bind([[SUPER + W]], ...)) is legal and
                # was invisible to the quote-shaped regex, so "Disable" wrote
                # hl.unbind above a bind that stayed live.
                _parsed_bind = _parse_bind_line(text[m_bind.start():])
                if _parsed_bind:
                    bind_key = normalize_key_chord(_parsed_bind[0])
                else:
                    flat = re.sub(r"\s+", " ", text)
                    if any(k and k in flat for k in clean_keys):
                        raise ValueError(
                            "the file holds a binding for this chord this plugin "
                            "cannot parse (a computed key or description); "
                            "refusing to edit it")
            if bind_key and bind_key in unbind_only_keys:
                # Never removed in unbind-only mode.
                new_lines.extend(unit_lines)
                continue
            if bind_key and bind_key in clean_keys:
                if bind_key in overrides:
                    if not _unit_is_single_statement(text):
                        raise ValueError(
                            "the line holding this chord also holds another "
                            "statement; refusing to edit it (split it into its "
                            "own line first)")
                    is_targeted = True
                elif identities:
                    if not _unit_is_single_statement(text):
                        raise ValueError(
                            "the line holding this chord also holds another "
                            "statement; refusing to edit it (split it into its "
                            "own line first)")
                    _parsed = _parse_bind_line(text)
                    if _parsed:
                        parsed_desc = _plain_value(_parsed[1])
                        parsed_action = _identity_value(_parsed[2])
                        for ident in identities:
                            # Both halves, not either: a rename that lands on the
                            # sibling's description (or command) used to match
                            # that sibling by the OR and delete it. Compared by
                            # VALUE: stripping quotes made every non-canonical
                            # literal (a wholly quoted command, [[...]], an escape)
                            # unrecognisable, and the row was duplicated instead of
                            # replaced.
                            if parsed_desc == ident[0] and parsed_action == ident[1]:
                                is_targeted = True
                                break
                    # Else: a different binding sharing this key (swap partner,
                    # or a second binding on the same chord) -> keep it.
                else:
                    if not _unit_is_single_statement(text):
                        raise ValueError(
                            "the line holding this chord also holds another "
                            "statement; refusing to edit it (split it into its "
                            "own line first)")
                    is_targeted = True

        if is_targeted:
            # Remember where the row was: appending the replacement at the end of
            # the block moved it away from its own comment on every save.
            removed_positions.append(len(new_lines))
            continue
        new_lines.extend(unit_lines)

    inserted_unbind_count = 0
    unbind_insert_at = 0
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
            # Compare against CODE, not raw lines: a copy of this exact line inside
            # a --[[ ]] block (the plugin writes it, a user comments it out to
            # re-enable by hand) used to suppress the emission, so Disable wrote
            # nothing and still reported success.
            # Hyprland's unbind removes the binds registered BEFORE it, so an
            # unbind that sits above a user bind does not disable it: with
            # unbind_at_end that one must not count as "already emitted" or Disable
            # wrote nothing and reported success on every plugin-written override.
            units = list(_lua_units(new_lines))
            if unbind_at_end:
                last_bind_index = -1
                for idx, (_u, text, is_comment) in enumerate(units):
                    if is_comment or not text:
                        continue
                    if _bind_head(text):
                        last_bind_index = idx
                already_emitted = any(
                    idx > last_bind_index and not is_comment and text
                    and _search_in_code(re.escape(line_str), text)
                    for idx, (_u, text, is_comment) in enumerate(units)
                )
            else:
                already_emitted = any(
                    not is_comment and text and _search_in_code(re.escape(line_str), text)
                    for _u, text, is_comment in units
                )
            if not already_emitted and line_str not in unbind_lines:
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
        # When the edited row was the file's last statement, the comments that
        # used to follow it are "leading" once it is removed, so insert_at landed
        # BELOW the row's old slot and the unbind was emitted after the new o.bind -
        # killing the shortcut the save had just written. Never insert past the
        # row being restored.
        if removed_positions:
            insert_at = min(insert_at, removed_positions[0])
        if unbind_at_end:
            insert_at = len(new_lines)
        unbind_insert_at = insert_at
        for offset, line_str in enumerate(unbind_lines):
            new_lines.insert(insert_at + offset, line_str)
        inserted_unbind_count = len(unbind_lines)

    if lines_to_add:
        for entry in lines_to_add:
            key = normalize_key_chord(entry.get("key", ""))
            if not key or not _key_is_plausible(key):
                # Fail closed at the sink: a future caller with an unsafe key used
                # to make the writer emit o.bind("", ...) and report success.
                raise ValueError("invalid key combination")
            stored_raw = (entry.get("raw_key") or "").strip()
            if (stored_raw and normalize_key_chord(stored_raw) == key
                    and _key_is_plausible(stored_raw)
                    and not re.search(r"[\\\"']", stored_raw)):
                # Nothing about the chord changed: re-emit the exact spelling from
                # the file so a description-only save cannot respell it.
                key = stored_raw
            raw_desc = _plain_value(entry.get("description", ""))
            if len(raw_desc) > MAX_TEXT_LEN:
                raise ValueError(
                    f"that description is longer than {MAX_TEXT_LEN} characters")
            desc = sanitize_lua_str(raw_desc, max_len=MAX_TEXT_LEN)
            cmd = entry.get("command", "")
            action = entry.get("action", "")

            # Raw Lua comes ONLY from the explicit dispatcher field, never from
            # `command`: the command can be an untrusted .desktop Exec, and
            # deciding "this is code" from a prefix in that data let `Exec=hl.…`
            # inject statements into the file Hyprland executes.
            fn_name = entry.get("fn_name") if entry.get("fn_name") in (
                "o.bind", "o.bind_toggle", "o.bind_hold") else "o.bind"
            if fn_name == "o.bind_toggle" and not entry.get("action_literal"):
                # o.bind_toggle prepends "omarchy-toggle-" to its third argument:
                # a NEW command is not a toggle name, so it goes through o.bind.
                fn_name = "o.bind"
            if fn_name == "o.bind_hold" and not entry.get("action_literal"):
                raise ValueError(
                    "this is a hold binding (press and release actions): its command "
                    "cannot be changed here; edit ~/.config/hypr/bindings.lua")
            opts_part = ""
            opts_literal = (entry.get("opts_literal") or "").strip()
            if opts_literal:
                # o.bind's fourth argument (locked/repeating/non_consuming) decides
                # how the key behaves; dropping it reported "saved" while the key
                # stopped repeating. A bind_hold row's tail is longer (stop
                # description, stop command, opts) and is kept verbatim.
                if fn_name == "o.bind_hold":
                    if not _lua_ast_balanced(opts_literal):
                        raise ValueError(
                            "refusing to rewrite this hold binding: its arguments do "
                            "not balance")
                    if not _hold_tail_is_shaped(opts_literal):
                        raise ValueError(
                            "refusing to rewrite this hold binding: its stop action "
                            "and options must be a string and a table")
                else:
                    problem = _lua_single_expression_problem(opts_literal)
                    if not problem and not _table_carries_no_code(opts_literal):
                        problem = "its options table may not run code"
                    if problem or not opts_literal.startswith("{"):
                        raise ValueError(
                            "refusing to rewrite this binding: its options table cannot "
                            "be validated (" + (problem or "not a table") + ")")
                opts_part = ", " + opts_literal

            if _is_raw_dispatcher(action):
                if _registers_a_binding(action):
                    raise ValueError("a raw dispatcher may not register another binding")
                problem = _lua_single_expression_problem(action.strip())
                if problem:
                    raise ValueError(f"refusing to write the raw dispatcher: {problem}")
                new_lines.append(f'{fn_name}("{key}", "{desc}", {action.strip()}{opts_part})')
            elif entry.get("action_literal"):
                # Unchanged command: re-emit the literal the file already holds, so
                # an untouched save cannot rewrite (or duplicate) it. It is
                # validated as ONE complete literal first: the documented CLI takes
                # this value as an argument, and `"x") os.execute(...) --` used to be
                # written as Lua.
                literal = entry["action_literal"].strip()
                value = _pure_literal(literal)
                if value is None:
                    raise ValueError(
                        "refusing to write this binding: its stored command is not "
                        "one complete string literal")
                if value == "":
                    raise ValueError(
                        "this binding has no command to keep: type the command you want")
                new_lines.append(f'{fn_name}("{key}", "{desc}", {literal}{opts_part})')
            else:
                # `command` is plain text typed by the user (never decoded); a stored
                # `action` literal from the file IS a literal, so it is decoded -
                # otherwise a default row's "omacalc" became the dead command
                # "\"omacalc\"".
                if cmd:
                    value = _plain_value(cmd)
                elif action:
                    stripped_action = action.strip()
                    if (stripped_action[:1] in "\"'" or stripped_action.startswith("[")) \
                            and _pure_literal(stripped_action) is None:
                        raise ValueError(
                            "that value starts like a Lua string but continues past "
                            "it; pass the command as a plain string")
                    value = _identity_value(action)
                else:
                    value = ""
                escaped_cmd = sanitize_lua_str(value, max_len=MAX_CMD_LEN)
                if len(_plain_value(value)) > MAX_CMD_LEN:
                    raise ValueError(f"that command is longer than {MAX_CMD_LEN} characters")
                new_lines.append(f'{fn_name}("{key}", "{desc}", "{escaped_cmd}"{opts_part})')

    # A one-for-one replacement goes back where it was (comment above it intact).
    if len(removed_positions) == 1 and lines_to_add and len(lines_to_add) == 1:
        pos = unbind_insert_at + inserted_unbind_count
        if 0 <= pos < len(new_lines):
            new_lines.insert(pos, new_lines.pop())

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
    if no_duplicate_keys:
        def _keys_bound(lines):
            found = []
            for _unit, text, is_comment in _lua_units(lines):
                if is_comment or not text:
                    continue
                start = 0
                while start < len(text):
                    head = _bind_head(text[start:])
                    if not head:
                        break
                    parsed = _parse_bind_line(text[start + head.start():])
                    if parsed:
                        found.append(normalize_key_chord(parsed[0]))
                    start += head.end()
            return found

        for key in no_duplicate_keys:
            norm = normalize_key_chord(key)
            if not norm:
                continue
            before = _keys_bound(current_lines).count(norm)
            after = _keys_bound(new_lines).count(norm)
            # Only a replacement can duplicate: binding a chord that had no user row
            # yet (every override of an Omarchy default, the plugin's main
            # operation) legitimately goes 0 -> 1.
            if before and after > before:
                raise ValueError(
                    "refusing to write: the edit would leave " + str(after) +
                    " bindings on " + norm + " instead of " + str(before) +
                    "; nothing was written")

    if require_removal:
        before_units = sum(1 for _u, t, c in _lua_units(current_lines) if t and not c)
        after_units = sum(1 for _u, t, c in _lua_units(new_lines) if t and not c)
        if after_units >= before_units:
            raise ValueError(
                "nothing was removed: the statement to delete could not be "
                "located; nothing was written")

    if require_absent:
        # Independent check: look for the chord anywhere in a unit's flattened
        # text, with a tolerant pattern. Reusing the anchored helpers that did the
        # removal let a blind spot certify itself.
        still_there = []
        absent_targets = {normalize_key_chord(k) for k in require_absent}
        absent_targets.discard("")
        for _unit, text, is_comment in _lua_units(new_lines):
            if is_comment or not text:
                continue
            # Independent of the bind-name list: any o.<method>("<chord>", ...
            # registration counts, including forms this writer does not emit
            # (o.bind_hold is shipped by Omarchy itself).
            for m_any in re.finditer(r'\bo\s*[.:]\s*\w+\s*\(', text):
                if not _search_in_code(re.escape(m_any.group(0)), text):
                    continue
                _k, _e, _ok = _read_lua_string(text, m_any.end())
                if _ok and _k and normalize_key_chord(_k) in absent_targets:
                    still_there.append(normalize_key_chord(_k))
        if still_there:
            raise ValueError(
                "could not remove the binding for " + ", ".join(sorted(set(still_there))) +
                "; nothing was written")
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
            written_desc = _plain_value(parsed[1])
            written_action = _identity_value(parsed[2])
            # Compared by value, not case-folded: a description that differs only
            # in case is a legal edit, and refusing it claimed "nothing was
            # written" after the file had been rewritten.
            if (written_desc == previous_identity[0]
                    and written_action == previous_identity[1]):
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

    # Last gate before the file is committed: compile it, when a Lua compiler is
    # installed. A file that does not parse makes Hyprland drop every user override
    # in it, and no shape check of ours can promise a parser's verdict.
    _assert_lua_compiles(newline.join(final_output), existing_content)

    # A command that changes nothing must not rewrite bytes: `enable` on a chord
    # with no unbind collapsed the file's blank lines and replaced the inode.
    if exists and (bom + newline.join(final_output) + newline) == original_content_for_backup:
        return {"success": True, "key": None, "message": "no change"}

    if exists:
        backup_path = resolved_path + ".bak"
        # Unique temp file + atomic replace instead of O_TRUNC on the predictable
        # .bak path: a planted hard link or symlink there cannot destroy another
        # file's contents. The .bak lands beside the resolved target so
        # dotfile-managed links keep working.
        bak_fd, bak_tmp = tempfile.mkstemp(dir=target_dir, prefix=".bindings_", suffix=".bak.tmp")
        try:
            with os.fdopen(bak_fd, "w", encoding="utf-8") as bak_f:
                bak_f.write(original_content_for_backup)
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

    fd, temp_file = tempfile.mkstemp(dir=target_dir, prefix=".bindings_", suffix=".tmp")
    try:
        if exists:
            try:
                mode = stat.S_IMODE(os.stat(resolved_path).st_mode)
            except OSError:
                mode = 0o600
            # Keep the owner bits, drop group/world WRITE: this file is Lua that
            # Hyprland executes, and the plugin must not hand back a writable copy.
            # Owner-only, like the .bak and the compile temp this same save writes:
            # a bind command can carry a secret, and group/world read leaked it.
            mode &= ~(stat.S_IWGRP | stat.S_IWOTH | stat.S_IRGRP | stat.S_IROTH
                      | stat.S_ISUID | stat.S_ISGID)
            try:
                os.fchmod(fd, mode | stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(bom + newline.join(final_output) + newline)
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
    hyprctl = _hyprctl()
    if not hyprctl:
        # No trustworthy binary: say so instead of reporting an applied reload.
        return {"success": False, "reload_output": "",
                "config_errors": "hyprctl was not found in a trusted location; "
                                 "reload the config by hand"}
    try:
        res = subprocess.run([hyprctl, "reload"], capture_output=True, text=True, timeout=10)
        err_res = subprocess.run([hyprctl, "configerrors"], capture_output=True, text=True, timeout=10)
        errors = err_res.stdout.strip() if err_res.returncode == 0 else ""
        # `hyprctl reload` exits 0 even when Hyprland rejects the file - and then
        # it drops every user override in it. A save that leaves errors behind is
        # not "applied".
        return {
            "success": res.returncode == 0 and not errors,
            "reload_output": res.stdout.strip(),
            "config_errors": errors,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def set_keybinding(key: str, description: str, command: str, action: str = "", old_key: str = "",
                   override_conflicts: bool = True,
                   old_description: str = "", old_command: str = "", old_action: str = "",
                   keep_disabled: bool = False):
    """Set, modify, or replace a keybinding.

    ``old_description`` / ``old_command`` / ``old_action`` describe the binding
    being edited in place. They are what identifies the exact line to replace
    when several bindings share the chord (a rename changes both the
    description and the command, so key alone cannot find it).
    """
    norm_key = normalize_key_chord(key)
    norm_old_key = normalize_key_chord(old_key) if old_key else ""

    if not norm_key or not _key_is_plausible(norm_key):
        return {"success": False, "error": "Invalid key combination"}

    if not command.strip() and action.strip() == "exec":
        return {"success": False,
                "error": "no command: pick an application or type the command to run"}
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
        if _registers_a_binding(action):
            return {"success": False,
                    "error": "invalid raw Lua dispatcher: it may not register another binding"}
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

    bind_entry = {
        "key": norm_key,
        # The chord as the user (or Omarchy) wrote it: normalization cannot
        # round-trip "ALT + Alt_R" (it becomes "ALT + ALT_R"), and an edit that
        # only changed the description used to respell the shortcut.
        "raw_key": key.strip() if normalize_key_chord(key) == norm_key else "",
        "description": description or command,
        "command": command,
        "action": action,
    }
    # The dialog re-sends the command it was given. When that value is still the
    # one stored, write the stored LITERAL back verbatim: re-encoding it changed
    # the bytes (and, when the identity then failed to match, appended a second
    # bind on the same chord).
    old_literal = (old_action or "").strip()
    if old_literal and not action.strip() and not _is_raw_dispatcher(old_literal):
        if _identity_value(old_literal) == _plain_value(command):
            bind_entry["action_literal"] = old_literal

    # The row's own spelling travels in old_key (the panel passes raw_key): prefer
    # it when the chord did not change, so a description-only save keeps
    # "ALT + Alt_R" exactly as the file had it.
    old_raw_key = (old_key or "").strip()
    if (old_raw_key and norm_old_key == norm_key
            and normalize_key_chord(old_raw_key) == norm_key
            and _key_is_plausible(old_raw_key)
            and not re.search(r"[\\\"']", old_raw_key)):
        bind_entry["raw_key"] = old_raw_key

    binds = [bind_entry]

    # Editing a binding in place replaces *that* binding only: a second user
    # binding on the same chord must survive. Any other key is an override.
    in_place = bool(norm_old_key) and norm_old_key == norm_key
    previous_entry = None
    if old_description or old_command or old_action:
        # The caller told us which row it is editing: use it for a chord change
        # too, so moving a binding frees the old chord instead of leaving a
        # duplicate statement behind.
        previous_entry = {
            "description": old_description,
            "command": old_command,
            "action": old_action,
        }
    elif in_place:
        same_key = [b for b in parse_user_bindings()["binds"] if b["key"] == norm_key]
        if len(same_key) == 1:
            previous_entry = same_key[0]
        elif len(same_key) > 1:
            return {
                "success": False,
                "error": "several bindings share this chord: pass the previous "
                         "description to say which one is being replaced",
            }
    elif norm_old_key:
        # A chord change must say WHICH row it is moving: guessing from the old
        # chord picks whatever else lives there (a swap partner, or a default's
        # neighbour) and would delete the wrong line.
        return {
            "success": False,
            "error": "moving a binding to another chord needs the previous "
                     "description/command: set <key> <desc> <cmd> <action> "
                     "<old_key> <old_desc> <old_cmd> <old_action>",
        }

    # Keep the row's call form and options table: rewriting an o.bind_toggle as
    # o.bind changed what the key does, and dropping { locked = true, repeating =
    # true } made media keys stop repeating while the panel said "applied".
    if norm_old_key or in_place:
        # Also for a move: the row lives at the OLD chord and must be found there.
        source_key = norm_old_key or norm_key
        user_rows = parse_user_bindings()["binds"]
        candidates = [b for b in user_rows if b["key"] == source_key]
        if not candidates:
            candidates = [d for d in parse_default_bindings() if d["key"] == source_key]
        target_ident = _written_bind_identity(previous_entry) if previous_entry else None
        for row in candidates:
            same = (target_ident is not None
                    and _plain_value(row.get("description")) == target_ident[0]
                    and _identity_value(row.get("action")) == target_ident[1])
            if same or len(candidates) == 1:
                bind_entry.setdefault("opts_literal", row.get("opts") or "")
                bind_entry.setdefault("fn_name", row.get("fn") or "o.bind")
                if (not bind_entry.get("action_literal")
                        and _pure_literal(row.get("action")) is not None
                        and _identity_value(row.get("action")) == _plain_value(command)):
                    bind_entry["action_literal"] = row.get("action")
                if row.get("fn") == "o.bind_hold" and bind_entry.get("fn_name") == "o.bind_hold":
                    # The stop-action tail belongs to the row: keep it byte-identical.
                    bind_entry["opts_literal"] = row.get("opts") or ""
                break
    binds = [bind_entry]

    override_keys = []
    if not in_place and override_conflicts:
        override_keys = [norm_key]
    try:
        write_user_bindings(lines_to_add=binds, unbinds_to_add=unbinds_to_add, keys_to_remove=keys_to_clean,
                            preserve_entry=binds[0], previous_entry=previous_entry,
                            override_keys=override_keys,
                            # An in-place edit replaces one row; if the output ends
                            # up with more binds on that chord than the input had,
                            # something was duplicated. This proof does not depend
                            # on the identity helper recognising every literal form.
                            # Only when a row is really being replaced: a first
                            # bind on a free chord legitimately goes 0 -> 1.
                            no_duplicate_keys=([norm_key]
                                               if (in_place and previous_entry) else []))
    except ValueError as e:
        return {"success": False, "error": str(e)}
    if keep_disabled:
        # Editing a row that was switched off must not switch it on: the only
        # reachable control on a disabled row is Edit, and a save silently
        # re-enabled the shortcut. Emit the unbind again, AFTER the bind (the
        # shadow unbind that hides the default stays where it is).
        try:
            write_user_bindings(unbinds_to_add=[norm_key], keys_to_remove=[],
                                unbind_at_end=True)
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
        "reload_errors": reload_status.get("config_errors", ""),
    }


def reset_keybinding(key: str, default_key: str = "", description: str = "",
                     action: str = ""):
    """Reset a keybinding back to default Omarchy configuration.

    ``description`` + ``action`` name ONE row: two binds can share a chord
    (Omarchy ships ALT+TAB with two actions), and deleting by chord alone removed a
    sibling the user never clicked.
    """
    norm_key = normalize_key_chord(key)
    norm_def = normalize_key_chord(default_key) if default_key else ""
    identity = {"key": norm_key, "description": description, "action": action} \
        if (description or action) else None

    if not norm_key or not _key_is_plausible(norm_key):
        return {"success": False, "error": "Invalid key combination"}

    keys_to_remove = [norm_key]
    if norm_def and norm_def != norm_key:
        keys_to_remove.append(norm_def)

    default_binds = parse_default_bindings()
    user_state = parse_user_bindings()

    # A moved override frees the chord it moved OFF. The match must be that
    # binding's own default (same action) AND that chord must carry an hl.unbind,
    # otherwise deleting an unrelated custom row re-enabled a shortcut the user had
    # deliberately disabled.
    for ub in user_state["binds"]:
        if ub["key"] != norm_key:
            continue
        matched_default = next(
            (d for d in default_binds
             if d["key"] not in (norm_key, norm_def) and d["action"] == ub["action"]),
            None
        )
        if (matched_default and matched_default["key"] in user_state["unbinds"]
                and matched_default["key"] not in keys_to_remove):
            keys_to_remove.append(matched_default["key"])

    try:
        # require_absent: the target must really be gone from the output, so a
        # statement this code cannot isolate can no longer be reported as
        # removed while it stays in the file.
        # Only demand a removal when there is something to remove: resetting a
        # chord that has no user override is already the default.
        state = parse_user_bindings()
        has_statement = (any(b["key"] in keys_to_remove for b in state["binds"])
                         or bool(state["unbinds"] & set(keys_to_remove)))
        if identity:
            # Identity-scoped: the chord may still carry a SIBLING binding the user
            # never clicked (Omarchy ships ALT+TAB twice), so requiring the whole
            # chord to be absent refused a legitimate delete. The default chord is
            # still part of the reset: a MOVED override leaves its hl.unbind behind,
            # and dropping it here left the shipped shortcut dead while the panel
            # toasted success.
            write_user_bindings(keys_to_remove=keys_to_remove, previous_entry=identity,
                                require_removal=True)
        else:
            write_user_bindings(keys_to_remove=keys_to_remove,
                                require_absent=keys_to_remove, require_removal=has_statement)
    except ValueError as e:
        return {"success": False, "error": str(e)}
    reload_status = reload_hyprland()
    # The write is what success means; a failed reload is reported separately so
    # the panel cannot say "not saved" about a change that landed.
    return {"success": True, "key": norm_key, "reload": reload_status,
            "reload_success": bool(reload_status.get("success", False))}


def disable_keybinding(key: str, description: str = "", action: str = ""):
    """Disable a keybinding.

    With a row identity (description + action) only that statement is touched and
    every other binding on the chord is kept; without one, every statement on the
    chord goes (the historical behaviour of the panel's Disable button).

    The unbind is emitted at the END so the user's own lines survive: Disable then
    Enable used to delete a hand-written command for good, since Enable only
    removed the unbind and the next save overwrote the .bak.
    """
    norm_key = normalize_key_chord(key)
    if not norm_key or not _key_is_plausible(norm_key):
        return {"success": False, "error": "Invalid key combination"}
    entry = {"key": norm_key, "description": description, "action": action} \
        if (description or action) else None
    try:
        write_user_bindings(unbinds_to_add=[norm_key],
                            keys_to_remove=[] if entry else [norm_key],
                            previous_entry=entry,
                            unbind_at_end=True)
    except ValueError as e:
        return {"success": False, "error": str(e)}
    reload_status = reload_hyprland()
    return {"success": True, "key": norm_key, "reload": reload_status,
            "reload_success": bool(reload_status.get("success", False))}


def enable_keybinding(key: str):
    """Re-enable a previously disabled default keybinding by removing its unbind."""
    norm_key = normalize_key_chord(key)
    if not norm_key or not _key_is_plausible(norm_key):
        return {"success": False, "error": "Invalid key combination"}
    try:
        # Enabling a chord with no unbind at all is an honest no-op success; any
        # unbind form that cannot be removed must be refused.
        content = _read_bounded_regular_file(USER_BINDINGS_PATH) or ""
        has_unbind = (norm_key in parse_user_bindings()["unbinds"]
                      or _file_mentions_unbind(content, norm_key))
        # Remove ONLY the hl.unbind: the chord's own o.bind lines (the user's
        # command, or one the plugin wrote) must come back to life.
        write_user_bindings(remove_unbinds_only=[norm_key], require_removal=has_unbind)
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
        try:
            if os.path.exists(path):
                mode = stat.S_IMODE(os.stat(path).st_mode)
                mode &= ~(stat.S_IWGRP | stat.S_IWOTH | stat.S_IRGRP | stat.S_IROTH
                          | stat.S_ISUID | stat.S_ISGID)
                os.fchmod(fd, mode | stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
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
            print(json.dumps({"success": False, "error": "Usage: set <key> <desc> <command> [action] [old_key] [old_desc] [old_command] [old_action] [keep_disabled]"}))
            sys.exit(1)
        key = sys.argv[2]
        desc = sys.argv[3]
        command = sys.argv[4]
        action = sys.argv[5] if len(sys.argv) > 5 else ""
        old_key = sys.argv[6] if len(sys.argv) > 6 else ""
        old_desc = sys.argv[7] if len(sys.argv) > 7 else ""
        old_command = sys.argv[8] if len(sys.argv) > 8 else ""
        old_action = sys.argv[9] if len(sys.argv) > 9 else ""
        # "1" when the row being edited is switched off: the save must not
        # silently switch it back on.
        keep_disabled = len(sys.argv) > 10 and sys.argv[10] in ("1", "true", "yes")
        try:
            res = set_keybinding(key, desc, command, action, old_key,
                                 old_description=old_desc, old_command=old_command,
                                 old_action=old_action, keep_disabled=keep_disabled)
        except Exception as e:  # noqa: BLE001 — always emit JSON for the UI
            res = {"success": False, "error": str(e)}
        print(json.dumps(res))

    elif cmd == "reset":
        if len(sys.argv) < 3:
            print(json.dumps({"success": False,
                              "error": "Usage: reset <key> [default_key] [description] [action]"}))
            sys.exit(1)
        key = sys.argv[2]
        def_key = sys.argv[3] if len(sys.argv) > 3 else ""
        row_desc = sys.argv[4] if len(sys.argv) > 4 else ""
        row_action = sys.argv[5] if len(sys.argv) > 5 else ""
        try:
            res = reset_keybinding(key, def_key, description=row_desc, action=row_action)
        except Exception as e:  # noqa: BLE001 — always emit JSON for the UI
            res = {"success": False, "error": str(e)}
        print(json.dumps(res))

    elif cmd == "disable":
        if len(sys.argv) < 3:
            print(json.dumps({"success": False,
                              "error": "Usage: disable <key> [description] [action]"}))
            sys.exit(1)
        key = sys.argv[2]
        row_desc = sys.argv[3] if len(sys.argv) > 3 else ""
        row_action = sys.argv[4] if len(sys.argv) > 4 else ""
        try:
            res = disable_keybinding(key, description=row_desc, action=row_action)
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
