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

## iCloud in a guest, which is where minting stops

Measured 2026-09-17. **A macOS 27.0 guest cannot register for Apple Push, so it
cannot hold a usable iCloud session, so it cannot mint a link.** That is the
whole finding. Everything else in this section is a symptom of it, and each
symptom points somewhere other than the cause — which is why it took a day to
reach.

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

**It is macOS 27, not this rig.** A second guest on this host, built by a
different implementation with a different provisioning path and first-boot
sequence, reproduces it exactly: 106 attempts, 106 failures, 0 successes, both
zeros exact, on a comparable denominator. Independently, a Mac admin hit the same
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

**Retesting when a 27.x lands is two minutes and needs no human.** Boot a guest
and count:

```sh
log show --last 30m --predicate 'process == "apsd"' --style compact \
  | grep -c 'obtained BAA certs'      # any number above zero means it is fixed
netstat -an | grep 5223               # a registered guest holds a connection here
```

**The action itself works**, which is worth keeping separate from the above.
`shortcuts run` on the publisher raised Shortcuts' own sheet — *Allow "Link
Probe" to create iCloud link?* — took a synthesized Always Allow, and ran on.
Nothing about being in a VM stops the action, the consent, or the click.

### The account-shaped symptoms, which are not the cause

Two accounts failed two different ways before the push finding explained both.
They are recorded because anyone debugging this without knowing about the SEP bug
will meet them first, and each is convincing on its own terms.

**An account that has never existed on Apple hardware is refused outright.** The
dedicated publishing account, whose only second factor was SMS to a phone
number, took its password and its code and then failed with a server verdict
rendered as a bare string:

> ICLOUD_UNSUPPORTED_DEVICE

Signing that account into a real iPhone did not fix it — it made the guest fail
*earlier*, at the SRP handshake with `AKAuthenticationServerError -3000076` and
no code sent anywhere. That reversal is the push bug showing through: with no
trusted device, Apple used the SMS path and the flow reached a code; once a
trusted device existed, Apple preferred a push approval the guest can never
receive.

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

**What this forces on a design.** Minting from a macOS 27 guest is not possible
today, by anyone, with any account — so the question is which way to wait.
A macOS 26 guest does register for push, but costs two things this project pins
27 for: `VZMacGuestProvisioningOptions` needs 27 on **both** sides, so a 26 base
cannot be provisioned headlessly and Setup Assistant becomes a hand step per
base; and 27-only actions would be imported into a 26 library, which is
unmeasured and is exactly the silent-corruption class this document exists to
catalog. Minting on real hardware remains the other option, with the
contamination hazard the README describes.

The account requirements still hold for whenever the guest half works, and are
worth settling in advance because they are slow to fix: the minting account must
have lived on real Apple hardware, which is what makes it eligible, and must not
have ADP enabled, which is what would let its data session become ready.

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
Observed rather than theorized: a local Time Machine snapshot was taken mid-session
at 12:20 and captured the VM. Snapshots also pin deleted blocks, so reclaiming
disk can appear to *reduce* free space until they are thinned. Exclude `~/.tart`
from Time Machine.

**Disk.** The IPSW is about 25 GB cached, a restored base about 32 GB, and a
restore needs roughly 60 GB free to be comfortable. `tart create --from-ipsw latest`
downloads and caches it, so `ipsw` is not a required tool.

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
5. **Does a clone keep a signed-in Apple Account session?** Still untested, and
   now for a blunter reason than before: no session has ever become ready in a
   guest here, so there has been nothing to clone. The layer underneath is also
   unmeasured — the attestation certificate that looked like evidence of
   inheritance turned out to be backdated the same way in a guest that was never
   cloned. See "Clones".
6. **How do you read, and assert, that Shortcuts iCloud sync is off in a
   guest?** `bake` does not check, and it should: a synced library carries one
   clone's imports into the next clone's, which is exactly the more-than-one-copy
   -by-name state the publisher refuses to mint from. Two routes are now ruled
   out rather than merely unknown. `MobileMeAccounts.plist` is **not written to
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
8. **Do setup questions survive a synthesized import?** `docs/simulator-harness.md`
   records a link that arrived with zero import questions where its siblings had
   three, the one difference being that its clicks were synthesized rather than
   human. Every import here is synthesized. An imported copy that lost its
   questions installs in one tap and leaves its credentials unset, with no error.

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
