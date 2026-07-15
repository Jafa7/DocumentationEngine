# Contributing

Documentation Engine takes engineering rules from
[`AGENTS.md`](AGENTS.md); it is the authoritative, provider-neutral source for
how to work in this repository. This file explains the contributor workflow
without redefining installation, product behavior or architecture.

## Development workflow

Choose the contributor checkout path from the README's
[Installation](README.md#contributor-or-unreleased-checkout) section. That is
the single source for consumer, MCP and contributor installation commands.

From that checkout, run the CLI against the current sources through `uv`:

```bash
uv run python -m docsystem --help
```

Do not set an ad-hoc `PYTHONPATH` or depend on an unrelated globally installed
`docsystem` executable while developing. The installed consumer path is
exercised separately by `scripts/installed_cli_smoke.sh`. See
[Development and release verification](README.md#development-and-release-verification)
in the README for the full contract between the two.

Keep a change narrowly scoped. Update tests and public documentation in the
same change when behavior or a public contract changes.

## Verification

Select the narrowest verification level that covers the risk, as defined in
[`AGENTS.md`](AGENTS.md):

### Structural only

For prose documentation, comments, badges or repository metadata with no
runtime, contract, packaging or generated-output effect, do not run a test
suite. Validate the changed structure and links when applicable, then run:

```bash
git diff --check
```

### Focused

For an isolated implementation or test change, run the directly affected tests
and lint the touched code. For example:

```bash
TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run pytest tests/test_workspace.py
TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run ruff check src/docsystem/workspace.py
git diff --check
```

Every configuration-behavior change must include and run focused configuration
tests.

### Full

Run the full gate once on a finished candidate when changing shared contracts
or schemas, CLI behavior, catalog/graph/section semantics, projection or
workspace safety, dependencies, packaging/CI, cross-module behavior or a
release candidate:

```bash
TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run pytest
TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run ruff check .
git diff --check
```

Native `/tmp` keeps pytest capture deterministic when WSL inherits Windows
temporary directories. Do not repeat an already-passing full gate after a
later structural-only edit unless it changes generated artifacts, packaging
inputs or test expectations.

### Additional packaging checks

Run only the additional checks relevant to the changed surface:

```bash
uv lock --check
./scripts/installed_cli_smoke.sh
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
  `Permission denied`. Run this pair before committing, and apply the same
  check to any other shell script you add or modify under `scripts/`.

Record which verification level you selected, why it applies, which commands
passed and which checks were deliberately not run. Never report an unrun check
as passing.

## What a change must satisfy

- Public documentation and code comments are English.
- Public product documentation, contracts, fixtures and examples are
  adopter-neutral and use synthetic identities by default. Do not publish
  private adopter prompts, logs, document bodies, planning or roadmap material,
  local runtime state or credentials. Put an explicitly authorized real
  adopter example in a clearly labeled integration guide, case study or
  compatibility profile, separate from the canonical generic contract. Follow
  the full rule in [`AGENTS.md`](AGENTS.md).
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
