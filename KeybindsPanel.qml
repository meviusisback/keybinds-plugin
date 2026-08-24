import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

Item {
  id: root

  property var shell: null
  property var manifest: null
  property bool closingFromHost: false
  readonly property bool opened: window.visible

  // Theme
  readonly property color foreground: Color.foreground
  readonly property color background: Color.background
  readonly property color accent: Color.accent
  readonly property color urgent: Color.urgent

  // State
  property var modelData: ({
    active: [],
    catalog: [],
    conflicts: [],
    total_active: 0,
    total_modified: 0,
    total_conflicts: 0
  })

  property bool loading: false
  property string searchQuery: ""
  property bool recordingSearch: false
  property string currentTab: "active" // "active" | "modified" | "catalog" | "conflicts"
  property string currentCategory: "All"
  property string toastMessage: ""
  property bool toastVisible: false

  readonly property var categories: [
    "All",
    "Window Management",
    "Workspaces",
    "Menus & System",
    "Applications",
    "Media & Audio"
  ]

  function open(payloadJson) {
    closingFromHost = false
    window.visible = true
    loadData()
    Qt.callLater(function() {
      if (searchInput) searchInput.forceActiveFocus()
    })
  }

  function close() {
    closingFromHost = true
    window.visible = false
  }

  function requestClose() {
    if (root.shell && typeof root.shell.hide === "function") {
      root.shell.hide((root.manifest && root.manifest.id) || "meviusisback.keybinds")
    } else {
      window.visible = false
    }
  }

  function showToast(msg) {
    root.toastMessage = msg
    root.toastVisible = true
    toastTimer.restart()
  }

  Timer {
    id: toastTimer
    interval: 3500
    onTriggered: root.toastVisible = false
  }

  function loadData() {
    root.loading = true
    listProc.running = false
    listProc.running = true
  }

  function saveKeybinding(key, desc, cmd, action, oldKey, overrideConflict) {
    root.loading = true
    setProc.running = false
    setProc.command = [
      Quickshell.env("HOME") + "/.config/omarchy/plugins/meviusisback.keybinds/backend/keybinds_manager.py",
      "set",
      key,
      desc,
      cmd,
      action || "",
      oldKey || ""
    ]
    setProc.running = true
  }

  function resetKeybinding(key, defaultKey) {
    root.loading = true
    resetProc.running = false
    resetProc.command = [
      Quickshell.env("HOME") + "/.config/omarchy/plugins/meviusisback.keybinds/backend/keybinds_manager.py",
      "reset",
      key,
      defaultKey || ""
    ]
    resetProc.running = true
  }

  function enableKeybinding(key) {
    root.loading = true
    enableProc.running = false
    enableProc.command = [
      Quickshell.env("HOME") + "/.config/omarchy/plugins/meviusisback.keybinds/backend/keybinds_manager.py",
      "enable",
      key
    ]
    enableProc.running = true
  }

  function disableKeybinding(key) {
    root.loading = true
    disableProc.running = false
    disableProc.command = [
      Quickshell.env("HOME") + "/.config/omarchy/plugins/meviusisback.keybinds/backend/keybinds_manager.py",
      "disable",
      key
    ]
    disableProc.running = true
  }

  function translateQtKey(event) {
    var key = event.key
    var text = event.text

    if (key === Qt.Key_Return || key === Qt.Key_Enter) return "RETURN"
    if (key === Qt.Key_Space) return "SPACE"
    if (key === Qt.Key_Escape) return "ESCAPE"
    if (key === Qt.Key_Tab || key === Qt.Key_Backtab) return "TAB"
    if (key === Qt.Key_Backspace) return "BACKSPACE"
    if (key === Qt.Key_Delete) return "DELETE"
    if (key === Qt.Key_Print) return "PRINT"
    if (key === Qt.Key_Left) return "LEFT"
    if (key === Qt.Key_Right) return "RIGHT"
    if (key === Qt.Key_Up) return "UP"
    if (key === Qt.Key_Down) return "DOWN"
    if (key === Qt.Key_Comma) return "comma"
    if (key === Qt.Key_Period) return "period"
    if (key === Qt.Key_Slash) return "slash"
    if (key === Qt.Key_Minus) return "minus"
    if (key === Qt.Key_Equal) return "equal"
    if (key === Qt.Key_BracketLeft) return "bracketleft"
    if (key === Qt.Key_BracketRight) return "bracketright"
    if (key === Qt.Key_PageUp) return "Page_Up"
    if (key === Qt.Key_PageDown) return "Page_Down"
    if (key === Qt.Key_Home) return "Home"
    if (key === Qt.Key_End) return "End"

    if (key >= Qt.Key_F1 && key <= Qt.Key_F12) {
      return "F" + (key - Qt.Key_F1 + 1)
    }

    if (key >= Qt.Key_0 && key <= Qt.Key_9) {
      return String.fromCharCode(key)
    }

    if (key >= Qt.Key_A && key <= Qt.Key_Z) {
      return String.fromCharCode(key)
    }

    if (text && text.length === 1 && text.charCodeAt(0) >= 33 && text.charCodeAt(0) <= 126) {
      return text.toUpperCase()
    }

    return ""
  }

  // Filtered active bindings (sorted alphabetically)
  readonly property var filteredActive: {
    var list = root.modelData.active || []
    var q = root.searchQuery.trim().toLowerCase()
    var cat = root.currentCategory

    var res = []
    for (var i = 0; i < list.length; i++) {
      var item = list[i]

      // Category filter
      if (cat !== "All" && item.category !== cat) continue

      // Search filter
      if (q.length > 0) {
        var matchKey = item.key && item.key.toLowerCase().indexOf(q) !== -1
        var matchDesc = item.description && item.description.toLowerCase().indexOf(q) !== -1
        var matchCmd = (item.command && item.command.toLowerCase().indexOf(q) !== -1) ||
                       (item.action && item.action.toLowerCase().indexOf(q) !== -1)
        var matchCat = item.category && item.category.toLowerCase().indexOf(q) !== -1

        if (!matchKey && !matchDesc && !matchCmd && !matchCat) continue
      }

      res.push(item)
    }
    return res
  }

  // Filtered modified & custom bindings
  readonly property var filteredModified: {
    var list = root.modelData.active || []
    var q = root.searchQuery.trim().toLowerCase()
    var cat = root.currentCategory

    var res = []
    for (var i = 0; i < list.length; i++) {
      var item = list[i]
      if (item.status !== "modified" && item.status !== "custom") continue
      if (cat !== "All" && item.category !== cat) continue

      if (q.length > 0) {
        var matchKey = item.key && item.key.toLowerCase().indexOf(q) !== -1
        var matchDesc = item.description && item.description.toLowerCase().indexOf(q) !== -1
        var matchCmd = (item.command && item.command.toLowerCase().indexOf(q) !== -1) ||
                       (item.action && item.action.toLowerCase().indexOf(q) !== -1)
        var matchCat = item.category && item.category.toLowerCase().indexOf(q) !== -1

        if (!matchKey && !matchDesc && !matchCmd && !matchCat) continue
      }

      res.push(item)
    }
    return res
  }

  // Filtered catalog presets
  readonly property var filteredCatalog: {
    var list = root.modelData.catalog || []
    var q = root.searchQuery.trim().toLowerCase()
    var cat = root.currentCategory

    var res = []
    for (var i = 0; i < list.length; i++) {
      var item = list[i]

      if (cat !== "All" && item.category !== cat) continue

      if (q.length > 0) {
        var matchName = item.name && item.name.toLowerCase().indexOf(q) !== -1
        var matchDesc = item.description && item.description.toLowerCase().indexOf(q) !== -1
        var matchKey = item.default_key && item.default_key.toLowerCase().indexOf(q) !== -1
        if (!matchName && !matchDesc && !matchKey) continue
      }

      res.push(item)
    }
    return res
  }

  // Filtered conflicts
  readonly property var filteredConflicts: {
    return root.modelData.conflicts || []
  }

  // --- Backend Subprocesses ---

  Process {
    id: listProc
    command: [Quickshell.env("HOME") + "/.config/omarchy/plugins/meviusisback.keybinds/backend/keybinds_manager.py", "list"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.loading = false
        if (text && text.trim().length > 0) {
          try {
            var parsed = JSON.parse(text)
            if (parsed) root.modelData = parsed
          } catch (e) {
            console.warn("KeybindsPanel: Failed to parse backend json:", e)
          }
        }
      }
    }
  }

  Process {
    id: setProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.loading = false
        root.loadData()
        root.showToast("Keybinding saved & applied to Hyprland!")
      }
    }
  }

  Process {
    id: resetProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.loading = false
        root.loadData()
        root.showToast("Keybinding reset to default!")
      }
    }
  }

  Process {
    id: enableProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.loading = false
        root.loadData()
        root.showToast("Keybinding re-enabled!")
      }
    }
  }

  Process {
    id: disableProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.loading = false
        root.loadData()
        root.showToast("Keybinding disabled!")
      }
    }
  }

  // Main Window
  // Start hidden: keepLoaded mounts this panel at every shell startup, and
  // Quickshell windows default to visible. Only open() may reveal it
  // (same contract as first-party overlays' `visible: root.opened`).
  FloatingWindow {
    id: window
    visible: false
    title: "Keybindings"
    color: root.background
    implicitWidth: Style.space(1000)
    implicitHeight: Style.space(760)
    minimumSize: Qt.size(Style.space(700), Style.space(520))

    onVisibleChanged: {
      if (!visible && !root.closingFromHost && root.shell && typeof root.shell.hide === "function") {
        root.shell.hide((root.manifest && root.manifest.id) || "meviusisback.keybinds")
      }
    }

    FocusScope {
      id: mainContainer
      anchors.fill: parent
      focus: true

      Keys.onPressed: function(event) {
        if (!root.recordingSearch) return

        var isSuper = (event.modifiers & Qt.MetaModifier) !== 0 || event.key === Qt.Key_Meta || event.key === Qt.Key_Super_L || event.key === Qt.Key_Super_R
        var isCtrl = (event.modifiers & Qt.ControlModifier) !== 0 || event.key === Qt.Key_Control
        var isAlt = (event.modifiers & Qt.AltModifier) !== 0 || event.key === Qt.Key_Alt || event.key === Qt.Key_AltGr
        var isShift = (event.modifiers & Qt.ShiftModifier) !== 0 || event.key === Qt.Key_Shift

        // Escape cancels search recording
        if (event.key === Qt.Key_Escape && !isSuper && !isCtrl && !isAlt && !isShift) {
          root.recordingSearch = false
          event.accepted = true
          return
        }

        // Ignore modifier-only keypresses alone
        if (event.key === Qt.Key_Control || event.key === Qt.Key_Shift || event.key === Qt.Key_Alt || event.key === Qt.Key_Meta || event.key === Qt.Key_Super_L || event.key === Qt.Key_Super_R) {
          event.accepted = true
          return
        }

        var keyName = root.translateQtKey(event)
        if (keyName.length > 0) {
          var mods = []
          if (isSuper) mods.push("SUPER")
          if (isShift) mods.push("SHIFT")
          if (isCtrl) mods.push("CTRL")
          if (isAlt) mods.push("ALT")

          var chord = mods.length > 0 ? (mods.join(" + ") + " + " + keyName) : keyName
          root.searchQuery = chord
          searchInput.text = chord
          root.recordingSearch = false
          event.accepted = true
        }
      }

      Keys.onEscapePressed: function(event) {
        if (root.recordingSearch) {
          root.recordingSearch = false
        } else if (editDialog.opened) {
          editDialog.close()
        } else {
          root.requestClose()
        }
        event.accepted = true
      }

      ColumnLayout {
        anchors.fill: parent
        anchors.margins: Style.space(22)
        spacing: Style.space(16)

        // 1. Top Header Bar with Clean Layout
        RowLayout {
          Layout.fillWidth: true
          spacing: Style.space(16)

          // App Icon & Title
          RowLayout {
            spacing: Style.space(12)

            // Clean white logo glyph without box
            Text {
              text: ""
              color: "white"
              font.family: Style.font.family
              font.pixelSize: Style.font.title + 8
            }

            ColumnLayout {
              spacing: 0

              Text {
                text: "Keybindings"
                color: root.foreground
                font.family: Style.font.family
                font.pixelSize: Style.font.title
                font.bold: true
              }

              Text {
                text: "Hyprland Shortcut Manager"
                color: Util.alpha(root.foreground, 0.55)
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
              }
            }
          }

          // Search Bar (Expands flexibly to fill available width)
          Item {
            Layout.fillWidth: true
            Layout.preferredHeight: Style.space(38)

            TextField {
              id: searchInput
              anchors.fill: parent
              placeholderText: root.recordingSearch
                ? "Listening... Press any shortcut combination (e.g. CTRL + O)"
                : "Search shortcuts, actions, commands..."
              text: root.searchQuery
              onTextChanged: root.searchQuery = text

              // Clear button
              Button {
                visible: root.searchQuery.length > 0
                anchors.right: parent.right
                anchors.rightMargin: Style.space(6)
                anchors.verticalCenter: parent.verticalCenter
                iconText: "✕"
                horizontalPadding: Style.space(6)
                verticalPadding: Style.space(2)
                onClicked: {
                  root.searchQuery = ""
                  searchInput.text = ""
                  root.recordingSearch = false
                }
              }
            }
          }

          // Action Buttons on Right
          RowLayout {
            spacing: Style.space(8)

            Button {
              text: "Add Keybinding"
              iconText: "➕"
              accent: root.accent
              selected: true
              horizontalPadding: Style.space(14)
              onClicked: editDialog.openCreate(null)
            }

            Button {
              iconText: ""
              tooltipText: "Refresh Keybindings"
              horizontalPadding: Style.space(10)
              onClicked: root.loadData()
            }

            Button {
              iconText: ""
              tooltipText: "Edit bindings.lua in Editor"
              horizontalPadding: Style.space(10)
              onClicked: Util.execDetached("omarchy-launch-config-editor $HOME/.config/hypr/bindings.lua")
            }
          }
        }

        // 2. Navigation Tabs (All Active vs Modified vs Catalog vs Conflicts)
        RowLayout {
          Layout.fillWidth: true
          spacing: Style.space(12)

          ButtonGroup {
            id: tabGroup

            Button {
              text: "All Active (" + (root.modelData.total_active || 0) + ")"
              selected: root.currentTab === "active"
              horizontalPadding: Style.space(16)
              onClicked: root.currentTab = "active"
            }

            Button {
              text: "⭐ Modified & Custom (" + (root.modelData.total_modified || 0) + ")"
              selected: root.currentTab === "modified"
              accent: (root.modelData.total_modified > 0) ? "#FF9800" : root.foreground
              horizontalPadding: Style.space(16)
              onClicked: root.currentTab = "modified"
            }

            Button {
              text: "Available Actions Catalog (" + ((root.modelData.catalog && root.modelData.catalog.length) || 0) + ")"
              selected: root.currentTab === "catalog"
              horizontalPadding: Style.space(16)
              onClicked: root.currentTab = "catalog"
            }

            Button {
              text: "⚠️ Conflicts (" + (root.modelData.total_conflicts || 0) + ")"
              selected: root.currentTab === "conflicts"
              accent: (root.modelData.total_conflicts > 0) ? root.urgent : root.foreground
              horizontalPadding: Style.space(16)
              onClicked: root.currentTab = "conflicts"
            }
          }

          Item { Layout.fillWidth: true }
        }

        // 3. Category Filter Chips & Record to Find on the Right
        RowLayout {
          visible: root.currentTab !== "conflicts"
          Layout.fillWidth: true
          spacing: Style.space(12)

          Flow {
            Layout.fillWidth: true
            spacing: Style.space(6)

            Repeater {
              model: root.categories

              BorderSurface {
                id: chip
                required property string modelData
                height: Style.space(26)
                width: chipLabel.implicitWidth + Style.space(20)
                radius: Style.cornerRadius
                readonly property bool isSelected: root.currentCategory === modelData

                color: isSelected
                  ? Util.alpha(root.accent, 0.22)
                  : (chipMouseArea.containsMouse ? Util.alpha(root.foreground, 0.08) : Util.alpha(root.foreground, 0.04))
                borderSpec: Border.flat(
                  isSelected ? root.accent : Util.alpha(root.foreground, 0.15),
                  1
                )

                MouseArea {
                  id: chipMouseArea
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.currentCategory = chip.modelData
                }

                Text {
                  id: chipLabel
                  anchors.centerIn: parent
                  text: chip.modelData
                  color: chip.isSelected ? root.accent : root.foreground
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                  font.bold: chip.isSelected
                }
              }
            }
          }

          // Record to Find Shortcut Button pinned to the right hand side
          Button {
            id: recordSearchBtn
            text: root.recordingSearch ? "Listening..." : "Record to Find"
            iconText: root.recordingSearch ? "⏺" : ""
            accent: root.recordingSearch ? root.accent : root.foreground
            selected: root.recordingSearch
            horizontalPadding: Style.space(14)
            verticalPadding: Style.space(4)
            Layout.alignment: Qt.AlignVCenter | Qt.AlignRight
            tooltipText: "Press a key combination on your keyboard to instantly find its assigned action"
            onClicked: {
              if (root.recordingSearch) {
                root.recordingSearch = false
              } else {
                root.recordingSearch = true
                mainContainer.forceActiveFocus()
              }
            }
          }
        }

        PanelSeparator { Layout.fillWidth: true }

        // 4. Main Scrollable Content Area
        ScrollView {
          id: scrollArea
          Layout.fillWidth: true
          Layout.fillHeight: true
          clip: true
          ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

          WheelHandler {
            target: scrollArea.contentItem
            acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
            onWheel: function(event) {
              var factor = (root.modelData.mouse_settings && root.modelData.mouse_settings.scroll_factor) || 1.0
              var natural = Boolean(root.modelData.mouse_settings && root.modelData.mouse_settings.natural_scroll)

              var dy = event.angleDelta.y
              if (dy === 0) dy = event.pixelDelta.y

              var direction = natural ? -1 : 1
              var step = (dy / 120.0) * 100.0 * factor * direction

              var flick = scrollArea.contentItem
              if (flick && flick.contentY !== undefined) {
                var newY = flick.contentY - step
                var maxY = Math.max(0, flick.contentHeight - flick.height)
                flick.contentY = Math.max(0, Math.min(maxY, newY))
                event.accepted = true
              }
            }
          }

          ColumnLayout {
            width: scrollArea.availableWidth
            spacing: Style.space(8)

            // =========================================================
            // --- TAB 1: ALL ACTIVE KEYBINDINGS (ALPHABETICAL) ---
            // =========================================================
            ColumnLayout {
              visible: root.currentTab === "active"
              Layout.fillWidth: true
              spacing: Style.space(8)

              Repeater {
                model: root.filteredActive

                BorderSurface {
                  id: activeRow
                  required property var modelData
                  Layout.fillWidth: true
                  Layout.preferredHeight: Style.space(64)
                  radius: Style.cornerRadius
                  color: activeRowMouse.containsMouse
                    ? Util.alpha(root.foreground, 0.06)
                    : Util.alpha(root.foreground, 0.02)
                  borderSpec: Border.flat(
                    (modelData && modelData.is_conflict)
                      ? root.urgent
                      : (activeRowMouse.containsMouse ? Util.alpha(root.foreground, 0.25) : Util.alpha(root.foreground, 0.1)),
                    (modelData && modelData.is_conflict) ? 1.5 : 1
                  )

                  MouseArea {
                    id: activeRowMouse
                    anchors.fill: parent
                    hoverEnabled: true
                  }

                  RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Style.space(16)
                    anchors.rightMargin: Style.space(16)
                    spacing: Style.space(14)

                    // Status Badge Pill
                    BorderSurface {
                      Layout.preferredHeight: Style.space(22)
                      Layout.preferredWidth: statusText.implicitWidth + Style.space(14)
                      Layout.alignment: Qt.AlignVCenter
                      radius: Style.cornerRadius

                      readonly property string st: (activeRow.modelData && activeRow.modelData.status) || "default"
                      readonly property bool isConf: Boolean(activeRow.modelData && activeRow.modelData.is_conflict)

                      color: isConf
                        ? Util.alpha(root.urgent, 0.2)
                        : (st === "custom" ? Util.alpha("#4CAF50", 0.2)
                          : (st === "modified" ? Util.alpha("#FF9800", 0.2)
                            : (st === "disabled" ? Util.alpha(root.foreground, 0.1) : Util.alpha(root.foreground, 0.05))))

                      borderSpec: Border.flat(
                        isConf
                          ? root.urgent
                          : (st === "custom" ? "#4CAF50"
                            : (st === "modified" ? "#FF9800"
                              : (st === "disabled" ? Util.alpha(root.foreground, 0.3) : Util.alpha(root.foreground, 0.15)))),
                        1
                      )

                      Text {
                        id: statusText
                        anchors.centerIn: parent
                        text: (activeRow.modelData && activeRow.modelData.is_conflict)
                          ? "⚠️ CONFLICT"
                          : (activeRow.modelData && activeRow.modelData.status ? activeRow.modelData.status.toUpperCase() : "DEFAULT")
                        color: (activeRow.modelData && activeRow.modelData.is_conflict)
                          ? root.urgent
                          : (activeRow.modelData && activeRow.modelData.status === "custom" ? "#4CAF50"
                            : (activeRow.modelData && activeRow.modelData.status === "modified" ? "#FF9800"
                              : (activeRow.modelData && activeRow.modelData.status === "disabled" ? Util.alpha(root.foreground, 0.4) : root.foreground)))
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption - 2
                        font.bold: true
                      }
                    }

                    // Action Info (Description, Command, Category)
                    ColumnLayout {
                      Layout.fillWidth: true
                      Layout.alignment: Qt.AlignVCenter
                      spacing: Style.space(2)

                      RowLayout {
                        spacing: Style.space(8)

                        Text {
                          text: (activeRow.modelData && activeRow.modelData.description) || "Action"
                          textFormat: Text.PlainText
                          color: (activeRow.modelData && activeRow.modelData.status === "disabled")
                            ? Util.alpha(root.foreground, 0.4)
                            : root.foreground
                          font.family: Style.font.family
                          font.pixelSize: Style.font.body
                          font.bold: true
                          font.strikeout: (activeRow.modelData && activeRow.modelData.status === "disabled")
                        }

                        // Category chip
                        BorderSurface {
                          Layout.preferredHeight: Style.space(16)
                          Layout.preferredWidth: catLabel.implicitWidth + Style.space(8)
                          radius: 3
                          color: Util.alpha(root.foreground, 0.05)
                          borderSpec: Border.flat(Util.alpha(root.foreground, 0.1), 1)

                          Text {
                            id: catLabel
                            anchors.centerIn: parent
                            text: (activeRow.modelData && activeRow.modelData.category) || "General"
                            textFormat: Text.PlainText
                            color: Util.alpha(root.foreground, 0.5)
                            font.family: Style.font.family
                            font.pixelSize: Style.font.caption - 3
                          }
                        }
                      }

                      Text {
                        Layout.fillWidth: true
                        text: (activeRow.modelData && (activeRow.modelData.command || activeRow.modelData.action)) || ""
                        textFormat: Text.PlainText
                        color: Util.alpha(root.foreground, 0.5)
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption
                        elide: Text.ElideRight
                      }
                    }

                    // Key Badge
                    KeyBadge {
                      Layout.alignment: Qt.AlignVCenter
                      keyText: (activeRow.modelData && activeRow.modelData.key) || ""
                      highlighted: Boolean(activeRow.modelData && activeRow.modelData.is_conflict)
                      accent: (activeRow.modelData && activeRow.modelData.is_conflict) ? root.urgent : root.accent
                    }

                    // Action Buttons
                    RowLayout {
                      Layout.alignment: Qt.AlignVCenter
                      spacing: Style.space(6)

                      // Edit
                      Button {
                        iconText: "✏️"
                        tooltipText: "Modify Keybinding"
                        horizontalPadding: Style.space(8)
                        verticalPadding: Style.space(4)
                        onClicked: editDialog.openEdit(activeRow.modelData)
                      }

                      // Enable (if disabled)
                      Button {
                        visible: Boolean(activeRow.modelData && activeRow.modelData.status === "disabled")
                        text: "Enable"
                        iconText: "✓"
                        accent: "#4CAF50"
                        tooltipText: "Re-enable Keybinding"
                        horizontalPadding: Style.space(8)
                        verticalPadding: Style.space(4)
                        onClicked: root.enableKeybinding(activeRow.modelData.key)
                      }

                      // Reset (if modified)
                      Button {
                        visible: Boolean(activeRow.modelData && activeRow.modelData.status === "modified")
                        iconText: "↺"
                        tooltipText: "Reset to Default (" + ((activeRow.modelData && activeRow.modelData.default_key) || "") + ")"
                        horizontalPadding: Style.space(8)
                        verticalPadding: Style.space(4)
                        onClicked: root.resetKeybinding(activeRow.modelData.key, activeRow.modelData.default_key)
                      }

                      // Disable / Delete
                      Button {
                        visible: Boolean(activeRow.modelData && activeRow.modelData.status !== "disabled")
                        iconText: (activeRow.modelData && activeRow.modelData.status === "custom") ? "🗑️" : "⊘"
                        tooltipText: (activeRow.modelData && activeRow.modelData.status === "custom") ? "Delete custom binding" : "Disable default binding"
                        horizontalPadding: Style.space(8)
                        verticalPadding: Style.space(4)
                        onClicked: {
                          if (activeRow.modelData.status === "custom") {
                            root.resetKeybinding(activeRow.modelData.key, "")
                          } else {
                            root.disableKeybinding(activeRow.modelData.key)
                          }
                        }
                      }
                    }
                  }
                }
              }

              // Empty state for active list
              BorderSurface {
                visible: root.filteredActive.length === 0
                Layout.fillWidth: true
                Layout.preferredHeight: Style.space(170)
                radius: Style.cornerRadius
                color: Util.alpha(root.foreground, 0.02)
                borderSpec: Border.flat(Util.alpha(root.foreground, 0.1), 1)

                ColumnLayout {
                  anchors.centerIn: parent
                  spacing: Style.space(10)

                  Text {
                    text: root.searchQuery.length > 0
                      ? ("No keybinding found for \"" + root.searchQuery + "\"")
                      : "No keybindings match your filter."
                    textFormat: Text.PlainText
                    color: root.foreground
                    font.family: Style.font.family
                    font.pixelSize: Style.font.body
                    font.bold: true
                    Layout.alignment: Qt.AlignHCenter
                  }

                  Text {
                    visible: root.searchQuery.length > 0
                    text: "This shortcut combination is currently free and unassigned."
                    color: Util.alpha(root.foreground, 0.6)
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                    Layout.alignment: Qt.AlignHCenter
                  }

                  Button {
                    visible: root.searchQuery.length > 0
                    text: "Create Keybinding with " + root.searchQuery
                    iconText: "➕"
                    accent: root.accent
                    selected: true
                    Layout.alignment: Qt.AlignHCenter
                    horizontalPadding: Style.space(18)
                    verticalPadding: Style.space(6)
                    onClicked: {
                      editDialog.openCreate({ default_key: root.searchQuery })
                    }
                  }
                }
              }
            }

            // =========================================================
            // --- TAB 2: MODIFIED & CUSTOM KEYBINDINGS ---
            // =========================================================
            ColumnLayout {
              visible: root.currentTab === "modified"
              Layout.fillWidth: true
              spacing: Style.space(8)

              Repeater {
                model: root.filteredModified

                BorderSurface {
                  id: modRow
                  required property var modelData
                  Layout.fillWidth: true
                  Layout.preferredHeight: Style.space(64)
                  radius: Style.cornerRadius
                  color: modRowMouse.containsMouse
                    ? Util.alpha(root.foreground, 0.06)
                    : Util.alpha(root.foreground, 0.02)
                  borderSpec: Border.flat(
                    modRowMouse.containsMouse ? Util.alpha(root.foreground, 0.25) : Util.alpha(root.foreground, 0.1),
                    1
                  )

                  MouseArea {
                    id: modRowMouse
                    anchors.fill: parent
                    hoverEnabled: true
                  }

                  RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Style.space(16)
                    anchors.rightMargin: Style.space(16)
                    spacing: Style.space(14)

                    // Status Badge Pill
                    BorderSurface {
                      Layout.preferredHeight: Style.space(22)
                      Layout.preferredWidth: modStatusText.implicitWidth + Style.space(14)
                      Layout.alignment: Qt.AlignVCenter
                      radius: Style.cornerRadius

                      readonly property string st: (modRow.modelData && modRow.modelData.status) || "modified"
                      color: (st === "custom") ? Util.alpha("#4CAF50", 0.2) : Util.alpha("#FF9800", 0.2)
                      borderSpec: Border.flat((st === "custom") ? "#4CAF50" : "#FF9800", 1)

                      Text {
                        id: modStatusText
                        anchors.centerIn: parent
                        text: (modRow.modelData && modRow.modelData.status ? modRow.modelData.status.toUpperCase() : "MODIFIED")
                        color: (modRow.modelData && modRow.modelData.status === "custom" ? "#4CAF50" : "#FF9800")
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption - 2
                        font.bold: true
                      }
                    }

                    // Action Info
                    ColumnLayout {
                      Layout.fillWidth: true
                      Layout.alignment: Qt.AlignVCenter
                      spacing: Style.space(2)

                      RowLayout {
                        spacing: Style.space(8)

                        Text {
                          text: (modRow.modelData && modRow.modelData.description) || "Action"
                          textFormat: Text.PlainText
                          color: root.foreground
                          font.family: Style.font.family
                          font.pixelSize: Style.font.body
                          font.bold: true
                        }

                        BorderSurface {
                          Layout.preferredHeight: Style.space(16)
                          Layout.preferredWidth: modCatLabel.implicitWidth + Style.space(8)
                          radius: 3
                          color: Util.alpha(root.foreground, 0.05)
                          borderSpec: Border.flat(Util.alpha(root.foreground, 0.1), 1)

                          Text {
                            id: modCatLabel
                            anchors.centerIn: parent
                            text: (modRow.modelData && modRow.modelData.category) || "Custom"
                            textFormat: Text.PlainText
                            color: Util.alpha(root.foreground, 0.5)
                            font.family: Style.font.family
                            font.pixelSize: Style.font.caption - 3
                          }
                        }
                      }

                      Text {
                        Layout.fillWidth: true
                        text: (modRow.modelData && (modRow.modelData.command || modRow.modelData.action)) || ""
                        textFormat: Text.PlainText
                        color: Util.alpha(root.foreground, 0.5)
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption
                        elide: Text.ElideRight
                      }
                    }

                    // Key Badge
                    KeyBadge {
                      Layout.alignment: Qt.AlignVCenter
                      keyText: (modRow.modelData && modRow.modelData.key) || ""
                    }

                    // Action Buttons
                    RowLayout {
                      Layout.alignment: Qt.AlignVCenter
                      spacing: Style.space(6)

                      Button {
                        iconText: "✏️"
                        tooltipText: "Modify Keybinding"
                        horizontalPadding: Style.space(8)
                        verticalPadding: Style.space(4)
                        onClicked: editDialog.openEdit(modRow.modelData)
                      }

                      Button {
                        visible: Boolean(modRow.modelData && modRow.modelData.status === "modified")
                        iconText: "↺"
                        tooltipText: "Reset to Default (" + ((modRow.modelData && modRow.modelData.default_key) || "") + ")"
                        horizontalPadding: Style.space(8)
                        verticalPadding: Style.space(4)
                        onClicked: root.resetKeybinding(modRow.modelData.key, modRow.modelData.default_key)
                      }

                      Button {
                        visible: Boolean(modRow.modelData && modRow.modelData.status === "custom")
                        iconText: "🗑️"
                        tooltipText: "Delete custom binding"
                        horizontalPadding: Style.space(8)
                        verticalPadding: Style.space(4)
                        onClicked: root.resetKeybinding(modRow.modelData.key, "")
                      }
                    }
                  }
                }
              }

              // Empty state for modified tab
              Text {
                visible: root.filteredModified.length === 0
                text: "No modified or custom keybindings found. All shortcuts are currently set to Omarchy defaults."
                color: Util.alpha(root.foreground, 0.5)
                font.family: Style.font.family
                font.pixelSize: Style.font.body
                Layout.alignment: Qt.AlignHCenter
                Layout.topMargin: Style.space(40)
              }
            }

            // =========================================================
            // --- TAB 3: AVAILABLE CATALOG ---
            // =========================================================
            ColumnLayout {
              visible: root.currentTab === "catalog"
              Layout.fillWidth: true
              spacing: Style.space(8)

              Repeater {
                model: root.filteredCatalog

                BorderSurface {
                  id: catalogRow
                  required property var modelData
                  Layout.fillWidth: true
                  Layout.preferredHeight: Style.space(60)
                  radius: Style.cornerRadius
                  color: catalogRowMouse.containsMouse
                    ? Util.alpha(root.foreground, 0.06)
                    : Util.alpha(root.foreground, 0.02)
                  borderSpec: Border.flat(
                    catalogRowMouse.containsMouse ? Util.alpha(root.foreground, 0.25) : Util.alpha(root.foreground, 0.1),
                    1
                  )

                  MouseArea {
                    id: catalogRowMouse
                    anchors.fill: parent
                    hoverEnabled: true
                  }

                  RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Style.space(16)
                    anchors.rightMargin: Style.space(16)
                    spacing: Style.space(14)

                    // Category tag
                    BorderSurface {
                      Layout.preferredHeight: Style.space(22)
                      Layout.preferredWidth: catPill.implicitWidth + Style.space(14)
                      Layout.alignment: Qt.AlignVCenter
                      radius: Style.cornerRadius
                      color: Util.alpha(root.accent, 0.1)
                      borderSpec: Border.flat(Util.alpha(root.accent, 0.3), 1)

                      Text {
                        id: catPill
                        anchors.centerIn: parent
                        text: catalogRow.modelData.category
                        textFormat: Text.PlainText
                        color: root.accent
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption - 2
                        font.bold: true
                      }
                    }

                    // Action Info
                    ColumnLayout {
                      Layout.fillWidth: true
                      Layout.alignment: Qt.AlignVCenter
                      spacing: Style.space(2)

                      Text {
                        text: catalogRow.modelData.name
                        textFormat: Text.PlainText
                        color: root.foreground
                        font.family: Style.font.family
                        font.pixelSize: Style.font.body
                        font.bold: true
                      }

                      Text {
                        Layout.fillWidth: true
                        text: catalogRow.modelData.description
                        textFormat: Text.PlainText
                        color: Util.alpha(root.foreground, 0.5)
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption
                        elide: Text.ElideRight
                      }
                    }

                    // Current Key or Suggested Key
                    RowLayout {
                      Layout.alignment: Qt.AlignVCenter
                      spacing: Style.space(8)

                      Text {
                        visible: !catalogRow.modelData.is_bound && catalogRow.modelData.default_key
                        text: "Suggested:"
                        color: Util.alpha(root.foreground, 0.4)
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption
                      }

                      KeyBadge {
                        keyText: catalogRow.modelData.current_key || catalogRow.modelData.default_key || ""
                        highlighted: catalogRow.modelData.is_bound
                      }
                    }

                    // "+ Bind" Button
                    Button {
                      Layout.alignment: Qt.AlignVCenter
                      text: catalogRow.modelData.is_bound ? "Rebind" : "+ Bind"
                      accent: root.accent
                      selected: !catalogRow.modelData.is_bound
                      horizontalPadding: Style.space(12)
                      verticalPadding: Style.space(4)
                      onClicked: editDialog.openCreate(catalogRow.modelData)
                    }
                  }
                }
              }

              // Empty state for catalog
              Text {
                visible: root.filteredCatalog.length === 0
                text: "No catalog actions match your search."
                color: Util.alpha(root.foreground, 0.4)
                font.family: Style.font.family
                font.pixelSize: Style.font.body
                Layout.alignment: Qt.AlignHCenter
                Layout.topMargin: Style.space(40)
              }
            }

            // =========================================================
            // --- TAB 4: CONFLICTS VIEW (POLISHED CARDS) ---
            // =========================================================
            ColumnLayout {
              visible: root.currentTab === "conflicts"
              Layout.fillWidth: true
              spacing: Style.space(12)

              Repeater {
                model: root.filteredConflicts

                BorderSurface {
                  id: conflictCard
                  required property var modelData
                  Layout.fillWidth: true
                  radius: Style.cornerRadius
                  color: Util.alpha(root.urgent, 0.06)
                  borderSpec: Border.flat(root.urgent, 1.5)
                  padding: Style.space(18)

                  ColumnLayout {
                    width: parent.width
                    spacing: Style.space(14)

                    // Header of Conflict Card
                    RowLayout {
                      Layout.fillWidth: true
                      spacing: Style.space(12)

                      KeyBadge {
                        keyText: conflictCard.modelData.key
                        highlighted: true
                        accent: root.urgent
                        fontSize: Style.font.body
                      }

                      BorderSurface {
                        Layout.preferredHeight: Style.space(22)
                        Layout.preferredWidth: confBadgeTxt.implicitWidth + Style.space(14)
                        radius: Style.cornerRadius
                        color: Util.alpha(root.urgent, 0.25)
                        borderSpec: Border.flat(root.urgent, 1)

                        Text {
                          id: confBadgeTxt
                          anchors.centerIn: parent
                          text: (conflictCard.modelData.bindings ? conflictCard.modelData.bindings.length : 2) + " ACTIONS COLLIDING"
                          color: root.urgent
                          font.family: Style.font.family
                          font.pixelSize: Style.font.caption - 2
                          font.bold: true
                        }
                      }

                      Item { Layout.fillWidth: true }
                    }

                    Text {
                      text: "The following actions are bound to this same shortcut. Click Rebind on one of them to resolve:"
                      color: Util.alpha(root.foreground, 0.8)
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                    }

                    PanelSeparator { Layout.fillWidth: true }

                    // Conflicting items list inside card
                    ColumnLayout {
                      Layout.fillWidth: true
                      spacing: Style.space(8)

                      Repeater {
                        model: conflictCard.modelData.bindings

                        BorderSurface {
                          id: confSubRow
                          required property var modelData
                          Layout.fillWidth: true
                          Layout.preferredHeight: Style.space(48)
                          radius: Style.cornerRadius
                          color: Util.alpha(root.foreground, 0.04)
                          borderSpec: Border.flat(Util.alpha(root.foreground, 0.12), 1)

                          RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: Style.space(14)
                            anchors.rightMargin: Style.space(14)
                            spacing: Style.space(12)

                            Text {
                              text: confSubRow.modelData.description || "Action"
                              textFormat: Text.PlainText
                              color: root.foreground
                              font.family: Style.font.family
                              font.pixelSize: Style.font.body
                              font.bold: true
                              Layout.preferredWidth: Style.space(260)
                            }

                            Text {
                              text: confSubRow.modelData.action || ""
                              textFormat: Text.PlainText
                              color: Util.alpha(root.foreground, 0.5)
                              font.family: Style.font.family
                              font.pixelSize: Style.font.caption
                              elide: Text.ElideRight
                              Layout.fillWidth: true
                            }

                            Button {
                              text: "Rebind"
                              iconText: "✏️"
                              accent: root.accent
                              selected: true
                              horizontalPadding: Style.space(12)
                              verticalPadding: Style.space(4)
                              onClicked: {
                                editDialog.openEdit({
                                  key: conflictCard.modelData.key,
                                  description: confSubRow.modelData.description,
                                  action: confSubRow.modelData.action
                                })
                              }
                            }
                          }
                        }
                      }
                    }
                  }
                }
              }

              // Clean empty state when no conflicts
              BorderSurface {
                visible: root.filteredConflicts.length === 0
                Layout.fillWidth: true
                Layout.preferredHeight: Style.space(100)
                radius: Style.cornerRadius
                color: Util.alpha("#4CAF50", 0.08)
                borderSpec: Border.flat("#4CAF50", 1)

                RowLayout {
                  anchors.centerIn: parent
                  spacing: Style.space(14)

                  Text {
                    text: "🎉"
                    font.pixelSize: Style.font.title + 8
                  }

                  ColumnLayout {
                    spacing: Style.space(2)

                    Text {
                      text: "No Keybinding Conflicts Detected"
                      color: "#4CAF50"
                      font.family: Style.font.family
                      font.pixelSize: Style.font.title
                      font.bold: true
                    }

                    Text {
                      text: "All active Hyprland shortcuts are cleanly mapped with zero collisions."
                      color: Util.alpha(root.foreground, 0.7)
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                    }
                  }
                }
              }
            }
          }
        }

        // 5. Toast Feedback Banner
        BorderSurface {
          id: toastCard
          visible: root.toastVisible
          Layout.fillWidth: true
          Layout.preferredHeight: Style.space(38)
          radius: Style.cornerRadius
          color: Util.alpha(root.accent, 0.2)
          borderSpec: Border.flat(root.accent, 1)

          RowLayout {
            anchors.centerIn: parent
            spacing: Style.space(10)

            Text {
              text: "✓"
              color: root.accent
              font.family: Style.font.family
              font.pixelSize: Style.font.body
              font.bold: true
            }

            Text {
              text: root.toastMessage
              color: root.foreground
              font.family: Style.font.family
              font.pixelSize: Style.font.body
              font.bold: true
            }
          }
        }
      }

      // Add / Edit Modal Dialog
      EditKeybindDialog {
        id: editDialog
        anchors.fill: parent
        allBindings: root.modelData.active
        catalog: root.modelData.catalog
        onSaved: function(key, desc, cmd, act, oldK, overrideConf) {
          root.saveKeybinding(key, desc, cmd, act, oldK, overrideConf)
        }
      }
    }
  }
}
