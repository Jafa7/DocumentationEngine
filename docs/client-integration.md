# AI client integration

Documentation Engine is provider- and adopter-neutral. This document describes
how a project-local wrapper can consume its public CLI and JSON contracts
without coupling the core package to one host, provider or project workflow.

## Consumer install, not a source checkout

A project-local wrapper depends on `docsystem` as an ordinary installed
package and invokes the `docsystem` console script produced by the build. It
never sets `PYTHONPATH` and never imports this repository's `src/` directly.
See the top-level README's [Installation](../README.md#installation) section
for the consumer/contributor boundary and
[`CONTRIBUTING.md`](../CONTRIBUTING.md#additional-packaging-checks) for the
reproducible installed-consumer check.

## Example profile

[`examples/generic-adopter/`](../examples/generic-adopter/) is a
small, public, runnable `.docsystem.toml` and Markdown tree that demonstrates
the profile shape described in [`docs/adoption.md`](adoption.md): logical
areas, an identifier namespace, and one intentionally unmigrated legacy
relation. It is a synthetic fixture for exercising the CLI and contains no
private adopter content.

## Machine-readable output for AI-agent wrappers

A project-local wrapper that drives `docsystem` from an AI agent should
prefer `--json` over parsing the default human-readable text, so it never has
to depend on prose wording remaining stable. `--json` is available on the
adoption-oriented, read-only commands:

- `docsystem readiness PROJECT --json`
- `docsystem migration-report PROJECT --json`
- `docsystem catalog PROJECT --explain --json`
- `docsystem changes PROJECT --json`
- `docsystem context DOCUMENT_ID PROJECT --json`

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
array. `context --json` returns the packet structured — `documents` (each
with typed `revision`, `navigation`, `explicit_sections`, `omitted_h2` and
`sections`),
`freshness`, `migrations`, `boundaries`, `related_omitted` and `stats` —
instead of the Markdown packet text. `navigation` keeps the established
complete Markdown prefix, including leading YAML front matter, while the typed
`revision` lets a client retain the exact value needed for a later
`--assume-known ID@REV` call. `sections` lists every ATX section in document
order as `{anchor, title,
level, lines, bytes}`, where `bytes` is the UTF-8 size of the raw section
slice — the same slice the projection hashes, identical on both serving
paths; a `--include ID#anchor` fetch returns the normalized form, which may
differ by a trailing-newline byte for a document's last section. `context --outline` (add `--json` for the structured form)
returns the same root shape with `"outline": true` and, per document, only
`id`, `path`, `revision`, `relations` and `sections` — no content — plus a
`stats` of `included_documents`, `listed_sections` and `total_section_bytes`;
a wrapper
budgeting tokens should call `--outline` first and follow up with `--include
ID#anchor` or a full `context` call once it knows what it needs. `--outline`
is incompatible with `--anchor`/`--include`, since it never returns content.

`context --json` also supports two content-omitting delta modes. With
`--assume-known ID@REV` (repeatable), a document that lands in the packet at
the declared current revision drops its `navigation` key and instead carries
`"content_omitted": {"reason": "assumed-known", "declared_revision": REV}`
while still listing `sections` and `omitted_h2`; the root additionally carries
`"assume_known_mismatches"` (a possibly empty array of `{"id",
"declared_revision", "current_revision"}` for declarations whose revision no
longer matches — those documents keep full content) and `stats` gains
`"assumed_known_omitted"`. These extra keys appear only when
`--assume-known` was passed, so existing payloads stay byte-stable. With
`--since GENERATION`, a document unchanged since that generation drops
`navigation` for `"content_omitted": {"reason": "unchanged-since",
"generation": GEN12}` (GEN12 = the generation's first twelve characters),
while a changed document keeps `navigation`, gains `"changed_sections":
[anchors...]` listing every anchor at any level whose slice hash differs or
is new, and carries the content of each changed H2 that is not already inside
`navigation` (i.e. not covered by the lead-in or `navigation.extend_through`)
in the existing `explicit_sections` array under the same anchor; a changed H1
or a changed `extend_through` H2 appears in `changed_sections` but is not
duplicated into `explicit_sections`. It also reports `removed_sections`,
typed `metadata_changes` with before/after values and
`source_changed_outside_sections`. Explicit `--anchor`/`--include` selections
win over unchanged-content omission. The selected retained generation is
verified against its manifest, shards, active configuration and reconstructed
hash before comparison. `--since` and
`--assume-known` are mutually exclusive, and neither combines with
`--outline`; a rejected combination exits `1` with no stdout.

A wrapper that speaks MCP can skip the CLI entirely and use
[the MCP adapter](mcp-adapter.md), which exposes these same read-only
commands as typed tools over the identical JSON contract.

`docsystem readiness PROJECT --json` is the entry point for an adoption
sequence: its `next_command` field names the single safe next command for
the project's current state (`init`, `doctor`, `migrate`, `index --write`, or
a `context` read), so a wrapper can drive the sequence without re-deriving
that policy itself. See [`docs/agent-contract.md`](agent-contract.md) for the
full read-only/mutating command classification an AI client should follow.

## Non-goals

Registry synchronization, project-specific orchestration and private history
or backup policy remain project-local. They are not part of the provider- and
adopter-neutral core contract.
