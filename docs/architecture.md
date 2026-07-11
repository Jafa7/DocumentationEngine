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
- bootstrap, diagnostics, adoption readiness reporting and migration tooling.

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
- projection retention;
- legacy path-relation migration and historical snapshot document types;
- provider adapters.

## Scalable projection

The target projection is sharded and generation-based:

```text
.docsystem/cache/
├── current.json
└── generations/<content-hash>/
    ├── manifest.json
    ├── areas/<logical-path>/_index.json
    ├── documents/<namespace>/<bucket>/<ID>.json
    └── reverse/<namespace>/<bucket>/<ID>.json
```

A stable ID maps to a document shard without a global routing table.

Read commands (`read`, `context`, `impact`) serve from the projection when it
is verified current. Verification enumerates the catalog's source paths
without parsing them, re-reads every included source and compares its sha256
with the generation manifest, and validates every document and reverse shard
up front. It also rejects the generation when the active configuration
fingerprint no longer matches the one recorded at build time — a normalized
digest of the documentation root identity, areas, identifiers, catalog
exclusions, `navigation.extend_through`, `relations.legacy_paths`,
`relations.snapshot_types`, the projection format and the schema version — so a
read-time policy change forces a rebuild instead of serving differently-shaped
output. Finally it reconstructs the canonical projection payload from the
loaded shards and compares its hash with the selected generation, so semantic
shard tampering (a dropped dependency, an altered revision) that leaves the
Markdown source hashes untouched is detected before any output is produced.
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
Readers validate schema, generation identity, the configuration fingerprint,
source hashes, required shards and the reconstructed generation hash; invalid,
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
document and reverse shards, active configuration fingerprint and reconstructed
generation hash. Both modes read the same shared view shape on either serving
path, so a delta served from a fresh projection's fast path is
byte-identical to the same delta reconstructed from direct Markdown, even when
the referenced generation is an older retained one rather than the current
pointer. `--since` and `--assume-known` are mutually exclusive, and neither
combines with `--outline`.

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

## Product sequence

1. Configuration contract, bootstrap and diagnostics.
2. Markdown catalog and hierarchical reachability validation.
3. Stable metadata, addressable sections and dependency graphs.
4. Working context, impact, adoption and sharded-projection vertical slice.
5. Mature migration workflow (`migrate`, `readiness`) for legacy path
   relations, plus `finish` handoff and privacy-safe adopter report drafts.
6. Thin Codex integration and generated agent instructions.
7. MCP adapter (an initial read-only stdio adapter ships as
   `docsystem.mcp_server`) and additional client integrations.
