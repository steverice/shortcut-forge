# Driving a macOS guest

Measured on 2026-09-16, host and guest both **macOS 27.0 (26A428)** on Apple
Silicon, `tart 2.37.0`, `vncdotool 1.4.2`. Everything here was paid for on a
running VM; where something is unverified it says so.

The goal was a guest whose Shortcuts library holds a release build and nothing
else, driven without touching the host's window server — the operator's own Mac
is a bad place to mint iCloud links from, because the publisher links whatever
it finds by name and cannot tell a clean build from a configured copy.

All of it is now code, in `shortcut_forge_lib.guest`: `tart` argv shapes in
`tart.py`, the blessing's constants and argv in `bless.py`, the client in
`vnc.py`, `ssh.py`, and the whole sequence in `bake()`. One command rebuilds a
base from nothing:

```sh
shortcut-forge bake mint-base --work-dir build/bake
```

It refuses to hand back a guest it has not watched render *and* accept a click.
Measured end to end from an IPSW on 2026-09-16: about twelve minutes, 239,634
distinct colors, and Safari started by a Dock click confirmed over SSH. Read
this document to change any of it; the modules carry the findings as comments,
and `tests/test_guest.py` asserts the ones a later edit could quietly undo.

## Installing tart, which is the one step with no obvious right answer

On this host it comes from **mise**, pinned in
`~/.config/mise/config.toml`:

```toml
"github:openai/tart" = "2.37.0"
```

Note the repository: `cirruslabs/tart` 301-redirects to **`openai/tart`**, and
the GitHub release is the only source that carries 2.37.0. It ships as
`tart.app`, a bundle rather than a bare binary.

**Homebrew is a dead end, in two ways at once.** `brew install
cirruslabs/cli/tart` taps and then dies with `Calling depends_on :macos with
depends_on macos: is disabled`; and even were it installable, that formula pins
**2.32.1**, below `tart.MIN_TART`. Reaching for `brew` first costs an hour and
ends at a version the code refuses.

A mise-installed tart is **not on `PATH`** — the shim lives at
`~/.local/share/mise/shims/tart`, so `which tart` finds nothing and a search of
the Cellar, the Caskroom and `$HOME` turns up nothing either. It is installed
and it works; it is just invisible to every obvious check. Put the shim
directory on `PATH`, or pass `--tart ~/.local/share/mise/shims/tart`.

**Credentials do not live in the work directory.** A bake writes tart's log and
the proof screenshot to `--work-dir`, but the record of how to reach the guest
goes to `~/.cache/shortcut-forge/bake/<name>.json`, mode 0600, and is read back
with `guest.bake.credentials(name)`. Call that rather than opening the file: a
repo's `build/` is erased by `git clean -xdf` and duplicated by every worktree,
and a password whose loss costs a person at a screen — which is what a base
carrying a signed-in Apple Account costs — belongs somewhere sturdier than
either. The lookup exists so that move does not reach a caller.

## The two flag choices that decide everything

**`--vnc`, not `--vnc-experimental`.** Both render. The experimental one killed
the VM twice, with an identical crash each time:

```
Virtualization  Base::assertion_trap()
Virtualization  -[_VZVirtualMachineAccessor addAccessorObserver:]
```

SIGTRAP, a private symbol, within a couple of minutes of GUI activity. tart's
own help says the feature "is experimental and there may be bugs present." No
public report of this crash exists in `openai/tart`, `cirruslabs/tart`, or
Apple's forums as of 2026-09-16, so it appears to be new with macOS 27.0.

`--vnc` instead uses **the guest's own Screen Sharing**, which is not private
API. It has run GUI activity without a crash since.

**`CI=true`, not `--no-graphics`.** Both suppress tart's host-side auto-open of
a VNC client, so neither puts a window on the host. But `--no-graphics` removes
the *display device*: there is no WindowServer, GUI apps cannot launch, and
`shortcuts list` fails with **"Couldn't communicate with a helper application."**
`--vnc-experimental` masks this because it supplies its own virtual display —
which is why the two are so often seen together.

A trap worth naming: a control experiment that boots with `--no-graphics` and no
VNC does not test "without VNC". It tests "without a display", so nothing GUI
happens and nothing crashes. That comparison proves nothing.

## Provisioning

`tart run --provisioning-opts` wraps `VZMacGuestProvisioningOptions`. From
`tart run --help`, verbatim:

> Requires the host to be running macOS 27 (or newer) and only takes effect on
> the first boot after creation of a macOS 27 (or newer) guest VM.

That is an **absolute floor of 27 on both sides**, not a relative host ≥ guest
rule. One comma-separated `key=value` list, five keys: `fullName`, `username`,
`password`, `logsInAutomatically`, `enablesRemoteLogin`.

It works, and it delivers what it claims: on first boot the account exists, is
logged in, answers SSH within about a minute, and **no Setup Assistant pane
survives**. The password is visible in `ps` on the host for the life of the run,
so generate a random one per bake rather than reusing a constant.

## Three things a clone will never show you

Found by baking from an IPSW after every step had been "verified" against clones
of a guest that was already configured. Re-applying configuration to a guest
that already has it proves nothing, and each of these hid behind that.

**`tart create` holds the guest after it exits.** A `tart run` issued
immediately dies with:

```
Error Domain=VZErrorDomain Code=2 "Failed to lock auxiliary storage."
NSUnderlyingError=... Code=35 "Resource temporarily unavailable"
```

Nothing is wrong; the restore has not let go. It takes **two** retries at five
second intervals, reliably, not occasionally. No boot happens on a run that
never took the lock, so a provisioning run that loses the race can simply be
reissued — provisioning applies to the first boot, and there was not one.

**`tart stop` is a power cut, not a shutdown.** A file written as root seconds
before it is *gone* on the next boot. Measured directly with a marker file:
`touch /Library/Preferences/stop-probe.marker`, `tart stop`, boot, and it does
not exist. This is how a bake silently lost the Remote Management configuration
it had just made, then failed four steps later with a refused connection and no
hint that step 3 was where it went wrong. Shut the guest down from inside —
`shutdown -h now`, backgrounded so the dying sshd does not hang the call — and
keep `tart stop` only as a fallback.

**Remote Management and Screen Sharing are different services, and kickstart
starts the wrong one.** `kickstart -activate` reports *"Activated Remote
Management"*, writes `ARD_AllLocalUsers` and `ARD_AllLocalUsersPrivs`, exits 0
— and leaves **nothing listening on 5900**. A client gets `Connection refused`,
which is a third distinct failure mode from the black frame and the refusal
message below. What binds the port is the separate launchd daemon, and it ships
enabled-but-not-running, so `launchctl enable` alone is a no-op:

```bash
launchctl enable system/com.apple.screensharing
launchctl kickstart -k system/com.apple.screensharing   # this is what binds 5900
```

Starting it once survives a reboot. Do both halves in the same step; the failure
otherwise appears long after the step that was actually incomplete reported
success.

## The black screen, and the privilege mask behind it

Screen Sharing can authenticate, serve frames, and return a **completely black
framebuffer** — one distinct color, indefinitely. Everything below was tried and
did **not** fix it: waiting, repeated captures, forcing updates with mouse moves,
disabling display sleep, `tccutil reset ScreenCapture`, attaching a real display
device, connecting at different times. No permission prompt ever appears, which
is the tell: nothing is *asking*.

The cause is that `kickstart`'s access **mode** and its privilege **mask** are
separate settings, and a plausible-looking invocation sets one without the other:

```bash
# WRONG. Leaves the mode at "all users", whose global privilege mask is zero,
# while writing privileges onto a per-user record that mode never consults.
kickstart -activate -configure -access -on -users probe -privs -all -restart -agent
```

In System Settings this appears as Remote Management **on**, with the "All local
users can access this computer to" sheet showing *every* toggle off, and Screen
Sharing grayed out as "currently being controlled by the Remote Management
service." A configured agent permitted to do nothing.

Setting the mask is **necessary but not sufficient**. Correct it with either:

```bash
# (a) specified-users mode, so the per-user privileges are the ones consulted
kickstart -configure -allowAccessFor -specifiedUsers
kickstart -configure -users <user> -access -on -privs -all

# (b) all-users mode with the global mask actually set
kickstart -activate -configure -allowAccessFor -allUsers -privs -all -restart -agent
```

Afterwards `/Library/Preferences/com.apple.RemoteManagement.plist` holds
`ARD_AllLocalUsers => true` and `ARD_AllLocalUsersPrivs => 1073742079`, the
all-privileges mask.

> **Correction, and read this before relying on the above.** An earlier revision
> of this document claimed these commands fix the black screen on their own.
> They do not. That claim came from testing on a guest that had *already* been
> through the System Settings flow described below; turning the sharing services
> off first does **not** undo whatever that flow created, so the test was
> measuring an already-blessed guest and the comparison was worthless.
>
> Measured afterwards on a genuinely virgin guest: a full headless bake — create,
> provision, `kickstart -allowAccessFor -allUsers -privs -all` logging *"Setting
> all users privileges to 1073742079"* — still produced a framebuffer of **1
> distinct color**.

**What actually made it render** was doing it through the UI: System Settings →
General → Sharing, turn **Remote Management off**, then turn **Screen Sharing
on**. That took the framebuffer from 1 color to ~242,000 immediately.

**The blessing is durable.** Once granted this way it survived a guest stop and
restart, and survived `tart clone` — including a clone on which the services
were turned off and re-enabled with `kickstart` alone. So it lives in the image
and belongs in a base built once and cloned per run; it does not need
re-applying per boot.

### Two clients, two failure modes for the same denial

On an unblessed guest the two TCC rows are missing, and the two clients report
that completely differently:

- **vncdotool** connects, authenticates, exits 0, and returns a well-formed
  1024×768 frame that is **entirely black**. Nothing indicates a permission
  problem.
- **Screen Sharing.app** refuses to connect at all, with macOS naming the
  remedy itself:

  > Screen Sharing is not permitted on "<host>". Disable and re-enable Screen
  > Sharing or Remote Management in System Settings before trying again.

Same underlying denial, and the second is the OS independently confirming both
the diagnosis and the fix — that message prescribes exactly the System Settings
toggle that writes the rows.

Two things follow. Apple's client is **not** privileged here: it is refused too,
so "use a different VNC client" is not a way around the grant. And the black
frame is simply what a third-party client renders when capture is denied — which
is why a smoke check must assert on content, since the failing case returns
success at every level a script can see.

**Use Screen Sharing.app to diagnose a black frame.** It converts a silent,
featureless failure into a sentence naming the cause.

### Bootstrapping: what to look at for the first blessing

`--vnc` is black until the blessing, and the blessing needs a working screen, so
the first pass has to come from somewhere else. What was actually used here:
the guest was running `--no-graphics --vnc-experimental`, and the operator
connected **Screen Sharing.app on the host to the loopback URL tart printed**
(`vnc://:<passphrase>@127.0.0.1:<port>`). Two toggles is well inside the couple
of minutes the crash took to arrive, but it is the crash-prone server, so keep
the visit short.

Note what this does **not** establish. That client connected to Virtualization's
own VNC server on loopback, not to the guest's Screen Sharing on the guest's IP.
Whether **Screen Sharing.app connecting to the guest IP renders where vncdotool
sees black** is untested, and it matters: if it renders, the blessing could be
done over `--vnc` with a human client and the experimental server would never be
needed at all. tart's native window (no `CI=true`) was never used and is a third
untested option.

### What the UI writes, and why it cannot be written from inside the guest

Identified by dumping a blessed guest and a virgin one and diffing. The entire
difference is **two rows in the system TCC database**:

```
kTCCServiceScreenCapture | com.apple.screensharing.agent | auth_value 2
kTCCServicePostEvent     | com.apple.screensharing.agent | auth_value 2
```

`auth_value 2` is "allowed". A virgin guest has neither row; a blessed one has
both. `PostEvent` matters as much as `ScreenCapture` — it is what lets a client
send clicks rather than only see pixels.

**They cannot be written headlessly.** Inserting them as root on a virgin guest:

```
attempt to write a readonly database    (sqlite exit=1)
```

The system TCC store is SIP-protected, so root is not enough and the screen
stays black. `tccutil reset` only removes entries, so it cannot help either; the
articles that recommend it work because a human then approves the re-prompt.
MDM is not an alternative: a macOS 27 guest enrolls and is never treated as
manageable (tart #1320). The only remaining route would be a recovery boot and
`csrutil disable`, which makes the base non-standard and has not been tried.

### Granting it headlessly: write the image while it is parked

**Solved.** SIP protects a *running* system; a stopped guest's disk is just a
file, and `TCC.db` carries no `SF_RESTRICTED` flag. Mount the Data volume and
write the rows directly. Demonstrated end to end twice, then reproduced
independently.

```bash
tart stop <guest>
hdiutil attach -nomount ~/.tart/vms/<guest>/disk.img
mount -t apfs -o noowners <data-dev> <mountpoint>
#   ... write the two rows into Library/Application Support/com.apple.TCC/TCC.db,
#   ... then PRAGMA wal_checkpoint(TRUNCATE)
sync; umount <mountpoint>; hdiutil detach <container-dev>
```

Three things make this better than it sounds:

- **`-o noowners` means no host root.** It lets an ordinary user write
  root-owned files in the mounted image, so the whole procedure runs without
  `sudo` on the host. Verified by completing it with no sudo credential
  available. If a future host policy forbade that mount flag, `sudo mount_apfs`
  is the fallback — still headless, but needing a cached credential.
- **SIP stays on.** Nothing is disabled and the guest is normally configured;
  it ends up holding exactly the two rows a human would have created. This is
  the same destination as the UI flow by a different road, which is what makes
  it acceptable where `csrutil disable` would not be — that would change what
  the base *is*.
- **The `csreq` blob is a constant, not a per-machine secret.** It decodes to
  `identifier "com.apple.screensharing.agent" and anchor apple` — identity and
  anchor, with nothing hardware-bound — and the same bytes worked on a guest
  with a different ecid and MAC. So no blessed base is needed as a seed. It
  must be re-extracted only if Apple re-signs the agent.

```
X'FADE0C000000003C0000000100000006000000020000001D636F6D2E6170706C652E7363726
5656E73686172696E672E6167656E7400000000000003'
```

Row values: `client_type=0, auth_value=2, auth_reason=4, auth_version=1,
flags=0, indirect_object_identifier='UNUSED',
indirect_object_identifier_type=0`, the rest NULL.

**Check the primary key before trusting `INSERT OR REPLACE`.** On macOS 27.0 it
is four columns:

```sql
PRIMARY KEY (service, client, client_type, indirect_object_identifier)
```

`indirect_object_identifier_type` is **not** among them. That is what makes a
second blessing override the rows rather than double them — measured against a
real store, three passes, two rows — and it is also what makes the value in that
column a free choice. Published descriptions of this table give a five-column
key that does include it, and on such a schema a NULL there would be fatal in a
way nothing reports: SQLite treats NULLs as distinct in a unique index, so every
`INSERT OR REPLACE` would degrade to a plain insert, and a re-blessed image
would carry two rows per service with no way to say which one wins at
authorization. A peer reproduced exactly that, against the described schema
rather than a store. Read the key out of the store you are writing to;
`tests/test_guest.py` pins the one measured here.

Given a free choice, **write 0** — fidelity, not correctness. macOS writes 0 for
these two rows, and a virgin guest's store writes 0 on the granted row it ships
with and NULL on the denied one, so 0 is what the UI flow would have left
behind.

**A blessing usually fills a gap, but it can override a denial.** A virgin guest
carries **no** `com.apple.screensharing.agent` row at all: the store ships with
two rows, neither of them this client. A Mac that has refused Screen Sharing
does carry them, at `auth_value = 0`. The key is the same either way, so
`INSERT OR REPLACE` handles both — worth knowing before reusing this on a
machine with a history.

**Do not compose a row by hand.** A fabricated or absent `csreq` produces a row
that dumps as perfectly correct and is ignored at authorization time — the same
silent-success shape as everything else here.

**The constancy is a property of Apple-signed clients, not of the technique.**
This blob is portable because it pins to `anchor apple` plus a bundle
identifier, and neither changes across machines or releases. A **third-party**
client's requirement pins to the *vendor's* signature instead, so it is not a
constant: it must be extracted from a guest where that grant was made, and
re-extracted whenever the vendor ships a new build. The mechanism generalizes to
any system TCC service — `kTCCServiceSystemPolicyAllFiles` (Full Disk Access)
included — but for anything not Apple-signed, expect a per-vendor-version blob
rather than a literal you can paste. Raised by the `home-platform` rehearsal
work, whose Full Disk Access grant is for a third-party disk utility.

### The other headless blocker: HiDPI drops every click

A virgin guest boots at **2× HiDPI** (a 2048×1536 framebuffer backing 1024×768
points), and at 2× the screen renders perfectly while **every pointer event is
silently dropped**. Keyboard events still land — Cmd-Space opens Spotlight —
which makes it especially misleading. Clicks were tried at native, half and Dock
coordinates; none registered.

Fix it offline in the same pass as the TCC rows, editing the guest's own plist:

```bash
/usr/libexec/PlistBuddy -c \
  "Set :DisplayAnyUserSets:Configs:0:DisplayConfig:0:CurrentInfo:Scale 1" \
  <mnt>/Library/Preferences/com.apple.windowserver.displays.plist
```

This is the clearest argument in this document for the click-confirmed-over-SSH
gate: a 2× guest passes every color count and every byte floor, renders a
flawless screenshot, and cannot be driven at all.

### Verify on a settled desktop, not a boot screen

The first verification run of the headless recipe reported `205 colors ->
RENDERS` and the capture was the **Apple boot logo**, taken before the guest had
finished booting. The recipe was fine; the assertion was timing-fragile, and it
could as easily have failed spuriously on an all-black instant of the boot
sequence. Wait for a desktop — poll until the frame stops changing, or until
something known-present appears — before asserting anything about rendering.

So the grant no longer needs a human at all, and it remains durable across
stop/restart and clone.

Two smaller differences, recorded but not load-bearing: a blessed guest carries
`naprivs: -1073741569` on the user record, and its `RemoteManagement.plist`
holds `ScreenSharingReqPermEnabled => false` and `VNCLegacyConnectionsEnabled =>
true` rather than the `ARD_AllLocalUsers*` keys, because Screen Sharing rather
than Remote Management is what ends up enabled. Neither made a virgin guest
render.

## Connecting a client

`--vnc-experimental` prints `vnc://:<four-word-passphrase>@127.0.0.1:<port>` —
loopback, so the printed password does not expose the guest to the LAN. It is
still printed to stdout and into any log, so redact it.

`--vnc` prints `vnc://<guest-ip>/`, i.e. Apple's Screen Sharing on the guest's
own address, port 5900.

**vncdotool must use ARD authentication**: `--username <account> --password
<account password>`. The legacy VNC password path (`kickstart -setvnclegacy`)
does not work with it — vncdotool falls back to prompting for a username and
then hangs until timeout. Note also that `vncdo` wants `HOST::PORT` with two
colons, and `--server` cannot take a `vnc://` URL verbatim.

## Importing a shortcut

There is no CLI import: the `shortcuts` binary has only `run`, `list`, `view`
and `sign`. `open <file>.shortcut` from an SSH session **does** reach the GUI
session and raise the add sheet, provided a display exists.

**The keyboard does not commit that sheet.** Return does nothing. Tab then Space
does nothing. Only a click on **Add Shortcut** imports it. At 1024×768 with one
window, that button sat at about (544, 168) — but locate it by matching the
framebuffer rather than hardcoding, because the sheet's contents vary.

Measured end to end on 2026-09-17, with nobody at the guest: two signed files
copied in over the SSH channel as base64 — 25 KB each, not worth mounting a
share for — then `open`ed one at a time and committed by a located click at
(543, 167), within a pixel of the figure above and found rather than assumed.
`shortcuts list` confirmed each one landed.

## Finding a button nobody can hardcode

Two sheets have to be clicked to drive Shortcuts, and what works on one does not
work on the other.

**Add Shortcut is a filled blue button, and still needs care.** The sheet's icon
tile is blue too and sits directly above it, so their rows merge into one band;
a matcher that measures a candidate's height from that band gets 96px where the
button is 27, and discards it. Take each box's vertical extent from its own
columns.

**The consent sheet has no default button at all.** Its three choices — Don't
Allow, Allow Once, Always Allow — are identical flat controls, so the iOS rule
that the affirmative is the bottom-most blue one finds *nothing* here. The
affirmative is the right-most, and the row has to be found some other way.

**Color cannot find it, because the sheet is translucent.** The same three
buttons measured neutral 228-236 over the Shortcuts window and (214,202,206)
over the desktop wallpaper. No fixed range covers both, and a local-average
window wide enough to see past a button reaches past the sheet's edge and
averages in the wallpaper. What survives both backdrops is structure: a *flat*
horizontal run 80-240px wide sitting about 20 luminance levels below the surface
on both sides. Both numbers are calibration, not taste — the three buttons
measured 125px wide with 9px between them, and the step held at 20 across the
two backdrops (232 on a 253 sheet, 207 on a 228 one) because a translucent
button and the surface under it are tinted together. Re-measure both if the
guest's resolution or appearance changes. Three further details, each of which
cost a failed run:

- **Sample the surface in a narrow window just outside each end**, and take the
  brightest pixel in it. An edge is antialiased over several pixels, so a single
  probe close in still reads the button and the step vanishes — but the buttons
  are only 9px apart, so a probe reaching further than that reads the neighboring
  button instead.
- **A button's own label interrupts its rows.** Only the slices above and below
  the text are flat all the way across, so a cluster has to bridge a text-height
  gap or every button reads as two 7px fragments, neither tall enough to qualify.
- **Park the pointer before capturing.** A hovered button renders differently
  enough to drop out of the matcher, and losing one is worse than finding none:
  with Always Allow hidden under the pointer, the right-most button found is
  Allow Once, so the run is answered the wrong way with nothing reporting an
  error. Measured — it happened once, and the only symptom was that a later run
  raised a fresh prompt.

**The blue matcher has a false positive worth knowing**: the selected row in the
System Settings sidebar is a blue rounded rectangle the size of a default button.
Anything that clicks the bottom-most blue thing will click it.

## `shortcuts run`, and consent

It executes over SSH once a display and a GUI session exist. It does not fail —
it **blocks**, on a per-capability consent dialog:

> Allow "<name>" to output 1 text item?  ·  Don't Allow / Allow Once / Always Allow

The shortcut's output is visible behind the dialog, so the run itself completed.
These are per capability per shortcut and persist until the device is erased, so
answering **Always Allow** once during bake bakes them into the base. Do not let
SSH time out while a prompt is pending: the invocation dies, and a later click
lands on nothing while the next run raises a fresh prompt.

**Killing Shortcuts is the cheap way back from a timed-out run.** The warning
above is worth stating as a recovery procedure, because it was paid for twice:
once SSH times out, the dialog on screen belongs to a dead invocation, and
clicking its buttons does nothing at all — the framebuffer does not change and
no error appears anywhere. `killall shortcuts Shortcuts ShortcutsViewService
BackgroundShortcutRunner` clears it, after which a fresh run raises a fresh
prompt that can be answered normally. Restarting the guest would clear it too;
nobody here needed to, but a guest where the `killall` does not take is not
stuck.

`shortcuts run --output-path -` exists on macOS 27 and is the supported way to
get a shortcut's result without the clipboard. Untested past the consent prompt.

**Show Result blocks `shortcuts run` on every run, not only the first.**
Measured 2026-09-18 in a throwaway clone with two signed probes that differed
only in their last action: with every permission already granted, the Show
Result probe was still waiting on its Cancel / Done sheet 30 seconds into its
second run, while the probe ending in a notification returned in under a
second, exit 0, clipboard set. A publisher meant to run headless therefore ends
in a notification, not Show Result; the consumer that hit this made that swap
on its side. Its message still reaches stdout either way.

**Three consent prompts, each per shortcut on first use, each blocking the
run until answered:**

> Allow "<name>" to copy to the clipboard?  ·  Don't Allow / Allow
>
> Allow "<name>" to display notifications?  ·  Don't Allow / Allow
>
> Allow "<name>" to output 1 text item?  ·  Don't Allow / Allow Once / Always Allow

The third comes from Show Result under the CLI. Because they are per shortcut,
a **replaced publisher re-asks** everything the old one had been allowed, so the
first mint after swapping the base's publisher answers the clipboard and
notification prompts again, and presumably the iCloud-link consent; the
clipboard and link ones only appear on a run that actually mints, so a dry run
cannot prime them.

**Two-button consent sheets need their own calibration.** The gray-button
finder missed the two-button sheet twice: once it sat over a System Settings
window, where Don't Allow is nearly invisible, and once over the Shortcuts
window, where the blue-button finder matched the blurred strips of blue tiles
under the sheet instead, a false positive. Both times the buttons were at
x 411 and x 612, 190 px wide. The table below was measured on three-button
sheets and does not cover this shape.

**The `killall` above also quits the Shortcuts app**, which changes what the
next sheet sits over: afterward the frontmost app was Finder with a restored
System Settings window, which is the backdrop that hid Don't Allow. `open -a
Shortcuts` after the `killall` restores a known backdrop.

One loose end: a fresh publisher build is 99 actions, while the base's copy
reads 100 in the gate, so the base holds a publisher from an older build or an
older library revision. It minted three good links regardless, and nobody has
looked at which action differs.

## iCloud in a guest: it works on 26, and cannot work on 27

Measured 2026-09-17. **Minting works end to end on a macOS 26.6.2 guest.** The
same rig on macOS 27.0 cannot register for Apple Push, so it cannot hold a usable
iCloud session, so it cannot mint a link — and every symptom of that points
somewhere other than the cause, which is why it took a day to reach.

**The end-to-end result, on 26.6.2.** The publishing Apple Account signed in;
`brctl status` reported 24 containers syncing with `has-synced-down` and
timestamps a minute old; the three real release builds imported; and the real
publisher, run over SSH, minted three live links:

```
$ shortcuts run "<publisher>"                   # exit 0
<the publisher's own done message, on stdout>

$ pbpaste
<li><a href="https://www.icloud.com/shortcuts/…">First</a></li>
<li><a href="https://www.icloud.com/shortcuts/…">Second</a></li>
<li><a href="https://www.icloud.com/shortcuts/…">Third</a></li>
```

Three things fall out of that transcript, each of which had been an open
question. **`pbpaste` over SSH does reach the guest's pasteboard.** **`shortcuts
run` exits 0 and prints the Show Result text on stdout**, so a shortcut's own
message is readable without the clipboard at all. And the consent sheet needed
answering by hand only because this guest had no Screen Sharing; the blessing
would have let the framebuffer matcher do it.

**A shortcut built for macOS 27 survives a 26 library.** This is the risk that
argued for pinning the guest to 27 at all: a consumer whose shortcuts use
27-only actions — Store Content and the live `Scan Code` — would be importing
them into an older library, and an action silently dropped on import is the
corruption class this document exists to catalog. Measured by importing three
real signed builds and diffing the guest's own library database against the XML
they were built from, the same comparison `sim.links.check_link` makes on a
simulator:

| built | actions installed | import questions installed |
|---|---|---|
| 317 actions, 3 questions, uses 27-only actions | 317, in order | 3 |
| 17 actions, 0 questions | 17, in order | 0 |
| 17 actions, 0 questions | 17, in order | 0 |

Every identifier in the same order, and the questions intact. These imports were
committed by a human click; whether a *synthesized* click preserves questions is
still open question 8.

**The root cause: the guest cannot mint its device identity key.** `apsd` asks
the Secure Enclave for the key that device activation needs, and the virtual SEP
refuses:

```
<sepk:* kid=0000000000000000>: unable to generate key: error e00002e2(-536870174)
SecKeyCreateRandomKey_ios failed: -25308 errSecInteractionNotAllowed
  "Interaction is not allowed with the Security Server."
(DeviceIdentity) "Failed to create reference key."
(DeviceIdentity) "Failed to copy AVP guest identity data" -> EINVAL
APSBAAClientIdentityProvider failed to obtain a BAA cert
```

No key means no activation, no activation means no push token, and no push token
means no trusted-device approval can be delivered and `cloudd` can never finish
acquiring an account. The failures start at the bake's own first boot, in a guest
that has never been asked to authenticate to anything.

**Count the successes, not the errors.** Across this guest's entire log store
since its bake — 4,400,623 lines:

| line | count |
|---|---|
| `attempting to fetch BAA certs` | 68 |
| `SecKeyCreateRandomKey_ios failed` | 69 |
| `failed to obtain a BAA cert` | 68 |
| `obtained BAA certs` | **0** |
| `signed nonce data with host VM identity` | **0** |

It tries every time and never once succeeds. A count of error lines would only
say the errors are frequent; the zero says the guest never reaches the line a
working guest logs. Note the denominator too — 68 attempts across a day is not a
busy loop, so a five-minute window on an idle guest shows zero of everything and
looks like the fault is absent.

**It is macOS 27, measured on this host.** A macOS 26.6.2 guest (build 25G83),
created from an IPSW by the same `tart create` on the same Mac, registers on its
first attempt:

| guest | `attempting to fetch BAA certs` | `obtained BAA certs` |
|---|---|---|
| macOS 27.0, this project's bake | 68 | **0** |
| macOS 27.0, another rig's bake | 106 | **0** |
| macOS 26.6.2 | 2 | **2** |
| macOS 27.2 beta 1 (`26B5086k`), retested 2026-09-18 | 20 | **0** |

**The attempt count is not a neutral denominator — it is a symptom.** A guest
that registers attempts twice and stops. A guest that cannot retries: 68 across a
day here, 106 on the other rig. So a healthy reading is *small and equal*, and a
large attempt count is itself the fault rather than a reassuring sample size.
Keep counting attempts, because a zero on a guest that has never tried still
means nothing — but read a big number as bad news, not as confidence.

A second macOS 27 guest on this host, built by a different implementation with a
different provisioning path and first-boot sequence, reproduces the failure
exactly: 106 attempts, 106 failures, 0 successes, both zeros exact. Independently, a Mac admin hit the same
thing from the MDM side and published the comparison
([Der Flounder, 2026-09-15](https://derflounder.wordpress.com/2026/09/15/enrolling-macos-golden-gate-27-0-0-virtual-machines-with-mdm-servers-does-not-work-correctly/)):
macOS 27.0 and 26.6.2 log the *same first eight lines* and diverge at exactly one
step, the mint. 26.6.2 logs `APSBAAClientIdentityProvider obtained BAA certs!`
and `signed nonce data with host VM identity!`; 27.0 logs `unable to generate
key`. His conclusion is that macOS 27.0.0 "erroneously assumes itself to be
running on a Mac equipped with a Secure Enclave." No Apple bug number and no
workaround exist as of 2026-09-17.

Two explanations were killed rather than argued away. It is **not the clone** —
the base fails identically, from its own first boot. And it is **not the launch
context**: tart's FAQ names these exact errors as the symptom of
Virtualization.framework lacking an unlocked `login.keychain`, and every VM here
had been launched from a sandboxed agent shell, so one was started by hand from a
Terminal in a GUI session with the keychain verified unlocked and `no-timeout`.
Identical failure. That was the leading theory and it was wrong.

**27.2 beta 1 does not fix it.** Measured 2026-09-18 on a guest baked from
the `26B5086k` IPSW, in the first 100 seconds after its restart: 20 attempts,
20 failures, 0 obtained, and nothing on port 5223, with the same
`Failed to create reference key` / `unable to generate key` error underneath.
The bake itself got as far as its final proof on that beta and stopped there:
a beta guest shows the pre-release license sheet at first login, so the desktop
is covered and the Dock probe click starts whatever is under the sheet rather
than Safari. The guest is provisioned, blessed and reachable over SSH by then,
which is all the retest needs, so a proof failure on a beta does not block it.

**Retesting when a 27.x lands is two minutes and needs no human.** Boot a guest,
give it a minute, and count — the denominator first, because a zero means
nothing until you know the guest has tried:

```sh
# 1. Did it attempt at all? Zero means it has not spoken yet, so wait.
log show --last 30m --predicate 'process == "apsd"' --style compact \
  | grep -c 'attempting to fetch BAA certs'

# 2. Did any attempt succeed? Fixed looks like a small number equal to (1).
log show --last 30m --predicate 'process == "apsd"' --style compact \
  | grep -c 'obtained BAA certs'

# 3. A registered guest also holds a connection here.
netstat -an | grep 5223
```

A fixed guest reads 2 and 2. A broken one reads a large number and 0.

Run this on a guest that has just booted: `apsd` retries hard at startup — 8
attempts inside the first 40 seconds — and then backs off to about 68 across a
day, so a window on a guest that has been idle for hours can show zero of
everything and look like the fault is absent. Both `grep -c 0` and the empty
`netstat` exit **1**, which is the expected result on a broken guest; a wrapper
using `set -e` will read the correct answer as a command failure. Verified
against a known-bad guest on 2026-09-17, which is how the denominator step and
this paragraph came to exist.

**The action itself works**, which is worth keeping separate from the above.
`shortcuts run` on the publisher raised Shortcuts' own sheet — *Allow "Link
Probe" to create iCloud link?* — took a synthesized Always Allow, and ran on.
Nothing about being in a VM stops the action, the consent, or the click.

### The account-shaped symptoms, which are not the cause

Two accounts failed two different ways before the push finding explained both.
They are recorded because anyone debugging this without knowing about the SEP bug
will meet them first, and each is convincing on its own terms.

**`ICLOUD_UNSUPPORTED_DEVICE` reads like a verdict on the account. It is a
verdict on the device.** The dedicated publishing account, whose only second
factor was SMS to a phone number, took its password and its code on a 27 guest
and then failed with that bare string — which looks exactly like Apple refusing
an account that has never lived on Apple hardware. The same account later signed
in **without trouble on the 26.6.2 guest**, where an SMS code was accepted just
as readily. Nothing about the account's eligibility was the problem; the 27
guest's broken device identity was.

Signing that account into a real iPhone did not fix the 27 guest either — it made
it fail *earlier*, at the SRP handshake with `AKAuthenticationServerError
-3000076` and no code sent anywhere. That reversal is the push bug showing
through: with no trusted device, Apple used the SMS path and the flow reached a
code; once a trusted device existed, Apple preferred a push approval the guest
can never receive.

**An account with Advanced Data Protection signs in and never becomes usable.**
System Settings showed it signed in, with a standing banner:

> Some iCloud Data Isn't Syncing. Your end-to-end encrypted data stored in
> iCloud can't be accessed on this device. Verify your account information to
> resume syncing.

Resume Data Sync spins for a minute and closes with nothing changed, which is
that prohibition surfacing unlabeled — a guest carries a second standing banner
saying it "can't be used to edit certain account information, sign in to Apple
services, access Find My and Apple Pay", and approving a new device for
end-to-end encrypted data is exactly that. With ADP on, every iCloud category is
end-to-end encrypted, so the data session never becomes ready and everything
built on CloudKit fails. ADP is a genuine second wall — an operator with it
enabled could not mint from a guest even on a macOS where push works — but it is
not what stopped us, and a non-ADP account would have hit the push bug instead.

**The settings pane is not evidence.** It displayed iCloud Drive **on, 128.3 GB
used**, on a guest that had:

```
$ ls -d ~/Library/Mobile\ Documents        # no such directory
$ ls ~/Library/Preferences/MobileMeAccounts.plist   # no such file
$ brctl status
brctl: self-check failed; error: Error Domain=BRCloudDocsErrorDomain Code=141 "Access denied"
```

and, in the log, `cloudd` looping on `CloudCoreInternal.SessionReadinessError
Code=3` for a blocking account-acquisition event while `akd` re-ran
`VMHostBAASigning` every 1.6 seconds. **Ask `brctl`, not the pane.** The pane
reports what the account is entitled to, not what this device has.

**And the error the shortcut prints is misleading.** With the account signed in
and both iCloud Drive and Shortcuts sync on, the action still failed with:

> Error: In order to do this, you must be signed into iCloud.

It is signed in. What it lacks is a *ready* session. Anything debugging this
from the shortcut's message alone will go looking in the wrong place.

**What this forces on a design.** Mint on **macOS 26** until Apple fixes 27.
Minting from a 27 guest is not possible today, by anyone, with any account, and
the one cost of dropping to 26 that cannot be worked around is provisioning:
`VZMacGuestProvisioningOptions` needs 27 on **both** sides, so a 26 base boots
into Setup Assistant and needs a human once per base. Everything else survives —
the imports are lossless, the account signs in, the session becomes ready, and
the links mint. The other cost that was feared, 27-only actions landing in a 26
library, was measured and does not materialize.

One account requirement does still hold, because it is a second wall rather than
a symptom: **the minting account must not have Advanced Data Protection
enabled.** With ADP every iCloud category is end-to-end encrypted and a new
device needs approving from an existing one, which a guest is forbidden to do, so
its data session never becomes ready no matter which macOS the guest runs. An
operator with ADP on cannot mint from a guest at all, which is the strongest
argument for a dedicated publishing account.

## The database is WAL

`~/Library/Shortcuts/Shortcuts.sqlite` is in WAL mode in the guest exactly as on
a real Mac. Measured in the guest: the main file dated 11:07 while its `-wal`
sidecar was written at 11:19, and reading the main file alone (`mode=ro`) **reported
zero rows** while the library was not empty.

Copying just `Shortcuts.sqlite` out of a guest therefore hands a reader a stale
snapshot. Use `sqlite3 <db> ".backup <out>"` or `VACUUM INTO` inside the guest,
or copy `.sqlite`, `-wal` and `-shm` together. An SSH session **can** read the
file — no TCC refusal.

## Clones

`tart clone` works and is **copy-on-write**: deleting a freshly made clone freed
no disk at all, and the clone only grows as the guest writes. A clone gets its
own IP. tart regenerates the MAC address on collision but does **not** regenerate
the `VZMacMachineIdentifier`, and Apple derives a VM's iCloud identity from the
host's Secure Enclave — so a clone on the same host should present as the same
device. Whether a clone keeps a signed-in Apple session is **untested**.

Base and clone must never run at once (identical machine identifiers), and
Virtualization allows **two** concurrent macOS guests per host.

**A clone keeps its base's signed-in Apple Account session.** Measured 2026-09-17
on macOS 26.6.2: a base with an account signed in was stopped and cloned, and the
clone — after a restart of its own — reported `brctl status` with 24 containers
syncing, the same library, and no re-challenge of any kind. This is the finding
the bake-once/clone-per-release shape depends on. It says nothing about macOS 27,
where no session can be established to clone in the first place.

**A hand-provisioned base does not auto-login, and its clones fail confusingly.**
A 27 guest gets auto-login from `VZMacGuestProvisioningOptions`'s
`logsInAutomatically`. A macOS 26 base has no provisioning — that flag needs 27
on both sides — so unless someone sets it, every clone boots to a **login
window**: `stat -f '%Su' /dev/console` reads `root`, `who` is empty, `bird` never
starts so there is no iCloud session, and the `shortcuts` CLI fails with

> Error: Couldn't communicate with a helper application.

which is the same message this document attributes to `--no-graphics` removing
the display. A reader who meets it on a clone will chase a display that is
working fine.

`sysadminctl -autologin set -userName <u> -password <p>` sets the user and then
fails to store the password — `SACSetAutoLoginPassword error:22`, no
`/etc/kcpassword` written — so auto-login still stops at the window. Writing that
file directly works: it is the password XORed against the fixed key
`7D 89 52 23 D2 BC DD EA A3 B9 1F`, zero-padded to a multiple of 12 (padding even
when the length already divides), installed `root:wheel` mode 600. After a
restart the console owner reads the account name and everything GUI-dependent
works. Set it on the **base**, once, so every clone inherits it.

**Whether a clone inherits its base's identity is unmeasured, and the obvious
evidence for it is worthless.** A clone's `akd` logs an attestation chain:

```
Basic Attestation VM Sub CA1 <- Basic Attestation VM Root CA - G1
  Not Valid Before: Tue Sep 15 22:32:26 2026
  Not Valid After:  Thu Sep 16 22:32:26 2027
```

A `notBefore` 24 hours before the base's bake minute looks like proof the clone
is presenting the base's certificate. It is not. **Every guest's certificate is
backdated 24 hours from its own issuance**, measured on a second guest that was
created from an IPSW and never cloned: its chain reads `notBefore` Sep 16
02:36:55 against a creation at about Sep 17 02:30, a backdate from an issuance
inside its own first boot. A clone made shortly after its base therefore has a
freshly issued certificate dated within minutes of that bake, which is
indistinguishable from an inherited one by timestamp alone.

This claim was written three times before it was right — as measured, then as
measured-but-narrow, then as a cache of unknown provenance — and each version
survived review. What settled it was not closer reading of the same artifact but
a different artifact: a guest with no clone. If a claim about lineage rests on a
timestamp, get the control.

To read the chain in any guest:

```sh
log show --last 10m --predicate 'process == "akd"' --style compact \
  | grep -A4 'Returning cached certificates'
```

**What Apple says, which is documentation rather than measurement.** *Using
iCloud with macOS Virtual Machines* says the framework detects a second copy
started **while another is already running** and builds a new identity for that
one, which then needs a human to reauthenticate before iCloud works. Serialized
copies are not covered by that sentence either way. Design as though a
concurrent start costs a reauthentication — it is free to honor and expensive to
discover — but do not claim the serialized case is proven, because nothing here
proves it.

## Two hazards worth designing around

**The base is a credential at rest.** Once it holds a signed-in Apple session it
is an unencrypted image with auto-login enabled, and every clone inherits it.
Observed rather than theorized: a local Time Machine snapshot was taken
mid-session at 12:20 and captured the VM.

**A Time Machine exclusion does not keep a guest out of a snapshot, and an
earlier version of this document said it did.** Exclusions govern only what is
copied *from* a snapshot to the backup destination. An APFS snapshot is a
whole-volume, immutable, point-in-time reference; no path can be omitted from
one, and nothing can be pruned out of one afterwards. The only granularity is
deleting an entire snapshot.

Measured 2026-09-17, with `~/.tart/vms` and `~/.tart/cache` both reporting
`[Excluded]` to `tmutil isexcluded`: deleting two 32 GB guests returned **no**
disk at all, because four local snapshots still referenced their blocks. Free
space kept falling afterwards as new snapshots were taken. A `tart delete` on a
snapshotted volume frees nothing until the snapshots holding those blocks go.

**The fix is a separate APFS volume**, not an exclusion. Snapshots are per
volume, so a guest on its own volume is never captured by a snapshot of the data
volume, and `tart delete` returns its space at once. A new volume in the same
container costs nothing and shares the container's free space, and `tart.home()`
honors `TART_HOME`, so pointing it there is a one-line change. To unstick a
volume that is already full, `tmutil thinlocalsnapshots / <bytes> 4` drops
snapshots until the target is met — which costs every local restore point it
deletes, and no Time Machine backups on the destination.

**Disk.** The IPSW is about 25 GB cached, a restored base about 32 GB, and a
restore needs roughly 60 GB free to be comfortable. `tart create --from-ipsw latest`
downloads and caches it, so `ipsw` is not a required tool. Check free space
immediately before a restore rather than during one: on a volume near capacity,
running out mid-restore costs both the download and the guest.

## Preparing a 26 base by hand

Measured 2026-09-18 while getting a hand-provisioned macOS 26.6.2 base ready to
clone for a release. Nobody touched the screen at any point; every step ran
over SSH or VNC from the host, and every claim below was read back rather than
assumed. Eight things, in the order they were hit.

**A hand-provisioned base has no Screen Sharing and no blessing.** Nothing
listened on 5900, `launchctl print system/com.apple.screensharing` said "Could
not find service", and the system TCC store had no screen-sharing rows. `bake()`
cannot run on 26 (provisioning needs 27 on both sides), so a 26 base gets
`bake.sharing_script` and the TCC write applied by hand, and the 09-17 consent
was answered by a person for exactly this reason.

**`bless.tcc_rows()` cannot be written as-is on macOS 26.** The insert fails
with "table access has no column named one_time_reprompt_eligible". 26's
`access` table has `service, client, client_type, auth_value, auth_reason,
auth_version, csreq, policy_id, indirect_object_identifier_type,
indirect_object_identifier, indirect_object_code_identity, flags,
last_modified, pid, pid_version, boot_uuid, last_reminded` — neither
`one_time_reprompt_eligible` nor `reminder_count`. The failed `executemany`
rolled back and wrote nothing. Writing the two rows with only the columns
present, and refusing to drop any column whose value is non-zero, gave both
rows `auth_value` 2; after a reboot the screen rendered (41,058 distinct colors)
and clicks landed. The fix for `bless` is to build the insert from
`PRAGMA table_info(access)` with that same refusal rule.

**`write_blessing()`'s scale step would also fail on this guest**, after the TCC
rows were committed: `:DisplayAnyUserSets:Configs:0:DisplayConfig:0:CurrentInfo:Scale`
does not exist in its displays plist, so `PlistBuddy Set` errors, which is the
half-applied state that function's docstring warns about. This guest already
runs at 1024x768 with `backingScaleFactor` 1 and does not need the step. Skip
the scale when the key is absent, and check `backingScaleFactor` over SSH
afterward instead.

**Auto-login via `/etc/kcpassword` works on the base.** The password XORed with
the bytes `7D 89 52 23 D2 BC DD EA A3 B9 1F`, zero-padded to the next multiple
of 12 (a 6-character password gives 12 bytes), written `root:wheel` `0600`, plus
`defaults write /Library/Preferences/com.apple.loginwindow autoLoginUser
<user>`. After a clean in-guest shutdown and boot, `/dev/console` is owned by
that user.

**Boot with `CI=true tart run <name> --vnc`, not `--no-graphics`.** The flag
section above already says why: `--no-graphics` removes the display, and a
guest without one reports the same "Couldn't communicate with a helper
application" from `shortcuts` as a guest nobody has logged into. A base booted
that way to read its library looked, from SSH, exactly like the auto-login
problem.

**Three VNC input quirks on this guest, each measured:**

- After about 30 seconds of no VNC activity, **the first click is dropped.**
  Alternating sidebar clicks landed 5 of 6, the miss being the first. A 1-second
  warm-up move did not fix it (3 of 4, again the first). Warm-up moves with a
  3.5-second pause did, 2 of 2 after 75 seconds idle. What earlier looked like
  "menus do not work" was mostly this.
- **Clicking a context-menu item does nothing**, even when the hover has
  visibly highlighted it. Hover the item and press Return.
- Deleting a shortcut takes a click on the red Delete button of a confirmation
  that names the shortcut ("Delete shortcut “<name>”?"); assert on the
  name before confirming. The grid reflows after each delete, so every target
  in turn sat at the same spot.

**Shortcuts iCloud Sync was on in the base, and it has to be off.** The delete
sheet said the shortcut would be deleted from every iCloud device, and Settings
showed the toggle on. With sync on, a clone's imports sync back into the base on
its next boot, which breaks the rule that a base holds only the publisher. It was
turned off in the base (Settings > General > iCloud Sync). Whether a guest mints
with sync off is what the v1.5.0 run measures; the operator's Mac minted two
earlier releases with it off.

## Minting from a clone, end to end

Measured 2026-09-18, the same day as the base preparation above, on a clone of
that base. Nobody touched the screen. Three live links came back, every one
passed a device-free comparison against the build it was minted from, and the
clone was shut down from inside and deleted. Nine things, in the order they
were hit.

**A clone of the prepared base reaches the desktop by itself.** The console
owner was the account about 30 seconds after SSH answered, `WFCloudKitSyncEnabled`
read 0 in the clone, `brctl status` showed 24 containers, and `tart`'s machine
id was identical for base and clone.

**Setup questions survive a synthesized import on macOS 26.6.2.** The largest
target (339 actions, 3 questions) was imported by VNC clicks and landed with 3
of 3 questions, all unanswered, read from the database; the link minted from it
carries all 3 with matching `ActionIndex`, `ParameterKey` and `Category`. Open
question 8 below is answered.

**The import flow, measured.** The file went in as base64 over SSH (checksums
compared), was opened with `open`, and after 6 seconds the blue-button finder
from the 09-17 probe located *Add Shortcut* on all three sheets, at the same
box on both no-question builds. A build with questions shows "Add Shortcut…"
with an ellipsis, then a separate "<name> Setup" window with the fields and a
Cancel / Add Shortcut row at the bottom; the finder found that button too.
Leaving the fields empty and clicking it keeps the questions. No consent
prompts appeared on import.

**A WAL-less copy fails the gate closed, not open.** The gate passed on the raw
triple (`.sqlite` + `-wal` + `-shm`) and on an in-guest
`sqlite3 <db> ".backup /tmp/x"`, both reading four shortcuts with the largest
at 339 actions, 3 questions, 0 answered. The main file alone read zero targets,
so the gate refused with "missing from the library" three times. `.backup` is
the simpler thing for a rig to do: one consistent file.

**The publisher raised no consent prompts.** The Always Allow answers given on
the base on 09-17 persisted and carried into the clone, so the gray-button
consent finder was not exercised on this run.

**`shortcuts run` blocks on a shortcut's final alert.** The publisher ends in an
alert (its done message, Cancel / Done). Its text reached stdout, but the run
did not return until Done was clicked: started 15:06:58, returned 15:07:51,
right after the click. The links were already on the pasteboard, and `pbpaste`
over SSH returned the exact markup. A headless rig either clicks Done (a blue
default button the finder would find) or the publisher stops ending on a
blocking alert.

**Do not hand links over through the host clipboard.** The consumer's page
updater reads the URLs from stdin in page order, so they were piped in rather
than overwriting the operator's pasteboard. A rig's output contract is name to
URL, nothing on the host clipboard.

**Records are fetchable at once.** All three links resolved through the records
API and passed within about a minute of minting.

**What it cost.** Preparing the base (boot, kcpassword, sharing, shutdown,
copy-on-write backup, TCC write, boot, three deletes, sync off, shutdown) took
about 30 minutes, most of it the VNC quirks above. Clone, boot, three imports,
gate, publish, verify and delete took about 12 minutes.

## Open questions

Written down so nobody assumes an answer. Each is cheap once a guest exists.

1. ~~Does Screen Sharing.app render on a virgin guest over `--vnc`?~~
   **Answered: no, and it does not show black either — it refuses to connect.**
   See "Two clients, two failure modes" below. The denial applies to Apple's own
   client too, so this is not third-party clients being unentitled.
2. **Does the blessing survive a guest OS update?** It survives a stop/restart
   and a clone. The update axis is untested, and it decides whether a base is a
   durable asset or something rebuilt every point release.
3. ~~What does the System Settings flow write that `kickstart` does not?~~
   **Answered**: two system TCC rows for `com.apple.screensharing.agent`, and
   SIP refuses to let root write them. See "What the UI writes" above. The only
   untried route is a recovery boot with `csrutil disable`.
4. **Does a base carrying third-party kexts or drivers behave the same?** Raised
   by the `home-platform` rehearsal work, whose bases carry SoftRAID; the bases
   measured here carry nothing third-party.
5. ~~**Does a clone keep a signed-in Apple Account session?**~~ **Answered on
   macOS 26.6.2: yes.** A clone of a signed-in base reported 24 iCloud containers
   syncing after its own restart, with no re-challenge. See "Clones". Two things
   near it are still open: whether a clone *inherits* its base's identity, which
   the attestation certificate cannot show either way, and whether any of this
   holds on a 27 guest, where no session exists to clone.
6. ~~**How do you read, and assert, that Shortcuts iCloud sync is off in a
   guest?**~~ **Answered 2026-09-18.** It is readable with no GUI:
   `~/Library/Group Containers/group.is.workflow.my.app/Library/Preferences/group.is.workflow.my.app.plist`,
   key `WFCloudKitSyncEnabled`. It read `false` right after the toggle was
   turned off in the guest, and reads `0` on a host Mac where sync has been off
   since 2026-09-11. Its value *before* the toggle was not read, so what "on"
   looks like — `1`, or the key absent — is not measured: assert `== 0` or
   `false`, never `!= 1`. The reason it mattered stands: a synced library
   carries one clone's imports into the next clone's, which is exactly the
   more-than-one-copy-by-name state the publisher refuses to mint from. Two
   routes had been ruled out before this one was found. `MobileMeAccounts.plist` is **not written to
   disk** in a guest whose session never became ready, so reading it answers
   nothing, and System Settings reported iCloud Drive on with storage used on a
   guest with no container at all — so neither the plist nor the pane can be
   trusted. `brctl status` does report the truth about iCloud Drive; the
   equivalent for Shortcuts specifically is still unknown, which is why this
   stays a question. A sync assertion that cannot see the setting would report
   success without having looked, the failure shape this whole document is about.
7. **Can a plain account — real hardware provenance, no ADP — reach CloudKit
   readiness in a guest?** This is now the question that decides whether minting
   from a guest is possible at all, and everything upstream of it is answered.
   Neither account tested could answer it: one was refused as an unsupported
   device before it got that far, the other had ADP and could never become ready.
   See "iCloud in a guest".
8. ~~**Do setup questions survive a synthesized import?**~~ **Answered on
   macOS 26.6.2, 2026-09-18: yes.** A 3-question build imported by VNC clicks
   landed with all 3, read from the database, and the link minted from it
   carries all 3 with matching `ActionIndex`, `ParameterKey` and `Category`
   ("Minting from a clone, end to end" above). The zero-question link that
   raised this question, recorded in `docs/simulator-harness.md`, remains
   unexplained, and every link still gets checked rather than trusted.

## Verifying a screen actually rendered

Assert on **content**, never on the client's exit code. A black framebuffer
returns cleanly: vncdo exits 0 and hands back a well-formed 1024×768 PNG. A
smoke check that tests `exit == 0` passes on exactly the condition it exists to
catch.

Two discriminators, at 1024×768 — which is what a blessed guest runs at, since
the blessing forces the scale to 1:

| | black | rendering |
|---|---|---|
| distinct colors | 1 | 7,552 – 245,469 |
| PNG bytes | 2,367 | 47,023 – 958,349 |

**Count colors, and treat bytes as a fallback.** A color count is
resolution-independent: one color is one color on any framebuffer. Bytes are
not, and an earlier revision of this table proved the point by getting it wrong
— it gave black as **9,239 bytes while claiming 1024×768**, when that figure was
measured on a 2× guest at 2048×1536. A real black frame at 1× is 2,367 bytes.

That error does not reject good frames, which is the reassuring way to misread
it. It does something quieter: anyone taking 9,239 as the signature of failure
and testing a 1× guest sees 2,367, matches nothing, and reads a **broken guest
as unrecognized rather than broken**. It also understated the margin — the real
1× separation is 2,367 to 47,023, twenty-fold, not the five-fold the old text
claimed from two rows measured at different resolutions.

So if you use bytes at all, calibrate them at the resolution they will run at,
and say which one that is. File size remains a proxy, good for "desktop or black
rectangle" and **not** for comparing two similar framebuffers.

And assert it **after a restart**, not on the boot that granted everything —
otherwise the check cannot tell a durable blessing from a momentary one. Content
plus restart is what proves the property; either alone does not.

### Rendering is only half the property

The blessing is **two** TCC rows, `kTCCServiceScreenCapture` and
`kTCCServicePostEvent`, and every check above tests only the first. A guest
granted screen capture but not post-event **renders perfectly and ignores every
click**. A color count passes it. A byte floor passes it. Then some later step
fails on its own assertion, far from the cause, with a perfectly good screenshot
attached — and whoever is debugging looks at the wrong thing.

Two ways to close it, depending on what the rig has:

- **Frame diff.** Capture, post an input event, capture again, and require the
  two frames to differ. No new dependency and harmless on any desktop. Choose
  the event carefully: a bare pointer move may not change the framebuffer at
  all, because the cursor is often sent as a separate VNC update rather than
  composited into it.
- **A real click-through, verified out of band.** Where the rig already has to
  click something, use that. Here the bake imports a shortcut by clicking **Add
  Shortcut** and then asserts over SSH that `shortcuts list` names it. That
  proves capture *and* post-event *and* the actual capability the design needs,
  and its assertion arrives through a channel the GUI cannot fake.
