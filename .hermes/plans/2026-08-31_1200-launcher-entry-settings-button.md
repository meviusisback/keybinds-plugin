# Plan: Add Launcher Entry Button to Keybinds Panel Settings

## Goal
Add a persistent button in the app's header bar so users can add/remove the "Keybindings Manager" entry from the Omarchy launcher at any time — not just on first run.

## Context
PR #7 added auto-inject: on first open, a `ConfirmDialog` asks to add the launcher entry. This doesn't always work (timing, dismissals, file issues). The user wants a manual fallback in the app UI.

## Approach
1. **Backend**: Add a `menu-remove` CLI command to `keybinds_manager.py`
2. **QML**: Add a button in the header action row that checks status and toggles the entry
3. Keep the existing first-run dialog as-is (it still provides the initial prompt)

## Step-by-step

### Step 1 — Backend: `menu-remove` command
**File**: `backend/keybinds_manager.py`

Add `remove_menu_entry()` function after `install_menu_entry()`:
- Read the JSONC config file
- If `MENU_ENTRY_KEY` not in data → return `{"success": True, "message": "Entry not present."}`
- Delete the key, write back atomically
- Return `{"success": True, "message": "Removed Keybindings Manager from launcher."}`

Add `elif cmd == "menu-remove":` dispatch in `main()`.

### Step 2 — QML: Header button for launcher entry
**File**: `KeybindsPanel.qml`

Add a new button in the header `RowLayout` (after the "Edit bindings.lua" button):

```qml
Button {
  id: launcherEntryBtn
  text: launcherEntryInstalled ? "✓ In Launcher" : "📋 Add to Launcher"
  iconText: launcherEntryInstalled ? "✓" : "📋"
  tooltipText: launcherEntryInstalled
    ? "Remove Keybindings Manager from Omarchy launcher"
    : "Add Keybindings Manager to Omarchy launcher"
  horizontalPadding: Style.space(10)
  onClicked: {
    if (launcherEntryInstalled) {
      menuRemoveProc.running = true
    } else {
      menuSetupWriteProc.running = true
    }
  }
}
```

Add property:
```qml
property bool launcherEntryInstalled: false
```

Add a new `Process` for `menu-remove`:
```qml
Process {
  id: menuRemoveProc
  command: [...backend..., "menu-remove"]
  stdout: StdioCollector {
    waitForEnd: true
    onStreamFinished: {
      // parse JSON, show toast, update state
      root.launcherEntryInstalled = false
      root.showToast(parsed.message || "Removed from launcher.")
      refreshMenuProc.running = true
    }
  }
}
```

Update `menuSetupWriteProc.onStreamFinished` to set `launcherEntryInstalled = true` on success.

On `open()`, after the existing `menuSetupCheckProc`, also set `launcherEntryInstalled` from the result.

### Step 3 — Update README
Mention the manual launcher entry option in the Usage section.

## Files modified
- `backend/keybinds_manager.py` — add `remove_menu_entry()` + `menu-remove` dispatch
- `KeybindsPanel.qml` — add `launcherEntryInstalled` property, button, `menuRemoveProc`
- `README.md` — mention the manual option

## Verification
- `python3 -c "import py_compile; py_compile.compile('backend/keybinds_manager.py', doraise=True)"`
- `python3 backend/keybinds_manager.py menu-check` — returns `{"exists": true/false}`
- `python3 backend/keybinds_manager.py menu-install` — idempotent
- `python3 backend/keybinds_manager.py menu-remove` — removes entry
- `python3 -m unittest discover -s tests -p "test_*.py"` — existing tests pass
