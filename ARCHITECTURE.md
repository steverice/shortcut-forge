# Architecture

## Overview

shortcut-forge is the infrastructure two shortcut-generating projects grew and
then shared: the code that turns Python into a Shortcuts plist, the checks that
catch what the validator cannot, the wrappers over the validator and signer,
and a harness that runs the result on a simulator. It exists so that the next
shortcut starts from a working toolchain instead of a copy of the last one.

The library takes a stance that shapes everything in it: **the plist is the
API.** Shortcuts has quietly failed on every abstraction that hid the plist, and
on several documented shapes that turned out to be wrong, so the primitives
return exactly the dictionaries Shortcuts serializes and the idioms are named
sequences of those. A generator can always see what it is writing.

The second stance is that **a finding is a measurement.** Every idiom's
docstring says what it works around and how that was established; the two
documents under `docs/` hold the measurements themselves. Nothing here claims a
platform behavior from a single run with a confound.

## Directory structure

```
src/shortcut_forge_lib/
  plist.py        The plist shapes. ts() derives attachment offsets; document() builds the root.
  uuids.py        random_uuids() for uncommitted builds; RoleUuids for committed, diffable ones.
  actions.py      ActionList: a list of actions with the idioms as methods.
  checks.py       Structural checks: offsets, UTF-16, references, dictionary keys, control flow, Run Shortcut.
  toolchain.py    validate() and sign(), over the shortcuts-playground plugin's CLIs.
  build.py        Shortcut and build_all(): write, check, validate, sign.
  publisher.py    share_links_shortcut(): mints iCloud links by name, refuses duplicates.
  types.py        OnStep, the progress callback the CLI draws.
  sim/
    harness.py    Simulator: boot, install by host file URL, run by URL, tap, type, read the database.
    certs.py      ensure_certs(): a throwaway CA the simulator is told to trust.
    probes.py     setup_probe(): the two-action import-question canary.
    links.py      install_from_link(), check_link(): does an iCloud link deliver the build?
src/shortcut_forge_cli/
  main.py         `shortcut-forge validate` and `sign`; the only place output is formatted.
docs/
  building-shortcuts.md   What Shortcuts does, measured.
  simulator-harness.md    What driving a simulator took, and what it cannot tell you.
tests/fixtures/           Real builds from both consumers, captured before the move.
```

## Key design decisions

**Primitives, then idioms, then a pipeline.** `plist.py` has no opinions.
`actions.py` has exactly the opinions that were paid for: `count_matches()`
because an If cannot branch on a dictionary value, control-flow markers that
always close a block with the action that opened it, only two `WFCondition`
values because only two are proven. `build.py` orders the work so that nothing
is signed until every document has been checked and validated.

**Checks are for silent failures.** The validator already rejects a malformed
plist. `checks.py` covers what imports cleanly and then does nothing: a Repeat
closed by an If, whose body never runs; a variable reading a dictionary key
that was renamed away, which speaks an empty slot; a wrapper naming a shortcut
that no longer exists, which resolves by name and so simply stops. Each is a
bug one of the consumers shipped or nearly shipped.

**Two UUID minters, because the consumers disagree.** brightwheel-checkin
mints fresh UUIDs and does not commit its build, since every build would differ
completely. car-greetings derives a stable UUID per role and commits the XML,
so a prompt edit is a readable diff. Both are right for their repo, so both
are supported, and `ActionList` takes any iterator.

**The signer writes next to the build.** `sign()` defaults `--output-dir` to
the XML's directory, so the signed file and the signer's dated XML archive land
beside the build rather than in a global folder under `~/Documents`. A debug
build with credentials baked in then leaves nothing outside the directory it
was written to.

**The harness imports without Xcode.** Detecting Simulator.app versus Device
Hub runs `xcode-select`, which used to happen at import time and made the
module unimportable on a machine without Xcode. It is lazy now, so the unit
tests and CI never touch it, and the consumers' tools can import the harness
before deciding whether to use it.

**Consumers take a path dependency.** Both live beside the library on the same
machine and change with it. A git URL pinned to a tag would make every library
edit a two-commit dance for no one's benefit yet.

## Data flow

**A build**, from a generator:

```
generator                      shortcut_forge_lib
  documents  ───────────────>  build_all(dist, [Shortcut(name, doc, xml_stem)], waived=[...])
                                 checks.check_all(doc)          structural, raises CheckError
                                 write_xml(doc, dist/name.xml)
                                 toolchain.validate(xml)        validate-shortcut, waivers by regex
                                 toolchain.sign(xml)            sign-shortcut --mode anyone --output-dir dist
                               <───────────────  {name: dist/name.shortcut}
```

**A simulator test**, from a project's own runner:

```
project test                   shortcut_forge_lib.sim
  ensure_certs(tls_dir)   ──>  ca.pem, server.pem              openssl
  Simulator.find()        ──>  a booted iOS 27 device          xcrun simctl
  sim.add_root_cert(ca)        trust the mock's CA
  sim.prepare_window()         make its window exist, front, measured, keyboard on
  sim.install(path)            simctl openurl file://…, tap Add Shortcut, poll the database
  sim.run_shortcut(name)       simctl openurl shortcuts://run-shortcut?name=…
  sim.tap_affirmative()        clear consent prompts by finding the blue button
  sim.stored_content()    <──  Shortcuts.sqlite + PersistentStorage
```

The project keeps its mock server, its scenarios, and its assertions; the
library keeps everything that would be the same for any shortcut.
