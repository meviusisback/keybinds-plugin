import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import qs.Commons
import qs.Ui

// Modal dialog for adding or modifying a keybinding
// Features an ergonomic, polished Smart Action Builder:
//  - Unified Segmented Tab Bar (Apps, Window, Workspace, System, Custom)
//  - 🚀 Launch App: Searchable list of installed apps (.desktop) with text truncation & terminal toggle
//  - 🪟 Window Control: Grouped window actions + interactive Directional D-Pad (Focus & Swap)
//  - 📑 Workspaces: Symmetrical Number Bar (1-10) + Relative/Special chips
//  - ⚡ System & Audio: Categorized sub-sections (Audio, Display, Menus)
//  - 💻 Custom Script: Freeform editor with quick syntax chips & tip box
//  - Visual Selection Highlights (accent glow + checkmarks on all active chips)
//  - Dedicated Configured Action confirmation banner
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
  property string actionCategory: "Applications"
  property string actionKey: ""
  property string oldKey: ""
  property string actionType: "app" // "app" | "window" | "workspace" | "system" | "custom"

  // Builder state & selection tracking
  property string selectedActionId: ""
  property bool runInTerminal: false
  property string appSearchQuery: ""
  property string windowNavMode: "focus" // "focus" | "swap"
  property string workspaceSubAction: "switch" // "switch" | "move" | "silent"
  property string workspaceTarget: ""

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
    root.workspaceTarget = ""
    root.windowNavMode = "focus"

    if (presetItem) {
      root.actionTitle = presetItem.name || presetItem.description || ""
      root.actionCommand = presetItem.command || ""
      root.actionDispatcher = presetItem.dispatcher || ""
      root.actionCategory = presetItem.category || "General"
      root.actionKey = presetItem.default_key || ""
      root.selectedActionId = presetItem.id || presetItem.name

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
      root.selectedActionId = ""
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
    root.selectedActionId = "custom"
    root.runInTerminal = false
    root.appSearchQuery = ""

    keyRecorder.value = root.actionKey
    root.opened = true
  }

  function selectApp(app) {
    if (!app) return
    root.selectedActionId = "app:" + app.name
    root.actionTitle = app.name
    var needsTerminal = app.terminal || root.runInTerminal
    root.runInTerminal = needsTerminal
    var cmd = app.exec || ""
    if (needsTerminal && !cmd.startsWith("ghostty -e ")) {
      cmd = "ghostty -e " + cmd
    }
    root.actionCommand = cmd
    root.actionDispatcher = "exec"
    root.actionCategory = "Applications"
  }

  function selectWindowAction(name, cmd, dsp, defKey) {
    root.selectedActionId = "win:" + name
    root.actionTitle = name
    root.actionCommand = cmd
    root.actionDispatcher = dsp
    root.actionCategory = "Window Management"
    if (!keyRecorder.value && defKey) {
      keyRecorder.value = defKey
      root.actionKey = defKey
    }
  }

  function selectDirectional(mode, dir, defKey) {
    var dirName = dir === "l" ? "Left" : (dir === "r" ? "Right" : (dir === "u" ? "Up" : "Down"))
    root.selectedActionId = "dir:" + mode + ":" + dir
    if (mode === "focus") {
      root.actionTitle = "Focus Window " + dirName
      root.actionCommand = "hyprctl dispatch movefocus " + dir
      root.actionDispatcher = 'hl.dsp.focus({ direction = "' + dir + '" })'
    } else {
      root.actionTitle = "Swap Window " + dirName
      root.actionCommand = "hyprctl dispatch swapwindow " + dir
      root.actionDispatcher = 'hl.dsp.window.swap({ direction = "' + dir + '" })'
    }
    root.actionCategory = "Window Management"
    if (!keyRecorder.value && defKey) {
      keyRecorder.value = defKey
      root.actionKey = defKey
    }
  }

  function selectWorkspace(target) {
    var mode = root.workspaceSubAction
    var isSpecial = (target === "Scratchpad")
    root.workspaceTarget = target
    root.selectedActionId = "ws:" + mode + ":" + target

    if (mode === "switch") {
      if (isSpecial) {
        root.actionTitle = "Toggle Scratchpad"
        root.actionCommand = "hyprctl dispatch togglespecialworkspace scratchpad"
        root.actionDispatcher = 'hl.dsp.workspace.toggle_special("scratchpad")'
        if (!keyRecorder.value) { keyRecorder.value = "SUPER + S"; root.actionKey = "SUPER + S" }
      } else if (target === "e+1") {
        root.actionTitle = "Next Workspace"
        root.actionCommand = "hyprctl dispatch workspace e+1"
        root.actionDispatcher = 'hl.dsp.focus({ workspace = "e+1" })'
        if (!keyRecorder.value) { keyRecorder.value = "SUPER + TAB"; root.actionKey = "SUPER + TAB" }
      } else if (target === "e-1") {
        root.actionTitle = "Previous Workspace"
        root.actionCommand = "hyprctl dispatch workspace e-1"
        root.actionDispatcher = 'hl.dsp.focus({ workspace = "e-1" })'
        if (!keyRecorder.value) { keyRecorder.value = "SUPER + SHIFT + TAB"; root.actionKey = "SUPER + SHIFT + TAB" }
      } else {
        root.actionTitle = "Switch to Workspace " + target
        root.actionCommand = "hyprctl dispatch workspace " + target
        root.actionDispatcher = 'hl.dsp.focus({ workspace = "' + target + '" })'
        var num = parseInt(target) % 10
        if (!keyRecorder.value) { keyRecorder.value = "SUPER + " + num; root.actionKey = "SUPER + " + num }
      }
    } else if (mode === "move") {
      if (isSpecial) {
        root.actionTitle = "Move Window to Scratchpad"
        root.actionCommand = "hyprctl dispatch movetoworkspace special:scratchpad"
        root.actionDispatcher = 'hl.dsp.window.move({ workspace = "special:scratchpad" })'
        if (!keyRecorder.value) { keyRecorder.value = "SUPER + ALT + S"; root.actionKey = "SUPER + ALT + S" }
      } else if (target === "e+1") {
        root.actionTitle = "Move Window to Next Workspace"
        root.actionCommand = "hyprctl dispatch movetoworkspace e+1"
        root.actionDispatcher = 'hl.dsp.window.move({ workspace = "e+1" })'
        if (!keyRecorder.value) { keyRecorder.value = "SUPER + SHIFT + RIGHT"; root.actionKey = "SUPER + SHIFT + RIGHT" }
      } else if (target === "e-1") {
        root.actionTitle = "Move Window to Previous Workspace"
        root.actionCommand = "hyprctl dispatch movetoworkspace e-1"
        root.actionDispatcher = 'hl.dsp.window.move({ workspace = "e-1" })'
        if (!keyRecorder.value) { keyRecorder.value = "SUPER + SHIFT + LEFT"; root.actionKey = "SUPER + SHIFT + LEFT" }
      } else {
        root.actionTitle = "Move Window to Workspace " + target
        root.actionCommand = "hyprctl dispatch movetoworkspace " + target
        root.actionDispatcher = 'hl.dsp.window.move({ workspace = "' + target + '" })'
        var n = parseInt(target) % 10
        if (!keyRecorder.value) { keyRecorder.value = "SUPER + SHIFT + " + n; root.actionKey = "SUPER + SHIFT + " + n }
      }
    } else if (mode === "silent") {
      if (isSpecial) {
        root.actionTitle = "Move Window to Scratchpad (Silent)"
        root.actionCommand = "hyprctl dispatch movetoworkspacesilent special:scratchpad"
        root.actionDispatcher = 'hl.dsp.window.move({ workspace = "special:scratchpad", follow = false })'
        if (!keyRecorder.value) { keyRecorder.value = "SUPER + SHIFT + ALT + S"; root.actionKey = "SUPER + SHIFT + ALT + S" }
      } else if (target === "e+1") {
        root.actionTitle = "Move Window to Next Workspace (Silent)"
        root.actionCommand = "hyprctl dispatch movetoworkspacesilent e+1"
        root.actionDispatcher = 'hl.dsp.window.move({ workspace = "e+1", follow = false })'
        if (!keyRecorder.value) { keyRecorder.value = "SUPER + SHIFT + ALT + RIGHT"; root.actionKey = "SUPER + SHIFT + ALT + RIGHT" }
      } else if (target === "e-1") {
        root.actionTitle = "Move Window to Previous Workspace (Silent)"
        root.actionCommand = "hyprctl dispatch movetoworkspacesilent e-1"
        root.actionDispatcher = 'hl.dsp.window.move({ workspace = "e-1", follow = false })'
        if (!keyRecorder.value) { keyRecorder.value = "SUPER + SHIFT + ALT + LEFT"; root.actionKey = "SUPER + SHIFT + ALT + LEFT" }
      } else {
        root.actionTitle = "Move Window to Workspace " + target + " (Silent)"
        root.actionCommand = "hyprctl dispatch movetoworkspacesilent " + target
        root.actionDispatcher = 'hl.dsp.window.move({ workspace = "' + target + '", follow = false })'
        var ns = parseInt(target) % 10
        if (!keyRecorder.value) { keyRecorder.value = "SUPER + SHIFT + ALT + " + ns; root.actionKey = "SUPER + SHIFT + ALT + " + ns }
      }
    }
    root.actionCategory = "Workspaces"
  }

  function selectSystemAction(name, cmd, defKey) {
    root.selectedActionId = "sys:" + name
    root.actionTitle = name
    root.actionCommand = cmd
    root.actionDispatcher = "exec"
    root.actionCategory = "Media & Audio"
    if (!keyRecorder.value && defKey) {
      keyRecorder.value = defKey
      root.actionKey = defKey
    }
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

  onOpenedChanged: {
    if (!opened) {
      keyRecorder.stopRecording()
    }
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
      width: Math.min(parent.width - Style.space(32), Style.space(720))
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
            color: root.accent
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

        // 2. Unified Segmented Tab Bar (When creating new)
        BorderSurface {
          visible: !root.isEditing
          Layout.fillWidth: true
          height: Style.space(38)
          radius: Style.cornerRadius
          color: Util.alpha(root.foreground, 0.04)
          borderSpec: Border.flat(Util.alpha(root.foreground, 0.12), 1)

          RowLayout {
            anchors.fill: parent
            anchors.margins: Style.space(3)
            spacing: Style.space(3)

            Repeater {
              model: [
                { id: "app", label: "🚀 Launch App" },
                { id: "window", label: "🪟 Window Control" },
                { id: "workspace", label: "📑 Workspace" },
                { id: "system", label: "⚡ System & Audio" },
                { id: "custom", label: "💻 Custom Script" }
              ]

              BorderSurface {
                id: tabBtn
                required property var modelData
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: Style.cornerRadius - 2
                color: root.actionType === modelData.id
                  ? Util.alpha(root.accent, 0.28)
                  : (tabMouse.containsMouse ? Util.alpha(root.foreground, 0.08) : "transparent")
                borderSpec: Border.flat(
                  root.actionType === modelData.id ? root.accent : "transparent",
                  root.actionType === modelData.id ? 1 : 0
                )

                MouseArea {
                  id: tabMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.actionType = tabBtn.modelData.id
                }

                Text {
                  anchors.centerIn: parent
                  textFormat: Text.PlainText
                  text: tabBtn.modelData.label
                  color: root.actionType === tabBtn.modelData.id ? root.accent : root.foreground
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                  font.bold: root.actionType === tabBtn.modelData.id
                }
              }
            }
          }
        }

        // 3. Structured Selection Panels

        // --- PANEL 1: Launch Application ---
        ColumnLayout {
          visible: !root.isEditing && root.actionType === "app"
          Layout.fillWidth: true
          spacing: Style.space(8)

          TextField {
            id: appSearchInput
            Layout.fillWidth: true
            placeholderText: "Search installed apps (e.g. Firefox, Spotify, Ghostty)..."
            text: root.appSearchQuery
            onTextChanged: root.appSearchQuery = text

            Button {
              visible: root.appSearchQuery.length > 0
              anchors.right: parent.right
              anchors.rightMargin: Style.space(6)
              anchors.verticalCenter: parent.verticalCenter
              iconText: "✕"
              horizontalPadding: Style.space(6)
              verticalPadding: Style.space(2)
              onClicked: {
                root.appSearchQuery = ""
                appSearchInput.text = ""
              }
            }
          }

          RowLayout {
            Layout.fillWidth: true

            Text {
              textFormat: Text.PlainText
              text: root.filteredApps.length + " apps found"
              color: Util.alpha(root.foreground, 0.5)
              font.family: Style.font.family
              font.pixelSize: Style.font.caption - 1
            }

            Item { Layout.fillWidth: true }

            BorderSurface {
              id: termToggle
              height: Style.space(26)
              width: termToggleRow.implicitWidth + Style.space(16)
              radius: Style.cornerRadius
              color: root.runInTerminal ? Util.alpha(root.accent, 0.22) : Util.alpha(root.foreground, 0.05)
              borderSpec: Border.flat(root.runInTerminal ? root.accent : Util.alpha(root.foreground, 0.18), 1)

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
                  text: "Run in terminal ($TERMINAL -e)"
                  color: root.runInTerminal ? root.accent : root.foreground
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption - 1
                  font.bold: root.runInTerminal
                }
              }
            }
          }

          // Scrollable App Chips with clean padding and text truncation
          ScrollView {
            id: appScrollView
            Layout.fillWidth: true
            Layout.preferredHeight: Style.space(120)
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

            Flow {
              width: appScrollView.availableWidth
              spacing: Style.space(6)
              bottomPadding: Style.space(8)

              Repeater {
                model: root.filteredApps

                BorderSurface {
                  id: appChip
                  required property var modelData
                  readonly property bool isSelected: root.selectedActionId === ("app:" + modelData.name) || root.actionTitle === modelData.name
                  height: Style.space(26)
                  width: Math.min(Style.space(210), appChipContent.implicitWidth + Style.space(16))
                  radius: Style.cornerRadius
                  color: isSelected
                    ? Util.alpha(root.accent, 0.28)
                    : (appMouse.containsMouse ? Util.alpha(root.foreground, 0.12) : Util.alpha(root.foreground, 0.05))
                  borderSpec: Border.flat(
                    isSelected ? root.accent : Util.alpha(root.foreground, 0.18),
                    isSelected ? 1.5 : 1
                  )

                  MouseArea {
                    id: appMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.selectApp(appChip.modelData)

                    ToolTip.visible: appMouse.containsMouse
                    ToolTip.delay: 500
                    ToolTip.text: appChip.modelData.exec ? ("Command: " + appChip.modelData.exec) : appChip.modelData.name
                  }

                  RowLayout {
                    id: appChipContent
                    anchors.fill: parent
                    anchors.leftMargin: Style.space(8)
                    anchors.rightMargin: Style.space(8)
                    spacing: Style.space(4)

                    Text {
                      textFormat: Text.PlainText
                      text: appChip.isSelected ? "✓" : "🚀"
                      color: appChip.isSelected ? root.accent : root.foreground
                      font.pixelSize: Style.font.caption - 2
                    }

                    Text {
                      Layout.fillWidth: true
                      textFormat: Text.PlainText
                      text: appChip.modelData.name
                      color: appChip.isSelected ? root.accent : root.foreground
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.bold: appChip.isSelected
                      elide: Text.ElideRight
                      maximumLineCount: 1
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
            text: "Window Actions:"
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
                { name: "Close Window", cmd: "hyprctl dispatch killactive", dsp: "hl.dsp.window.close()", key: "SUPER + W", icon: "✕" },
                { name: "Toggle Fullscreen", cmd: "hyprctl dispatch fullscreen", dsp: "hl.dsp.window.fullscreen()", key: "SUPER + F", icon: "⛶" },
                { name: "Toggle Floating", cmd: "hyprctl dispatch togglefloating", dsp: "hl.dsp.window.float({ action = \"toggle\" })", key: "SUPER + T", icon: "🪟" },
                { name: "Toggle Split", cmd: "hyprctl dispatch layoutmsg togglesplit", dsp: "hl.dsp.layout(\"togglesplit\")", key: "SUPER + J", icon: "⇄" },
                { name: "Pop Window Out (Pin)", cmd: "omarchy-hyprland-window-pop", dsp: "exec", key: "SUPER + O", icon: "📌" },
                { name: "Close All Windows", cmd: "omarchy-hyprland-window-close-all", dsp: "exec", key: "CTRL + ALT + DELETE", icon: "🗑️" },
                { name: "Toggle Transparency", cmd: "omarchy-hyprland-window-transparency-toggle", dsp: "exec", key: "SUPER + BACKSPACE", icon: "👻" },
                { name: "Toggle Gaps", cmd: "omarchy-hyprland-window-gaps-toggle", dsp: "exec", key: "SUPER + SHIFT + BACKSPACE", icon: "↔" }
              ]

              BorderSurface {
                id: winChip
                required property var modelData
                readonly property bool isSelected: root.selectedActionId === ("win:" + modelData.name) || root.actionTitle === modelData.name
                height: Style.space(26)
                width: winChipRow.implicitWidth + Style.space(16)
                radius: Style.cornerRadius
                color: isSelected
                  ? Util.alpha(root.accent, 0.28)
                  : (winMouse.containsMouse ? Util.alpha(root.foreground, 0.12) : Util.alpha(root.foreground, 0.05))
                borderSpec: Border.flat(isSelected ? root.accent : Util.alpha(root.foreground, 0.18), isSelected ? 1.5 : 1)

                MouseArea {
                  id: winMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.selectWindowAction(winChip.modelData.name, winChip.modelData.cmd, winChip.modelData.dsp, winChip.modelData.key)
                }

                RowLayout {
                  id: winChipRow
                  anchors.centerIn: parent
                  spacing: Style.space(4)

                  Text {
                    textFormat: Text.PlainText
                    text: winChip.isSelected ? "✓" : winChip.modelData.icon
                    color: winChip.isSelected ? root.accent : root.foreground
                    font.pixelSize: Style.font.caption - 1
                  }

                  Text {
                    textFormat: Text.PlainText
                    text: winChip.modelData.name
                    color: winChip.isSelected ? root.accent : root.foreground
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                    font.bold: winChip.isSelected
                  }
                }
              }
            }
          }

          // Directional Navigation Cluster
          RowLayout {
            Layout.fillWidth: true
            spacing: Style.space(8)

            Text {
              textFormat: Text.PlainText
              text: "Directional Controls:"
              color: Util.alpha(root.foreground, 0.75)
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              font.bold: true
            }

            BorderSurface {
              height: Style.space(26)
              radius: Style.cornerRadius
              color: Util.alpha(root.foreground, 0.05)
              borderSpec: Border.flat(Util.alpha(root.foreground, 0.15), 1)

              RowLayout {
                anchors.fill: parent
                anchors.margins: Style.space(2)
                spacing: Style.space(2)

                BorderSurface {
                  Layout.fillHeight: true
                  width: Style.space(88)
                  radius: Style.cornerRadius - 2
                  color: root.windowNavMode === "focus" ? Util.alpha(root.accent, 0.28) : "transparent"
                  borderSpec: Border.flat(root.windowNavMode === "focus" ? root.accent : "transparent", 1)

                  MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.windowNavMode = "focus"
                  }
                  Text {
                    anchors.centerIn: parent
                    textFormat: Text.PlainText
                    text: "🎯 Move Focus"
                    color: root.windowNavMode === "focus" ? root.accent : root.foreground
                    font.pixelSize: Style.font.caption - 1
                    font.bold: root.windowNavMode === "focus"
                  }
                }

                BorderSurface {
                  Layout.fillHeight: true
                  width: Style.space(88)
                  radius: Style.cornerRadius - 2
                  color: root.windowNavMode === "swap" ? Util.alpha(root.accent, 0.28) : "transparent"
                  borderSpec: Border.flat(root.windowNavMode === "swap" ? root.accent : "transparent", 1)

                  MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.windowNavMode = "swap"
                  }
                  Text {
                    anchors.centerIn: parent
                    textFormat: Text.PlainText
                    text: "⇄ Swap Window"
                    color: root.windowNavMode === "swap" ? root.accent : root.foreground
                    font.pixelSize: Style.font.caption - 1
                    font.bold: root.windowNavMode === "swap"
                  }
                }
              }
            }

            Item { Layout.fillWidth: true }

            // Compact Directional D-Pad row
            RowLayout {
              spacing: Style.space(6)

              Repeater {
                model: [
                  { dir: "l", label: "⬅️ Left", key: "LEFT" },
                  { dir: "u", label: "⬆️ Up", key: "UP" },
                  { dir: "d", label: "⬇️ Down", key: "DOWN" },
                  { dir: "r", label: "➡️ Right", key: "RIGHT" }
                ]

                BorderSurface {
                  id: dirBtn
                  required property var modelData
                  readonly property bool isSelected: root.selectedActionId === ("dir:" + root.windowNavMode + ":" + modelData.dir)
                  height: Style.space(26)
                  width: dirBtnText.implicitWidth + Style.space(16)
                  radius: Style.cornerRadius
                  color: isSelected
                    ? Util.alpha(root.accent, 0.28)
                    : (dirMouse.containsMouse ? Util.alpha(root.foreground, 0.12) : Util.alpha(root.foreground, 0.05))
                  borderSpec: Border.flat(isSelected ? root.accent : Util.alpha(root.foreground, 0.18), isSelected ? 1.5 : 1)

                  MouseArea {
                    id: dirMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                      var defK = (root.windowNavMode === "focus")
                        ? "SUPER + " + dirBtn.modelData.key
                        : "SUPER + SHIFT + " + dirBtn.modelData.key
                      root.selectDirectional(root.windowNavMode, dirBtn.modelData.dir, defK)
                    }
                  }

                  Text {
                    id: dirBtnText
                    anchors.centerIn: parent
                    textFormat: Text.PlainText
                    text: dirBtn.isSelected ? ("✓ " + dirBtn.modelData.label) : dirBtn.modelData.label
                    color: dirBtn.isSelected ? root.accent : root.foreground
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                    font.bold: dirBtn.isSelected
                  }
                }
              }
            }
          }
        }

        // --- PANEL 3: Workspace Builder ---
        ColumnLayout {
          visible: !root.isEditing && root.actionType === "workspace"
          Layout.fillWidth: true
          spacing: Style.space(8)

          // Operation Selector
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
              onClicked: {
                root.workspaceSubAction = "switch"
                if (root.workspaceTarget) root.selectWorkspace(root.workspaceTarget)
              }
            }

            Button {
              text: "🪟 Move Window"
              selected: root.workspaceSubAction === "move"
              horizontalPadding: Style.space(12)
              verticalPadding: Style.space(4)
              onClicked: {
                root.workspaceSubAction = "move"
                if (root.workspaceTarget) root.selectWorkspace(root.workspaceTarget)
              }
            }

            Button {
              text: "🤫 Move Silently"
              selected: root.workspaceSubAction === "silent"
              horizontalPadding: Style.space(12)
              verticalPadding: Style.space(4)
              onClicked: {
                root.workspaceSubAction = "silent"
                if (root.workspaceTarget) root.selectWorkspace(root.workspaceTarget)
              }
            }
          }

          // Row A: Workspaces 1-10 Number Bar
          Text {
            textFormat: Text.PlainText
            text: "Workspaces 1 – 10:"
            color: Util.alpha(root.foreground, 0.75)
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            font.bold: true
          }

          RowLayout {
            Layout.fillWidth: true
            spacing: Style.space(4)

            Repeater {
              model: ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]

              BorderSurface {
                id: wsNumChip
                required property string modelData
                readonly property bool isSelected: root.selectedActionId === ("ws:" + root.workspaceSubAction + ":" + modelData)
                Layout.fillWidth: true
                height: Style.space(26)
                radius: Style.cornerRadius
                color: isSelected
                  ? Util.alpha(root.accent, 0.28)
                  : (wsNumMouse.containsMouse ? Util.alpha(root.foreground, 0.12) : Util.alpha(root.foreground, 0.05))
                borderSpec: Border.flat(isSelected ? root.accent : Util.alpha(root.foreground, 0.18), isSelected ? 1.5 : 1)

                MouseArea {
                  id: wsNumMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.selectWorkspace(wsNumChip.modelData)
                }

                Text {
                  anchors.centerIn: parent
                  textFormat: Text.PlainText
                  text: wsNumChip.isSelected ? ("✓ " + wsNumChip.modelData) : wsNumChip.modelData
                  color: wsNumChip.isSelected ? root.accent : root.foreground
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                  font.bold: wsNumChip.isSelected
                }
              }
            }
          }

          // Row B: Relative & Special Targets
          Text {
            textFormat: Text.PlainText
            text: "Relative & Special Workspaces:"
            color: Util.alpha(root.foreground, 0.75)
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            font.bold: true
          }

          RowLayout {
            Layout.fillWidth: true
            spacing: Style.space(8)

            Repeater {
              model: [
                { id: "e+1", label: "⏭️ Next Workspace (e+1)" },
                { id: "e-1", label: "⏮️ Previous Workspace (e-1)" },
                { id: "Scratchpad", label: "📌 Special Scratchpad" }
              ]

              BorderSurface {
                id: wsRelChip
                required property var modelData
                readonly property bool isSelected: root.selectedActionId === ("ws:" + root.workspaceSubAction + ":" + modelData.id)
                Layout.fillWidth: true
                height: Style.space(26)
                radius: Style.cornerRadius
                color: isSelected
                  ? Util.alpha(root.accent, 0.28)
                  : (wsRelMouse.containsMouse ? Util.alpha(root.foreground, 0.12) : Util.alpha(root.foreground, 0.05))
                borderSpec: Border.flat(isSelected ? root.accent : Util.alpha(root.foreground, 0.18), isSelected ? 1.5 : 1)

                MouseArea {
                  id: wsRelMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.selectWorkspace(wsRelChip.modelData.id)
                }

                Text {
                  anchors.centerIn: parent
                  textFormat: Text.PlainText
                  text: wsRelChip.isSelected ? ("✓ " + wsRelChip.modelData.label) : wsRelChip.modelData.label
                  color: wsRelChip.isSelected ? root.accent : root.foreground
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                  font.bold: wsRelChip.isSelected
                }
              }
            }
          }
        }

        // --- PANEL 4: Media & System Controls ---
        ColumnLayout {
          visible: !root.isEditing && root.actionType === "system"
          Layout.fillWidth: true
          spacing: Style.space(6)

          // Sub-Section A: Audio & Media
          Text {
            textFormat: Text.PlainText
            text: "🔊 Audio & Playback:"
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
                { name: "Volume Up", cmd: "omarchy-audio-output-volume raise", key: "XF86AudioRaiseVolume", icon: "🔊" },
                { name: "Volume Down", cmd: "omarchy-audio-output-volume lower", key: "XF86AudioLowerVolume", icon: "🔉" },
                { name: "Mute Audio", cmd: "omarchy-audio-output-volume mute-toggle", key: "XF86AudioMute", icon: "🔇" },
                { name: "Mute Microphone", cmd: "omarchy-audio-input-mute", key: "XF86AudioMicMute", icon: "🎤" },
                { name: "Play / Pause", cmd: "omarchy-shell media playPause", key: "XF86AudioPlay", icon: "⏯️" },
                { name: "Next Track", cmd: "omarchy-shell media next", key: "XF86AudioNext", icon: "⏭️" },
                { name: "Previous Track", cmd: "omarchy-shell media previous", key: "XF86AudioPrev", icon: "⏮️" }
              ]

              BorderSurface {
                id: audChip
                required property var modelData
                readonly property bool isSelected: root.selectedActionId === ("sys:" + modelData.name) || root.actionTitle === modelData.name
                height: Style.space(24)
                width: audChipRow.implicitWidth + Style.space(14)
                radius: Style.cornerRadius
                color: isSelected
                  ? Util.alpha(root.accent, 0.28)
                  : (audMouse.containsMouse ? Util.alpha(root.foreground, 0.12) : Util.alpha(root.foreground, 0.05))
                borderSpec: Border.flat(isSelected ? root.accent : Util.alpha(root.foreground, 0.18), isSelected ? 1.5 : 1)

                MouseArea {
                  id: audMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.selectSystemAction(audChip.modelData.name, audChip.modelData.cmd, audChip.modelData.key)
                }

                RowLayout {
                  id: audChipRow
                  anchors.centerIn: parent
                  spacing: Style.space(4)

                  Text {
                    textFormat: Text.PlainText
                    text: audChip.isSelected ? "✓" : audChip.modelData.icon
                    color: audChip.isSelected ? root.accent : root.foreground
                    font.pixelSize: Style.font.caption - 2
                  }
                  Text {
                    textFormat: Text.PlainText
                    text: audChip.modelData.name
                    color: audChip.isSelected ? root.accent : root.foreground
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption - 1
                    font.bold: audChip.isSelected
                  }
                }
              }
            }
          }

          // Sub-Section B: Display & Capture
          Text {
            textFormat: Text.PlainText
            text: "☀️ Display & Capture:"
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
                { name: "Raise Brightness", cmd: "brightnessctl set +5%", key: "XF86MonBrightnessUp", icon: "☀️" },
                { name: "Lower Brightness", cmd: "brightnessctl set 5%-", key: "XF86MonBrightnessDown", icon: "🔅" },
                { name: "Capture Screenshot", cmd: "omarchy-capture-screenshot", key: "PRINT", icon: "📸" },
                { name: "Screenrecord", cmd: "omarchy-capture-screenrecording", key: "ALT + PRINT", icon: "🎥" },
                { name: "Color Picker", cmd: "pkill hyprpicker || hyprpicker -a", key: "SUPER + PRINT", icon: "🎨" }
              ]

              BorderSurface {
                id: dispChip
                required property var modelData
                readonly property bool isSelected: root.selectedActionId === ("sys:" + modelData.name) || root.actionTitle === modelData.name
                height: Style.space(24)
                width: dispChipRow.implicitWidth + Style.space(14)
                radius: Style.cornerRadius
                color: isSelected
                  ? Util.alpha(root.accent, 0.28)
                  : (dispMouse.containsMouse ? Util.alpha(root.foreground, 0.12) : Util.alpha(root.foreground, 0.05))
                borderSpec: Border.flat(isSelected ? root.accent : Util.alpha(root.foreground, 0.18), isSelected ? 1.5 : 1)

                MouseArea {
                  id: dispMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.selectSystemAction(dispChip.modelData.name, dispChip.modelData.cmd, dispChip.modelData.key)
                }

                RowLayout {
                  id: dispChipRow
                  anchors.centerIn: parent
                  spacing: Style.space(4)

                  Text {
                    textFormat: Text.PlainText
                    text: dispChip.isSelected ? "✓" : dispChip.modelData.icon
                    color: dispChip.isSelected ? root.accent : root.foreground
                    font.pixelSize: Style.font.caption - 2
                  }
                  Text {
                    textFormat: Text.PlainText
                    text: dispChip.modelData.name
                    color: dispChip.isSelected ? root.accent : root.foreground
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption - 1
                    font.bold: dispChip.isSelected
                  }
                }
              }
            }
          }

          // Sub-Section C: System Tools & Menus
          Text {
            textFormat: Text.PlainText
            text: "⚙️ System & Menus:"
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
                { name: "Lock Screen", cmd: "omarchy-system-lock", key: "SUPER + CTRL + L", icon: "🔒" },
                { name: "Clipboard Manager", cmd: "omarchy-shell shell toggle omarchy.clipboard", key: "SUPER + CTRL + V", icon: "📋" },
                { name: "Emoji Picker", cmd: "omarchy-shell shell toggle omarchy.emojis", key: "SUPER + CTRL + E", icon: "😀" },
                { name: "Omarchy Menu", cmd: "omarchy-menu toggle", key: "SUPER + SPACE", icon: "⚡" }
              ]

              BorderSurface {
                id: sysMenuChip
                required property var modelData
                readonly property bool isSelected: root.selectedActionId === ("sys:" + modelData.name) || root.actionTitle === modelData.name
                height: Style.space(24)
                width: sysMenuChipRow.implicitWidth + Style.space(14)
                radius: Style.cornerRadius
                color: isSelected
                  ? Util.alpha(root.accent, 0.28)
                  : (sysMenuMouse.containsMouse ? Util.alpha(root.foreground, 0.12) : Util.alpha(root.foreground, 0.05))
                borderSpec: Border.flat(isSelected ? root.accent : Util.alpha(root.foreground, 0.18), isSelected ? 1.5 : 1)

                MouseArea {
                  id: sysMenuMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.selectSystemAction(sysMenuChip.modelData.name, sysMenuChip.modelData.cmd, sysMenuChip.modelData.key)
                }

                RowLayout {
                  id: sysMenuChipRow
                  anchors.centerIn: parent
                  spacing: Style.space(4)

                  Text {
                    textFormat: Text.PlainText
                    text: sysMenuChip.isSelected ? "✓" : sysMenuChip.modelData.icon
                    color: sysMenuChip.isSelected ? root.accent : root.foreground
                    font.pixelSize: Style.font.caption - 2
                  }
                  Text {
                    textFormat: Text.PlainText
                    text: sysMenuChip.modelData.name
                    color: sysMenuChip.isSelected ? root.accent : root.foreground
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption - 1
                    font.bold: sysMenuChip.isSelected
                  }
                }
              }
            }
          }
        }

        // --- DEDICATED CONFIGURED ACTION CONFIRMATION BANNER ---
        BorderSurface {
          visible: root.actionType !== "custom" && root.actionTitle.length > 0
          Layout.fillWidth: true
          height: Style.space(42)
          radius: Style.cornerRadius
          color: Util.alpha(root.accent, 0.14)
          borderSpec: Border.flat(root.accent, 1)

          RowLayout {
            anchors.fill: parent
            anchors.leftMargin: Style.space(12)
            anchors.rightMargin: Style.space(12)
            spacing: Style.space(10)

            Text {
              textFormat: Text.PlainText
              text: "✓"
              color: root.accent
              font.bold: true
              font.pixelSize: Style.font.body + 2
            }

            ColumnLayout {
              Layout.fillWidth: true
              spacing: Style.space(1)

              Text {
                textFormat: Text.PlainText
                text: root.actionTitle
                color: root.foreground
                font.family: Style.font.family
                font.pixelSize: Style.font.body
                font.bold: true
                elide: Text.ElideRight
              }

              Text {
                textFormat: Text.PlainText
                text: "Configured: " + (root.actionDispatcher && root.actionDispatcher !== "exec" ? root.actionDispatcher : root.actionCommand)
                color: Util.alpha(root.foreground, 0.65)
                font.family: Style.font.family
                font.pixelSize: Style.font.caption - 1
                elide: Text.ElideRight
              }
            }

            BorderSurface {
              height: Style.space(20)
              width: catTagText.implicitWidth + Style.space(12)
              radius: Style.cornerRadius - 2
              color: Util.alpha(root.accent, 0.2)
              borderSpec: Border.flat(Util.alpha(root.accent, 0.4), 1)

              Text {
                id: catTagText
                anchors.centerIn: parent
                textFormat: Text.PlainText
                text: root.actionCategory
                color: root.accent
                font.family: Style.font.family
                font.pixelSize: Style.font.caption - 2
                font.bold: true
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

          // Syntax Tip Banner
          BorderSurface {
            Layout.fillWidth: true
            height: Style.space(28)
            radius: Style.cornerRadius
            color: Util.alpha(root.foreground, 0.04)
            borderSpec: Border.flat(Util.alpha(root.foreground, 0.12), 1)

            RowLayout {
              anchors.fill: parent
              anchors.leftMargin: Style.space(10)
              anchors.rightMargin: Style.space(10)
              spacing: Style.space(6)

              Text {
                textFormat: Text.PlainText
                text: "💡"
                font.pixelSize: Style.font.caption - 2
              }
              Text {
                textFormat: Text.PlainText
                text: "Tip: Use 'ghostty -e <cmd>' for CLI tools, or 'hyprctl dispatch <dispatcher> <args>' for window controls."
                color: Util.alpha(root.foreground, 0.6)
                font.family: Style.font.family
                font.pixelSize: Style.font.caption - 2
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
            onValueChanged: {
              if (keyRecorder.value && keyRecorder.value !== root.actionKey) {
                root.actionKey = keyRecorder.value
              }
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
            enabled: root.actionTitle.trim().length > 0 && root.actionKey.trim().length > 0 && (root.actionCommand.trim().length > 0 || (root.actionDispatcher && root.actionDispatcher.trim().length > 0))
            onClicked: {
              root.saved(
                root.actionKey,
                root.actionTitle,
                root.actionCommand,
                root.actionDispatcher || "",
                root.oldKey,
                true
              )
              keyRecorder.stopRecording()
              root.opened = false
            }
          }
        }
      }
    }
  }
}
