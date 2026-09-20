# idb fixtures

Captured by `tests/capture_idb_fixtures.py` on 2026-09-19, from a
402x874-point device (UDID `157363D5-38AF-48E0-B33B-F6BD8100B7BB`), with idb `idb-cli 1.6.0`.
`runtime.json` is that device's `simctl list devices --json` at capture time.

One directory per screen. `all-ax.json` and `all-axbridge.json` are the two
backends' `describe-all` output for it; each `point-<x>-<y>.json` is
`describe-point <x> <y>` at that exact point, so a fake idb can answer by argv
rather than by replay order. `warmup.txt` is what idb prints when it has nothing
to report: a fresh companion says it on its first read, and an empty hit test
says it at any time. The two are the same sentence, which is why the harness
treats both as "nothing on screen" with one rule. This capture's copy of
`warmup.txt` came from an empty hit test far off screen (the companion had already reconnected by the time the first read landed).

Recapture on a new runtime by booting one device, leaving it idle, and running
the script again with a different output directory. Nothing here boots a
simulator.

## Findings from this capture

On this 402x874 iPhone 18 Pro / iOS 27.0 runtime, `ax` is the backend that
sees the presented `setup-question` sheet (6 elements, including *Add
Shortcut* and the question's heading) and `axbridge` sees the same sheet
buried in a much larger tree (158 elements, with the navigation bar and the
rest of the view hierarchy in it) — the order the spec's §3 assumes, not
reversed.

The device writes *Don't Allow* with a typographic apostrophe (U+2019), not
the straight one (U+0027) a keyboard types. An earlier capture on this same
device compared the label against a straight-apostrophe constant and
silently dropped every seed for it; `DONT_ALLOW` now holds the device's own
spelling as a `\u2019` escape — written as the escape rather than
pasting the character itself, because the two are indistinguishable in an
editor — and both the clipboard-consent and output-sheet scans in this
capture found and seeded it correctly. See `SEEDS` in `seeds.txt`.

The field did **not** keep `zz424242` verbatim this run: the script read its
`AXValue` 1.5 seconds after `idb ui text` and got `zz42422`, one character
short, while `setup-question-keyboard/screen.png` shows the QuickType
suggestion bar already offering the full `"zz424242"` at that same moment —
the keystrokes had landed, but the field's own accessibility value had not
yet caught up to them. A previous capture on this device read the field back
verbatim after the same 1.5-second wait. Read together, the two captures say
the field is not autocapitalizing the sample (neither run saw a capital
letter appear), but a fixed settle time after `idb ui text` is not always
enough for the field's `AXValue` to have converged — `fill` and
`answer_prompt` should poll the field until it stops changing rather than
trust one read on a timer.

## The canary, run for real (2026-09-20)

`tests/test_sim_canary.py` ran against both runtimes it watches, each on the
device named in the task brief, both still 402x874 points — the seed
fractions held without correction. Runtimes, from `xcrun simctl list
runtimes`: iOS 27.0 (27.0 - 24A434) and iOS 27.2 (27.2 - 24B5084k), matching
the builds the module docstring already names. Both runs passed both tests.

`test_answering_the_setup_question` took the branch the docstring predicts
for each runtime: on iOS 27.0, confirming the setup-question page installed
nothing — the probe never appeared in `ZSHORTCUT` at all — and on iOS 27.2
beta, it committed the typed answer (`WFTextActionText` read back as
`"424242"`). Neither run disagreed with the measured expectations in the
module docstring.

A separate measurement pass, outside the pytest suite, ran the same
`install(skip_setup=False)` / `fill("424242")` sequence on each device and
printed what the tree held immediately afterward, before calling `confirm`:
`/tmp/canary-overlay-27-0.log` and `/tmp/canary-overlay-27-2.log`. Both logs
show the identical shape: 33 elements carrying the `KeyboardKey` trait, *Add
Shortcut* absent from that tree (the keyboard was covering it), and no
*Continue* first-run tip present. `confirm` then reported pressing *Add
Shortcut* on both devices — a button its own log shows was not yet in the
tree it had to search, which is only possible if `_clear_overlay` took its
keyboard-`Close` branch first, since the other branch it has (the *Continue*
tip) is the one the same log shows was absent. `SEEDS` (the consent-dialog
hit-test positions) went unexercised in every run today: installing this
probe raises no consent prompt, so nothing here re-measured those fractions,
and none needed correcting from what a prior capture already recorded.

Logs: `/tmp/canary-27-0.log`, `/tmp/canary-27-2.log` (the pytest runs);
`/tmp/canary-overlay-27-0.log`, `/tmp/canary-overlay-27-2.log` (the overlay
measurement).

## The second canary — `SEEDS` and `ASK_FIELD`, exercised on hardware for the first time (2026-09-20)

`test_the_runners_dialogs_are_found_where_the_seeds_say` is the first thing on
this branch to exercise `answer_prompt`, `cancel_prompt`, and their shared
`_wait_for_field` against a live device — nothing before it ever called
`_wait_for_field`, unit tests included; it existed with no caller and no test
of its own. Running it caught a real bug: `_wait_for_field` hit-tested the
`ASK_FIELD` seed and accepted the first element of the right type, with no
check that it actually belonged to the runner's dialog. The Shortcuts
Library's own search bar sits at almost exactly that point (`y` 168–212
against the seed's `y` 203), and it is what the screen shows for several
seconds after `run_shortcut` while Shortcuts relaunches — `terminate_shortcuts`
precedes every `run_shortcut` in this suite, so that relaunch happens on
every call. `_wait_for_field` now also requires the hit's pid to differ from
`frontmost_pid()`, checked fresh per candidate rather than once before the
poll (a pid read before the poll captures whatever was frontmost *before*
the relaunch — SpringBoard, measured at 10418 in one trace — and every
element of the newly-relaunched Shortcuts then differs from it, which
accepts the search bar all over again).

Run for real, on both runtimes, after that fix:

- **iOS 27.0 (27.0 - 24A434), freshly erased** (`xcrun simctl shutdown`,
  `erase`, `boot`, then the full `tests/test_sim_canary.py`, all three tests):
  `clear_prompts` pressed `['Allow', 'Always Allow']`, in that order — the
  clipboard consent and the output-permission sheet, both raised for real on
  a device that had never granted either. Not an empty list: an erased
  device is the only one that reaches this path, since a device that has
  already granted its consents skips straight past `clear_prompts` with
  nothing to clear.
- **iOS 27.2 beta 1 (27.2 - 24B5084k)**, booted under
  `DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer` per the
  task brief (not erased): `clear_prompts` also pressed
  `['Allow', 'Always Allow']`, same order. All three tests passed on both
  runtimes.

`SEEDS`'s consent-dialog fractions needed no correction on either runtime —
both are still the 402x874-point screen the original capture measured. This
is the first run on this branch where any of those seeds actually resolved a
real consent dialog rather than replaying a capture of one.
