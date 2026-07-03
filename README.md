# Documentation Engine

Documentation Engine is a provider-neutral toolkit for maintaining structured
Markdown knowledge that remains usable by humans and AI clients as a project
grows.

The project is in an early extraction stage. Its first integration fixture is
Paradigmarium.

## Principles

- Markdown is the editable source of truth.
- Stable IDs survive file moves and title changes.
- Generated indexes are deterministic projections, never a second truth.
- Human navigation and machine retrieval use the same dependency model.
- Context selection exposes omissions instead of silently truncating meaning.
- Mechanical maintenance is automated; semantic decisions remain reviewable.
- AI integrations are adapters around a provider-neutral core.

## Initial CLI

```bash
python -m docsystem init .
python -m docsystem doctor .
python -m docsystem show-config .
python -m docsystem catalog .
python -m docsystem validate .
python -m docsystem read DOC-001 .
python -m docsystem read DOC-001 . --anchor purpose
python -m docsystem dependencies DOC-001 .
python -m docsystem dependencies DOC-001 . --reverse
```

`init` creates a project-local `.docsystem.toml` and the configured
documentation root. It does not create empty documentation hierarchies.

`catalog` lists Markdown source files under paths mapped by logical roles in
`[areas]`. `validate` requires each document to be linked from the nearest
`README.md` or `index.md`; nested indexes must themselves be linked from the
nearest parent index. `doctor` includes navigation and metadata validation.

Every cataloged Markdown document starts with YAML front matter containing a
stable `id` and positive `revision`. Semantic relations use stable IDs:
`derived_from`, `depends_on`, `related` and `supersedes` contain ID lists;
`validated_against` contains `ID@revision` freshness pins. Unknown fields are
preserved for project-specific policy.

`read` resolves a whole document, navigation prefix or ATX section by stable
ID. `dependencies` reports deterministic forward or reverse semantic edges.
Stale revision pins are visible warnings rather than blocking errors because
historical snapshot policy is not part of the initial metadata contract.

## Status

The first milestones establish configuration, project bootstrapping,
provider-neutral Markdown discovery, hierarchical navigation, stable metadata,
addressable sections and dependency graphs. Sharded projections, bounded
context packets and provider integrations remain subsequent milestones.
