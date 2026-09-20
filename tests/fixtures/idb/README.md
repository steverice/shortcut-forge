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
