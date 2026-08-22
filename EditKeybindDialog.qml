import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import qs.Commons
import qs.Ui

// Modal dialog for adding or modifying a keybinding
// Strictly implements Omarchy border insets, standard typography, and clean spacing
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
            text: ""
            color: "white"
            font.family: Style.font.family
            font.pixelSize: Style.font.title + 8
          }

          ColumnLayout {
            Layout.fillWidth: true
            spacing: Style.space(2)

            Text {
              text: root.isEditing ? "Edit Keybinding" : "Create New Keybinding"
              color: root.foreground
              font.family: Style.font.family
              font.pixelSize: Style.font.title
              font.bold: true
            }

            Text {
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

        // 5. Interactive Shortcut Key Input
        ColumnLayout {
          Layout.fillWidth: true
          spacing: Style.space(6)

          Text {
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
