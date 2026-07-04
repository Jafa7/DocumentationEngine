# Architecture

## Product boundary

Documentation Engine owns deterministic documentation mechanics:

- project configuration and logical area mapping;
- Markdown metadata and stable IDs;
- hierarchical navigation validation;
- dependency and reverse-dependency graphs;
- bounded context packets;
- impact and changed-section analysis;
- versioned, sharded machine projections;
- bootstrap, diagnostics and migration tooling.

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

A stable ID maps to a document shard without a global routing table. Consumers
load only the target, required dependencies and reverse records needed for the
operation.

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

## Planned milestones

1. Configuration contract, bootstrap and diagnostics.
2. Markdown catalog and hierarchical reachability validation.
3. Stable metadata, addressable sections and dependency graphs.
4. Sharded projection, context and impact commands.
5. Migration/adoption workflow for existing projects.
6. Thin Codex integration and generated agent instructions.
7. MCP adapter and additional client integrations.
