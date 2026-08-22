#!/usr/bin/env python3
import unittest
import os
import sys
import tempfile

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


if __name__ == "__main__":
    unittest.main()
