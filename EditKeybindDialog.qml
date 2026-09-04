import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import qs.Commons
import qs.Ui

// Modal dialog for adding or modifying a keybinding
// Features a Smart Action Builder with:
//  - Installed Applications discovery & search (from .desktop files)
//  - Full Presets Catalog with categorized filtering
//  - Interactive Workspace Target Builder (1-10, Next, Prev, Scratchpad)
//  - Advanced Custom Command input with quick dispatcher helper chips
//  - Smart Suggested Free Shortcuts & live conflict detection
Item {
  id: root

  property bool opened: false
  property bool isEditing: false
  property var itemData: null
  property var allBindings: []
  property var catalog: []
  property var apps: []

  property string actionTitle: ""
  property string actionCommand: ""
  property string actionDispatcher: ""
  property string actionCategory: "Custom"
  property string actionKey: ""
  property string oldKey: ""
  property string actionType: "app" // "app" | "preset" | "workspace" | "custom"

  // Builder state
  property bool runInTerminal: false
  property string appSearchQuery: ""
  property string catalogSearchQuery: ""
  property string catalogCategoryFilter: "All"
  property string workspaceSubAction: "switch" // "switch" | "move" | "silent"

  readonly property var catalogCategories: [
    "All",
    "Window Management",
    "Workspaces",
    "Menus & System",
    "Applications",
    "Media & Audio"
  ]

  property color background: Color.background
  property color foreground: Color.foreground
  property color accent: Color.accent
  property color urgent: Color.urgent

  signal saved(string key, string description, string command, string action, string oldKey, bool overrideConflict)
  signal canceled()

  // Filtered Installed Applications
  readonly property var filteredApps: {
    var list = root.apps || []
    var q = (root.appSearchQuery || "").trim().toLowerCase()
    if (!q) return list.slice(0, 36)
    var out = []
    for (var i = 0; i < list.length; i++) {
      var a = list[i]
      if (a.name.toLowerCase().indexOf(q) !== -1 ||
          (a.comment && a.comment.toLowerCase().indexOf(q) !== -1) ||
          a.exec.toLowerCase().indexOf(q) !== -1) {
        out.push(a)
        if (out.length >= 36) break
      }
    }
    return out
  }

  // Filtered Catalog Presets
  readonly property var filteredPresets: {
    var list = root.catalog || []
    var cat = root.catalogCategoryFilter
    var q = (root.catalogSearchQuery || "").trim().toLowerCase()
    var out = []
    for (var i = 0; i < list.length; i++) {
      var p = list[i]
      if (cat !== "All" && p.category !== cat) continue
      if (q.length > 0 &&
          p.name.toLowerCase().indexOf(q) === -1 &&
          p.description.toLowerCase().indexOf(q) === -1 &&
          p.category.toLowerCase().indexOf(q) === -1) {
        continue
      }
      out.push(p)
      if (out.length >= 30) break
    }
    return out
  }

  // Smart Suggested Free Combinations
  readonly property var suggestedKeys: {
    var bound = {}
    if (root.allBindings) {
      for (var i = 0; i < root.allBindings.length; i++) {
        var b = root.allBindings[i]
        if (b && b.status !== "disabled" && b.key) {
          bound[b.key.toUpperCase()] = true
        }
      }
    }

    // Extract first letter of current action title
    var firstLetter = ""
    if (root.actionTitle && root.actionTitle.trim().length > 0) {
      var cleanTitle = root.actionTitle.trim()
      for (var j = 0; j < cleanTitle.length; j++) {
        var ch = cleanTitle.charAt(j).toUpperCase()
        if (ch >= 'A' && ch <= 'Z') {
          firstLetter = ch
          break
        }
      }
    }

    var candidates = []
    if (firstLetter) {
      candidates.push("SUPER + " + firstLetter)
      candidates.push("SUPER + ALT + " + firstLetter)
      candidates.push("SUPER + SHIFT + " + firstLetter)
      candidates.push("SUPER + CTRL + " + firstLetter)
    }

    var letters = ["B", "D", "E", "H", "M", "N", "R", "T", "U", "Y", "Z", "A", "C", "F", "K", "L", "P", "Q", "S", "W"]
    for (var k = 0; k < letters.length; k++) {
      var l = letters[k]
      if (l !== firstLetter) {
        candidates.push("SUPER + " + l)
        candidates.push("SUPER + ALT + " + l)
        candidates.push("SUPER + SHIFT + " + l)
        candidates.push("SUPER + CTRL + " + l)
      }
    }

    var suggestions = []
    for (var c = 0; c < candidates.length; c++) {
      var chord = candidates[c]
      if (!bound[chord.toUpperCase()] && suggestions.indexOf(chord) === -1) {
        if (chord.toUpperCase() !== root.actionKey.toUpperCase()) {
          suggestions.push(chord)
          if (suggestions.length >= 4) break
        }
      }
    }

    return suggestions
  }

  function openCreate(presetItem) {
    root.isEditing = false
    root.itemData = presetItem || null
    root.oldKey = ""
    root.runInTerminal = false
    root.appSearchQuery = ""
    root.catalogSearchQuery = ""
    root.catalogCategoryFilter = "All"
    root.workspaceSubAction = "switch"

    if (presetItem) {
      root.actionTitle = presetItem.name || presetItem.description || ""
      root.actionCommand = presetItem.command || ""
      root.actionDispatcher = presetItem.dispatcher || ""
      root.actionCategory = presetItem.category || "General"
      root.actionKey = presetItem.default_key || ""
      root.actionType = "preset"
    } else {
      root.actionTitle = ""
      root.actionCommand = ""
      root.actionDispatcher = ""
      root.actionCategory = "Applications"
      root.actionKey = ""
      root.actionType = "app"
    }

    keyRecorder.value = root.actionKey
    root.opened = true
  }

  function openEdit(bindingItem) {
    root.isEditing = true
    root.itemData = bindingItem || null
    root.actionTitle = (bindingItem && bindingItem.description) || ""
    root.actionCommand = (bindingItem && (bindingItem.command || bindingItem.action)) || ""
    root.actionDispatcher = (bindingItem && bindingItem.action) || ""
    root.actionCategory = (bindingItem && bindingItem.category) || "Custom"
    root.actionKey = (bindingItem && bindingItem.key) || ""
    root.oldKey = (bindingItem && bindingItem.key) || ""
    root.actionType = "custom"
    root.runInTerminal = false
    root.appSearchQuery = ""
    root.catalogSearchQuery = ""

    keyRecorder.value = root.actionKey
    root.opened = true
  }

  function selectApp(app) {
    root.actionTitle = app.name
    var cmd = app.exec
    if (root.runInTerminal && !app.terminal) {
      cmd = "ghostty -e " + cmd
    }
    root.actionCommand = cmd
    root.actionDispatcher = "exec"
    root.actionCategory = "Applications"
  }

  function selectPreset(preset) {
    root.actionTitle = preset.name
    root.actionCommand = preset.command
    root.actionDispatcher = preset.dispatcher || ""
    root.actionCategory = preset.category
    if (!keyRecorder.value && preset.default_key) {
      keyRecorder.value = preset.default_key
    }
  }

  function selectWorkspace(target) {
    var mode = root.workspaceSubAction
    var isSpecial = (target === "Scratchpad")
    var isRelative = (target === "e+1" || target === "e-1")

    if (mode === "switch") {
      if (isSpecial) {
        root.actionTitle = "Toggle Scratchpad"
        root.actionCommand = "hyprctl dispatch togglespecialworkspace scratchpad"
        root.actionDispatcher = 'hl.dsp.workspace.toggle_special("scratchpad")'
        if (!keyRecorder.value) keyRecorder.value = "SUPER + S"
      } else if (target === "e+1") {
        root.actionTitle = "Next Workspace"
        root.actionCommand = "hyprctl dispatch workspace e+1"
        root.actionDispatcher = 'hl.dsp.focus({ workspace = "e+1" })'
        if (!keyRecorder.value) keyRecorder.value = "SUPER + TAB"
      } else if (target === "e-1") {
        root.actionTitle = "Previous Workspace"
        root.actionCommand = "hyprctl dispatch workspace e-1"
        root.actionDispatcher = 'hl.dsp.focus({ workspace = "e-1" })'
        if (!keyRecorder.value) keyRecorder.value = "SUPER + SHIFT + TAB"
      } else {
        root.actionTitle = "Switch to Workspace " + target
        root.actionCommand = "hyprctl dispatch workspace " + target
        root.actionDispatcher = 'hl.dsp.focus({ workspace = "' + target + '" })'
        var num = parseInt(target) % 10
        if (!keyRecorder.value) keyRecorder.value = "SUPER + " + num
      }
    } else if (mode === "move") {
      if (isSpecial) {
        root.actionTitle = "Move Window to Scratchpad"
        root.actionCommand = "hyprctl dispatch movetoworkspace special:scratchpad"
        root.actionDispatcher = 'hl.dsp.window.move({ workspace = "special:scratchpad" })'
        if (!keyRecorder.value) keyRecorder.value = "SUPER + ALT + S"
      } else {
        root.actionTitle = "Move Window to Workspace " + target
        root.actionCommand = "hyprctl dispatch movetoworkspace " + target
        root.actionDispatcher = 'hl.dsp.window.move({ workspace = "' + target + '" })'
        var n = parseInt(target) % 10
        if (!keyRecorder.value) keyRecorder.value = "SUPER + SHIFT + " + n
      }
    } else if (mode === "silent") {
      if (isSpecial) {
        root.actionTitle = "Move Window to Scratchpad (Silent)"
        root.actionCommand = "hyprctl dispatch movetoworkspacesilent special:scratchpad"
        root.actionDispatcher = 'hl.dsp.window.move({ workspace = "special:scratchpad", follow = false })'
        if (!keyRecorder.value) keyRecorder.value = "SUPER + SHIFT + ALT + S"
      } else {
        root.actionTitle = "Move Window to Workspace " + target + " (Silent)"
        root.actionCommand = "hyprctl dispatch movetoworkspacesilent " + target
        root.actionDispatcher = 'hl.dsp.window.move({ workspace = "' + target + '", follow = false })'
        var ns = parseInt(target) % 10
        if (!keyRecorder.value) keyRecorder.value = "SUPER + SHIFT + ALT + " + ns
      }
    }
    root.actionCategory = "Workspaces"
  }

  function appendDispatcher(snippet) {
    if (!root.actionCommand || root.actionCommand.trim().length === 0) {
      root.actionCommand = snippet
    } else {
      root.actionCommand = root.actionCommand.trim() + " " + snippet
    }
  }

  function close() {
    root.opened = false
    keyRecorder.stopRecording()
    root.canceled()
  }

  visible: opened

  // Scrim backdrop
  Rectangle {
    anchors.fill: parent
    color: Util.alpha(root.background, 0.88)

    MouseArea {
      anchors.fill: parent
      onClicked: root.close()
    }

    // Modal Card
    BorderSurface {
      id: card
      width: Math.min(parent.width - Style.space(32), Style.space(700))
      implicitHeight: Math.min(parent.height - Style.space(32), cardLayout.implicitHeight + card.contentTopInset + card.contentBottomInset)
      anchors.centerIn: parent
      color: root.background
      borderSpec: Border.flat(Util.alpha(root.foreground, 0.25), 1)
      radius: Style.cornerRadius
      padding: Style.space(24)

      MouseArea {
        anchors.fill: parent
        onClicked: {} // Swallow clicks inside modal
      }

      ColumnLayout {
        id: cardLayout
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.topMargin: card.contentTopInset
        anchors.leftMargin: card.contentLeftInset
        anchors.rightMargin: card.contentRightInset
        spacing: Style.space(12)

        // 1. Header
        RowLayout {
          Layout.fillWidth: true
          spacing: Style.space(14)

          Text {
            textFormat: Text.PlainText
            text: ""
            color: "white"
            font.family: Style.font.family
            font.pixelSize: Style.font.title + 6
          }

          ColumnLayout {
            Layout.fillWidth: true
            spacing: Style.space(2)

            Text {
              textFormat: Text.PlainText
              text: root.isEditing ? "Edit Keybinding" : "Create Keybinding"
              color: root.foreground
              font.family: Style.font.family
              font.pixelSize: Style.font.title
              font.bold: true
            }

            Text {
              textFormat: Text.PlainText
              text: root.isEditing
                ? "Update the shortcut chord or command parameters below."
                : "Pick an application, preset action, workspace control, or custom command."
              color: Util.alpha(root.foreground, 0.6)
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
          }
        }

        PanelSeparator { Layout.fillWidth: true }

        // 2. Action Source Mode Selector (When creating new)
        RowLayout {
          visible: !root.isEditing
          Layout.fillWidth: true
          spacing: Style.space(6)

          Text {
            textFormat: Text.PlainText
            text: "Mode:"
            color: root.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            font.bold: true
          }

          Button {
            text: "🚀 Application"
            selected: root.actionType === "app"
            horizontalPadding: Style.space(12)
            verticalPadding: Style.space(4)
            onClicked: root.actionType = "app"
          }

          Button {
            text: "⚡ Presets"
            selected: root.actionType === "preset"
            horizontalPadding: Style.space(12)
            verticalPadding: Style.space(4)
            onClicked: root.actionType = "preset"
          }

          Button {
            text: "📑 Workspace"
            selected: root.actionType === "workspace"
            horizontalPadding: Style.space(12)
            verticalPadding: Style.space(4)
            onClicked: root.actionType = "workspace"
          }

          Button {
            text: "💻 Custom / Raw"
            selected: root.actionType === "custom"
            horizontalPadding: Style.space(12)
            verticalPadding: Style.space(4)
            onClicked: root.actionType = "custom"
          }
        }

        // 3. Dynamic Mode Content Section
        // --- MODE A: Installed Applications ---
        ColumnLayout {
          visible: !root.isEditing && root.actionType === "app"
          Layout.fillWidth: true
          spacing: Style.space(8)

          RowLayout {
            Layout.fillWidth: true
            spacing: Style.space(8)

            TextField {
              id: appSearchInput
              Layout.fillWidth: true
              placeholderText: "Search installed apps (e.g. Firefox, Spotify, Ghostty)..."
              text: root.appSearchQuery
              onTextChanged: root.appSearchQuery = text
            }

            BorderSurface {
              id: termToggle
              height: Style.space(32)
              width: termToggleRow.implicitWidth + Style.space(16)
              radius: Style.cornerRadius
              color: root.runInTerminal ? Util.alpha(root.accent, 0.22) : Util.alpha(root.foreground, 0.06)
              borderSpec: Border.flat(root.runInTerminal ? root.accent : Util.alpha(root.foreground, 0.2), 1)

              MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: {
                  root.runInTerminal = !root.runInTerminal
                  if (root.actionCommand) {
                    if (root.runInTerminal && !root.actionCommand.startsWith("ghostty -e ")) {
                      root.actionCommand = "ghostty -e " + root.actionCommand
                    } else if (!root.runInTerminal && root.actionCommand.startsWith("ghostty -e ")) {
                      root.actionCommand = root.actionCommand.replace("ghostty -e ", "")
                    }
                  }
                }
              }

              RowLayout {
                id: termToggleRow
                anchors.centerIn: parent
                spacing: Style.space(6)

                Text {
                  textFormat: Text.PlainText
                  text: root.runInTerminal ? "☑" : "☐"
                  color: root.runInTerminal ? root.accent : root.foreground
                  font.pixelSize: Style.font.caption
                }
                Text {
                  textFormat: Text.PlainText
                  text: "Terminal App"
                  color: root.runInTerminal ? root.accent : root.foreground
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                  font.bold: root.runInTerminal
                }
              }
            }
          }

          // Scrollable App Chips
          ScrollView {
            id: appScrollView
            Layout.fillWidth: true
            Layout.preferredHeight: Style.space(110)
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

            Flow {
              width: appScrollView.availableWidth
              spacing: Style.space(6)

              Repeater {
                model: root.filteredApps

                BorderSurface {
                  id: appChip
                  required property var modelData
                  height: Style.space(26)
                  width: appChipRow.implicitWidth + Style.space(16)
                  radius: Style.cornerRadius
                  color: root.actionTitle === modelData.name
                    ? Util.alpha(root.accent, 0.25)
                    : (appMouse.containsMouse ? Util.alpha(root.foreground, 0.12) : Util.alpha(root.foreground, 0.05))
                  borderSpec: Border.flat(
                    root.actionTitle === modelData.name ? root.accent : Util.alpha(root.foreground, 0.18),
                    1
                  )

                  MouseArea {
                    id: appMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.selectApp(appChip.modelData)
                  }

                  RowLayout {
                    id: appChipRow
                    anchors.centerIn: parent
                    spacing: Style.space(6)

                    Text {
                      textFormat: Text.PlainText
                      text: "🚀"
                      font.pixelSize: Style.font.caption - 2
                    }

                    Text {
                      textFormat: Text.PlainText
                      text: appChip.modelData.name
                      color: root.actionTitle === appChip.modelData.name ? root.accent : root.foreground
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.bold: root.actionTitle === appChip.modelData.name
                    }
                  }
                }
              }
            }
          }
        }

        // --- MODE B: Presets Catalog ---
        ColumnLayout {
          visible: !root.isEditing && root.actionType === "preset"
          Layout.fillWidth: true
          spacing: Style.space(8)

          RowLayout {
            Layout.fillWidth: true
            spacing: Style.space(8)

            TextField {
              id: presetSearchInput
              Layout.fillWidth: true
              placeholderText: "Search catalog presets (e.g. Fullscreen, Close, Focus, Volume)..."
              text: root.catalogSearchQuery
              onTextChanged: root.catalogSearchQuery = text
            }
          }

          // Category Filter Pills
          Flow {
            Layout.fillWidth: true
            spacing: Style.space(6)

            Repeater {
              model: root.catalogCategories

              BorderSurface {
                id: catPill
                required property string modelData
                height: Style.space(22)
                width: catPillText.implicitWidth + Style.space(14)
                radius: Style.cornerRadius
                color: root.catalogCategoryFilter === modelData
                  ? Util.alpha(root.accent, 0.25)
                  : (catMouse.containsMouse ? Util.alpha(root.foreground, 0.1) : Util.alpha(root.foreground, 0.04))
                borderSpec: Border.flat(
                  root.catalogCategoryFilter === modelData ? root.accent : Util.alpha(root.foreground, 0.15),
                  1
                )

                MouseArea {
                  id: catMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.catalogCategoryFilter = catPill.modelData
                }

                Text {
                  id: catPillText
                  anchors.centerIn: parent
                  text: catPill.modelData
                  color: root.catalogCategoryFilter === catPill.modelData ? root.accent : root.foreground
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption - 1
                  font.bold: root.catalogCategoryFilter === catPill.modelData
                }
              }
            }
          }

          // Scrollable Preset Chips
          ScrollView {
            id: presetScrollView
            Layout.fillWidth: true
            Layout.preferredHeight: Style.space(110)
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

            Flow {
              width: presetScrollView.availableWidth
              spacing: Style.space(6)

              Repeater {
                model: root.filteredPresets

                BorderSurface {
                  id: presetChip
                  required property var modelData
                  height: Style.space(26)
                  width: presetChipText.implicitWidth + Style.space(16)
                  radius: Style.cornerRadius
                  color: root.actionTitle === modelData.name
                    ? Util.alpha(root.accent, 0.25)
                    : (presetMouse.containsMouse ? Util.alpha(root.foreground, 0.12) : Util.alpha(root.foreground, 0.05))
                  borderSpec: Border.flat(
                    root.actionTitle === modelData.name ? root.accent : Util.alpha(root.foreground, 0.18),
                    1
                  )

                  MouseArea {
                    id: presetMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.selectPreset(presetChip.modelData)
                  }

                  Text {
                    id: presetChipText
                    anchors.centerIn: parent
                    text: presetChip.modelData.name
                    color: root.actionTitle === presetChip.modelData.name ? root.accent : root.foreground
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                    font.bold: root.actionTitle === presetChip.modelData.name
                  }
                }
              }
            }
          }
        }

        // --- MODE C: Workspace Action Builder ---
        ColumnLayout {
          visible: !root.isEditing && root.actionType === "workspace"
          Layout.fillWidth: true
          spacing: Style.space(8)

          // Sub-action toggle
          RowLayout {
            spacing: Style.space(8)

            Text {
              textFormat: Text.PlainText
              text: "Operation:"
              color: root.foreground
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              font.bold: true
            }

            Button {
              text: "🎯 Switch Focus"
              selected: root.workspaceSubAction === "switch"
              horizontalPadding: Style.space(12)
              verticalPadding: Style.space(4)
              onClicked: root.workspaceSubAction = "switch"
            }

            Button {
              text: "🪟 Move Window"
              selected: root.workspaceSubAction === "move"
              horizontalPadding: Style.space(12)
              verticalPadding: Style.space(4)
              onClicked: root.workspaceSubAction = "move"
            }

            Button {
              text: "🤫 Move Silently"
              selected: root.workspaceSubAction === "silent"
              horizontalPadding: Style.space(12)
              verticalPadding: Style.space(4)
              onClicked: root.workspaceSubAction = "silent"
            }
          }

          // Target workspace chips
          Text {
            textFormat: Text.PlainText
            text: "Target Workspace (Click to apply):"
            color: Util.alpha(root.foreground, 0.75)
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            font.bold: true
          }

          Flow {
            Layout.fillWidth: true
            spacing: Style.space(6)

            Repeater {
              model: ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "e+1", "e-1", "Scratchpad"]

              BorderSurface {
                id: wsChip
                required property string modelData
                height: Style.space(26)
                width: wsChipText.implicitWidth + Style.space(16)
                radius: Style.cornerRadius
                color: wsMouse.containsMouse ? Util.alpha(root.accent, 0.22) : Util.alpha(root.foreground, 0.06)
                borderSpec: Border.flat(wsMouse.containsMouse ? root.accent : Util.alpha(root.foreground, 0.2), 1)

                MouseArea {
                  id: wsMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.selectWorkspace(wsChip.modelData)
                }

                Text {
                  id: wsChipText
                  anchors.centerIn: parent
                  text: wsChip.modelData === "e+1" ? "Next (e+1)" : (wsChip.modelData === "e-1" ? "Prev (e-1)" : (wsChip.modelData === "Scratchpad" ? "📌 Scratchpad" : "WS " + wsChip.modelData))
                  color: root.foreground
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                  font.bold: true
                }
              }
            }
          }
        }

        // 4. Details Fields (Action Name & Command)
        ColumnLayout {
          Layout.fillWidth: true
          spacing: Style.space(4)

          Text {
            textFormat: Text.PlainText
            text: "Action Name / Description:"
            color: root.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            font.bold: true
          }

          TextField {
            id: descInput
            Layout.fillWidth: true
            text: root.actionTitle
            placeholderText: "e.g. Launch Terminal, Toggle Fullscreen, My Script"
            onTextChanged: root.actionTitle = text
          }
        }

        ColumnLayout {
          Layout.fillWidth: true
          spacing: Style.space(4)

          RowLayout {
            Layout.fillWidth: true
            Text {
              textFormat: Text.PlainText
              text: "Command / Dispatcher:"
              color: root.foreground
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              font.bold: true
            }

            Item { Layout.fillWidth: true }

            Text {
              textFormat: Text.PlainText
              visible: root.actionDispatcher.length > 0 && root.actionDispatcher !== "exec"
              text: "Lua Dispatcher: " + root.actionDispatcher
              color: root.accent
              font.family: Style.font.family
              font.pixelSize: Style.font.caption - 2
            }
          }

          TextField {
            id: cmdInput
            Layout.fillWidth: true
            text: root.actionCommand
            placeholderText: "e.g. ghostty, hyprctl dispatch killactive, omarchy capture screenshot"
            onTextChanged: {
              root.actionCommand = text
              if (root.actionType === "custom") {
                root.actionDispatcher = ""
              }
            }
          }

          // Quick Dispatcher Insert Chips (For Custom / Raw mode or Editing)
          Flow {
            visible: root.actionType === "custom" || root.isEditing
            Layout.fillWidth: true
            spacing: Style.space(6)

            Repeater {
              model: [
                { label: "+ exec", val: "ghostty -e " },
                { label: "+ killactive", val: "hyprctl dispatch killactive" },
                { label: "+ togglefloating", val: "hyprctl dispatch togglefloating" },
                { label: "+ fullscreen", val: "hyprctl dispatch fullscreen 0" },
                { label: "+ movefocus l", val: "hyprctl dispatch movefocus l" },
                { label: "+ swapwindow l", val: "hyprctl dispatch swapwindow l" },
                { label: "+ workspace", val: "hyprctl dispatch workspace " }
              ]

              BorderSurface {
                id: insertChip
                required property var modelData
                height: Style.space(22)
                width: insertChipText.implicitWidth + Style.space(12)
                radius: Style.cornerRadius
                color: insertMouse.containsMouse ? Util.alpha(root.accent, 0.2) : Util.alpha(root.foreground, 0.05)
                borderSpec: Border.flat(insertMouse.containsMouse ? root.accent : Util.alpha(root.foreground, 0.15), 1)

                MouseArea {
                  id: insertMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.appendDispatcher(insertChip.modelData.val)
                }

                Text {
                  id: insertChipText
                  anchors.centerIn: parent
                  text: insertChip.modelData.label
                  color: root.foreground
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption - 1
                }
              }
            }
          }
        }

        // 5. Interactive Shortcut Key Input & Suggestions
        ColumnLayout {
          Layout.fillWidth: true
          spacing: Style.space(6)

          Text {
            textFormat: Text.PlainText
            text: "Keyboard Shortcut:"
            color: root.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            font.bold: true
          }

          KeyRecorder {
            id: keyRecorder
            Layout.fillWidth: true
            allBindings: root.allBindings
            currentDescription: root.isEditing ? root.actionTitle : ""
            onKeyChanged: function(newK) {
              root.actionKey = newK
            }
          }

          // Smart Suggested Free Combinations Card
          ColumnLayout {
            visible: root.suggestedKeys.length > 0
            Layout.fillWidth: true
            spacing: Style.space(4)

            RowLayout {
              spacing: Style.space(6)

              Text {
                textFormat: Text.PlainText
                text: "💡"
                font.pixelSize: Style.font.caption
              }

              Text {
                textFormat: Text.PlainText
                text: "Suggested Free Shortcuts (Click to pick):"
                color: Util.alpha(root.foreground, 0.75)
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
                font.bold: true
              }
            }

            Flow {
              Layout.fillWidth: true
              spacing: Style.space(6)

              Repeater {
                model: root.suggestedKeys

                BorderSurface {
                  id: suggestChip
                  required property string modelData
                  height: Style.space(26)
                  width: suggestChipRow.implicitWidth + Style.space(16)
                  radius: Style.cornerRadius
                  color: suggestMouse.containsMouse ? Util.alpha(root.accent, 0.24) : Util.alpha(root.accent, 0.10)
                  borderSpec: Border.flat(
                    suggestMouse.containsMouse ? root.accent : Util.alpha(root.accent, 0.4),
                    1
                  )

                  MouseArea {
                    id: suggestMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                      keyRecorder.value = suggestChip.modelData
                      root.actionKey = suggestChip.modelData
                      keyRecorder.stopRecording()
                    }
                  }

                  RowLayout {
                    id: suggestChipRow
                    anchors.centerIn: parent
                    spacing: Style.space(4)

                    Text {
                      textFormat: Text.PlainText
                      text: "✨"
                      font.pixelSize: Style.font.caption - 2
                    }

                    Text {
                      textFormat: Text.PlainText
                      text: suggestChip.modelData
                      color: root.accent
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.bold: true
                    }
                  }
                }
              }
            }
          }
        }

        PanelSeparator { Layout.fillWidth: true }

        // 6. Modal Footer Buttons
        RowLayout {
          Layout.fillWidth: true
          spacing: Style.space(12)

          Item { Layout.fillWidth: true }

          Button {
            text: "Cancel"
            horizontalPadding: Style.space(18)
            verticalPadding: Style.space(6)
            onClicked: root.close()
          }

          Button {
            text: keyRecorder.hasConflict ? "Override & Save" : "Save & Apply"
            accent: keyRecorder.hasConflict ? root.urgent : root.accent
            selected: true
            horizontalPadding: Style.space(22)
            verticalPadding: Style.space(6)
            enabled: root.actionTitle.length > 0 && root.actionKey.length > 0
            onClicked: {
              root.saved(
                root.actionKey,
                root.actionTitle,
                root.actionCommand,
                root.actionDispatcher || "",
                root.oldKey,
                true
              )
              root.opened = false
            }
          }
        }
      }
    }
  }
}
