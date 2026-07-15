# Multi-catalog federation

Documentation Engine can build one direct-Markdown graph over every available
source in a local [documentation workspace](workspace-sources.md). Federation
does not merge project ownership: it qualifies each local stable ID with the
workspace source name.

```text
project-a::DOC-001
shared-guides::GUIDE-004#security
```

The source name is the stable lowercase name authored in `workspace.toml`.
The document ID and optional canonical anchor retain the owning project's
syntax. The same local ID may exist in several sources without collision.

## Authored cross-source relations

Cross-source semantic relations remain authored Markdown metadata. Use a
quoted `source::ID` value in any ordinary semantic relation field:

```yaml
depends_on: ["shared-guides::GUIDE-004"]
related: ["project-b::DOC-012"]
validated_against: ["shared-guides::GUIDE-004@3"]
```

Unqualified IDs continue to mean the current source. A single-source query
does not invent an edge for a qualified value: it reports a visible
`requires workspace federation` boundary. A federation query resolves the
value only when the named source exists, is available and contains the exact
document ID. Unknown sources/documents, malformed addresses and self-edges
fail closed.

Sources that opt into `relations.legacy_paths = "resolve-with-warning"` keep
their resolved path-to-ID mappings in federated dependency/context diagnostics.
Federation does not silently turn adoption compatibility into authored IDs.

Cross-source Markdown-path inference is deliberately unsupported. Use stable
qualified IDs for semantic relations and normal Markdown links for navigation
inside one source.

## Commands

```bash
docsystem federation catalog . --workspace /path/to/workspace --json
docsystem federation dependencies project-a::DOC-001 . --workspace /path/to/workspace
docsystem federation dependencies shared-guides::GUIDE-004 . \
  --workspace /path/to/workspace --reverse
docsystem federation references project-a::DOC-001#purpose . \
  --workspace /path/to/workspace --transitive --json
docsystem federation context project-a::DOC-001#purpose . \
  --workspace /path/to/workspace --depth 1 --json
docsystem federation context project-a::DOC-001 . \
  --workspace /path/to/workspace --include shared-guides::GUIDE-004#security
docsystem federation impact shared-guides::GUIDE-004 . \
  --workspace /path/to/workspace --json
docsystem federation index . --workspace /path/to/workspace --write
docsystem federation index . --workspace /path/to/workspace
docsystem federation changes . --workspace /path/to/workspace --json
```

The positional project is used only to discover `.docsystem.local.toml` when
`--workspace` and `DOCSYSTEM_WORKSPACE` are absent. Federated output contains
source names and source-relative Markdown paths, never private absolute paths.

`context` preserves source Markdown. It returns each selected document's
navigation prefix, optional exact sections, every omitted H2 anchor and graph
relations omitted by the selected depth/filter. It
does not summarize or truncate source text to a token budget. Dependencies
follow `derived_from`, `depends_on` and `validated_against`; opt-in
`--include-related` also follows `related` and `supersedes`. `--include`
addresses are repeatable.

Every federation command requires all registered sources and their catalogs
to be available and valid. This intentionally strong completeness gate means
an unavailable or invalid source produces diagnostics on stderr, exit code 1
and no partial stdout. Revision pins remain visible as current, stale or
historical snapshot evidence.

## Workspace projection

`federation index --write` builds a disposable projection below the workspace
root. It writes no source-owned cache and never modifies source Markdown or
configuration. The projection uses immutable content-addressed generations,
an atomic current pointer, per-source objects and one aggregate graph object.
An unchanged source keeps the same object identity across generations, so a
one-source update does not rewrite every other source shard.

`federation index` checks whether the selected generation is current.
`federation changes` reports source-level `added`, `removed` and `modified`
states without returning document bodies or private absolute paths. Workspace
membership, source visibility, projection-relevant configuration and every
Markdown source hash are bound into the generation.

Existing federation queries prefer a verified workspace projection. They
still prove freshness across every registered source before serving a complete
answer, but skip repeated Markdown, YAML and graph parsing. When the projection
is absent they use direct Markdown normally. A stale, incompatible or corrupt
projection produces one bounded stderr warning and a complete direct rebuild;
direct and projected stdout remain byte-identical.

The stable `projection: direct-markdown` JSON field and `Source mode: direct
Markdown` text label describe Markdown as the authoritative content source;
they are not execution-provenance fields. Projection fallback provenance is
reported separately on stderr so the semantic packet stays byte-identical.

## Source-qualified bounded maintenance

Federation remains a read graph, not a cross-source writer. A caller may use
the ordinary `maintenance` command with `--source NAME` to update one selected
source only when that source opts into `write = "managed-maintenance"` in
`workspace.toml`. The write requires both the canonical source block hash and
the deterministic preview hash; recovery requires the exact source-local
journal manifest hash. See [workspace source selection](workspace-sources.md).

Each source owns its journal and lock. There is deliberately no atomic write,
rollback or shared workstream state across sources. A successful source write
refreshes only that source's disposable project projection. It makes any
existing aggregate federation generation stale; federated reads then fall
back visibly to direct Markdown until `federation index --write` explicitly
builds a new complete generation. Direct and rebuilt-projection query stdout
remain identical.

## Trust and write boundaries

Federated queries are read-only. The explicit `federation index --write`
operation writes only disposable workspace projection state. Federation does
not copy or synchronize sources, authorize cross-source writes, acquire
concurrent-write locks, authenticate remote users or run a server. Existing
single-source commands and projections are unchanged.

A federated read never grants permission to modify another source. The narrow
selected-source maintenance policy does not authorize arbitrary edits,
cross-source synchronization or deletion.
