# Architecture

## Product boundary

Documentation Engine owns deterministic documentation mechanics:

- project configuration and logical area mapping;
- Markdown metadata and stable IDs;
- hierarchical navigation validation;
- dependency and reverse-dependency graphs;
- inspectable context packets with explicit coverage and omissions;
- impact and changed-section analysis;
- versioned, sharded machine projections;
- bootstrap, diagnostics, adoption readiness reporting and migration tooling;
- guarded managed-block maintenance preview, journaled write and recovery.

It does not decide whether an architectural claim is correct, whether a review
is persuasive, or whether selected context is semantically sufficient.

## Layers

```text
Human / AI client
        |
Provider adapter (Codex, Claude Code, MCP, CLI)
        |
Documentation Engine core
        |
Project policy and profile
        |
Markdown source of truth
        |
Generated sharded projection
```

The core must work without an AI client. Integrations translate client actions
into stable core operations.

## Configuration model

Scripts address logical roles such as `roadmap` or `reviews`. A project maps
those roles to local paths in `.docsystem.toml`. Renaming a directory therefore
does not require patching engine code.

Hard invariants are not configurable:

- generated data cannot override Markdown;
- stable IDs cannot be silently reused;
- projection updates are atomic;
- stale projections are detectable;
- omitted context remains visible;
- snapshot pins and freshness pins have distinct semantics.

Project policy may configure:

- documentation root and language;
- logical area paths;
- ID namespaces;
- templates and document types;
- lifecycle states;
- review policy;
- graph-health advisory thresholds and required metadata fields;
- projection retention;
- legacy path-relation migration and historical snapshot document types;
- provider adapters.

A local workspace registry is an outer selection layer, not another project
configuration table. It maps a stable source name to one contained project
root, after which the existing configuration, catalog, graph and projection
contracts apply unchanged. It deliberately does not aggregate multiple
sources; see [workspace source selection](workspace-sources.md).

## Scalable projection

The target projection is sharded and generation-based:

```text
.docsystem/cache/
├── current.json
└── generations/<content-hash>/
    ├── manifest.json
    ├── areas/<logical-path>/_index.json
    ├── documents/<namespace>/<bucket>/<ID>.json
    ├── reverse/<namespace>/<bucket>/<ID>.json
    ├── references/<namespace>/<bucket>/<ID>.json
    └── reverse-references/<namespace>/<bucket>/<ID>.json
```

A stable ID maps to a document shard without a global routing table.
`references` uses the same deterministic routing and verifies only the graph
shards reached by its query after validating the manifest root and source
freshness; unrelated shard bodies are not read.

Read commands (`read`, `context`, `impact`) serve from the projection when it
is verified current. Verification enumerates the catalog's source paths
without parsing them, re-reads every included source and compares its sha256
with the generation manifest, and validates every consumed document and
reverse shard against its recorded hash. It also rejects the generation when the active configuration
fingerprint no longer matches the one recorded at build time — a normalized
digest of the documentation root identity, areas, identifiers, catalog
exclusions, `navigation.extend_through`, `relations.legacy_paths`,
`relations.snapshot_types`, `relations.snapshot_rules`, authored context views,
the projection format and the schema version — so a read-time policy change
forces a rebuild instead of serving differently-shaped output. The generation ID is the canonical hash of
the complete manifest,
including the hashes of document, reverse, reference, and reverse-reference
shards. Semantic shard tampering (a dropped dependency, an altered revision)
or a matching edit to a shard hash therefore invalidates the immutable
generation before any output is produced.
None of this parses Markdown on the fast path — what it removes is Markdown,
metadata and link parsing plus dependency-graph reconstruction, not source I/O;
a stat-based freshness cache that avoids re-hashing unchanged sources remains a
possible performance polish. Both serving paths reduce to one shared view
shape, so output is byte-identical regardless of which path produced it.

Each generation name is a hash of its canonical derived content together with
the configuration fingerprint that shaped it, so a semantic configuration
change yields a distinct generation. A new generation is assembled in a staging
directory and renamed into place before the small `current.json` pointer is
atomically replaced. Existing generation directories are never rewritten.
Readers validate schema, the manifest-root generation identity, the configuration
fingerprint, source hashes and required shard hashes; invalid,
stale or corrupt projections fall back to direct Markdown with a diagnostic.

Projection writes assume a single writer. Two concurrent `index --write`
runs do not corrupt a generation (staging plus rename is atomic per
generation), but retention cleanup may delete an older generation that a
concurrent reader has already selected; that reader then falls back to
direct Markdown with a visible diagnostic rather than serving mixed state.
Coordinating multiple writers is a caller/orchestrator responsibility, not
core engine behavior.

Retained generation manifests also drive two token-economy `context` modes
that omit content only when omission is provably safe, never as a silent
budget cut. `--assume-known ID@REV` is a client-declared cache: an agent
states a document it already holds, and the engine omits that document's
navigation excerpt only while its current revision still equals `REV`; a
stale declaration serves full content and emits a mismatch note. `--since
GENERATION` is a delta briefing against a retained generation manifest: for
each packet document the engine compares the per-section sha256 recorded in
that manifest against the current view (any level, no filtering) and reports
every differing or new anchor as `changed_sections` — the complete truth
signal. Documents whose source hash is unchanged are omitted entirely. For a
changed document, navigation is served as usual (it already covers everything
before the first H2, plus any `navigation.extend_through` H2s), and every
changed H2 that is *not* already inside that navigation prefix is attached as
a full `### Changed section` block — its H3+ descendants travel with it,
since a nested change also changes the enclosing H2's own slice hash. A
changed H1 or a changed H2 already inside `extend_through` is never
re-emitted as a block (navigation already served it), but it still appears in
`changed_sections`. A document absent from the referenced generation is
served in full: navigation plus every non-extend_through H2 block, with a
`new since GENERATION` note. This makes the packet's `Omitted H2` coverage
line truthful by construction: it lists only H2s that are neither inside
navigation nor emitted as a block. Anchors present only in the retained
generation are reported under `removed_sections`; semantic projection fields
report typed before/after values under `metadata_changes`; and a changed source
with no current or removed section anchor is marked as changed outside
addressable sections. An explicit `--anchor` or `--include` selection is never
discarded merely because its document is unchanged in the generation. Before
these comparisons, the retained generation is verified from its manifest,
document and reverse shards, active configuration fingerprint and manifest-root
generation hash. Both modes read the same shared view shape on either serving
path, so a delta served from a fresh projection's fast path is
byte-identical to the same delta reconstructed from direct Markdown, even when
the referenced generation is an older retained one rather than the current
pointer. `--since` and `--assume-known` are mutually exclusive, and neither
combines with `--outline`.

`context --compact` is a presentation projection over the same selected
documents. It unions overlapping navigation and explicit section line ranges,
emits disjoint original-Markdown fragments, and preserves every requested
address/reason mapping in a separate manifest. It performs no semantic
similarity matching or summarization. Direct and verified-projection paths
share the same line-range algorithm and therefore remain byte-identical.

## Markdown catalog and navigation

The catalog classifies every Markdown file below the documentation root.
Included files belong to a configured logical role, excluded files record the
first matching ordered catalog glob, and files in neither category are
unmapped validation errors. Exclusions are applied before source parsing.

An area mapped to `.` owns root-level documents and provides a fallback for the
whole tree. A more deeply nested area mapping takes precedence when configured
areas overlap.

Human navigation is hierarchical. Every non-index document must be linked from
the nearest `README.md` or `index.md` in its directory or an ancestor directory
within the same logical area. An index at an area's root is a navigation root;
a nested index must be linked from its nearest parent index. Having both index
names in one directory is invalid.

## Stable metadata and context addressing

Cataloged documents start with YAML front matter. The initial core contract
requires only a configured stable ID and a positive revision. Optional type,
status and additional project fields remain policy data rather than hard-coded
core behavior. Duplicate keys are rejected recursively so identity and policy
data cannot be silently overwritten by YAML parsing.

Semantic relations use stable IDs so file moves do not rewrite the dependency
graph. `validated_against` uses `ID@revision`; stale pins are reported without
assuming whether a project treats the document as current truth or a historical
snapshot. Human navigation continues to use ordinary relative Markdown links.
Graph queries fail closed when invalid metadata prevents a complete answer;
they never present a silently filtered partial graph as complete.

Historical classification is explicit project policy. `snapshot_types` matches
the pin-owning document by type; `snapshot_rules` can match its type, status or
both. Matching pins remain graph edges and packet evidence, but no longer act
as freshness warnings. The normalized rules are part of the projection
fingerprint, so changing lifecycle policy forces direct fallback and rebuild.

For adoption only, `relations.legacy_paths = "resolve-with-warning"` allows the
four path relations to resolve relative to their source document. Resolved
values become ordinary canonical ID edges and remain visible as migration
warnings. URLs and non-document resources are recorded as boundaries rather
than invented edges. The strict stable-ID contract remains the default: a
legacy path resolving to a cataloged document is still blocked in `strict`
mode until it is migrated or the project opts into `resolve-with-warning`.
Boundaries are never document relations, so they never require
`resolve-with-warning`: they are non-blocking in both modes. Catalog
resolution of legacy values (`relation_migrations`, `relation_boundaries`) is
independent of `relations.legacy_paths`, so `migration-report` and
`readiness` can report what a project could migrate before it opts into the
compatibility mode; only the resulting graph edges and validation severity
depend on the configured mode.

## Graph health

Graph health is a read-only interpretation of the existing graph, not a new
authority layer. `graph-health` derives factual metrics from authored metadata
edges, observed Markdown references, generated containment and explicit
boundaries. Optional project thresholds turn selected measurements into
advisory smells; the core does not infer architecture, rewrite relations or
promote an observed/generated edge into semantic authority. A verified
projection and direct Markdown reduce to the same health facts and output.
See [`graph-health.md`](graph-health.md) for the exact policy and CLI contract.

`docsystem migrate` computes a deterministic plan of the same resolved
mappings, previews it by default, and — only with an explicit `--apply` —
rewrites the exact YAML scalar span of each resolved value in place, leaving
the rest of the document (formatting, comments, unknown fields, the body and
all boundaries) untouched. `apply` re-validates the plan against a scratch
copy of the documentation tree before writing, and writes every affected file
through a temporary file that is renamed into place only after all temporary
writes succeed, so a failure never leaves a partially migrated multi-file
change. `docsystem readiness` is a read-only report over the same catalog
data — blocking errors, resolvable migrations, boundaries, stale pins and
projection state — with no source-mutating side effects.

`readiness`, `migration-report`, `catalog --explain`, `changes` and `context`
accept `--json`: one deterministic JSON value (sorted keys, stable field
names) that carries the same data as the text form plus the diagnostics that
otherwise go only to stderr, so a machine or AI-agent client never has to
parse human-oriented prose to act on the result. Every `--json` root is an
object carrying `"schema_version": 1`, bumped only on a breaking change to an
existing field; new fields may be added without a bump. Adding `--json` to a
command never changes its exit code or its default text output. The MCP
adapter (`docsystem.mcp_server`) is a thin subprocess wrapper over exactly
this CLI contract, exposing only read-only commands as tools. Text-preserving
MCP tools keep stdout byte-for-byte compatible, and their packet variants add
the same non-fatal diagnostics without forcing clients to parse stderr.

`context --json` additionally exposes each included document's typed
`revision` and lists its sections as `{anchor, title, level, lines, bytes}` in
document order, where `bytes` is
the UTF-8 size of the raw section slice (the same slice the projection
hashes; a `--include` fetch returns it normalized, so terminal sections may
differ by a trailing-newline byte) — a size map an agent can use to budget
before requesting content. The established `navigation` value remains the
complete Markdown prefix, including YAML front matter. `context --outline`
(text or `--json`) selects the same document set (`--depth`,
`--include-related` still apply) but omits navigation and section content
entirely, printing only that size map, so an agent can inspect a document's
shape for the cost of one small packet before deciding what to fetch with
`--include ID#anchor`. `--outline` therefore never combines with `--anchor`
or `--include`, which select content it does not return.

Optional `[context.views.NAME]` entries are project-authored progressive query
policy over the same semantic dependency graph. Each view fixes tier,
outline/navigation delivery, forward/reverse/both direction, depth, relation
filters and the `authored` layer. Direct Markdown and verified projections
reduce to the same outgoing/reverse edge views before traversal. Every edge
stopped by a filter or depth is emitted as a deterministic omission; a view
never raises edge authority, asserts semantic completeness or limits later
section/full-document access. Observed/generated layers remain outside this
first view contract rather than being approximated from authored relations.
Reverse and bidirectional traversal requires a globally valid catalog semantic
graph, while forward traversal keeps the existing selected-source validation
boundary.

ATX headings outside fenced code blocks form deterministic addressable
sections. A section includes nested headings until the next heading at the same
or a higher level. Duplicate generated headings receive deterministic numeric
suffixes.

A standalone HTML anchor containing only `id` or `name` may immediately precede
an ATX heading. Its valid value becomes the exact canonical anchor; generated
slugging remains unchanged for all other headings. Parser diagnostics reject
malformed, orphaned, multiple, duplicate and explicit/generated collisions.

The default navigation read ends before the first H2. A project may configure
canonical H2 anchors in `navigation.extend_through`; the result remains one
contiguous prefix ending after the furthest matching section. Missing anchors
fall back to the default, while a configured non-H2 match is invalid.

## Managed maintenance

A project may declare a bounded `[[maintenance]]` target in `.docsystem.toml`:
one canonical `source_document`/`source_anchor` owns the authored bytes of a
block, and a fixed list of `occurrences` names every document/section where a
replica may exist, each with one declared role — `current`, `historical`,
`example`, `snapshot` or `unmanaged`. Config validation is deterministic and
fails closed: unknown keys or roles, an empty or duplicate target name, an
empty or duplicate occurrence list, an invalid ID/anchor address, or an
occurrence that overlaps the declared source address are all rejected before
any Markdown is read.

```toml
[[maintenance]]
name = "install-version"
source_document = "DOC-001"
source_anchor = "install-block"

[[maintenance.occurrences]]
document = "DOC-002"
anchor = "quickstart"
role = "current"

[[maintenance.occurrences]]
document = "DOC-003"
anchor = "changelog-v1"
role = "historical"
```

The block itself is delimited by exact, deterministic HTML-comment markers —
`<!-- docsystem:source target=NAME -->`/`<!-- /docsystem:source target=NAME -->`
around the canonical block, and `<!-- docsystem:managed target=NAME -->`/
`<!-- /docsystem:managed target=NAME -->` around a replica. A marker must
occupy its own line; markers inside fenced code are inert, so documentation
that shows the syntax as an example is never mistaken for a real managed
block. Missing, duplicate, nested or crossed markers, and a marker whose span
escapes its declared section, are fail-closed diagnostics rather than a
guessed location.

`docsystem maintenance TARGET PROJECT --check|--preview|--write [--json]` reads the
canonical source block and every declared occurrence and reports, per
occurrence, its role and disposition. Only a `current` occurrence is preview
eligible and can be `clean` or `drifted`; every other role is reported as
visible, excluded evidence with its role as the reason and is never diffed.
Markers are never part of the compared payload. Comparison is exact over the
engine's decoded Markdown text: the shared reader canonicalizes platform line
endings to `\n`, but does not summarize, reflow or otherwise rewrite semantic
content. `--check` and `--preview` report the same
deterministic text/JSON result; only the exit-code contract differs: `--check`
returns `0` for a clean target and a stable non-zero `2` on drift, while
`--preview` always returns `0` for a valid target so it composes as a
read-only inspection rather than a gate. Invalid config, an unknown target, an
unknown or ambiguous document/section/marker address, or a graph-blocking
metadata error all fail closed with exit `1`, diagnostics on stderr only, and
empty stdout. Check and preview never mutate Markdown.

Each report carries exact section, marker and content line ranges plus
document/section/block hashes for the source and every eligible occurrence —
evidence used to detect stale input. Every item also links to a read-only
`change-plan` view of the canonical
source address (reverse, direct), reusing the same explainable graph
evidence `change-plan` exposes elsewhere; this is planning context, not write
authority. `maintenance` prefers a verified, generation-bound projection for
document/section resolution and falls back to direct Markdown with a visible
stderr diagnostic on any staleness, incompatibility or corruption — either
path produces byte-identical stdout for the same target.

An optional `--expect-source-hash SHA256` continuation guard compares a
previously observed source block hash with the current canonical block. A
malformed or stale value fails closed before any report is emitted; it is
evidence continuity, not write authority.

Write is a separate explicit mode requiring that source hash and a workstream
ID. It re-resolves raw UTF-8 files, replaces only managed marker interiors for
drifted `current` occurrences, preserves unrelated bytes and line endings, and
submits all files as one bounded journal transaction. The canonical source is
held as a read guard across admission, apply and validation. The rebuilt
catalog, metadata, section and graph contracts must validate; otherwise the
journal restores all touched files. `maintenance-recover` verifies immutable
generation evidence and refuses recovery over newer source. Successful write
and recovery rebuild the disposable projection; a refresh failure is visible
and falls back to direct Markdown without weakening Markdown authority.

## Product sequence

1. Configuration contract, bootstrap and diagnostics.
2. Markdown catalog and hierarchical reachability validation.
3. Stable metadata, addressable sections and dependency graphs.
4. Working context, impact, adoption and sharded-projection vertical slice.
5. Mature migration workflow (`migrate`, `readiness`) for legacy path
   relations, plus `finish` handoff and privacy-safe adopter report drafts.
6. Provider-neutral client contract and generated agent instructions.
7. MCP adapter (an initial read-only stdio adapter ships as
   `docsystem.mcp_server`) and additional client integrations.
8. Local workspace source selection for independently owned profiles, before
   any atomic cross-source federation design.
9. Managed maintenance preview followed by guarded, journaled bounded write
   and conflict-safe explicit recovery.
10. Agent-declared context coverage feedback that records body-free evidence
    only when progressive expansion exposes a material reproducible gap.
11. Project-authored purpose context views with visible relation/depth
    omissions and unrestricted on-demand expansion.
