# Contributing

Documentation Engine takes engineering rules from
[`AGENTS.md`](AGENTS.md); it is the authoritative, provider-neutral source for
how to work in this repository. This file only adds the mechanics of setting
up a contributor checkout and the checks a change must pass — it does not
duplicate `AGENTS.md`, [`README.md`](README.md) or
[`docs/architecture.md`](docs/architecture.md).

## Contributor setup vs. consumer install

A consumer installs the published package from PyPI:

```bash
pip install documentation-engine
pip install "documentation-engine[mcp]"
```

Contributors, and anyone tracking unreleased development, instead work from a
source/editable checkout of this repository:

```bash
git clone https://github.com/Jafa7/DocumentationEngine
cd DocumentationEngine
uv sync
```

Run the CLI as a module against `src/` — `uv run python -m docsystem ...` — or
with `PYTHONPATH=src`. Do not depend on an installed `docsystem` console
script while developing; that is the consumer path, exercised separately by
`scripts/installed_cli_smoke.sh`. See
[Development setup vs. consumer install](README.md#development-setup-vs-consumer-install)
in the README for the full contract between the two.

## Checks

While iterating, run the focused check for the module you are changing (for
example a single `uv run pytest tests/test_<module>.py`, or
`uv run ruff check src/docsystem/<module>.py`) rather than the full gate after
every edit. Before handing off or opening a pull request, run the complete
gate once, from inside WSL if you are on Windows — native `/tmp` keeps pytest
capture deterministic and avoids permission issues with Windows-side temp
directories:

```bash
TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run pytest
TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run ruff check .
uv lock --check
./scripts/installed_cli_smoke.sh
git diff --check
git ls-files --stage scripts/installed_cli_smoke.sh
test -x scripts/installed_cli_smoke.sh
```

- `uv lock --check` verifies `uv.lock` still matches `pyproject.toml`; run it
  whenever you touch dependencies.
- `./scripts/installed_cli_smoke.sh` builds a wheel, installs it into an
  isolated venv, and runs the installed `docsystem` entry point against a
  fresh fixture from an unrelated directory; run it whenever packaging,
  entry points or the CLI surface change.
- `git diff --check` catches trailing whitespace and conflict markers before
  they land.
- The `git ls-files`/`test -x` pair confirms `scripts/installed_cli_smoke.sh`
  kept its `100755` executable bit — Windows-side Git staging over
  `\\wsl.localhost` can silently drop it and break CI with
  `Permission denied`. Apply the same check to any other shell script you add
  or modify under `scripts/`.

## What a change must satisfy

- Public documentation and code comments are English.
- The core package (`src/docsystem/`) stays provider-neutral; do not add
  Claude-, Copilot- or any other agent-specific behavior there. Provider
  adapters live outside the core (see `docs/mcp-adapter.md` for the pattern).
- Every change to configuration behavior needs tests.
- Any change to a public contract — CLI flags, output format, `--json`
  schemas, MCP tools — needs a `CHANGELOG.md` entry and the relevant doc
  update in the same change.
- Do not commit private or local generated state: `.docsystem.toml` and
  `.docsystem/` belong to adopting projects, not this repository, and
  generated projection/cache output is disposable. See
  [local state safety](docs/local-state-safety.md).

## External contributions

Work on a branch and open a pull request; do not push directly to `main`.
Fill in the pull request template's checklist, including which checks you ran
and which are not applicable (with a reason). See
[the adopter reporting guide](docs/adopter-reporting.md) if you are instead
filing a problem found while adopting Documentation Engine in another
project, and [`SECURITY.md`](SECURITY.md) if you found a vulnerability.
