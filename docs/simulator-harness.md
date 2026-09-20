# The simulator harness

`shortcut_forge_lib.sim` installs real shortcuts into the real Shortcuts app on an
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

> **Through System Events this stopped working on 2026-09-17. Through the
> accessibility API addressed by process id it runs again as of 2026-09-18
> (`sim/ax.py`, branch `devicehub-ax-by-pid`), and idb is the planned end
> state.** System Events reports Device Hub with a unix id of 0, no windows and
> no menu bar, under either process name, however it is launched; the same
> process answers `AXUIElementCreateApplication(pid)` with its windows, frames,
> menu bar and presses. The sidebar and the device screen are exposed by
> neither, so taps still go through measured coordinates. See "Device Hub is
> invisible to System Events, and idb is the way out" before spending time here.

Xcode 27 deleted `Simulator.app` and replaced it with **Device Hub**
(`Xcode.app/Contents/Applications/DeviceHub.app`, process `DeviceHub`), which
shows simulators and real devices together in one window with a sidebar. The
harness detects which one is installed and drives either; everything that
differs lives in the two `_Host` classes in `sim/harness.py`. `xcrun simctl` is
untouched under both, so every assertion about on-disk state is headless
regardless.

Device Hub is driven through the accessibility API by process id (`sim/ax.py`),
not through System Events. On macOS 27.0 (26A428) with Xcode 27.0 (27A266a),
System Events lists Device Hub with a unix id of 0, no windows and no menu bar,
under either of its names (`DeviceHub`, `Device Hub`), after a restart of
System Events, and however the app was launched, while
`AXUIElementCreateApplication(pid)` reads the same process fine. The process is
found by its executable path rather than its bundle id, because a Mac with an
Xcode beta installed has two Device Hubs sharing one bundle id, and with both
running LaunchServices answers a bundle-id lookup with a process id of -1.
Simulator.app keeps the System Events route, which works there.

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
  Escape clears the field, but on an *empty* field it moves focus to the "+"
  button instead, and the device name typed next opens that button's menu and
  picks an entry by its letters — measured on macOS 27.0, where it landed on
  "Apple TV…" and opened the New Simulator sheet. The harness clicks the field
  again after Escape, which puts focus back on it either way. A device popped
  out into its own window carries the main window's title; the harness takes
  the larger of the two, which is the one with the sidebar.
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

### Device Hub is invisible to System Events, and idb is the way out

Measured 2026-09-17. Device Hub is running, visible, not background-only, and
frontmost, and System Events reports **zero windows and no menu bar** for it —
under `DeviceHub` and under `Device Hub`, after quitting it, deleting
`com.apple.dt.Devices` and relaunching, with a simulator booted. Accessibility
itself is fine: the same query returns 24 windows for Finder and 1 for iTerm2.
Someone else measured the same thing from a different codebase
([XcodeBuildMCP #535](https://github.com/getsentry/XcodeBuildMCP/issues/535):
"Device Hub exposes zero accessibility attributes. Finder, as a control, exposes
20"), so this is the app, not this Mac.

Re-measured 2026-09-18 on Xcode 27.2 beta 1 (`27B5019j`): the same through
System Events. Its Device Hub, frontmost, reports zero windows, no menu bar and
zero UI elements under both process names, while the window server lists its
two windows. Nothing in the 27.2 beta notes mentions accessibility.

**Corrected the same day: it is System Events that cannot see Device Hub, not
the accessibility API.** Measured by another session on macOS 27.0 (`26A428`)
with Xcode 27.0 (`27A266a`), from an unsandboxed shell with Accessibility
granted. System Events answered `{unix id 0, 0 windows}` and "Can't get menu
bar 1" for both process names, after `killall "System Events"`, after every way
of launching the app, and with only one Device Hub running; `application
process id <pid>` failed with -1728. `AXUIElementCreateApplication(pid)`, on the
same process, returned AXTitle "Device Hub", two windows each with an AXTitle
(`iPhone 17 Pro Max – iOS 27.0`, en dash and version suffix), AXPosition and
AXSize, the full menu bar, `Controls > Home` pressable (it pressed Home on the
device), `Device > Keyboard > Simulate Hardware Keyboard` with its check mark
readable, and a working AXRaise. Three details: `AXFocusedWindow` has an empty
title, so the front window is the one flagged AXMain in AXWindows; activation
works through `NSRunningApplication`; and with two Xcodes' Device Hubs running,
a bundle-id lookup answers with process id -1, so the pid comes from the
executable path. Still exposed by neither route: the sidebar rows, the search
field, the device screen, and SwiftUI sheets (the New Simulator sheet has no
AXSheets entry; Escape closes it). Escape on an *empty* sidebar search field
moves focus to the "+" button, and the device name typed next opens that menu
and picks an entry by its letters; the field is clicked again after Escape.

With that route in the harness (`sim/ax.py`, commit `33633a8` on branch
`devicehub-ax-by-pid`), a consumer's full simulator suite ran green on the iOS
27.0 simulator: install, consent taps, Ask for Input typing, notification and
stored-content reads, across three suite subsets and about twenty runs. So the
System Events harness is repairable, and the "no accessibility tree" heading
this section carried until then was wrong.

There is no fallback host. `Xcode.app/Contents/Developer/Applications` — where
`Simulator.app` lived — does not exist in Xcode 27, nothing named Simulator.app
is on disk, and LaunchServices has no registration for it, so `_detect_host()`
has only one branch that can ever match.

**What still works, which is most of what a harness needs.** The window server
answers everything AX will not: `CGWindowListCopyWindowInfo` gives Device Hub's
windows with owner, title and bounds, and a device window is titled with the
device name alone — no `– iOS <version>` suffix, so `_title_is()` matching on
name *and* version rejects it while name-only matching finds it. Screenshots
(`simctl io … screenshot`) and every database assertion were always headless.
Clicks are posted CGEvents and never needed AX; per XcodeBuildMCP, even Device
Hub's menus respond to CoreGraphics clicks they will not expose to scripting.

**What the repair does not change, and why idb is still the end state.** The
by-pid route restores windows, menus and AXRaise, which removes the
wrong-window hazard that sank the window-server attempt (a patched run on that
route reached the import sheet, measured the wrong window, and tapped into empty
space while reporting no error). What it cannot restore is anything under the
window's chrome: the sidebar is still driven by typed filter and clicks by
position, the device screen is still measured inside the bezel, taps are still
posted mouse events that need a visible window and Accessibility permission,
and buttons are still found by color. idb needs none of that (below), and its
tree queries name the buttons. The bridge makes today's suite run; the rebuild
makes it honest.

**Use [idb](https://github.com/facebook/idb) instead.** It injects touches into
the simulator rather than driving the mouse, so no window, no geometry, no
occlusion, and no pointer takeover:

```sh
idb ui tap X Y          # device points, not screen points
idb ui describe-all     # the frontmost app's elements, with bounds and a11y info
idb ui describe-point X Y   # whatever is under a point, in any process
```

That deletes rather than ports most of this file's hard-won machinery — the
bezel measurement, the device-pixel-to-screen-point mapping, the blue-button
pixel matching, the HiDPI click loss, the hovered-button dropout. Finding *Add
Shortcut* becomes a lookup by label. Apple's own direction agrees: Xcode 27
points automation at `devicectl` and `simctl` rather than at GUI scripting.

It has since been run on 27, and it works, with one limit that decides how the
rebuild has to find buttons. The measurements are in the next section and the
rebuild notes in the one after.

**And idb does not replace the macOS guest.** The guest exists to *be a device* —
a real identity with a working iCloud session and an isolated library — which is
what minting a link requires. A simulator can sign into an Apple Account but
[iCloud Drive and CloudKit do not reliably work there](https://developer.apple.com/forums/thread/712304),
which is the same missing-device-identity wall that makes a macOS 27 guest
useless (`docs/macos-guest.md`). idb drives a screen; it does not confer a
CloudKit session. There is also no `shortcuts` CLI on iOS, so a simulator would
mean driving the app's UI to run a publisher and scraping the result, where the
guest answers over SSH.

### idb on Xcode 27, measured

Measured 2026-09-18: Xcode 27.0 (27A266a) on macOS 27.0, an iPhone 17 Pro
simulator on iOS 27.0, idb 1.6.0, and Device Hub not running at any point.
Install is `brew trust facebook/fb` and then `brew install facebook/fb/idb`;
Homebrew refuses the formula from an untrusted tap. 1.6.0, released 2026-09-17,
already looks for SimulatorKit under `Contents/SharedFrameworks`, where Xcode 27
moved it, so the patched companions circulating for the old path are not
needed.

| | |
|---|---|
| open a signed `.shortcut` as a host file URL, tap *Add Shortcut* at the frame `describe-all` gave | installed; the row appeared in `Shortcuts.sqlite` |
| *Set Up Shortcut*, then *Skip Setup*, then the Replace / Keep Both / Cancel alert | every button labeled, every tap landed |
| `shortcuts://run-shortcut`, Ask for Input, `idb ui text`, *Done*, *Allow* on the clipboard consent | the typed digits came back from `simctl pbpaste` |
| `idb ui text "Wi-Fi 123"` into a search field | arrived verbatim: no keycode table, no autocapitalization |
| `idb ui button HOME` | works |
| `idb screenshot` | "Failed to capture a screenshot" with no GUI attached; `simctl io … screenshot` works |

Frames from `describe-all`, the point given to `idb ui tap`, and a `simctl io
screenshot` share one coordinate space: device points, 3 px per point on this
device. Nothing is mapped and nothing is measured.

**Tree queries see only the frontmost app.** `describe-all`, `describe MARKER`,
`wait MARKER`, a tap by marker, `--match`, both backends (`--api ax` and
`--api axbridge`), and the `modal` field of `--format complete` all walk the
frontmost application and stop; the gRPC request has no process field
(`AccessibilityInfoRequest` carries a point, a marker, a backend and filters,
nothing else). Shortcuts draws its import sheet, the Replace alert and the
"Could not connect to the server" error in its own process, so those are in the
tree. It runs shortcuts out of process, and `com.apple.ShortcutsUI` draws the
Ask for Input dialog, the "Allow … to copy to the clipboard?" alert and the
"Allow … to output 1 text item?" sheet. While one of those is up, every tree
query still returns the library underneath it, `wait Done` times out, and
`--format complete` reports `modal: null`. `describe-point X Y` is a
system-wide hit test and does see them: it returned the `TextArea` with its
`AXValue`, and *Done*, *Cancel*, *Allow* and *Always Allow* as labeled buttons
with frames, at about 0.2 s a call. Touches and typed text reach them too.

`--api axbridge` is worth using for the frontmost app: on the library screen it
returned 80 elements to the default backend's 8, including the navigation bar
and each tile's Play button, which the default backend drops.

**Quitting Device Hub shuts down every booted simulator**, as quitting
Simulator.app did. A harness boots with `simctl boot` and never launches Device
Hub. `boot()` in `sim/harness.py` still calls `host().launch()`, which opens it;
that call goes. A person running Device Hub alongside does no harm, and idb's
touches do not care whether a window is showing the device.

**The output-permission sheet.** A run started by URL ends with "Allow … to
output 1 text item?" (Don't Allow / Allow Once / Always Allow), because the URL
runner hands the last action's output back to its caller. Left pending, the
next run request logs a start and a finish two seconds apart and shows no
dialog — from outside, exactly what a dropped run URL looks like, and three
"dropped" runs in a row here were this. *Always Allow* clears it for the
device's lifetime, like the other consents. Do not choose *Allow Once*; it asks
again on the next run.

Seen once in three imports: after *Add Shortcut*, Shortcuts opened the new
shortcut's Apple Intelligence description view ("Preparing support for describe
a shortcut") instead of returning to the library. The install had landed either
way, so nothing after an install may assume the library is what is on screen.

After a companion is killed by hand, every command fails on a missing socket
until `idb kill` resets the client's registry; the next command then spawns a
fresh companion.

### The rebuild, when it happens

Link verification no longer needs a device: fetching the link's payload from
iCloud and diffing it against `dist/<name>.xml` cannot be fooled by a tap that
landed wrong, so that is the route for links. The harness is rebuilt only for
the three flows a device still uniquely exercises, and only when those tests
are wanted again.

**Input.** Poll `describe-point` at the field's region until a `TextArea`
answers (about 2 s after the run URL). Type with `idb ui text`. Read the same
point back and require its `AXValue` to equal what was typed, which is the
check the old harness never had. Tap *Done* at the frame the hit test returned.
Here the field sat at (23, 123) 356×114 and Done at (207, 252) 172×54; treat
those as places to start a sweep, not constants. Done and Cancel are
distinguishable by label, which retires the trap where the blue-button rule
submitted an empty answer.

**Consent.** Two kinds, found two ways. In-app alerts (Replace / Keep Both, run
errors) are in the frontmost tree: `describe-all --match Allow`, or by label.
Runner consents (clipboard, network, output) are not: sweep `describe-point`
down the sheet's region, dedupe by frame, and tap the button labeled *Allow* or
*Always Allow*. Clear the output-permission sheet after every URL-started run,
or the next run does nothing and says nothing.

**Questions.** All in the frontmost tree. *Add Shortcut*, *Set Up Shortcut*,
*Next*, *Skip Setup*, each question's heading text, and the Replace alert were
all present by label. Whether a link's sheet offers *Set Up Shortcut* becomes a
label check rather than a count of blue rectangles, and `links.install_from_link`
keeps its retry and swaps only the finder.

**What survives in `sim/harness.py`.** Dead: both `_Host` classes,
`_detect_host`, `_mouse_click`, `_type_mac`, `_press_escape`, `_title_is`,
`_screen_box`, `_widest_gap`, `KEYCODES`, `window_rect`, `focus_window`,
`prepare_window`, `menu_item`, `menu_click`, `ensure_hardware_keyboard`,
`_mapping`, and the Quartz and numpy imports — about half the file. Unchanged:
`find`, `boot` (minus the launch), `wait_booted`, `erase`, `add_root_cert`,
`terminate_shortcuts`, `run_shortcut`, `screenshot`, `image`, `db_path`,
`library`, `shortcut_actions`, `stored_content`, and the plist helpers.
Changing shape: `tap` takes points; `type_text` is one call; `blue_buttons` and
`tap_affirmative` become one label lookup with the two-tier search above, and
`answer_prompt` and `cancel_prompt` collapse into it; `install` keeps its retry
loop and swaps the finder. A consumer suite that calls `blue_buttons` or
`image` directly needs the same swap.

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
support matrix and the probes behind it. `shortcut_forge_lib.sim.probes.setup_probe`
is the two-action canary that measures it on any runtime, and
`links.check_link` counts the questions an installed copy actually holds.

**iOS 27.2 beta 1 (`24B5084k`) commits the answer again.** Measured 2026-09-18
with `setup_probe`, driven through idb on a simulator, with iOS 27.0 (`24A434`)
run through the identical procedure as the control:

| runtime | answer typed, *Add Shortcut* tapped | nothing typed, *Skip Setup* tapped |
|---|---|---|
| iOS 27.0 `24A434` | nothing installed | installed, holding the placeholder |
| iOS 27.2 beta 1 `24B5084k` | installed, holding the typed answer | installed, holding the placeholder |

Each value was read out of `Shortcuts.sqlite`, not judged from the screen. Two
attempts before the valid one were wrong in ways the old blue-button harness
would have hidden: after typing, the software keyboard covers *Add Shortcut*,
and a tap at the button's own frame lands on the keys; and on a fresh device,
dismissing the keyboard with its *Close* button raises a first-run typing tip
whose *Continue* hands focus back to the field and brings the keyboard back.
The canary now clears both before it taps, and tapping *Add Shortcut* was
confirmed by the editor opening on the installed shortcut afterward.

This is a beta result, and the matrix already holds one beta that got this
right before a later beta broke it. **When 27.2 ships:** run the canary on the
release build, and then update the consumer-facing text that tells people to
finish with Skip Setup — the install instructions and the note carried in the
last setup question — so users on the fixed release are not steered around a
bug they no longer have. brightwheel-checkin's `TESTING.md` names its own
copies of that text.

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
