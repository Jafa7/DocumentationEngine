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
```

`init` creates a project-local `.docsystem.toml` and the configured
documentation root. It does not create empty documentation hierarchies.

## Status

The first milestone establishes configuration, invariants and project
bootstrapping. Markdown parsing, sharded projections and Codex integration will
be extracted in subsequent milestones.

