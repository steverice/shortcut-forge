"""Driving a headless macOS guest: create it, bless it, prove it can be driven.

Measured against macOS 27.0 on Apple Silicon with tart 2.37.0. What each finding
cost and how it was established is in `docs/macos-guest.md`. Three are worth
repeating here, because getting any of them wrong fails silently:

- Screen capture and pointer input are **separate** grants. A guest holding only
  the first renders flawlessly and ignores every click.
- A denied capture is not an error. The client connects, authenticates, exits 0,
  and hands back a well-formed frame of exactly one color.
- A guest at 2x HiDPI renders perfectly and drops every pointer event, while
  keyboard events still land.

So a check that asks "did the client succeed" or even "did a screen appear"
certifies guests that cannot be driven. Prove input by clicking something and
confirming the effect over SSH, after a restart.

Nothing is imported here: `bake` needs the `[guest]` extra, and `tart` and
`bless` are useful without it.
"""
