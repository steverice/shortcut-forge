# The simulator harness

`shortcut_forge.sim` installs real shortcuts into the real Shortcuts app on an
iOS simulator, runs them, taps through their prompts, and reads their state
back off the device. Until it existed the only way to test a shortcut was to
AirDrop a build to a phone and see what happened. These notes were written in
brightwheel-checkin, whose suite is the worked example, and moved here with
the code. None of this is documented by Apple, and most of it failed silently
first.

## What you need

- **An iOS 27 iPhone simulator.** Xcode → Settings → Components.
  `Simulator.find()` picks a booted one, or boots the first it finds.
- **Accessibility permission** for the terminal running the tests: System
  Settings → Privacy & Security → Accessibility. Taps are synthesized as real
  mouse events, so without this the cursor moves and nothing is pressed.
- Leave the device window alone while a run is going — the taps go to real
  screen coordinates.

## Simulator.app, or Device Hub

Xcode 27 deleted `Simulator.app` and replaced it with **Device Hub**
(`Xcode.app/Contents/Applications/DeviceHub.app`, process `DeviceHub`), which
shows simulators and real devices together in one window with a sidebar. The
harness detects which one is installed and drives either; everything that
differs lives in the two `_Host` classes in `sim/harness.py`. `xcrun simctl` is
untouched under both, so every assertion about on-disk state is headless
regardless.

Three things are genuinely harder under Device Hub, and are worth knowing when
a run misbehaves:

- **Selecting the device is a click, not a command.** There is one window and it
  shows whichever device the sidebar has selected, so "no window for this
  device" is the ordinary state. Nothing in the sidebar reaches the
  accessibility tree — the split view reports *zero* children — so the harness
  filters the sidebar by typing the device name and clicks rows by position,
  checking the window title after each until it matches. Never send ⌘A hoping to
  clear that filter: ⌘A is Select All for the *device list*, and its modifier
  leaks into the clicks that follow, which quietly gathers up a multi-selection.
- **The mapping is measured, not computed.** `Point Accurate` and
  `Show Device Bezels` are both gone, bezels are always drawn, and there is no
  1:1 zoom, so `_screen_box` finds the screen in pixels: it is the widest gap
  between the two walls of dark either side of it. The bezel reads dark in both
  system appearances, and in dark mode so does the window background — which
  costs nothing, because merging the two only thickens the wall and never moves
  its inner edge. It runs once per window, on the home screen, since a dimmed
  backdrop behind a sheet swallows the screen's own edges. A shape check against
  the device's real aspect ratio turns that into an error rather than a bad
  mapping, and the failing screenshot is kept as
  `artifacts/measure-failed.png`.
- **Points and pixels are not the same number.** `screencapture -R` takes a rect
  in points and writes pixels, so on a Retina display the image is twice the
  size of the window it captured, while Quartz click coordinates stay in points.
  Every measurement divides by that scale. Also clamp the capture *width* to the
  display, not only its origin: a window can start at a negative x, and clamping
  one without the other runs the region past the far edge, where whatever window
  sits behind gets measured as bezel.

**Other devices can stay booted.** The harness brings its own device's window to
the front and verifies it arrived, because every menu it touches applies to the
frontmost window. A visionOS device in front has no "Show Device Bezels" item at
all, which used to kill the run on a missing menu item rather than on anything
real. Display settings are applied only if the frontmost window offers them; the
hardware keyboard is not optional, so a missing one there is still an error, and
it names the likely cause.

Leave the device window alone while the suite runs — the taps go to real screen
coordinates.

**The simulator cannot open files from `~/Desktop`.** `xcrun simctl openurl
<udid> file:///Users/…/Desktop/X.shortcut` fails with "Operation not permitted":
the Desktop is one of macOS's privacy-protected folders, and the simulator
process has no grant for it. The harness opens files from the repo and from temp
paths for that reason. `~/Documents` and `~/Downloads` are protected the same
way and are likely to fail too, though neither has been tried.

## What had to be worked out

None of this is documented by Apple, and most of it failed silently first.

**Installing.** `shortcuts://import-shortcut?url=…` only accepts iCloud links —
it rejects anything else *before* fetching it, so a local file cannot be
imported that way. What does work is opening the file as a **host** file URL:
simulator processes are ordinary Mac processes and see the Mac's filesystem, so
`simctl openurl "file:///Users/…/Brightwheel Check In.shortcut"` opens the
normal import sheet. **The library name comes from the filename**, not from
`WFWorkflowName`, so the signed files have to be named exactly what the
wrappers call.

**Tapping.** A synthesized click needs a `MouseMoved` event first *and*
`kCGMouseEventClickState` set; with either missing the cursor moves to the right
place and nothing is pressed. Under Simulator.app coordinates are exact once it
is set to *Window → Point Accurate* with *Show Device Bezels* off: the device
screen then starts at the window origin plus a 52pt title bar, at 3 device
pixels per point, and the harness sets both itself. Device Hub offers neither,
so there the screen is measured inside the bezel — see above.

**Finding the button.** In every prompt shape the button we want is the
bottom-most iOS-blue one — "Allow", "Always Allow", "Add Shortcut" — so the
harness finds blue rectangles by color and taps the lowest. No text
recognition, and it survives light and dark mode. Tall blue shapes are filtered
out because the shortcut tile on the import sheet is also blue.

**One trap in that rule:** the *Ask for Input* dialog's **Done** button is blue
too, so blind-tapping submits it empty — which this shortcut reads as "resend me
a code", five times over, and then gives up. Tests that expect a prompt use
`answer_prompt()` and stop the generic clearing once traffic has started.

**Typing.** `CGEventKeyboardSetUnicodeString` does nothing here. The Simulator
passes raw HID keycodes through to the guest, so every character arrives as
whatever keycode 0 is: typing `123456` puts `Aaaaaa` in the field. Real US
virtual keycodes are the only thing that works.

**HTTPS.** `simctl keychain <udid> add-root-cert` installs a CA into the
device's trust store, which is what lets Get Contents of URL talk to the mock.
iOS rejects leaf certificates without a `subjectAltName`, without
`extendedKeyUsage = serverAuth`, or valid for much beyond a year, so
`sim/certs.py` sets all three. **Erasing the device drops the trust**, so the
harness re-adds it after an erase.

**Reading Store Content.** It is two hops on disk. `ZSTOREDVALUE` in
`Shortcuts.sqlite` holds the key in `ZDISPLAYNAME` and a keyed archive naming a
file; the value itself lives in `Library/Shortcuts/PersistentStorage/<uuid>`.
Reading only the row gives you a key and no value, which is misleading rather
than obviously empty. The payload sits under `NS.string` or, when it came out of
an action, under a `WFObjectRepresentation`'s `object`.

**Consent prompts** are per capability per shortcut — running another shortcut,
network access, the clipboard — and persist until the device is erased. The
suite absorbs them in one throwaway priming run.

**Which window.** More than one simulator can be booted, and AppleScript's
"window 1" is then whichever is frontmost. Taps silently go to the wrong device
— or, if the window sizes differ, to a computed point off-screen entirely. The
harness matches the window by title (device name + OS version) and raises it
before measuring or clicking.

**Focus before typing.** Neither the Ask for Input dialog nor the setup wizard
focuses its text field, so typing straight into either goes nowhere and leaves
an empty answer. Tap the field first. The software keyboard being visible is
*not* a sign that the hardware keyboard is disconnected — it can be connected
and showing anyway, so that is not a useful diagnostic.

**Autocapitalization is on**, so `zzemail` arrives as `Zzemail`. The canary
types digits to sidestep it; anything asserting on typed letters has to account
for it or turn it off on the device.

**A run URL can be dropped** if it arrives while Shortcuts is still shutting
down — nothing happens and no error is raised. The runner re-issues the run
once if no traffic has appeared and no prompt is on screen.

## Setup questions, and a canary for them

Setup questions regressed during the iOS 27 beta cycle: answering them and
tapping "Add Shortcut" does nothing at all, while Skip Setup commits the
answers. brightwheel-checkin's `TESTING.md` carries the version-by-version
support matrix and the probes behind it. `shortcut_forge.sim.probes.setup_probe`
is the two-action canary that measures it on any runtime, and
`links.check_link` counts the questions an installed copy actually holds.

## Importing on a Mac

Worth knowing before minting iCloud links, since the link is a snapshot of
whatever the sharing device holds: **a Mac import is lossless.** The shipping
Attendance build was signed under a throwaway name, imported into the macOS 27
Shortcuts library, and read back out of `~/Library/Shortcuts/Shortcuts.sqlite`,
which has the same schema as the simulator's:

| | source | after a Mac import |
|---|---|---|
| actions | 251 | 251 |
| identifier sequence | — | identical |
| parameters differing | — | none |
| action UUIDs (164 of them) | — | all preserved |

Nothing is rewritten, dropped, or reminted on the way in.

**And nothing is rewritten on the way out either.** That copy was exported from
the Mac's Shortcuts (File → Export, *For: Anyone*, which re-signs through
iCloud), the exported file imported on an iOS 27 simulator, and the actions read
back off the device: 251 actions again, identifier sequence identical, and after
canonicalizing Shortcuts' own text-token form the *only* three differences were
the setup answers, which Skip Setup had emptied during the import.

The action that made this worth testing survives exactly:

| | `is.workflow.actions.scanbarcode` |
|---|---|
| source | `{'WFScanCodeActionMode': 0}` |
| after macOS export → iOS import | `{'WFScanCodeActionMode': 0}` |

No `imageFile` appears and the live-scanner mode is intact, so the macOS variant
of that action does not get substituted in passing. That was the plausible
silent failure — a shortcut that imports cleanly, looks right, and has no camera
at the school door.

### iCloud links carry setup questions — but check every one

The **actions** survive every route tested: 251 actions, identical identifiers,
zero differences after canonicalizing text tokens, `scanbarcode` intact. Only
`WFWorkflowImportQuestions` ever went missing, and only once.

Everything measured, each count read out of `ZSHORTCUT.ZIMPORTQUESTIONSDATA`
rather than inferred from what a sheet offered:

| | questions |
|---|---|
| the built file | 3 |
| macOS library copy, imported with Add Shortcut and no values filled | **3** |
| the same copy after a File → Export | **3** (export changes nothing) |
| macOS export → file → iOS import | **3** |
| link minted on an **iPhone** → iOS import | **3** |
| link minted on the **Mac** from that copy → iOS import | **3** |
| link minted on the Mac from the *first* probe → iOS import | **0** |

So iCloud links carry questions, from either platform, and "Add Shortcut with
the fields left empty" on macOS behaves like iOS's Skip Setup — the copy keeps
its questions.

**The last row is unexplained.** Three theories were tried and all three are
dead: an iCloud link does not strip questions (five links say otherwise);
completing setup does not consume them (the macOS copy kept all three); and
exporting does not clear them (measured before and after). The only other
difference between that probe and the rest is that its clicks were synthesized
rather than made by a person, which is not a mechanism, just the remaining
variable.

A note on what sent that investigation wrong twice: every `Brightwheel*` shortcut
in the macOS library reports zero import questions, which looked like strong
evidence that something was stripping them. They are **debug builds**, which are
generated with no import questions at all. They were never evidence.

Since the cause is unknown, rely on the check rather than the rule. It is free:
open the link yourself before sending it to anyone. A sheet offering **Set Up
Shortcut** has the questions; one offering **Add Shortcut** does not, and will
install in one tap leaving `not set` in the email, password and check-in code
actions — no error, no prompt, and a shortcut that cannot sign in.

Two limits worth keeping on the file result. It establishes that the
round-tripped shortcut is structurally identical to the build the suite
exercises, not that it was separately run end to end. And a shortcut with no
questions still installs and runs — it simply has no credentials in it.

**Deleting the shortcut didn't take its link down.** Two links still opened an
import sheet after the shortcut they were minted from had been deleted on the
Mac. ZZ Import Probe's (`7ba4b5a8…`) was checked on 2026-09-11, the day after
it was minted. Brightwheel Check In's (`b1ff396f…`) was checked minutes after the
deletion. Both deletions happened with iCloud sync for Shortcuts off, so they
may never have reached iCloud, and a deletion with sync on is unmeasured. No way
to revoke a link is known, so treat every link you mint as public for good.
Mint from builds with no credentials in them.

### The publisher's pickers go stale every release

`Create iCloud Link for Shortcut` takes a workflow reference, and the picker
stores both an identifier and a display name:

```
identifier : 5427F12F-…      the target's ZWORKFLOWID
title      : { key: "ZZ Target" }
subtitle   : { key: "ZZ Target" }
image      : { uri: intents-remote-image-proxy:… }
```

Delete the target and import it again — which is exactly what shipping a new
build requires, since iOS skips a same-name import and macOS installs a second
copy beside it, numbered or not — and it comes back with a fresh `ZWORKFLOWID`. The picker keeps the old one. Measured: stored
`5427F12F-…` against a live `28E1CE52-…`.

**The editor gives no sign of it.** It goes on displaying the target's name and
renders it as a resolved token, because the name is stored beside the identifier
as display metadata. Nothing is marked broken.

**And it is fatal: the picker does not fall back to the name.** Run on the Mac
with iCloud access allowed, after deleting and re-importing the target, the probe
stops and asks for a shortcut to be picked again. (An earlier attempt was
confounded — the probe had been denied iCloud access, and failed on permissions
before reaching the question.) So `Run Shortcut`'s behavior does not carry over:
it resolves its `workflowIdentifier` by name, and this picker does not.

The consequence worth guarding against is still the quiet one. If the old
copy is still in the library — renamed, or simply not cleared out — a picker
holding its identifier would mint a link for the **previous build** with nothing
on screen to say so. `links.check_link` catches that for free, since a link to a
stale build fails the comparison against `dist/<name>.xml`; a second reason it
earns its place.

**Nor can it be pre-seeded by name**, which is how the wrappers get away with
never knowing Brightwheel Attendance's identifier. `Run Shortcut` has a name field
the runtime reads — `WFWorkflowName`, with a `workflowIdentifier` that matches
nothing — and the picker has no equivalent. Three shapes were generated, imported,
and run on the Mac with the target present and iCloud access allowed:

| pre-seeded with | on run |
|---|---|
| a fresh UUID that matches nothing, plus the name | asks for a shortcut |
| the name only, no identifier | asks for a shortcut |
| the name in the identifier slot | asks for a shortcut |

All three validate, import, and display the target's name in the editor, so
nothing short of running one reveals the problem. The identifier must be a real,
live workflow id, and no such id exists before import — the generator cannot
supply one, and neither can any build step.

So `Brightwheel Share Links` saves the three trips through the share sheet but
not the picking, and the picking has to be redone every release.

### Looking the shortcut up at run time avoids the picker entirely

Storing no reference is what fixes it. A probe ran **Get My Shortcuts**, walked
the result with **Repeat with Each**, kept the item whose name matched, and passed
that item to Create iCloud Link as a *variable* instead of a picked value. Each
step was checked on its own:

- **The variable survives import.** Read back off an iOS 27 simulator, the
  `shortcut` parameter still holds
  `{"Value": {"VariableName": "Repeat Item", "Type": "Variable"}, …}` rather than
  being reset to an empty picker.
- **The lookup matches.** On the Mac the match branch ran, and the result was the
  iCloud page for the target.
- **The output is a usable URL.** Building Share Links' exact
  `<li><a href="…">` markup and copying it gave
  `<li><a href="https://www.icloud.com/shortcuts/686774d5…">ZZ Target</a></li>`
  on the clipboard, which is exactly what brightwheel's `update_links.py` reads.

A shortcut coerced to text gives its name, so the match needs no property
lookup: a Text action holding the Repeat Item, then Match Text and Count, the
same pattern `ActionList.count_matches()` emits. `Get Shortcut Attributes`
looks like the obvious tool and is not: its `attribute` enum covers only
toggles — share sheet, Apple Watch, menu bar, running when locked — and has no
name.

**Reading a Mac-run shortcut's output: use the clipboard, primed.** A shortcut
run on the Mac can only report back through the screen or the clipboard, and the
screen lies — see the Show Content note below. So have the probe copy its result,
put a sentinel on the clipboard before the run (`printf SENTINEL | pbcopy`), and
read it with `pbpaste` afterwards. The sentinel is what makes a run that copied
nothing look different from a result. And nobody may copy anything between the
run and the read: pasting a note into chat once overwrote a result, which the
sentinel check caught.

One thing looked like a bug and was not. Put the link straight into **Show
Content** and it reads "minted: Shortcuts", followed by the rendered page. That
is only a rich preview. The link's text form is the URL, as the clipboard shows.
Check the clipboard, not what the screen displays.

A fresh import is simply found again on the next run, so nothing needs
re-picking. The one hazard left is duplicates. Re-importing without deleting
can leave `Brightwheel Attendance 1` beside the original, and an unanchored name
match takes both. It can also leave a second copy under the exact same name,
which an anchored match takes too (see below). Count every copy, and treat more
than one as an error rather than guessing which is the new build.

**A whole library coerces to its names, one per line, and Match Text honors
`(?m)`.** Checking every copy at once means counting over the library's names
rather than one item at a time, which rested on two things nobody had measured.
A probe ran Get My Shortcuts on the Mac, put the result in a Text action, and ran
three anchored patterns over it with Match Text and Count. The library held two
shortcuts named exactly `ZZ Target` and one `ZZ Target 1`.

| pattern | Shortcuts counted | Python's `re`, same text |
|---|---|---|
| `(?m)^ZZ Target( \d+)?$` | 3 | 3 |
| `(?m)^ZZ Target \d+$` | 1 | 1 |
| `(?m)^ZZ Target$` | 2 | 2 |

The text is the names joined by `\n` and nothing else, so no lookaround or
separator-based fallback is needed. It held 123 names against 125 visible rows in
`Shortcuts.sqlite`. All four Brightwheel shortcuts were there once each. The two
missing were old, empty `New Shortcut 6` and `New Shortcut 7`. Nothing in their
rows sets them apart from `New Shortcut 4`, which is equally empty and was
returned. Why they were skipped isn't known.

**A duplicate isn't always numbered, and choosing to replace doesn't replace.**
That fixture came from importing `ZZ Target.shortcut` twice, 21 seconds apart,
over an existing `ZZ Target` on the Mac. For the first import the user chose to
replace the existing shortcut. That added a second row named exactly
`ZZ Target`, and the original stayed. More than twenty minutes later it was still
not tombstoned, not flagged `ZHIDDENFROMLIBRARYANDSYNC`, and had an unchanged
modification time, and Get My Shortcuts returned both. The second import didn't
replace, and produced `ZZ Target 1`. Either way, anchoring the match isn't
enough to catch a duplicate; count every copy, numbered or not.

**Replace hides the old copy from the app, and nowhere else.** With three rows
in the database, the app listed two, `ZZ Target` and `ZZ Target 1` (the user's
report). `shortcuts list --show-identifiers` gave all three. To find out which
`ZZ Target` the app was showing, the user renamed it to `ZZ Visible` and deleted
it. The row that disappeared was the copy Replace had added, so the original was
the one the app hid. Once the replacement was gone, the original reappeared in
the app. So a Replace duplicate can be cleared from the app: delete the copy you
can see, and the hidden one comes back into view. The delete left no row behind
at all. No shortcut in that database was tombstoned, including ones deleted
earlier in the session. iCloud sync for Shortcuts was off on that Mac, and
Replace and Delete with sync on haven't been measured.

Once, on a freshly erased simulator, the first `simctl openurl` of an iCloud
link left the home screen showing and no import sheet. The same link opened
normally on the next try. A second erase followed by an immediate open didn't
reproduce it. The verifier used to blame the link ("is the link still
live?") after a single miss, so `links.install_from_link` now opens each link
a second time before giving up. Since the miss won't reproduce on demand,
`tests/test_sim_support.py` covers the retry with a fake simulator.

## What the simulator cannot tell you

- **Scan Code does not exist there.** A simulator has no camera, and a shortcut
  reaching `is.workflow.actions.scanbarcode` dies with "an action could not be
  found". brightwheel's test builds seed the school code so the run never takes that
  branch, which means **its QR scanning path is not covered by its tests** and
  still needs a device.
- **Triggers are not exercised.** The tests start a run by URL. `simctl
  location` could drive a geofence, but a trigger cannot be generated into a
  shortcut in the first place, so there is nothing built to test.

## Two dead ends, so nobody spends the afternoon again

**Writing rows into `Shortcuts.sqlite` directly.** It gets tantalizingly close —
`siriactionsd` picks the row up and hashes it — but library membership lives in
a CRDT blob in `ZLIBRARY.ZDATA` (it starts with the magic `crdt`), so an
injected shortcut never appears and cannot be run by name. Use the import sheet.

**`shortcuts://import-shortcut`.** iCloud URLs only, as above. It is not even a
verb in `WorkflowKit`.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `no Add Shortcut button appeared` | Accessibility permission, or the Simulator window is off-screen or obscured |
| Taps land in the wrong place | Something re-enabled device bezels or changed the window scale; rerun, the harness resets both |
| A test hangs then fails to settle | Look at the artifacts directory you gave `Simulator` — a prompt shape the harness did not recognize |
| Everything fails after an erase | The CA is re-added automatically, but only on the run that erased |
| `could not select … in Device Hub's sidebar` | Seen once, straight after an erase, with the terminal in front afterward: the search text never reached the field and no row click registered. Rerunning worked, and it didn't recur in two later runs. Bring Device Hub forward and rerun |
