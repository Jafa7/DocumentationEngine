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

## Planned milestones

1. Configuration contract, bootstrap and diagnostics.
2. Markdown catalog and hierarchical reachability validation.
3. Sharded projection, context and impact commands.
4. Migration/adoption workflow for existing projects.
5. Thin Codex integration and generated agent instructions.
6. MCP adapter and additional client integrations.

