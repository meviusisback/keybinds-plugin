import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import qs.Commons
import qs.Ui

// Modal dialog for adding or modifying a keybinding
// Strictly implements Omarchy border insets, standard typography, clean spacing, and smart free key recommendations
Item {
  id: root

  property bool opened: false
  property bool isEditing: false
  property var itemData: null
  property var allBindings: []
  property var catalog: []

  property string actionTitle: ""
  property string actionCommand: ""
  property string actionCategory: "Custom"
  property string actionKey: ""
  property string oldKey: ""
  property string actionType: "preset" // "preset" | "custom"

  property color background: Color.background
  property color foreground: Color.foreground
  property color accent: Color.accent
  property color urgent: Color.urgent

  signal saved(string key, string description, string command, string action, string oldKey, bool overrideConflict)
  signal canceled()

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
    // 1. If we have a first letter, prioritize variations of that letter
    if (firstLetter) {
      candidates.push("SUPER + " + firstLetter)
      candidates.push("SUPER + ALT + " + firstLetter)
      candidates.push("SUPER + SHIFT + " + firstLetter)
      candidates.push("SUPER + CTRL + " + firstLetter)
    }

    // 2. Standard ergonomic chords
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

    if (presetItem) {
      root.actionTitle = presetItem.name || presetItem.description || ""
      root.actionCommand = presetItem.command || ""
      root.actionCategory = presetItem.category || "General"
      root.actionKey = presetItem.default_key || ""
      root.actionType = "preset"
    } else {
      root.actionTitle = ""
      root.actionCommand = ""
      root.actionCategory = "Custom"
      root.actionKey = ""
      root.actionType = "custom"
    }

    keyRecorder.value = root.actionKey
    root.opened = true
  }

  function openEdit(bindingItem) {
    root.isEditing = true
    root.itemData = bindingItem || null
    root.actionTitle = (bindingItem && bindingItem.description) || ""
    root.actionCommand = (bindingItem && (bindingItem.action || bindingItem.command)) || ""
    root.actionCategory = (bindingItem && bindingItem.category) || "Custom"
    root.actionKey = (bindingItem && bindingItem.key) || ""
    root.oldKey = (bindingItem && bindingItem.key) || ""
    root.actionType = (bindingItem && bindingItem.source === "default") ? "preset" : "custom"

    keyRecorder.value = root.actionKey
    root.opened = true
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
      width: Math.min(parent.width - Style.space(64), Style.space(640))
      implicitHeight: card.contentTopInset + card.contentBottomInset + cardLayout.implicitHeight
      anchors.centerIn: parent
      color: root.background
      borderSpec: Border.flat(Util.alpha(root.foreground, 0.25), 1)
      radius: Style.cornerRadius
      padding: Style.space(28)

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
        spacing: Style.space(16)

        // 1. Header
        RowLayout {
          Layout.fillWidth: true
          spacing: Style.space(14)

          Text {
            textFormat: Text.PlainText
            text: ""
            color: "white"
            font.family: Style.font.family
            font.pixelSize: Style.font.title + 8
          }

          ColumnLayout {
            Layout.fillWidth: true
            spacing: Style.space(2)

            Text {
              textFormat: Text.PlainText
              text: root.isEditing ? "Edit Keybinding" : "Create New Keybinding"
              color: root.foreground
              font.family: Style.font.family
              font.pixelSize: Style.font.title
              font.bold: true
            }

            Text {
              textFormat: Text.PlainText
              text: root.isEditing
                ? "Update the shortcut combination or action details below."
                : "Assign a shortcut to a system action or define a custom terminal command."
              color: Util.alpha(root.foreground, 0.6)
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
          }
        }

        PanelSeparator { Layout.fillWidth: true }

        // 2. Action Type Selector (Only when creating new)
        RowLayout {
          visible: !root.isEditing
          Layout.fillWidth: true
          spacing: Style.space(12)

          Text {
            textFormat: Text.PlainText
            text: "Action Source:"
            color: root.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.body
            font.bold: true
          }

          ButtonGroup {
            id: typeGroup

            Button {
              text: "Preset Catalog Action"
              selected: root.actionType === "preset"
              horizontalPadding: Style.space(16)
              verticalPadding: Style.space(4)
              onClicked: root.actionType = "preset"
            }
            Button {
              text: "Custom Command / Script"
              selected: root.actionType === "custom"
              horizontalPadding: Style.space(16)
              verticalPadding: Style.space(4)
              onClicked: root.actionType = "custom"
            }
          }
        }

        // Preset Selector Row (when creating from catalog)
        ColumnLayout {
          visible: !root.isEditing && root.actionType === "preset"
          Layout.fillWidth: true
          spacing: Style.space(6)

          Text {
            textFormat: Text.PlainText
            text: "Popular Catalog Presets:"
            color: Util.alpha(root.foreground, 0.8)
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            font.bold: true
          }

          Flow {
            Layout.fillWidth: true
            spacing: Style.space(6)

            Repeater {
              model: (root.catalog && root.catalog.slice(0, 10)) || []

              BorderSurface {
                id: presetChip
                required property var modelData
                height: Style.space(28)
                width: chipText.implicitWidth + Style.space(18)
                radius: Style.cornerRadius
                color: root.actionTitle === modelData.name
                  ? Util.alpha(root.accent, 0.22)
                  : (chipMouse.containsMouse ? Util.alpha(root.foreground, 0.08) : Util.alpha(root.foreground, 0.04))
                borderSpec: Border.flat(
                  root.actionTitle === modelData.name ? root.accent : Util.alpha(root.foreground, 0.15),
                  1
                )

                MouseArea {
                  id: chipMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: {
                    root.actionTitle = modelData.name
                    root.actionCommand = modelData.command
                    root.actionCategory = modelData.category
                    if (!keyRecorder.value && modelData.default_key) {
                      keyRecorder.value = modelData.default_key
                    }
                  }
                }

                Text {
                  textFormat: Text.PlainText
                  id: chipText
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

        // 3. Name / Description Field
        ColumnLayout {
          Layout.fillWidth: true
          spacing: Style.space(4)

          Text {
            textFormat: Text.PlainText
            text: "Action Name / Description:"
            color: root.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.body
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

        // 4. Command Field (Visible for custom actions or when editing)
        ColumnLayout {
          visible: root.actionType === "custom" || root.isEditing
          Layout.fillWidth: true
          spacing: Style.space(4)

          Text {
            textFormat: Text.PlainText
            text: "Command / Dispatcher:"
            color: root.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.body
            font.bold: true
          }

          TextField {
            id: cmdInput
            Layout.fillWidth: true
            text: root.actionCommand
            placeholderText: "e.g. alacritty -e btop, omarchy-capture-screenshot"
            onTextChanged: root.actionCommand = text
          }
        }

        // 5. Interactive Shortcut Key Input & Suggestions
        ColumnLayout {
          Layout.fillWidth: true
          spacing: Style.space(8)

          Text {
            textFormat: Text.PlainText
            text: "Keyboard Shortcut:"
            color: root.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.body
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
            spacing: Style.space(6)

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
              spacing: Style.space(8)

              Repeater {
                model: root.suggestedKeys

                BorderSurface {
                  id: suggestChip
                  required property string modelData
                  height: Style.space(28)
                  width: suggestChipRow.implicitWidth + Style.space(18)
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
            horizontalPadding: Style.space(20)
            verticalPadding: Style.space(6)
            onClicked: root.close()
          }

          Button {
            text: keyRecorder.hasConflict ? "Override & Save" : "Save & Apply"
            accent: keyRecorder.hasConflict ? root.urgent : root.accent
            selected: true
            horizontalPadding: Style.space(24)
            verticalPadding: Style.space(6)
            enabled: root.actionTitle.length > 0 && root.actionKey.length > 0
            onClicked: {
              root.saved(
                root.actionKey,
                root.actionTitle,
                root.actionCommand,
                "",
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
