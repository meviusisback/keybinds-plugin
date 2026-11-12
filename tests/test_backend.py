#!/usr/bin/env python3
import unittest
import os
import sys
import tempfile
import json

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
                action='hl.dsp.window.fullscreen({ mode = "fullscreen" })', old_key="SUPER + F")
            # Step 3: move "Full width" SUPER + ALT + F -> SUPER + F (the swap)
            keybinds_manager.set_keybinding(
                key="SUPER + F", description="Full width", command="",
                action='hl.dsp.window.fullscreen({ mode = "maximized" })', old_key="SUPER + ALT + F")

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
        parsed = keybinds_manager.parse_user_bindings()
        self.assertEqual(parsed["binds"], [])
        self.assertEqual(parsed["unbinds"], set())
        with self.assertRaises(ValueError):
            keybinds_manager.write_user_bindings(unbinds_to_add=["SUPER + Z"])


if __name__ == "__main__":
    unittest.main()