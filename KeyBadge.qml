import QtQuick
import qs.Commons
import qs.Ui

// Visual keyboard badge for rendering shortcut combinations (e.g. SUPER + SHIFT + RETURN)
Item {
  id: root

  property string keyText: ""
  property color foreground: Color.foreground
  property color accent: Color.accent
  property color keyBackground: Util.alpha(Color.foreground, 0.08)
  property color keyBorder: Util.alpha(Color.foreground, 0.22)
  property real fontSize: Style.font.caption
  property bool highlighted: false

  implicitWidth: row.implicitWidth
  implicitHeight: row.implicitHeight

  readonly property var keyParts: {
    if (!root.keyText) return []
    var raw = String(root.keyText).replace(/,/g, " + ").split("+")
    var res = []
    for (var i = 0; i < raw.length; i++) {
      var item = raw[i].trim()
      if (item.length > 0) res.push(item)
    }
    return res
  }

  Row {
    id: row
    spacing: Style.space(4)
    anchors.verticalCenter: parent.verticalCenter

    Repeater {
      model: root.keyParts

      Row {
        id: partRow
        required property string modelData
        required property int index
        spacing: Style.space(4)
        anchors.verticalCenter: parent.verticalCenter

        readonly property bool isModifier: {
          var u = modelData.toUpperCase()
          return u === "SUPER" || u === "CTRL" || u === "ALT" || u === "SHIFT" || u === "WIN"
        }

        BorderSurface {
          id: badge
          anchors.verticalCenter: parent.verticalCenter
          implicitHeight: Style.space(24)
          implicitWidth: Math.max(Style.space(24), keyLabel.implicitWidth + Style.space(12))
          radius: Style.cornerRadius
          color: root.highlighted
            ? Util.alpha(root.accent, 0.25)
            : (partRow.isModifier ? Util.alpha(root.accent, 0.12) : root.keyBackground)
          borderSpec: Border.flat(
            root.highlighted
              ? root.accent
              : (partRow.isModifier ? Util.alpha(root.accent, 0.5) : root.keyBorder),
            1
          )

          Text {
            id: keyLabel
            anchors.centerIn: parent
            text: partRow.modelData
            textFormat: Text.PlainText
            color: root.highlighted ? root.accent : (partRow.isModifier ? root.accent : root.foreground)
            font.family: Style.font.family
            font.pixelSize: root.fontSize
            font.bold: true
          }
        }

        Text {
          visible: partRow.index < root.keyParts.length - 1
          anchors.verticalCenter: parent.verticalCenter
          text: "+"
          color: Util.alpha(root.foreground, 0.4)
          font.family: Style.font.family
          font.pixelSize: root.fontSize
          font.bold: true
        }
      }
    }
  }
}
