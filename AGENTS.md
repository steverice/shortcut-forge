# AGENTS.md

Instructions for AI coding agents working on this codebase.

## Project structure

```
src/
  shortcut_forge/        # the library; no argparse, argcomplete, or Rich anywhere in it
    plist.py             # the plist shapes, verbatim: ts(), out(), var(), act(), document(), ...
    uuids.py             # random_uuids(), RoleUuids
    actions.py           # ActionList: the idioms (count_matches, control-flow markers)
    checks.py            # structural checks that run before the validator
    toolchain.py         # validate(), sign(): wrappers over the plugin's CLIs
    build.py             # Shortcut, build_all()
    publisher.py         # share_links_shortcut()
    types.py             # OnStep
    sim/
      harness.py         # Simulator: boot, install, run, tap, type, read state
      certs.py           # ensure_certs(): throwaway CA the simulator trusts
      probes.py          # setup_probe(): the import-question canary
      links.py           # install_from_link(), check_link()
  shortcut_forge_cli/    # `shortcut-forge validate` and `sign`
docs/
  building-shortcuts.md  # what Shortcuts actually does, measured on devices
  simulator-harness.md   # what driving a simulator took, and what it cannot tell you
tests/
  fixtures/              # real builds from the two consumer projects
```

## Key conventions

**The plist is the API.** `plist.py` returns exactly the dictionaries Shortcuts
serializes; nothing is abstracted past them. When a shape is uncertain, the
answer is a device measurement or an unsigned plist from an iCloud share link,
never a guess from the plugin's documentation. `docs/building-shortcuts.md`
lists where that documentation is wrong.

**Every idiom carries its reason.** `ActionList.count_matches()` exists because
an If cannot branch on a dictionary value and an empty string satisfies "has
any value". If you add an idiom, its docstring says what it works around and
how that was established.

**Checks catch silent failures, not validator failures.** The validator already
rejects a malformed plist. `checks.py` is for things that import cleanly and
then do nothing: a Repeat closed by an If, a dictionary key nothing defines.

**The consumers must not change.** brightwheel-checkin and car-greetings build
on this library as an editable path dependency. car-greetings commits its XML,
so `git diff dist/` there is the fidelity test; brightwheel's builds are
compared with UUIDs normalized. Run both before changing a primitive's output.

**Signed files land next to the XML.** `sign()` defaults `--output-dir` to the
XML's directory, so the signer's dated archive of the unsigned XML goes into
the build directory too. Do not change this default: a debug build with
credentials baked in must stay inside the directory it was written to.

## Architecture rules

- `shortcut_forge` never imports Rich, argparse, or argcomplete. The CLI is the
  only place that formats output.
- `shortcut_forge.sim.harness` must import without Xcode present. Host
  detection is lazy (`host()`), and unit tests never call it.
- Only `LESS_THAN` and `GREATER_THAN` are exported as `WFCondition` values.
  Other comparisons have not been proven to branch correctly on a numeric
  input; do not add one without a measurement.
- The library raises; it never calls `sys.exit()`. The CLI's `main()` is the
  one broad exception boundary.

## Code quality standards

Per the `project-conventions` skill's python layer: full type annotations,
`from __future__ import annotations` first, `X | None`, narrow exception
handling, `Path` for paths, long-form CLI flags in every subprocess call.
Lint ignores in `pyproject.toml` are `S603`/`S607` (every subprocess call passes
a fixed argv to a tool found by name) and, for tests only, `S101` and the usual
test relaxations. Do not add more without raising it.

## Testing

```
make test          # unit tests; fakes stand in for the validator, the signer, and the simulator
make test-integ    # runs openssl for real
```

Tests in `tests/` mirror `src/`. The fixtures in `tests/fixtures/` are real
builds captured before the library existed; `test_checks.py` runs every check
over them, so a change that rejects a real build fails here first. Fake
`validate-shortcut` and `sign-shortcut` scripts are written onto a temporary
`PATH` by the toolchain tests. No test boots a simulator.

## Commits and releases

Conventional commits + gitmoji; the pre-commit hook adds the emoji, so never
type it. Run `make check` before every commit. Never bump the version locally.

## External tool dependencies

| Tool | Purpose | Required |
|---|---|---|
| `validate-shortcut`, `sign-shortcut` | from the shortcuts-playground plugin; validating and signing | for `build_all()` and the CLI |
| `xcrun simctl`, Device Hub or Simulator.app | driving a simulator | for `shortcut_forge.sim` |
| `openssl` | the throwaway CA | for `sim.certs` |
| `osascript`, `screencapture` | window geometry and taps | for `shortcut_forge.sim` |
