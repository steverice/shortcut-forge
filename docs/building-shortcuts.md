# Building Shortcuts programmatically

What Shortcuts actually does, measured on devices and simulators. These
notes were written in brightwheel-checkin, whose shortcuts are the worked
examples below, and moved here when the code that embodies them did. The
toolchain is the shortcuts-playground plugin, which supplies
`validate-shortcut`, `sign-shortcut`, `resolve-icon`, a bundled ToolKit
snapshot, and a library of real shortcuts under `golden-shortcuts/`;
`shortcut_forge_lib.toolchain` wraps the first two.

A claim in here is a measurement. When one turns out to be wrong, withdraw
it rather than patch a new theory over it, and say what was measured.

## Ground truth beats documentation

The single most useful technique found here, and the one to reach for first.

**An iCloud share link exposes an unsigned plist.** Ask for a link to a working
shortcut and read it directly:

```bash
curl -s https://www.icloud.com/shortcuts/api/records/<id> -o rec.json
# fields.shortcut.value.downloadURL      -> unsigned binary plist, readable
# fields.signedShortcut.value.downloadURL -> AEA1, encrypted, not readable
# fields.icon_glyph / fields.icon_color   -> right there in the JSON
python3 -c "import plistlib;print(plistlib.load(open('shared.bin','rb')))"
```

Signed `.shortcut` files really are opaque — `AEA1` magic bytes, and `aa extract`
fails with "Failed to create decryption stream" — so a shared *file* is useless.
A shared *link* is not.

Every serialization bug in this project's history was a case where a verified
sample would have given the answer in one step instead of a test-on-device round
trip. When a plist shape is uncertain, ask for a link before inferring.

The `golden-shortcuts/` library is the other source of truth, and it is worth
grepping before trusting prose: if no golden shortcut uses a pattern, treat that
pattern as unproven.

## Where the bundled docs are wrong

Each of these passes the validator, imports cleanly, and then misbehaves.

| Topic | Documented | Actually works |
|---|---|---|
| Format Date custom pattern | `WFDateFormat="Custom"` + pattern in `WFDateFormatString` | Pattern goes **in `WFDateFormat`**, with `WFDateFormatStyle="Custom"` and no `WFDateFormatString` |
| `WFRequestVariable` | ACTIONS.md File Body example shows `WFTextTokenString` | Must be a `WFTextTokenAttachment` (SKILL.md rule 9 is the correct one) |
| Multi-condition If | CONTROL_FLOW.md shows numeric rows with `WFNumberValue` | Numeric rows import empty and red; use Match Text + Count + a numeric If |
| "Open Code Scanner" | Grounding catalog lists `com.apple.BarcodeScanner.BarcodeScannerIntent` under that display name | Plain `is.workflow.actions.openapp` with `WFAppIdentifier` + `WFSelectedApp`. (This project no longer opens the app at all — `scanbarcode` replaced it — but the lesson stands.) |
| Glyph numbers | `shortcuts-official-glyph-mapping.json` | Right for many entries, but `59692` documented as `circledDownArrow` renders as a chevron |
| `scanbarcode` | macOS-only, and requires `imageFile` | Works on iOS 27 as a **live scanner**: `WFScanCodeActionMode = 0`, no image input, output named `QR/Barcodes` |

The Format Date one is the nastiest: the validator only enforces its
`WFDateFormatString` rules when `WFDateFormat == "Custom"`, so the wrong shape is
precisely the shape it never checks, and it yields an **empty string** rather than
an error.

The `scanbarcode` row is the clearest case of the bundled ToolKit snapshot being
*incomplete* rather than wrong: `toolkit-v78-ios27-tool-ids.json` has no entry,
so the validator concludes macOS-only, and the documented `imageFile` parameter
describes the macOS scan-an-image variant. The iOS action is a live camera
scanner that returns decoded text directly. Absence from the snapshot is not
evidence of absence on the device.

**AppIntents need an `AppIntentDescriptor`**, and no verified example of one
exists in the catalogs or the golden library. An invented descriptor does not
degrade gracefully — it makes the *entire shortcut* fail to import with "contains
features not supported on this device". Prefer an ordinary
`is.workflow.actions.*` action whenever one exists, even when an AppIntent shares
its display name.

## Actions with no verified example

Treat these as unproven until a sample turns up. Each appears in the golden
library with **empty parameters**, or not at all, so their wiring is guesswork:

| Action | Status |
|---|---|
| `Get Item from List` | present with no parameters — no example of `WFItemSpecifier` or any index |
| `Split Text` | present with no parameters — no example of a separator in use |
| `Run Shortcut` | no golden example; shape recovered from a user's share link |
| AppIntent `AppIntentDescriptor` | no example anywhere; an invented one breaks the whole import |
| Multi-condition `WFConditions` numeric rows | no example; imports red and empty |

Working around a missing shape is usually cheap. Carrying a name and an id
through a loop *looks* like it needs Split Text; reading both off the loop item
by key path does the same job with primitives that are proven.

## Loops

- **Nested loops renumber the item.** A Repeat with Each inside a `Repeat 2`
  exposes its item as **`Repeat Item 2`**, not `Repeat Item` — a *count*-style
  outer loop shifts the numbering just as a nested Repeat with Each does, which
  `BEST_PRACTICES.md` does not say. Confirmed on device with a probe. Getting it
  wrong fails silently: the item reads empty and everything derived from it is
  empty. Capture the numbered variable into a named one immediately so it appears
  exactly once.
- **An If compares a variable to a *literal*, never to another variable.** To
  compare two runtime values, paste them into one string and match a fixed
  pattern — `11`/`00` versus `01`/`10` for a pair of booleans.
- **Idempotency makes retries free.** If each iteration already skips work that
  is done, an outer retry loop needs no memory of what succeeded.

## Choose from Menu

`WFMenuItems` on the start action and the `WFMenuItemTitle` of each case must
match exactly, and there must be one case per item. A mismatch imports without
complaint and misroutes at runtime.

## Measured on a simulator

Findings from device probes, recorded here because a claim that lives only in a
design document cannot be checked by anyone else.

- **`Get Group` works for `WFGroupIndex` 1 through 5**, and returns **one value
  per match** — a list, newline-joined when coerced to text — not just the first
  match's group. Group 4 returning different values for two students is what
  proves it tracks matches rather than merely accepting the index.
- **`Match Text` honors `^` as start-of-string.** An anchored pattern returned 1
  match where the unanchored one returned 4 on the same body. The anchor works;
  what it anchors to is the trap. Nothing reads a parsed response by position
  any more — see the re-serialization finding below.
- **`Repeat with Each` iterates a `Matches` output**, and the repeat item coerces
  to the matched substring, so `Match Text` *on the item* isolates one record.
- **`Get Group` against a single repeat item does not work.** It stores nothing
  and raises no error.
- **`is.workflow.actions.math` subtracts two runtime values**: `WFInput` and
  `WFMathOperand` both as action-output attachments, `WFMathOperation` `-`, read
  back as `Calculation Result`. This is how to compare two counts; the
  paste-and-match trick below only works for single digits.
- **Get Dictionary Value works on a parsed URL response, and dot paths work.**
  Measured against the roster endpoint: `students` returns a list that `Count`
  and `Repeat with Each` both handle, and inside the loop
  `student.object_id`, `student.first_name` and `room_states.1.room.object_id`
  all resolve off the repeat item. **Array indices in a dot path are 1-based** —
  `room_states.1` is the first element and `room_states.0` returns nothing.

  Two things it will not hand back as text: a **boolean** and a **list**. Both
  coerce to nothing. Read the containing dictionary instead —
  `room_states.1` coerces to `{"checked_in":true,…}` — and match the single
  pair out of that, which is order-independent and so unaffected by the
  re-serialization below.

  A list will not coerce to text, but it **does count**. `room_states` off a
  repeat item feeds `Count` directly and gives that child's own number of
  rooms, which is what the per-child guards use. A **missing** key counts 0
  rather than erroring — `brightwheel's `test_a_restructured_roster_stops`` renames the key
  away and the count guard is what stops the run.

  This does not reopen the prohibition on the check path's *branching*: the
  documented failures were presence tests, a dictionary parsed out of a plain
  string, and a lookup keyed by a runtime value. Reading a fixed key path off a
  response Shortcuts has already parsed is a different operation, and it is the
  one that survives key reordering.
- **`Get Contents of URL` hands Match Text a re-serialization, not the bytes on
  the wire.** A JSON response is parsed into a dictionary, and coercing that to
  text writes the keys back out in the dictionary's own order. Measured against
  a body whose wire order began `object_id, billing_status, first_name`: the
  device saw `billing_status, profile_photo, last_name, user_type,
  raw_passcode, object_id, first_name`, putting `profile_photo.object_id`
  *before* the student's own. It was stable across three runs in one process,
  which is not a contract.

  So a pattern may depend on a key's **name**, never on its **position**, and
  never on two keys being adjacent. Single-pair patterns like
  `"state"\s*:\s*"1"` are safe and are why nothing noticed until a pattern
  needed several fields from one record. Anything that has to pair fields must
  bound itself with brace structure — `[^{}]*` inside one object, or a nested
  object skipped explicitly — rather than with distance.

  **"First key" is a position too, and this one reached production.** The
  guardian id was read off `/users/me` with
  `^\{\s*"object_id"\s*:\s*"([^"]+)"`, on the reasoning that the wanted
  `object_id` is the first of that body's nineteen top-level keys while the
  other three belong to a photo, an auth method and a school invite. It is
  first on the wire, every time. It stopped being first in the
  re-serialization on 2026-08-31: the match returned nothing, the roster call
  went to `/guardians//students_for_checkin`, and that 404 — a body with no
  students in it — was announced by the roster guard as Brightwheel having
  returned no children. A read that broke, reported as an API that had not.
  Both guardian-id reads now use `Get Dictionary Value` on `object_id`.
- **A numeric `If` accepts a Math output directly.** Feeding
  `Calculation Result` to `WFCondition=2, WFNumberValue="0"` branches correctly:
  a difference of 1 took the greater-than branch, 0 took the else branch. So two
  runtime counts can be compared as Count -> Count -> Math subtract -> If, with
  no intermediate count.
- **Format Date emits an IANA timezone name.** `WFDateFormatStyle` `Custom` with
  the pattern in **`WFDateFormat`**: `VV` gives `America/Los_Angeles`, `VVVV`
  gives `Los Angeles Time`, `zzzz` gives `Pacific Daylight Time`, `ZZZZZ` gives
  `-07:00`. `DATE_TIME.md` says to set `WFDateFormat` to `Custom` and put the
  pattern in `WFDateFormatString`; that shape returns **empty**, silently.

## Silent failures to design against

Shortcuts rarely errors. It does the wrong thing quietly, so build checks that
distinguish "worked" from "looked like it worked".

- **An empty string satisfies "has any value."** A blank hour passed the guard's
  emptiness check, lost the following numeric comparison, and reported itself as
  "too early" — pointing at the wrong cause entirely. Measure presence with a
  match count.
- **A success test can match its own error body.** Testing for `"checkins"`
  reported both children checked out while nothing was posted, because the
  empty-body error is `422 {"checkins":"cannot process empty checkins"}`. Pick a
  token that appears *only* on success and verify it against real error bodies.
- **Global stored content outlives the shortcut that wrote it.** Deleting a
  shortcut leaves everything it put in the shared store behind; six throwaway
  probes were deleted and all fourteen of their global keys survived. "Delete
  and re-import to reset" is therefore not true for anything stored globally,
  which is where the school code lives. The session token is scoped to the
  shortcut for exactly this reason, so a re-import does clear that.
- **A same-name import is silently skipped — on iOS.** iOS keeps the old version
  with no warning, which is indistinguishable from a code change that did
  nothing. **macOS does something different and just as quiet:** it installs a
  second copy with a number appended, so importing `ZZ Probe Two` twice leaves
  `ZZ Probe Two` and `ZZ Probe Two 1` side by side and the new build is the
  numbered one. And choosing to *replace* the existing shortcut doesn't: it
  added a second copy under the exact same name and hid the original from the
  app, which still showed one. Get My Shortcuts and `shortcuts list` still
  returned both, and the original reappeared in the app once the new copy was
  deleted (`simulator-harness.md`, "Replace hides the old copy from the app, and nowhere
  else"; measured with iCloud sync off). Either way, delete before importing.
- **An imported shortcut is named after its file, and GitHub renames release
  assets.** GitHub replaced the spaces in `v1.3.0`'s loose assets with dots, so
  `Brightwheel Check In.shortcut` downloads as `Brightwheel.Check.In.shortcut`.
  Imported on an iOS 27 simulator, that file installed as `Brightwheel.Check.In`,
  not the `WFWorkflowName` inside it. The wrappers call Brightwheel Attendance by
  its exact name, so a loose file from a release installs shortcuts that can't
  find each other. The zip is unaffected: it's built locally, and its entries
  keep their spaces. iCloud links carry the name too.
- **Get Contents of URL exposes no HTTP status code.** Success has to be
  determined from the body.
- **Handing off to another app lets the run continue.** A clipboard read after an
  `Open App` sees stale content; a blocking **Show Alert** immediately after the
  hand-off holds the run until the user returns. Worth knowing generally, though
  this project no longer needs it — `scanbarcode` returns the decoded text
  in-process, so the hand-off went away entirely.
- **Gray input chips are normal.** An action showing a gray `Input` chip rather
  than a colored token is displaying an implicit connection to the previous
  action, not a broken wire. Inserting an action between such a pair silently
  redirects the input.
- **A control-flow block must be closed by its own action identifier.** A
  `repeat.each` opened with `WFControlFlowMode=0` and closed with a
  `conditional` at mode 2 does not error, does not warn, and does not run its
  body even once — the actions between the markers are simply skipped. It looks
  exactly like a loop whose collection was empty. Closing a Repeat with a
  Repeat, and an If with an If, is the rule; `checks.check_control_flow`
  refuses any other pairing, and `ActionList` cannot produce one.
- **Dictionary actions return empty rather than failing.** See the prohibition
  above; this is the specific reason they cost four separate debugging rounds.

## iOS 27 automations

Automations are no longer separate objects: a shortcut carries one or more
triggers at the top, which is why the Automation tab's "+" now starts a shortcut.
Triggers are added to the shortcut itself.

They cannot be generated, and a placeholder is not worth emitting. Every
variant of the location triggers requires the placemark — the ToolKit catalog
lists `WFArriveLocation` as the first parameter of both
`enter_location` and `enter_location_between`, and `WFLeaveLocation` likewise —
and it is a `redacted-local-location-token`, a device-specific value only the
on-device picker can produce.

A share link shows the split: both triggers came through in `WFWorkflowTriggers`
with their `WFArriveStartTime` / `WFArriveEndTime` and `WFArriveTimeRange`
intact and **without** any `WFArriveLocation`. The time range travels, the
placemark does not.

**Verified on iOS 27** by building that exact shape — an arrival trigger with a
time range and no placemark — signing it and importing it on a simulator: the
shortcut imports cleanly and **the trigger is silently discarded**.
`ZTRIGGERCOUNT` is 0, `ZTRIGGER` and `ZUNIFIEDTRIGGER` are empty, and the
Automation list says "No Automations". It does not import as a broken
automation you could then fix; there is simply no automation, so a generated
stub would save nobody a step. So **attach triggers last**: re-importing a
rebuilt shortcut replaces it and loses them.

A trigger stub is only possible at all for the 4 of 42 cataloged triggers that
take no parameters — external drive connected, file modified, folder changed,
and Wi-Fi disconnect-from-any. Nothing location- or time-based is among them.

Triggers also report **no output** (`outputTypeIdentifiers: ["none"]`), so a
shortcut cannot tell which one woke it. That is a real absence, not missing
metadata: 13 of the 42 cataloged triggers *do* declare an output, including
message, email, notification and file triggers.

Arrival triggers also require Settings → Privacy & Security → Location Services →
Shortcuts set to **Always**. "While Using the App" makes a geofence silently
never fire.

## Icons

An icon is a glyph number plus a color, and **nothing else**. There is no
custom-image escape hatch: `WFWorkflowIconImageData` exists as a key in
WorkflowKit, but setting it does nothing. With a glyph number alongside it the
glyph wins; with the image alone the shortcut imports with `ZGLYPHNUMBER = 0`
and draws an empty tile. The `ZSHORTCUTICON` table has columns for background
color, glyph number and the owning shortcut — there is nowhere for an image to
go.

There is also no authored description. The whole `WFWorkflow*` key set has no
Description, Subtitle or Summary; the "About This Shortcut" block on the import
sheet is derived from the actions, which is where "Can Run When Locked" comes
from. So the import sheet shows a name, an icon, and the setup questions —
nothing else you can write.

That makes a question's own `Text` the only prose slot on the whole import
flow, which is why both warnings the setup flow needs are appended to questions
rather than living somewhere more sensible: the iOS 27 "Add Shortcut is dead,
tap Skip Setup" note on the last question, and the case-sensitivity warning on
the password. `Text` takes `\n\n` and renders the paragraph break; both were
checked on a simulator.

`data/shortcuts-official-glyph-mapping.json` is not trustworthy: it calls
`59692` `circledDownArrow`, and it renders as a chevron. Rather than guess,
measure — install one shortcut per candidate number on a simulator and look.
This run of ten, read off an iOS 27 library:

| Number | Renders as | | Number | Renders as |
|---|---|---|---|---|
| 59690 | ✓ checkmark | | 59695 | ⏩ fast-forward |
| 59691 | $ in a circle | | 59696 | ‹ chevron-left |
| 59692 | ⌄ chevron-down | | 59697 | i info |
| 59693 | ⤓ download tray | | 59698 | π |
| 59694 | € euro | | 59699 | ▶ play |

Consecutive numbers are unrelated to each other, so there is no neighborhood to
search — sweeping to find a *specific* idea is wasteful. To pick a particular
icon, choose it in the on-device icon picker and then read
`ZSHORTCUTICON.ZGLYPHNUMBER` straight out of `Shortcuts.sqlite`; an iCloud share
link works too, since the record JSON exposes `icon_glyph` without a download.

Color values are palette keys, not RGB: `4292093695` renders green, not the
yellow or magenta its bytes suggest. `resolve-icon --color <name>` maps a name
to the right integer, and it has been right every time it was checked — pink,
green and red all rendered as named.

**The fastest way to choose an icon is the device's own picker**, not a sweep.
Open a shortcut → the name control → Choose Icon: it has a "Search Symbols"
field over the whole catalog and the 15 colors. Pick one, then read
`ZSHORTCUTICON.ZGLYPHNUMBER` out of `Shortcuts.sqlite` to learn its number. That
is how `62020` (sunrise) and `62019` (sunset) were identified — searching for
them by name through `resolve-icon` finds nothing, because its vocabulary is
much smaller than the picker's.

## Showing an image

There is no image parameter on any alert. Show Alert takes a title and a
message and nothing else. What works is carrying the PNG as base64 in a Text
action and decoding it at runtime:

    Text (base64)  ->  Base64 Encode [mode: Decode]  ->  Show Content

Three things that are easy to get wrong:

- **Use Show Content (`is.workflow.actions.showresult`), not Quick Look.**
  Quick Look renders the image but titles the sheet with the raw base64 string.
  Show Content has no title bar at all.
- **Something must follow Show Content.** Left as the last action, the image
  becomes the shortcut's own output, and handing an image back to the caller
  needs consent — *"Allow … to output 1 image?"* — on the very run that is
  trying to be helpful. Any following action displaces it;
  `is.workflow.actions.nothing` says so explicitly.
- **Store Content's `WFInput` must be a `WFTextTokenString` carrying exactly
  one object placeholder.** A literal string imports as an empty Content
  parameter, so the marker that records "already shown" has to come from a Text
  action rather than being written inline. Silent if you get it wrong: the gate
  never closes and the guide shows every run.

The marker is stored with `WFStoredContentGlobalValue: False`, scoping it to
the shortcut, so each wrapper explains itself once and neither speaks for the
other. Presence is measured with a match count, because an empty string still
satisfies "has any value".

Quantized to 64 colors the diagrams are ~104 KB each, ~138 KB as base64, which
takes a wrapper from 4 KB to about 149 KB. Flat UI art loses nothing at 64
colors.
## Practical notes

- **Comment discipline is enforced.** The validator requires a Comment
  immediately before every control-flow start. A helper that appends actions must
  be called *before* the comment, not between it and the `If`.
- **UUIDs must look random.** Repeating-hex placeholders are a hard error. Mint
  them with `uuidgen`.
- **Waive validator rules by name, never wholesale.** brightwheel-checkin
  waives five, each with its reason recorded — among them both `scanbarcode`
  complaints, which a device-exported shortcut disproves.
  Everything else stays fatal. That guard has caught real regressions, including a
  malformed control-flow block and a genuinely empty parameter.
- **Signing is flaky, not broken.** `shortcuts sign` intermittently returns
  "Failed to modify some records" or a 500; the wrapper retries after converting
  to a binary plist. Re-run before investigating the plist.
