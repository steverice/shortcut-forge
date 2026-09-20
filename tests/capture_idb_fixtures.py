#!/usr/bin/env python3
"""Capture idb's own output on a booted device, as the fixtures the unit tests replay.

Run once per runtime, with the device already booted and idle:

    uv run python tests/capture_idb_fixtures.py <udid> tests/fixtures/idb

It writes one directory per screen, each holding `all-ax.json`,
`all-axbridge.json`, a `point-<x>-<y>.json` per interesting point — named for
the exact argv the fake idb will be asked for — and a screenshot. It also
writes `seeds.txt`, the seed table for `harness.py` printed as Python, and
`warmup.txt`, what a companion says on its first read after being dropped.

Every call goes through plain subprocess, not through the harness, because this
runs before the harness it is capturing for exists. Nothing here boots a
device: a capture on a device that was just booted measures a different screen
than the one the tests will replay.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

from shortcut_forge_lib.plist import write_xml
from shortcut_forge_lib.sim.probes import ask_probe, setup_probe
from shortcut_forge_lib.toolchain import sign

UDID = sys.argv[1]
OUT = Path(sys.argv[2])
WORK = OUT / "_build"
ANSWER = "424242"
SETUP_NAME = "ZZ Fixture Setup"
ASK_NAME = "ZZ Fixture Ask"

#: The labels a seed has to be able to find: the ones only the Shortcuts
#: runner draws, in a process no tree query reaches. Not `OK` — a run error's
#: alert belongs to the Shortcuts app itself and is in the frontmost tree.
SEED_LABELS = ("Always Allow", "Allow", "Allow Once", "Don't Allow", "Done", "Cancel")

#: Typed into the setup question's field. The letters are deliberate: `Wi-Fi 123`
#: arrived verbatim through `idb ui text`, but it starts with a capital and the
#: answer field is documented to autocapitalize. What the field keeps settles
#: that, in the one place the answer matters.
SAMPLE = "zz424242"


def sh(*args: str, check: bool = False, timeout: float = 180) -> str:
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if check and r.returncode:
        raise SystemExit(f"{args[:4]} failed ({r.returncode}): {r.stderr.strip()[:300]}")
    return r.stdout


def idb(*args: str) -> str:
    """idb's stdout, or its stderr when it failed — both are fixtures."""
    r = subprocess.run(["idb", *args, "--udid", UDID], capture_output=True, text=True, check=False)
    return r.stdout if r.returncode == 0 else r.stderr


def elements(backend: str) -> list[dict]:
    try:
        parsed = json.loads(idb("ui", "describe-all", "--api", backend))
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def tree() -> list[dict]:
    """Whichever backend can see the screen: the default first, as the harness will."""
    found = elements("ax")
    if len(found) > 1:
        return found
    other = elements("axbridge")
    return other if len(other) > len(found) else found


def at(x: int, y: int) -> dict | None:
    try:
        parsed = json.loads(idb("ui", "describe-point", str(x), str(y)))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def size() -> tuple[int, int]:
    for backend in ("ax", "axbridge"):
        for e in elements(backend):
            if e.get("type") == "Application":
                f = e["frame"]
                return int(f["width"]), int(f["height"])
    raise SystemExit("no Application element — is the device booted, and has the companion warmed up?")


def tap(e: dict, settle: float = 1.5) -> None:
    f = e["frame"]
    idb("ui", "tap", str(int(f["x"] + f["width"] / 2)), str(int(f["y"] + f["height"] / 2)))
    time.sleep(settle)


def find(label: str, kind: str | None = None) -> dict | None:
    return next((e for e in tree() if e.get("AXLabel") == label and (kind is None or e.get("type") == kind)), None)


def wait_for(label: str, timeout: float = 45) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        e = find(label)
        if e:
            return e
        time.sleep(1)
    raise SystemExit(f"no {label!r} within {timeout}s; on screen: {[e.get('AXLabel') for e in tree()][:15]}")


def capture(scene: str, points: list[tuple[int, int]]) -> Path:
    """Write both trees, a `point-x-y.json` per point, and a screenshot."""
    d = OUT / scene
    d.mkdir(parents=True, exist_ok=True)
    for backend in ("ax", "axbridge"):
        (d / f"all-{backend}.json").write_text(idb("ui", "describe-all", "--api", backend))
    for x, y in points:
        (d / f"point-{x}-{y}.json").write_text(idb("ui", "describe-point", str(x), str(y)))
    sh("xcrun", "simctl", "io", UDID, "screenshot", str(d / "screen.png"))
    print(f"  captured {scene}: {len(points)} point(s)")
    return d


def scan(w: int, h: int, step: int = 8) -> dict[str, dict]:
    """Every labeled button on the screen, by hit test, in three columns.

    Three columns and not one: the runner's dialogs put their buttons side by
    side, and the Ask dialog's *Cancel* and *Done* straddle the middle with the
    gap between them sitting exactly on it — measured 2026-09-19 on a 402-point
    device, where Cancel centers at x 108 and Done at x 292. A scan down the
    center alone finds neither. A quarter, a half and three quarters of the
    width catch a two-button row and a stacked sheet alike.

    This is the measurement the seeds come from: a dialog the frontmost tree
    cannot see has no other way to be found the first time. It costs about a
    point per third of a second, so three columns is a couple of minutes —
    which is exactly why the harness gets a seed table instead of doing this at
    run time.
    """
    found: dict[str, dict] = {}
    for x in (int(w * 0.25), w // 2, int(w * 0.75)):
        for y in range(int(h * 0.05), int(h * 0.98), step):
            e = at(x, y)
            if e is None:
                continue
            label = e.get("AXLabel")
            if label and label not in found and (e.get("type") == "Button" or "Button" in (e.get("traits") or [])):
                found[label] = e
                print(f"    ({x}, {y}): {label!r} at {e['frame']}")
    return found


def open_url(url: str) -> None:
    for _ in range(4):
        if subprocess.run(["xcrun", "simctl", "openurl", UDID, url], capture_output=True, check=False).returncode == 0:
            return
        time.sleep(3)
    raise SystemExit(f"the device refused {url}")


def build(name: str, doc: dict) -> Path:
    return sign(write_xml(doc, WORK / f"{name}.xml"), name=name, output_dir=WORK)


def open_file(path: Path) -> None:
    sh("xcrun", "simctl", "terminate", UDID, "com.apple.shortcuts")
    time.sleep(1.5)
    open_url("file://" + urllib.parse.quote(str(path.resolve())))
    time.sleep(4)


def main() -> None:
    if UDID not in sh("xcrun", "simctl", "list", "devices", "booted"):
        raise SystemExit(f"{UDID} is not booted. Boot it by hand; this script never does.")
    WORK.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    seeds: dict[str, tuple[float, float]] = {}

    # A companion's first read after it is dropped, which is the one sentence
    # the runner has to treat as "nothing yet" rather than as a failure.
    sh("pkill", "-f", f"idb_companion --udid {UDID}")
    sh("idb", "disconnect", UDID)
    time.sleep(1)
    (OUT / "warmup.txt").write_text(idb("ui", "describe-all", "--api", "ax"))
    print("warm-up line:", (OUT / "warmup.txt").read_text().strip()[:90])
    for _ in range(30):  # the same poll `wait_booted` will do
        if len(tree()) > 1:
            break
        time.sleep(1)

    w, h = size()
    print(f"device is {w}x{h} points")

    # 1. The library, the screen everything else is layered over.
    sh("xcrun", "simctl", "terminate", UDID, "com.apple.shortcuts")
    time.sleep(1.5)
    open_url("shortcuts://")
    time.sleep(4)
    capture("library", [(w // 2, h // 2)])

    # 2. The import sheet, in the Shortcuts app's own tree.
    setup_path = build(SETUP_NAME, setup_probe(SETUP_NAME))
    open_file(setup_path)
    sheet = wait_for("Set Up Shortcut")
    fx, fy = (
        int(sheet["frame"]["x"] + sheet["frame"]["width"] / 2),
        int(sheet["frame"]["y"] + sheet["frame"]["height"] / 2),
    )
    capture("import-sheet", [(fx, fy)])

    # 3. The setup question page, keyboard down: the field and both buttons.
    tap(sheet)
    time.sleep(2)
    field = next((e for e in tree() if e.get("type") in ("TextField", "TextArea")), None)
    if field is None:
        seen = [(e.get("type"), e.get("AXLabel")) for e in tree()][:20]
        raise SystemExit(f"no text field on the question page: {seen}")
    points = [
        (int(e["frame"]["x"] + e["frame"]["width"] / 2), int(e["frame"]["y"] + e["frame"]["height"] / 2))
        for e in (field, find("Add Shortcut", "Button"), find("Skip Setup", "Button"), find("Next", "Button"))
        if e
    ]
    capture("setup-question", points)

    # 4. The same page with the keyboard up, which is what covers the buttons.
    #    The same points as above, so a test can show that the tree still lists
    #    a button whose position now answers with a keyboard key — plus the
    #    keyboard's own Close, and a first-run typing tip if this device raises
    #    one, because `confirm` has to press both.
    tap(field)
    idb("ui", "text", SAMPLE)
    time.sleep(1.5)
    kept = next((e.get("AXValue") for e in tree() if e.get("type") in ("TextField", "TextArea")), None)
    print(f"  typed {SAMPLE!r}, the field kept {kept!r}")
    overlay = [e for e in (find("Close", "Button"), find("Continue", "Button")) if e]
    capture("setup-question-keyboard", points + [_center(e) for e in overlay])
    print(f"  keyboard overlay: {[e.get('AXLabel') for e in overlay] or 'nothing'}")

    # 5. The runner's Ask dialog, which no tree query can see.
    open_file(build(ASK_NAME, ask_probe(ASK_NAME)))
    tap(wait_for("Add Shortcut"))
    time.sleep(3)
    sh("xcrun", "simctl", "terminate", UDID, "com.apple.shortcuts")
    time.sleep(1.5)
    open_url("shortcuts://run-shortcut?name=" + urllib.parse.quote(ASK_NAME))
    time.sleep(6)
    ask = scan(w, h)
    field_seed = None
    for y in range(int(h * 0.05), int(h * 0.98), 8):
        e = at(w // 2, y)
        if e is not None and e.get("type") in ("TextField", "TextArea"):
            field_seed = (w // 2, y)
            break
    if field_seed is None:
        raise SystemExit("the Ask dialog never showed a text field; did the run start?")
    fx, fy = field_seed
    seeds["_field"] = (round(fx / w, 3), round(fy / h, 3))
    ask_points = {label: seed_of(e, w, h) for label, e in ask.items() if label in SEED_LABELS}
    capture("ask-dialog", [(fx, fy), *[point for _fraction, point in ask_points.values()]])
    for label, (fraction, _point) in ask_points.items():
        seeds[label] = fraction

    # 6. The clipboard consent, then the output sheet: answer and allow through.
    idb("ui", "tap", str(fx), str(fy))
    time.sleep(1)
    idb("ui", "text", ANSWER)
    time.sleep(1)
    capture("ask-dialog-typed", [(fx, fy)])
    if "Done" in ask:
        tap(ask["Done"], settle=4)
    consent = scan(w, h)
    if consent:
        points = {label: seed_of(e, w, h) for label, e in consent.items() if label in SEED_LABELS}
        capture("clipboard-consent", [point for _fraction, point in points.values()])
        for label, (fraction, _point) in points.items():
            seeds.setdefault(label, fraction)
        allow = consent.get("Always Allow") or consent.get("Allow")
        if allow:
            tap(allow, settle=5)
    output = scan(w, h)
    if output:
        points = {label: seed_of(e, w, h) for label, e in output.items() if label in SEED_LABELS}
        capture("output-sheet", [point for _fraction, point in points.values()])
        for label, (fraction, _point) in points.items():
            seeds.setdefault(label, fraction)
        allow = output.get("Always Allow") or output.get("Allow")
        if allow:
            tap(allow, settle=3)

    _write_seeds(seeds, w, h)
    _write_readme(w, h)
    print(f"\nfixtures in {OUT}. Paste {OUT / 'seeds.txt'} into harness.py's SEEDS.")


def _center(e: dict) -> tuple[int, int]:
    f = e["frame"]
    return int(f["x"] + f["width"] / 2), int(f["y"] + f["height"] / 2)


def seed_of(e: dict, w: int, h: int) -> tuple[tuple[float, float], tuple[int, int]]:
    """A seed's fraction, and the point that fraction derives back to.

    The point is derived from the *rounded* fraction, not from the element's
    center, because `harness.py` holds the rounded number and computes
    `int(w * fx)` from it. Naming the capture after the raw center would leave
    the fixture a pixel away from the point the finder asks for.
    """
    cx, cy = _center(e)
    fx, fy = round(cx / w, 3), round(cy / h, 3)
    return (fx, fy), (int(w * fx), int(h * fy))


def _write_seeds(seeds: dict[str, tuple[float, float]], w: int, h: int) -> None:
    """Print the seed table as Python, Always Allow before Allow."""
    order = ["Always Allow", "Allow", "Allow Once", "Don't Allow", "Done", "Cancel"]
    lines = ["SEEDS: tuple[tuple[str, float, float], ...] = ("]
    for label in order:
        if label in seeds:
            fx, fy = seeds[label]
            lines.append(f'    ("{label}", {fx:.3f}, {fy:.3f}),   # ({int(fx * w)}, {int(fy * h)}) on {w}x{h}')
    lines.append(")")
    if "_field" in seeds:
        fx, fy = seeds["_field"]
        lines.append("")
        lines.append(f"ASK_FIELD: tuple[float, float] = ({fx:.3f}, {fy:.3f})   # ({int(fx * w)}, {int(fy * h)})")
    missing = [label for label in order if label not in seeds]
    if missing:
        lines.append("")
        lines.append(f"# never seen in this capture: {', '.join(missing)}")
    (OUT / "seeds.txt").write_text("\n".join(lines) + "\n")
    print("\n" + "\n".join(lines))


def _write_readme(w: int, h: int) -> None:
    runtime = sh("xcrun", "simctl", "list", "devices", "--json")
    (OUT / "runtime.json").write_text(runtime)
    (OUT / "README.md").write_text(
        f"""# idb fixtures

Captured by `tests/capture_idb_fixtures.py` on {time.strftime("%Y-%m-%d")}, from a
{w}x{h}-point device (UDID `{UDID}`), with idb `{sh("brew", "list", "--versions", "idb-cli").strip()}`.
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
"""
    )


if __name__ == "__main__":
    main()
