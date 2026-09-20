# idb fixtures

Captured by `tests/capture_idb_fixtures.py` on 2026-09-19, from a
402x874-point device (UDID `157363D5-38AF-48E0-B33B-F6BD8100B7BB`), with idb `idb-cli 1.6.0`.
`runtime.json` is that device's `simctl list devices --json` at capture time.

One directory per screen. `all-ax.json` and `all-axbridge.json` are the two
backends' `describe-all` output for it; each `point-<x>-<y>.json` is
`describe-point <x> <y>` at that exact point, so a fake idb can answer by argv
rather than by replay order. `warmup.txt` is what a companion prints on its
first read after being dropped, which is the same sentence an empty hit test
prints.

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

The field kept `zz424242` verbatim: `idb ui text` delivered the sample
unchanged and the answer field did not autocapitalize it, so `fill` and
`answer_prompt` can trust that what they type is what the field reads back.

`SEED_LABELS` in this script spells *Don't Allow* with a straight apostrophe
(`'`, U+0027). The device's own `AXLabel` for that button uses a typographic
one (`’`, U+2019, confirmed byte-for-byte in the captured JSON). The two never
compare equal, so every scan in this capture that walked past a *Don't Allow*
button — the clipboard consent sheet and the output sheet both have one — saw
it, printed it, and then silently dropped it: it is absent from every
`point-<x>-<y>.json` file and from `seeds.txt`'s `SEEDS` table, and named in
its "never seen in this capture" line despite being on screen twice. `Done`,
`Cancel`, `Allow`, `Always Allow`, and `Allow Once` were all matched
correctly because none of them contain an apostrophe. Left as the brief wrote
it deliberately — this file is the record of the mismatch rather than a
silent fix — but Task 4's `harness.py` will need either a `’` in its own seed
table or a normalized comparison before a "deny" flow can be seeded for any
of these sheets.
