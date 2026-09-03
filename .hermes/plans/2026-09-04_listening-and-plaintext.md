# Plan: Fix SUPER chord recording + marketplace PlainText blocker

## Goals
1. Fix the "Listening" key recorder: pressing `SUPER + A` records only `A` (Super stripped).
2. Resolve the marketplace review block (omacom/omarchy-plugin-marketplace#4577, HANCORE-linux):
   config-derived strings rendered without `Text.PlainText`.

## Root cause (listening)
- `KeyRecorder.qml` final-keypress branch (L601–613) **overwrites** modifier flags with
  `event.modifiers` ("strictly set ... so old ones are not preserved"). For a chord that
  Hyprland already **binds** (`SUPER + A` → Botty toggle in bindings.lua:40), the compositor
  consumes the chord and forwards the plain `a` keypress with the Super modifier bit
  stripped → pills wiped, value = "A". Unbound chords keep modifiers and work, which is
  why it seems random.
- Same class of bug in the panel's "Record to Find" capture (`KeybindsPanel.qml` L445–480):
  it composes from `event.modifiers` only.

## Approach (listening)
- Track *physically held* modifiers independently of the final event:
  - `Keys.onPressed` on a modifier key already sets pills (existing branch) — keep.
  - Add `Keys.onReleased` for modifier keys → clear the corresponding tracked flag.
  - Final keypress: compose with `event.modifiers | tracked state` (OR, never overwrite
    with stripping), then commit.
- Apply the same OR-of-tracked-modifiers logic to the panel's record-to-find handler.
- Commit only on a non-modifier key (existing behavior preserved).

## Approach (PlainText / marketplace #4577)
Add `textFormat: Text.PlainText` to every `Text` element currently lacking it across
`KeybindsPanel.qml`, `KeyRecorder.qml`, `EditKeybindDialog.qml`, `KeyBadge.qml`
(audited list: panel L508/518/526/693/829/987/1061/1162/1262/1291/1345/1359/1447/1455/1463/1490/1498;
recorder L267/299/323/376/434/490/526/535; dialog L179/190/198/217/251/295/314/337/359/386/391/436/441;
badge L79). Dynamic config-derived ones are mandatory (status badges, chips, toasts);
static labels hardened too so the next automated pass is clean.
Then reply on #4577 with the fix commit for re-verification.

## Steps
1. Patch KeyRecorder.qml: tracked-modifier state + onReleased + OR-composition.
2. Patch KeybindsPanel.qml: same in record-to-find.
3. Add Text.PlainText everywhere (4 QML files).
4. `python3 -m unittest discover tests` (backend untouched but run anyway).
5. Manual verify: record SUPER + A, SUPER + SHIFT + A (bound), SUPER + ALT + B (unbound).
6. Push, reply on #4577 with new commit hash.

## Risks
- If Hyprland swallows even the Super-L press event before the chord (unlikely — the
  pill branch exists precisely because modifier presses arrive), tracked-super would be
  missed; fallback in that case is querying modifiers via Hyprland IPC (out of scope,
  verify manually in step 5).
- Release events for swallowed chords (Super released after consumed A) may not arrive →
  a stale tracked-Super could leak into the *next* keypress; mitigate by clearing tracked
  state at startRecording() and on commit.
