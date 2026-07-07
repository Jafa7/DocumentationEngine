# Paradigmarium integration

Documentation Engine is provider-neutral; Paradigmarium is its first
downstream consumer, not a dependency of the core package. This document is
public integration guidance only — it never embeds private Paradigmarium
planning content, and the core package (`src/docsystem/`) never contains
Paradigmarium-specific behavior.

## Consumer install, not a source checkout

A Paradigmarium-style wrapper depends on `docsystem` as an ordinary installed
package and invokes the `docsystem` console script produced by the build. It
never sets `PYTHONPATH` and never imports this repository's `src/` directly.
See the "Development setup vs. consumer install" section of the top-level
[`README.md`](../README.md) and `scripts/installed_cli_smoke.sh` for the
reproducible check of that install path.

## Example profile

[`examples/paradigmarium-profile/`](../examples/paradigmarium-profile/) is a
small, public, runnable `.docsystem.toml` and Markdown tree that demonstrates
the profile shape described in [`docs/adoption.md`](adoption.md): logical
areas, an identifier namespace, and one intentionally unmigrated legacy
relation. It is a fixture for exercising the CLI, not the private
Paradigmarium plan tree.

## Machine-readable output for AI-agent wrappers

A Paradigmarium wrapper that drives `docsystem` from an AI agent should
prefer `--json` over parsing the default human-readable text, so it never has
to depend on prose wording remaining stable. `--json` is available on the
adoption-oriented, read-only commands:

- `docsystem readiness PROJECT --json`
- `docsystem migration-report PROJECT --json`
- `docsystem catalog PROJECT --explain --json`
- `docsystem changes PROJECT --json`

Each prints one deterministic JSON object to stdout (sorted keys, stable
field names) carrying the same diagnostics that the text form prints as
human `ERROR`/`WARNING` lines on stderr. In `--json` mode those human
stderr diagnostics may be suppressed since the JSON payload already carries
the same information in structured form, so a wrapper does not need to read
stderr to make a decision. Exit codes are unchanged by `--json`: `0` on
success or a ready project, `1` otherwise.

Every `--json` root is an object with a `"schema_version": 1` field; it is
bumped only on a breaking change to an existing field, while new fields may
be added without a bump, so a wrapper should tolerate unknown keys. List
payloads live under a named key rather than as a bare array root:
`catalog --json` returns `{"schema_version": 1, "documents": [...]}`,
`catalog --explain --json` returns `{"schema_version": 1, "memberships":
[...]}`, `migration-report --json` returns `resolved` and `boundaries`
arrays, and `changes --json` returns a `status` string plus a `changes`
array.

`docsystem readiness PROJECT --json` is the entry point for an adoption
sequence: its `next_command` field names the single safe next command for
the project's current state (`init`, `doctor`, `migrate`, `index --write`, or
a `context` read), so a wrapper can drive the sequence without re-deriving
that policy itself. See [`docs/agent-contract.md`](agent-contract.md) for the
full read-only/mutating command classification an AI client should follow.

## Non-goals

Registry synchronization, finish orchestration, private history/backup and
any other Paradigmarium-specific orchestration remain project-local to
Paradigmarium. They are not, and will not become, part of this public
package or its documentation.
