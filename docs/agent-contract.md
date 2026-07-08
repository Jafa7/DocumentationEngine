# Agent contract

This document describes how an AI client (a coding agent, an MCP adapter, or
a Paradigmarium-style wrapper) should safely drive the `docsystem` CLI. It is
provider-neutral: it describes the core CLI's contract, not any specific
provider's orchestration.

## Read-only vs. mutating commands

Three operations write anything:

- `docsystem init PROJECT` creates `.docsystem.toml` and the documentation
  root. It refuses to overwrite an existing configuration, but it is a
  bootstrap/mutating command, not a read-only one: an agent must not run it
  without explicit approval, the same as any other command that creates
  files in the target tree.
- `docsystem migrate PROJECT --apply` rewrites resolved legacy relation
  values in place.
- `docsystem index PROJECT --write` writes a new projection generation
  below `.docsystem/cache`.

Every other command — `doctor`, `show-config`, `catalog`, `validate`, `read`,
`dependencies`, `context`, `impact`, `migration-report`, `migrate` without
`--apply`, `readiness`, and `index`/`changes` without `--write` — is
read-only. An agent may call any read-only command freely to inspect project
state before deciding whether a mutating command is warranted.

`docsystem migrate` without `--apply` is always a preview: it computes and
prints the same plan `--apply` would write, but touches nothing. An agent
should treat the presence of `--apply` as the sole signal that a command
will mutate the source tree, and should surface that distinction to the
human or calling system before using it, exactly like any other
destructive/hard-to-reverse action.

## Protect local-only state before risky work

An adopting project may keep its documentation root, `.docsystem.toml`,
`.docsystem/` projection cache and orchestration/runtime state outside git.
Those files are still source-of-truth operational state. An agent must not
use a clean git status as evidence that broad filesystem operations are safe:
ignored files can be destroyed while git remains clean.

Before recursive copy, move, delete or sync commands; generated migrations;
bulk rewrites; cross-OS shell snippets; or any task that touches ignored
local-only documentation, the agent must run the project's local backup
command or stop and ask the user to run it. The backup command and destination
are project-local policy and should live outside reusable public
documentation. See [`docs/local-state-safety.md`](local-state-safety.md) for
the portable contract a downstream project should implement.

When installing or adopting Documentation Engine for a project that will keep
important state outside git, the agent must ask the user where backups should
be stored and then write that choice only to local ignored policy, such as
`.agents/local/backup-policy.md`. It must not assume a default personal path
or commit the user's backup location to public documentation.

## Always pass the project root explicitly

Every command accepts the project root as a positional argument and only
falls back to the current working directory when it is omitted. That
fallback exists for interactive human use. An agent must always pass the
project root explicitly and must never rely on its own working directory
matching the target project: agent processes are routinely started in
unrelated directories, and an implicit-cwd invocation against the wrong
directory produces a confusing `configuration not found` error at best and
operates on an unintended project at worst.

## Prefer `--json` over parsing text

`readiness`, `migration-report`, `catalog --explain`, `changes` and
`context` accept `--json` and print one deterministic JSON value instead of
tab-separated or prose text. An agent should use `--json` wherever it needs
to branch on the result programmatically, rather than parsing the default
human-readable output, which is free to add explanatory text over time.
Every `--json` root is an object carrying `"schema_version": 1`; an agent
should check it before assuming field semantics, since it is bumped only on
breaking changes while new fields may appear without a bump. See
[`docs/paradigmarium-integration.md`](paradigmarium-integration.md) for the
schema each command's `--json` output follows. MCP clients get the same
read-only commands as typed tools via
[the MCP adapter](mcp-adapter.md); it never exposes a mutating command.

## Exit codes and diagnostics

Every command returns `0` on success (or, for `readiness`, when the project
is ready) and `1` otherwise. Human text output writes `ERROR` and `WARNING`
diagnostics to stderr while stdout carries only the command's data. In
`--json` mode, diagnostics may be carried structurally in the JSON payload
and human stderr diagnostics may be suppressed. An agent should treat a
non-zero exit code as authoritative and should not infer failure or success
from stdout text alone.

## Adoption sequence

For an existing project, `docsystem readiness PROJECT --json` is the
starting point. Its `next_command` field names the single safe next
command for the project's current state — never a source-mutating default.
An agent driving adoption should call `readiness`, follow `next_command`,
and re-check `readiness` after each step, rather than assuming a fixed
command order.

## Boundaries and stable IDs are human decisions

`migration-report` and `readiness` distinguish `resolved` legacy relations
(a relative path that unambiguously maps to a cataloged stable ID) from
`boundary` values (external URLs and resources that are not, and must never
become, document relations). An agent must not invent a stable ID for a
boundary value or silently resolve one; `docsystem migrate` only ever
touches values the engine has already classified as unambiguously resolved.
Stable IDs themselves are project-assigned identity, not something an agent
should generate as a side effect of an unrelated task.

## Context is explicit, never silently truncated

`docsystem context ID PROJECT` reports exactly what it included (navigation,
explicit sections, dependency traversal) and what it omitted, instead of
truncating to a token budget. An agent that needs more context should expand
the request with `--depth`, `--include-related`, or `--include ID#anchor`
rather than assuming omitted material is irrelevant. The packet's final
`Packet stats` section reports included/omitted counts and the body size in
lines and UTF-8 bytes; an agent should use it to decide whether an expanded
follow-up request fits its budget instead of re-measuring the output.
