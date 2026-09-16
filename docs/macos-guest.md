# Driving a macOS guest

Measured on 2026-09-16, host and guest both **macOS 27.0 (26A428)** on Apple
Silicon, `tart 2.37.0`, `vncdotool 1.4.2`. Everything here was paid for on a
running VM; where something is unverified it says so.

The goal was a guest whose Shortcuts library holds a release build and nothing
else, driven without touching the host's window server — the operator's own Mac
is a bad place to mint iCloud links from, because the publisher links whatever
it finds by name and cannot tell a clean build from a configured copy.

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
Sharing greyed out as "currently being controlled by the Remote Management
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
flags=0, indirect_object_identifier='UNUSED'`, the rest NULL.

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
gate: a 2× guest passes every colour count and every byte floor, renders a
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

## `shortcuts run`, and consent

It executes over SSH once a display and a GUI session exist. It does not fail —
it **blocks**, on a per-capability consent dialog:

> Allow "<name>" to output 1 text item?  ·  Don't Allow / Allow Once / Always Allow

The shortcut's output is visible behind the dialog, so the run itself completed.
These are per capability per shortcut and persist until the device is erased, so
answering **Always Allow** once during bake bakes them into the base. Do not let
SSH time out while a prompt is pending: the invocation dies, and a later click
lands on nothing while the next run raises a fresh prompt.

`shortcuts run --output-path -` exists on macOS 27 and is the supported way to
get a shortcut's result without the clipboard. Untested past the consent prompt.

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
5. **Does a clone keep a signed-in Apple Account session?** `tart clone` does not
   regenerate the `VZMacMachineIdentifier` and Apple derives a VM's iCloud
   identity from the host's Secure Enclave, so it plausibly does — untested.
6. **Do setup questions survive a synthesized import?** `docs/simulator-harness.md`
   records a link that arrived with zero import questions where its siblings had
   three, the one difference being that its clicks were synthesized rather than
   human. Every import here is synthesized. An imported copy that lost its
   questions installs in one tap and leaves its credentials unset, with no error.

## Verifying a screen actually rendered

Assert on **content**, never on the client's exit code. A black framebuffer
returns cleanly: vncdo exits 0 and hands back a well-formed 1024×768 PNG. A
smoke check that tests `exit == 0` passes on exactly the condition it exists to
catch.

Two discriminators, measured here on 1024×768:

| | black | rendering |
|---|---|---|
| distinct colors | 1 | ~242,000 |
| PNG bytes | 9,239 | 47,023 – 420,579 |

Counting colors needs an image library; file size needs nothing and separates by
5× at its narrowest. File size is a proxy, good for "desktop or black rectangle"
and **not** for comparing two similar framebuffers.

And assert it **after a restart**, not on the boot that granted everything —
otherwise the check cannot tell a durable blessing from a momentary one. Content
plus restart is what proves the property; either alone does not.

### Rendering is only half the property

The blessing is **two** TCC rows, `kTCCServiceScreenCapture` and
`kTCCServicePostEvent`, and every check above tests only the first. A guest
granted screen capture but not post-event **renders perfectly and ignores every
click**. A colour count passes it. A byte floor passes it. Then some later step
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
