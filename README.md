# shortcut-forge

Generate, check, sign, and simulator-test iOS Shortcuts from Python.

A Shortcuts plist is a poor thing to edit by hand: variable references are
UUID-keyed, text parameters carry character offsets that must line up with
U+FFFC placeholders, and control flow is flat runs of actions sharing a group
identifier. This library builds those plists from plain Python, checks the
things that pass Apple's own import and then fail silently on a device, wraps
the shortcuts-playground validator and signer, and drives an iOS Simulator well
enough to install a shortcut, run it, tap through its prompts, and read its
state back off the device.

It was pulled out of two working projects,
[brightwheel-checkin](https://github.com/steverice/brightwheel-checkin) and a
private CarPlay shortcut, and everything in it was paid for on a device. The
findings are written down in [`docs/building-shortcuts.md`](docs/building-shortcuts.md),
[`docs/simulator-harness.md`](docs/simulator-harness.md), and
[`docs/macos-guest.md`](docs/macos-guest.md).

It also bakes the macOS guest that publishing needs. An iCloud share link cannot
be revoked and carries whatever library minted it, so links are minted from a
throwaway VM holding nothing else. `shortcut-forge bake <name>` builds one from
an IPSW without a human at the screen, and refuses to hand back a guest it has
not watched render *and* accept a click.

## Install

The library is not on PyPI. Take it from a checkout as an editable path
dependency; both projects that use it do this:

```toml
[project]
dependencies = ["shortcut-forge[sim]"]   # drop [sim] if you never touch a simulator

[tool.uv.sources]
shortcut-forge = { path = "../shortcut-forge", editable = true }
```

Validating and signing need the shortcuts-playground Claude Code plugin's
`validate-shortcut` and `sign-shortcut` on `PATH` (or `SHORTCUT_FORGE_VALIDATOR`
and `SHORTCUT_FORGE_SIGNER` pointing at them). The simulator harness needs
Xcode, an iOS simulator, and Accessibility permission for the terminal, because
taps are synthesized as real mouse events.

Baking a guest is the `[guest]` extra, plus `tart` 2.37 or newer and macOS 27 or
newer on this Mac — `--provisioning-opts` needs 27 on both host and guest, and
without it a first boot stops at Setup Assistant with nobody to answer it.

## Quick start

```python
from pathlib import Path

from shortcut_forge_lib.actions import GREATER_THAN, ActionList
from shortcut_forge_lib.build import Shortcut, build_all
from shortcut_forge_lib.plist import document, out, ts, var
from shortcut_forge_lib.uuids import random_uuids

a = ActionList(random_uuids())
a.comment("Says hello, or complains if Shortcut Input was empty.")
u_text = a.text(ts("Hello, ", var("Shortcut Input")), name="Greeting")
has_input = a.count_matches(var("Shortcut Input"))        # Text -> Match Text -> Count
g = a.uuid()
a.if_open(g, condition=GREATER_THAN, number=0, source=out(has_input, "Count"))
a.notify(body=ts(out(u_text, "Greeting")))
a.if_else(g)
a.notify(body=ts("Nothing was handed in."))
a.if_close(g)

doc = document("Hello", a, glyph=59690, color=4292093695, input_classes=["WFStringContentItem"])
signed = build_all(Path("dist"), [Shortcut("Hello", doc)], waived=["Shortcuts Playground prompt text"])
print(signed["Hello"])   # dist/Hello.shortcut, ready to AirDrop
```

`build_all()` writes the XML, runs the structural checks, runs the validator
with the waivers you name, and signs. A check or validation failure stops it
before anything is signed.

## What is in it

| Module | What it gives you |
|---|---|
| `shortcut_forge_lib.plist` | The plist primitives: `ts()`, `out()`, `var()`, `attach()`, `dict_field()`, `kv()`, `act()`, `comment()`, `document()`, `import_question()`, `write_xml()`. |
| `shortcut_forge_lib.uuids` | `random_uuids()` for builds that are not committed; `RoleUuids` for builds that are, so the diff is readable. |
| `shortcut_forge_lib.actions` | `ActionList`: a list of actions with the proven idioms as methods, including `count_matches()` and balanced control-flow markers. |
| `shortcut_forge_lib.checks` | Offsets, UTF-16 safety, dangling references, dictionary keys, control-flow pairing, Run Shortcut targets. |
| `shortcut_forge_lib.toolchain` | `validate()` and `sign()` over the plugin's tools. |
| `shortcut_forge_lib.build` | `Shortcut` and `build_all()`. |
| `shortcut_forge_lib.publisher` | A shortcut that mints iCloud share links for other shortcuts by name. |
| `shortcut_forge_lib.sim` | `Simulator` (install, run, tap, type, read state), a throwaway CA, the setup-question canary, and the iCloud link checker. |
| `shortcut_forge_lib.guest` | Baking a throwaway macOS guest and proving a VNC client can see and drive it: `tart` argv, the offline TCC blessing, SSH, and `bake()`. |
| `shortcut-forge` (CLI) | `validate`, `sign`, and `bake` subcommands, for a shell script with nothing else to call. |

## Testing

```
make test          # unit tests: no plugin, no simulator, no network
make test-integ    # runs openssl for real
make check         # lint + unit tests
```

## License

[MIT](LICENSE).
