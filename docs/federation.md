# Read-only multi-catalog federation

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

## Trust and write boundaries

Federation is read-only and currently reads authoritative Markdown directly.
It does not create a workspace projection, copy or synchronize sources,
authorize cross-source writes, acquire concurrent-write locks, authenticate
remote users or run a server. Existing single-source commands and projections
are unchanged.

Future bounded write support must retain per-source authorization and separate
change journals. A federated read never grants permission to modify another
source.
