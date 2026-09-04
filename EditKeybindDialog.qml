import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import qs.Commons
import qs.Ui

// Modal dialog for adding or modifying a keybinding
// Features a Smart Action Builder with:
//  - 🚀 Launch App: Searchable list of installed apps (.desktop) with 1-click select
//  - 🪟 Window & Layout: 1-click controls for close, fullscreen, float, split, and focus/swap directions
//  - 📑 Workspaces: 1-click generator for Switch (1-10, Next, Prev, Scratchpad) and Move
//  - ⚡ Media & System: 1-click controls for volume, brightness, media, screenshots, lock
//  - 💻 Custom Command: Manual text editor with quick syntax insert chips (+exec, +killactive...)
//  - Smart Suggested Free Shortcuts & live collision detection
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
  property string actionCategory: "Applications"
  property string actionKey: ""
  property string oldKey: ""
  property string actionType: "app" // "app" | "window" | "workspace" | "system" | "custom"

  // Builder state
  property bool runInTerminal: false
  property string appSearchQuery: ""
  property string workspaceSubAction: "switch" // "switch" | "move" | "silent"

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
    root.workspaceSubAction = "switch"

    if (presetItem) {
      root.actionTitle = presetItem.name || presetItem.description || ""
      root.actionCommand = presetItem.command || ""
      root.actionDispatcher = presetItem.dispatcher || ""
      root.actionCategory = presetItem.category || "General"
      root.actionKey = presetItem.default_key || ""

      if (presetItem.category === "Workspaces") {
        root.actionType = "workspace"
      } else if (presetItem.category === "Window Management") {
        root.actionType = "window"
      } else if (presetItem.category === "Media & Audio" || presetItem.category === "Menus & System") {
        root.actionType = "system"
      } else {
        root.actionType = "app"
      }
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

  function selectWindowAction(name, cmd, dsp, defKey) {
    root.actionTitle = name
    root.actionCommand = cmd
    root.actionDispatcher = dsp
    root.actionCategory = "Window Management"
    if (!keyRecorder.value && defKey) keyRecorder.value = defKey
  }

  function selectDirectional(mode, dir, defKey) {
    var dirName = dir === "l" ? "Left" : (dir === "r" ? "Right" : (dir === "u" ? "Up" : "Down"))
    if (mode === "focus") {
      root.actionTitle = "Focus Window " + dirName
      root.actionCommand = "hyprctl dispatch movefocus " + dir
      root.actionDispatcher = "hl.dsp.focus." + (dir === "l" ? "left()" : (dir === "r" ? "right()" : (dir === "u" ? "up()" : "down()")))
    } else {
      root.actionTitle = "Swap Window " + dirName
      root.actionCommand = "hyprctl dispatch swapwindow " + dir
      root.actionDispatcher = 'hl.dsp.window.swap({ direction = "' + dir + '" })'
    }
    root.actionCategory = "Window Management"
    if (!keyRecorder.value && defKey) keyRecorder.value = defKey
  }

  function selectWorkspace(target) {
    var mode = root.workspaceSubAction
    var isSpecial = (target === "Scratchpad")

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

  function selectSystemAction(name, cmd, defKey) {
    root.actionTitle = name
    root.actionCommand = cmd
    root.actionDispatcher = "exec"
    root.actionCategory = "Media & Audio"
    if (!keyRecorder.value && defKey) keyRecorder.value = defKey
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
                : "Select an action below — command & dispatcher are configured automatically."
              color: Util.alpha(root.foreground, 0.6)
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
          }
        }

        PanelSeparator { Layout.fillWidth: true }

        // 2. Action Category Tabs (When creating new)
        RowLayout {
          visible: !root.isEditing
          Layout.fillWidth: true
          spacing: Style.space(6)

          Button {
            text: "🚀 Launch App"
            selected: root.actionType === "app"
            horizontalPadding: Style.space(10)
            verticalPadding: Style.space(4)
            onClicked: root.actionType = "app"
          }

          Button {
            text: "🪟 Window Control"
            selected: root.actionType === "window"
            horizontalPadding: Style.space(10)
            verticalPadding: Style.space(4)
            onClicked: root.actionType = "window"
          }

          Button {
            text: "📑 Workspace"
            selected: root.actionType === "workspace"
            horizontalPadding: Style.space(10)
            verticalPadding: Style.space(4)
            onClicked: root.actionType = "workspace"
          }

          Button {
            text: "⚡ System & Audio"
            selected: root.actionType === "system"
            horizontalPadding: Style.space(10)
            verticalPadding: Style.space(4)
            onClicked: root.actionType = "system"
          }

          Button {
            text: "💻 Custom Script"
            selected: root.actionType === "custom"
            horizontalPadding: Style.space(10)
            verticalPadding: Style.space(4)
            onClicked: root.actionType = "custom"
          }
        }

        // 3. Structured Selection Panels (NO raw command typing needed!)

        // --- PANEL 1: Launch Application ---
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

        // --- PANEL 2: Window & Layout Controls ---
        ColumnLayout {
          visible: !root.isEditing && root.actionType === "window"
          Layout.fillWidth: true
          spacing: Style.space(8)

          Text {
            textFormat: Text.PlainText
            text: "Window Actions (Click to select):"
            color: Util.alpha(root.foreground, 0.75)
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            font.bold: true
          }

          Flow {
            Layout.fillWidth: true
            spacing: Style.space(6)

            Repeater {
              model: [
                { name: "Close Window", cmd: "hyprctl dispatch killactive", dsp: "hl.dsp.window.close()", key: "SUPER + W" },
                { name: "Toggle Fullscreen", cmd: "hyprctl dispatch fullscreen", dsp: "hl.dsp.window.fullscreen()", key: "SUPER + F" },
                { name: "Toggle Floating", cmd: "hyprctl dispatch togglefloating", dsp: "hl.dsp.window.float({ action = \"toggle\" })", key: "SUPER + T" },
                { name: "Toggle Split", cmd: "hyprctl dispatch layoutmsg togglesplit", dsp: "hl.dsp.layout(\"togglesplit\")", key: "SUPER + J" },
                { name: "Pop Window Out (Pin)", cmd: "omarchy-hyprland-window-pop", dsp: "exec", key: "SUPER + O" },
                { name: "Close All Windows", cmd: "omarchy-hyprland-window-close-all", dsp: "exec", key: "CTRL + ALT + DELETE" },
                { name: "Toggle Transparency", cmd: "omarchy-hyprland-window-transparency-toggle", dsp: "exec", key: "SUPER + BACKSPACE" },
                { name: "Toggle Gaps", cmd: "omarchy-hyprland-window-gaps-toggle", dsp: "exec", key: "SUPER + SHIFT + BACKSPACE" }
              ]

              BorderSurface {
                id: winChip
                required property var modelData
                height: Style.space(26)
                width: winChipText.implicitWidth + Style.space(16)
                radius: Style.cornerRadius
                color: root.actionTitle === modelData.name
                  ? Util.alpha(root.accent, 0.25)
                  : (winMouse.containsMouse ? Util.alpha(root.foreground, 0.12) : Util.alpha(root.foreground, 0.05))
                borderSpec: Border.flat(root.actionTitle === modelData.name ? root.accent : Util.alpha(root.foreground, 0.18), 1)

                MouseArea {
                  id: winMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.selectWindowAction(winChip.modelData.name, winChip.modelData.cmd, winChip.modelData.dsp, winChip.modelData.key)
                }

                Text {
                  id: winChipText
                  anchors.centerIn: parent
                  text: winChip.modelData.name
                  color: root.actionTitle === winChip.modelData.name ? root.accent : root.foreground
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                  font.bold: root.actionTitle === winChip.modelData.name
                }
              }
            }
          }

          Text {
            textFormat: Text.PlainText
            text: "Move Focus / Swap Window Direction:"
            color: Util.alpha(root.foreground, 0.75)
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            font.bold: true
          }

          RowLayout {
            spacing: Style.space(6)

            Button {
              text: "Focus ⬅️ Left"
              horizontalPadding: Style.space(10)
              verticalPadding: Style.space(3)
              onClicked: root.selectDirectional("focus", "l", "SUPER + LEFT")
            }
            Button {
              text: "Focus ➡️ Right"
              horizontalPadding: Style.space(10)
              verticalPadding: Style.space(3)
              onClicked: root.selectDirectional("focus", "r", "SUPER + RIGHT")
            }
            Button {
              text: "Focus ⬆️ Up"
              horizontalPadding: Style.space(10)
              verticalPadding: Style.space(3)
              onClicked: root.selectDirectional("focus", "u", "SUPER + UP")
            }
            Button {
              text: "Focus ⬇️ Down"
              horizontalPadding: Style.space(10)
              verticalPadding: Style.space(3)
              onClicked: root.selectDirectional("focus", "d", "SUPER + DOWN")
            }
          }

          RowLayout {
            spacing: Style.space(6)

            Button {
              text: "Swap ⬅️ Left"
              horizontalPadding: Style.space(10)
              verticalPadding: Style.space(3)
              onClicked: root.selectDirectional("swap", "l", "SUPER + SHIFT + LEFT")
            }
            Button {
              text: "Swap ➡️ Right"
              horizontalPadding: Style.space(10)
              verticalPadding: Style.space(3)
              onClicked: root.selectDirectional("swap", "r", "SUPER + SHIFT + RIGHT")
            }
            Button {
              text: "Swap ⬆️ Up"
              horizontalPadding: Style.space(10)
              verticalPadding: Style.space(3)
              onClicked: root.selectDirectional("swap", "u", "SUPER + SHIFT + UP")
            }
            Button {
              text: "Swap ⬇️ Down"
              horizontalPadding: Style.space(10)
              verticalPadding: Style.space(3)
              onClicked: root.selectDirectional("swap", "d", "SUPER + SHIFT + DOWN")
            }
          }
        }

        // --- PANEL 3: Workspace Builder ---
        ColumnLayout {
          visible: !root.isEditing && root.actionType === "workspace"
          Layout.fillWidth: true
          spacing: Style.space(8)

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

        // --- PANEL 4: Media & System Controls ---
        ColumnLayout {
          visible: !root.isEditing && root.actionType === "system"
          Layout.fillWidth: true
          spacing: Style.space(8)

          Text {
            textFormat: Text.PlainText
            text: "Common System Shortcuts (Click to select):"
            color: Util.alpha(root.foreground, 0.75)
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            font.bold: true
          }

          Flow {
            Layout.fillWidth: true
            spacing: Style.space(6)

            Repeater {
              model: [
                { name: "Volume Up", cmd: "omarchy-audio-output-volume raise", key: "XF86AudioRaiseVolume" },
                { name: "Volume Down", cmd: "omarchy-audio-output-volume lower", key: "XF86AudioLowerVolume" },
                { name: "Mute Audio", cmd: "omarchy-audio-output-volume mute-toggle", key: "XF86AudioMute" },
                { name: "Mute Microphone", cmd: "omarchy-audio-input-mute", key: "XF86AudioMicMute" },
                { name: "Play / Pause Media", cmd: "playerctl play-pause", key: "XF86AudioPlay" },
                { name: "Next Track", cmd: "playerctl next", key: "XF86AudioNext" },
                { name: "Previous Track", cmd: "playerctl previous", key: "XF86AudioPrev" },
                { name: "Raise Brightness", cmd: "brightnessctl set +5%", key: "XF86MonBrightnessUp" },
                { name: "Lower Brightness", cmd: "brightnessctl set 5%-", key: "XF86MonBrightnessDown" },
                { name: "Capture Screenshot", cmd: "omarchy-capture-screenshot", key: "PRINT" },
                { name: "Screenrecord", cmd: "omarchy-capture-screenrecording", key: "ALT + PRINT" },
                { name: "Color Picker", cmd: "pkill hyprpicker || hyprpicker -a", key: "SUPER + PRINT" },
                { name: "Lock Screen", cmd: "omarchy-system-lock", key: "SUPER + CTRL + L" },
                { name: "Clipboard Manager", cmd: "omarchy-shell shell toggle omarchy.clipboard", key: "SUPER + CTRL + V" },
                { name: "Emoji Picker", cmd: "omarchy-shell shell toggle omarchy.emojis", key: "SUPER + CTRL + E" },
                { name: "Omarchy Menu", cmd: "omarchy-menu toggle", key: "SUPER + SPACE" }
              ]

              BorderSurface {
                id: sysChip
                required property var modelData
                height: Style.space(26)
                width: sysChipText.implicitWidth + Style.space(16)
                radius: Style.cornerRadius
                color: root.actionTitle === modelData.name
                  ? Util.alpha(root.accent, 0.25)
                  : (sysMouse.containsMouse ? Util.alpha(root.foreground, 0.12) : Util.alpha(root.foreground, 0.05))
                borderSpec: Border.flat(root.actionTitle === modelData.name ? root.accent : Util.alpha(root.foreground, 0.18), 1)

                MouseArea {
                  id: sysMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.selectSystemAction(sysChip.modelData.name, sysChip.modelData.cmd, sysChip.modelData.key)
                }

                Text {
                  id: sysChipText
                  anchors.centerIn: parent
                  text: sysChip.modelData.name
                  color: root.actionTitle === sysChip.modelData.name ? root.accent : root.foreground
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                  font.bold: root.actionTitle === sysChip.modelData.name
                }
              }
            }
          }
        }

        // --- AUTOMATED ACTION SUMMARY BADGE (Shown for structured modes) ---
        BorderSurface {
          visible: root.actionType !== "custom" && root.actionTitle.length > 0
          Layout.fillWidth: true
          height: Style.space(40)
          radius: Style.cornerRadius
          color: Util.alpha(root.accent, 0.12)
          borderSpec: Border.flat(root.accent, 1)

          RowLayout {
            anchors.fill: parent
            anchors.leftMargin: Style.space(12)
            anchors.rightMargin: Style.space(12)
            spacing: Style.space(8)

            Text {
              textFormat: Text.PlainText
              text: "✓"
              color: root.accent
              font.bold: true
              font.pixelSize: Style.font.body
            }

            ColumnLayout {
              Layout.fillWidth: true
              spacing: 0

              Text {
                textFormat: Text.PlainText
                text: root.actionTitle
                color: root.foreground
                font.family: Style.font.family
                font.pixelSize: Style.font.body - 1
                font.bold: true
                elide: Text.ElideRight
              }

              Text {
                textFormat: Text.PlainText
                text: "Configured: " + (root.actionDispatcher ? root.actionDispatcher : root.actionCommand)
                color: Util.alpha(root.foreground, 0.65)
                font.family: Style.font.family
                font.pixelSize: Style.font.caption - 2
                elide: Text.ElideRight
              }
            }
          }
        }

        // --- PANEL 5: Custom Script & Manual Command (ONLY in custom mode or when editing) ---
        ColumnLayout {
          visible: root.actionType === "custom" || root.isEditing
          Layout.fillWidth: true
          spacing: Style.space(6)

          ColumnLayout {
            Layout.fillWidth: true
            spacing: Style.space(3)

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
              placeholderText: "e.g. My Custom Script"
              onTextChanged: root.actionTitle = text
            }
          }

          ColumnLayout {
            Layout.fillWidth: true
            spacing: Style.space(3)

            Text {
              textFormat: Text.PlainText
              text: "Command / Dispatcher:"
              color: root.foreground
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              font.bold: true
            }

            TextField {
              id: cmdInput
              Layout.fillWidth: true
              text: root.actionCommand
              placeholderText: "e.g. ghostty -e btop, hyprctl dispatch killactive"
              onTextChanged: {
                root.actionCommand = text
                if (root.actionType === "custom") {
                  root.actionDispatcher = ""
                }
              }
            }

            // Quick Dispatcher Helper Chips
            Flow {
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
        }

        // 4. Interactive Shortcut Key Input & Suggestions
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

        // 5. Modal Footer Buttons
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
