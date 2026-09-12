# Contributing

## Development setup

Requires [uv](https://docs.astral.sh/uv/):

```
git clone <this repo> shortcut-forge
cd shortcut-forge
uv sync --dev
uv run pre-commit install --hook-type commit-msg --hook-type pre-commit
```

`uv sync` installs the package in editable mode with the dev dependencies into
a local `.venv`, including the `sim` extra's numpy, Pillow, and pyobjc. Use
`uv run <command>` to run tools in that environment.

## Commit conventions

[Conventional Commits](https://www.conventionalcommits.org/) with
[Gitmoji](https://gitmoji.dev/). Write `feat: …`, `fix: …`, `docs: …`,
`style: …`, `refactor: …`, `test: …`, `chore: …`, or `ci: …`; the commit-msg
hook validates the format and prepends the emoji. Include a scope when it helps
(`feat(checks): …`). Use `BREAKING CHANGE:` in the body or `!` after the type
for a breaking change.

## Running checks

```
make lint      # ruff check + ruff format --check + ty check
make test      # unit tests (excludes integration)
make check     # lint + test — run before every commit
```

## Pull requests

1. Branch from `main`.
2. Make changes with conventional commits.
3. Run `make check`.
4. Open a PR against `main`. CI validates commit messages, lint, and tests.

## Releases

Version bumps and the changelog are the release workflow's job. Never run
`cz bump` locally and never hand-edit the version or `CHANGELOG.md`.

## Adding a finding

Anything learned on a device belongs in `docs/building-shortcuts.md` or
`docs/simulator-harness.md`, written as a measurement rather than a theory:
what was built, what was done, what the device held afterward. A claim that
lives only in a code comment cannot be checked by anyone else.
