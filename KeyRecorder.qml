import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import qs.Commons
import qs.Ui

// Interactive Keybinding Recorder
// Supports live keyboard listening, modifier toggle pills, quick key buttons, and real-time conflict checking
Item {
  id: root

  property string value: ""
  property bool recording: false
  property string previousValueBeforeRecord: ""
  property color foreground: Color.foreground
  property color background: Color.background
  property color accent: Color.accent
  property color urgent: Color.urgent

  // Modifier state flags
  property bool modSuper: false
  property bool modCtrl: false
  property bool modAlt: false
  property bool modShift: false
  property string mainKey: ""

  // Physically-held modifiers tracked from raw press/release events.
  // Hyprland consumes bound chords (e.g. SUPER + A) and forwards the final
  // key with its modifier bits stripped, so event.modifiers alone cannot be
  // trusted; we rebuild the chord from the modifier keys we saw go down.
  property bool holdSuper: false
  property bool holdCtrl: false
  property bool holdAlt: false
  property bool holdShift: false

  signal keyChanged(string newKey)

  // List of all active bindings for real-time collision detection
  property var allBindings: []
  property string currentDescription: ""

  readonly property var activeConflict: checkConflict(root.value)
  readonly property bool hasConflict: activeConflict !== null

  onValueChanged: {
    if (!root.recording) {
      parseCurrentValue(root.value)
    }
  }

  function parseCurrentValue(str) {
    if (!str) {
      root.modSuper = false
      root.modCtrl = false
      root.modAlt = false
      root.modShift = false
      root.mainKey = ""
      return
    }

    var raw = String(str).replace(/,/g, "+").split("+")
    var superOn = false
    var ctrlOn = false
    var altOn = false
    var shiftOn = false
    var key = ""

    for (var i = 0; i < raw.length; i++) {
      var p = raw[i].trim()
      var u = p.toUpperCase()
      if (u === "SUPER" || u === "WIN" || u === "META" || u === "MOD4") superOn = true
      else if (u === "CTRL" || u === "CONTROL") ctrlOn = true
      else if (u === "ALT" || u === "MOD1") altOn = true
      else if (u === "SHIFT") shiftOn = true
      else if (p.length > 0) key = p
    }

    root.modSuper = superOn
    root.modCtrl = ctrlOn
    root.modAlt = altOn
    root.modShift = shiftOn
    root.mainKey = key
  }

  function composeKey() {
    var mods = []
    if (root.modSuper) mods.push("SUPER")
    if (root.modShift) mods.push("SHIFT")
    if (root.modCtrl) mods.push("CTRL")
    if (root.modAlt) mods.push("ALT")

    var result = ""
    if (mods.length > 0 && root.mainKey) {
      result = mods.join(" + ") + " + " + root.mainKey
    } else if (mods.length > 0 && !root.mainKey) {
      result = mods.join(" + ")
    } else {
      result = root.mainKey
    }

    root.value = result
    root.keyChanged(result)
    return result
  }

  function normalizeKey(keyChord) {
    if (!keyChord) return ""
    var raw = String(keyChord).replace(/,/g, "+").split("+")
    var mods = []
    var key = ""
    var order = { "SUPER": 1, "SHIFT": 2, "CTRL": 3, "CONTROL": 3, "ALT": 4 }

    for (var i = 0; i < raw.length; i++) {
      var p = raw[i].trim()
      var u = p.toUpperCase()
      if (u === "SUPER" || u === "WIN" || u === "META" || u === "MOD4") {
        if (mods.indexOf("SUPER") === -1) mods.push("SUPER")
      } else if (u === "SHIFT") {
        if (mods.indexOf("SHIFT") === -1) mods.push("SHIFT")
      } else if (u === "CTRL" || u === "CONTROL") {
        if (mods.indexOf("CTRL") === -1) mods.push("CTRL")
      } else if (u === "ALT" || u === "MOD1") {
        if (mods.indexOf("ALT") === -1) mods.push("ALT")
      } else if (p.length > 0) {
        key = p
      }
    }

    mods.sort(function(a, b) { return (order[a] || 99) - (order[b] || 99) })

    if (key) {
      var uKey = key.toUpperCase()
      if (uKey === "RETURN" || uKey === "ENTER" || uKey === "SPACE" || uKey === "ESCAPE"
          || uKey === "TAB" || uKey === "BACKSPACE" || uKey === "DELETE" || uKey === "PRINT"
          || uKey === "LEFT" || uKey === "RIGHT" || uKey === "UP" || uKey === "DOWN"
          || (uKey.charAt(0) === "F" && !isNaN(parseInt(uKey.substring(1))))) {
        key = uKey
      } else if (key.length === 1 && !isNaN(parseInt(key))) {
        key = key
      } else if (key.length === 1) {
        key = key.toUpperCase()
      }
    }

    if (mods.length > 0 && key) return mods.join(" + ") + " + " + key
    if (mods.length > 0 && !key) return mods.join(" + ")
    return key
  }

  function checkConflict(key) {
    if (!key || !root.allBindings || root.allBindings.length === 0) return null
    var target = normalizeKey(key)
    if (!target) return null

    var collisions = []
    for (var i = 0; i < root.allBindings.length; i++) {
      var b = root.allBindings[i]
      if (b.status === "disabled" || b.is_mouse) continue
      if (normalizeKey(b.key) === target) {
        if (root.currentDescription && b.description === root.currentDescription) continue
        collisions.push(b)
      }
    }

    return collisions.length > 0 ? collisions : null
  }

  function startRecording() {
    root.previousValueBeforeRecord = root.value
    // Reset all modifier states cleanly for the new recording session
    root.modSuper = false
    root.modCtrl = false
    root.modAlt = false
    root.modShift = false
    root.mainKey = ""
    root.holdSuper = false
    root.holdCtrl = false
    root.holdAlt = false
    root.holdShift = false
    root.recording = true
    keyCaptureFocus.forceActiveFocus()
  }

  function stopRecording() {
    root.recording = false
    // Modifier release events can be swallowed by the compositor once
    // recording stops; drop held state so it never leaks into the next session.
    root.holdSuper = false
    root.holdCtrl = false
    root.holdAlt = false
    root.holdShift = false
    if (!root.value && root.previousValueBeforeRecord) {
      root.value = root.previousValueBeforeRecord
      parseCurrentValue(root.value)
    }
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

  implicitWidth: containerLayout.implicitWidth
  implicitHeight: containerLayout.implicitHeight

  ColumnLayout {
    id: containerLayout
    width: parent.width
    spacing: Style.space(12)

    // 1. Sleek Key Display & Recording Box
    BorderSurface {
      id: box
      Layout.fillWidth: true
      Layout.preferredHeight: Style.space(56)
      radius: Style.cornerRadius
      color: root.recording
        ? Util.alpha(root.accent, 0.12)
        : (boxMouse.containsMouse ? Util.alpha(root.foreground, 0.06) : Util.alpha(root.foreground, 0.03))
      borderSpec: Border.flat(
        root.hasConflict
          ? root.urgent
          : (root.recording ? root.accent : (boxMouse.containsMouse ? Util.alpha(root.foreground, 0.3) : Util.alpha(root.foreground, 0.15))),
        root.recording ? 1.5 : 1
      )

      MouseArea {
        id: boxMouse
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: {
          if (!root.recording) root.startRecording()
          else root.stopRecording()
        }
      }

      RowLayout {
        anchors.fill: parent
        anchors.leftMargin: Style.space(16)
        anchors.rightMargin: Style.space(16)
        spacing: Style.space(14)

        // Keyboard Icon
        Text {
          textFormat: Text.PlainText
          text: ""
          color: root.recording ? root.accent : (root.hasConflict ? root.urgent : "white")
          font.family: Style.font.family
          font.pixelSize: Style.font.title + 2
        }

        // Live badge or recording indicator
        Item {
          Layout.fillWidth: true
          Layout.fillHeight: true

          // Recording mode
          RowLayout {
            visible: root.recording
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(10)

            Rectangle {
              width: Style.space(10)
              height: Style.space(10)
              radius: width / 2
              color: root.accent

              SequentialAnimation on opacity {
                loops: Animation.Infinite
                running: root.recording
                PropertyAnimation { to: 0.2; duration: 400 }
                PropertyAnimation { to: 1.0; duration: 400 }
              }
            }

            Text {
              textFormat: Text.PlainText
              text: "Listening for keys... Press combination (e.g. CTRL + O)"
              color: root.accent
              font.family: Style.font.family
              font.pixelSize: Style.font.body
              font.bold: true
            }
          }

          // Idle mode with value
          RowLayout {
            visible: !root.recording && root.value.length > 0
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(8)

            KeyBadge {
              keyText: root.value
              fontSize: Style.font.body
              highlighted: root.hasConflict
              accent: root.hasConflict ? root.urgent : root.accent
            }
          }

          // Empty state
          Text {
            textFormat: Text.PlainText
            visible: !root.recording && root.value.length === 0
            anchors.verticalCenter: parent.verticalCenter
            text: "Click Record or select modifier pills below..."
            color: Util.alpha(root.foreground, 0.45)
            font.family: Style.font.family
            font.pixelSize: Style.font.body
          }
        }

        // Action Buttons
        RowLayout {
          spacing: Style.space(8)

          Button {
            text: root.recording ? "Done" : "Record"
            iconText: root.recording ? "✓" : "⏺"
            accent: root.recording ? root.accent : root.foreground
            selected: root.recording
            horizontalPadding: Style.space(14)
            verticalPadding: Style.space(6)
            onClicked: {
              if (root.recording) root.stopRecording()
              else root.startRecording()
            }
          }

          Button {
            visible: root.value.length > 0
            iconText: "✕"
            tooltipText: "Clear shortcut"
            horizontalPadding: Style.space(10)
            verticalPadding: Style.space(6)
            onClicked: {
              root.value = ""
              root.modSuper = false
              root.modCtrl = false
              root.modAlt = false
              root.modShift = false
              root.mainKey = ""
              root.keyChanged("")
              root.stopRecording()
            }
          }
        }
      }
    }

    // 2. Modifier Toggle Buttons & Key Field
    RowLayout {
      Layout.fillWidth: true
      spacing: Style.space(8)

      Text {
        textFormat: Text.PlainText
        text: "Modifiers:"
        color: Util.alpha(root.foreground, 0.7)
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        font.bold: true
      }

      Button {
        text: "SUPER"
        selected: root.modSuper
        accent: root.accent
        horizontalPadding: Style.space(12)
        verticalPadding: Style.space(4)
        onClicked: {
          root.modSuper = !root.modSuper
          root.composeKey()
        }
      }

      Button {
        text: "CTRL"
        selected: root.modCtrl
        accent: root.accent
        horizontalPadding: Style.space(12)
        verticalPadding: Style.space(4)
        onClicked: {
          root.modCtrl = !root.modCtrl
          root.composeKey()
        }
      }

      Button {
        text: "ALT"
        selected: root.modAlt
        accent: root.accent
        horizontalPadding: Style.space(12)
        verticalPadding: Style.space(4)
        onClicked: {
          root.modAlt = !root.modAlt
          root.composeKey()
        }
      }

      Button {
        text: "SHIFT"
        selected: root.modShift
        accent: root.accent
        horizontalPadding: Style.space(12)
        verticalPadding: Style.space(4)
        onClicked: {
          root.modShift = !root.modShift
          root.composeKey()
        }
      }

      Item { Layout.fillWidth: true }

      Text {
        textFormat: Text.PlainText
        text: "Key:"
        color: Util.alpha(root.foreground, 0.7)
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        font.bold: true
      }

      TextField {
        id: manualKeyField
        Layout.preferredWidth: Style.space(110)
        text: root.mainKey
        placeholderText: "e.g. A, TAB"
        onTextChanged: {
          if (root.mainKey !== text.trim()) {
            root.mainKey = text.trim()
            root.composeKey()
          }
        }
      }
    }

    // 3. Quick Key Chips
    Flow {
      Layout.fillWidth: true
      spacing: Style.space(6)

      Repeater {
        model: ["RETURN", "SPACE", "ESCAPE", "TAB", "BACKSPACE", "DELETE", "PRINT", "F1", "F2", "F5", "F10", "F12"]

        BorderSurface {
          id: quickKeyChip
          required property string modelData
          height: Style.space(26)
          width: chipTxt.implicitWidth + Style.space(16)
          radius: Style.cornerRadius
          color: root.mainKey.toUpperCase() === modelData
            ? Util.alpha(root.accent, 0.22)
            : (chipMouse.containsMouse ? Util.alpha(root.foreground, 0.1) : Util.alpha(root.foreground, 0.04))
          borderSpec: Border.flat(
            root.mainKey.toUpperCase() === modelData ? root.accent : Util.alpha(root.foreground, 0.15),
            1
          )

          MouseArea {
            id: chipMouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: {
              root.mainKey = quickKeyChip.modelData
              manualKeyField.text = quickKeyChip.modelData
              root.composeKey()
            }
          }

          Text {
            textFormat: Text.PlainText
            id: chipTxt
            anchors.centerIn: parent
            text: quickKeyChip.modelData
            color: root.mainKey.toUpperCase() === quickKeyChip.modelData ? root.accent : root.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            font.bold: root.mainKey.toUpperCase() === quickKeyChip.modelData
          }
        }
      }
    }

    // 4. Conflict Alert Banner with proper internal margin
    BorderSurface {
      id: conflictCard
      visible: root.hasConflict
      Layout.fillWidth: true
      Layout.preferredHeight: conflictCard.contentTopInset + conflictCard.contentBottomInset + conflictRowLayout.implicitHeight
      radius: Style.cornerRadius
      color: Util.alpha(root.urgent, 0.12)
      borderSpec: Border.flat(root.urgent, 1)
      padding: Style.space(14)

      Item {
        anchors.fill: parent
        anchors.topMargin: conflictCard.contentTopInset
        anchors.rightMargin: conflictCard.contentRightInset
        anchors.bottomMargin: conflictCard.contentBottomInset
        anchors.leftMargin: conflictCard.contentLeftInset

        RowLayout {
          id: conflictRowLayout
          anchors.fill: parent
          spacing: Style.space(12)

          Text {
            textFormat: Text.PlainText
            text: "⚠️"
            font.pixelSize: Style.font.title
          }

          ColumnLayout {
            Layout.fillWidth: true
            spacing: Style.space(2)

            Text {
              textFormat: Text.PlainText
              text: "Shortcut Collision Detected"
              color: root.urgent
              font.family: Style.font.family
              font.pixelSize: Style.font.body
              font.bold: true
            }

            Text {
              textFormat: Text.PlainText
              Layout.fillWidth: true
              text: {
                if (!root.activeConflict || root.activeConflict.length === 0) return ""
                var descList = []
                for (var i = 0; i < root.activeConflict.length; i++) {
                  descList.push('"' + root.activeConflict[i].description + '"')
                }
                return 'The shortcut ' + root.value + ' is currently bound to ' + descList.join(", ") + '. Saving will reassign this shortcut.'
              }
              color: root.foreground
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }
          }
        }
      }
    }
  }

  // Keyboard Event Catcher Scope
  FocusScope {
    id: keyCaptureFocus
    anchors.fill: parent
    focus: root.recording

    Keys.enabled: root.recording
    Keys.priority: Keys.BeforeItem

    // Focus lost mid-chord (e.g. Alt+Tab) means its release goes elsewhere:
    // drop the held state so a stale modifier can't contaminate this session.
    onActiveFocusChanged: {
      if (!activeFocus && root.recording) {
        root.holdSuper = false
        root.holdCtrl = false
        root.holdAlt = false
        root.holdShift = false
      }
    }

    Keys.onPressed: function(event) {
      if (!root.recording) return

      var isSuper = (event.modifiers & Qt.MetaModifier) !== 0 || event.key === Qt.Key_Meta || event.key === Qt.Key_Super_L || event.key === Qt.Key_Super_R
      var isCtrl = (event.modifiers & Qt.ControlModifier) !== 0 || event.key === Qt.Key_Control
      var isAlt = (event.modifiers & Qt.AltModifier) !== 0 || event.key === Qt.Key_Alt || event.key === Qt.Key_AltGr
      var isShift = (event.modifiers & Qt.ShiftModifier) !== 0 || event.key === Qt.Key_Shift

      // Escape alone without any modifiers cancels recording and restores previous value
      if (event.key === Qt.Key_Escape && !isSuper && !isCtrl && !isAlt && !isShift) {
        root.stopRecording()
        event.accepted = true
        return
      }

      // Modifier-only keypress: track held state and live-update modifier pills.
      // Held flags come from the physical key identity only, never from
      // event.modifiers: AltGr reports ControlModifier+AltModifier but releases
      // as a single Alt event, which would latch a phantom CTRL that nothing clears.
      if (event.key === Qt.Key_Control || event.key === Qt.Key_Shift || event.key === Qt.Key_Alt || event.key === Qt.Key_AltGr || event.key === Qt.Key_Meta || event.key === Qt.Key_Super_L || event.key === Qt.Key_Super_R) {
        root.holdCtrl = event.key === Qt.Key_Control
        root.holdShift = event.key === Qt.Key_Shift
        root.holdAlt = event.key === Qt.Key_Alt || event.key === Qt.Key_AltGr
        root.holdSuper = event.key === Qt.Key_Meta || event.key === Qt.Key_Super_L || event.key === Qt.Key_Super_R
        root.modSuper = isSuper || root.holdSuper
        root.modCtrl = isCtrl || root.holdCtrl
        root.modAlt = isAlt || root.holdAlt
        root.modShift = isShift || root.holdShift
        root.mainKey = ""
        root.composeKey()
        event.accepted = true
        return
      }

      var keyName = root.translateQtKey(event)
      if (keyName.length > 0) {
        // Union of the event's modifiers and the physically-tracked ones:
        // Hyprland strips modifier bits when it consumes a bound chord, so
        // event.modifiers alone would record bare "A" for a SUPER + A chord.
        var superOn = (event.modifiers & Qt.MetaModifier) !== 0 || root.holdSuper
        var ctrlOn = (event.modifiers & Qt.ControlModifier) !== 0 || root.holdCtrl
        var altOn = (event.modifiers & Qt.AltModifier) !== 0 || root.holdAlt
        var shiftOn = (event.modifiers & Qt.ShiftModifier) !== 0 || root.holdShift
        root.modSuper = superOn
        root.modCtrl = ctrlOn
        root.modAlt = altOn
        root.modShift = shiftOn
        root.mainKey = keyName
        manualKeyField.text = keyName
        root.composeKey()
        root.recording = false
        // Chord committed: drop held state so it cannot leak into the next session
        root.holdSuper = false
        root.holdCtrl = false
        root.holdAlt = false
        root.holdShift = false
        event.accepted = true
      }
    }

    Keys.onReleased: function(event) {
      if (!root.recording) return
      // Clear tracked state when a modifier physically goes up, so a chord
      // typed after releasing Super doesn't inherit it.
      if (event.key === Qt.Key_Control) root.holdCtrl = false
      else if (event.key === Qt.Key_Shift) root.holdShift = false
      else if (event.key === Qt.Key_Alt || event.key === Qt.Key_AltGr) root.holdAlt = false
      else if (event.key === Qt.Key_Meta || event.key === Qt.Key_Super_L || event.key === Qt.Key_Super_R) root.holdSuper = false
    }
  }
}
