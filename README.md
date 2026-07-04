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
python -m docsystem catalog . --explain
python -m docsystem validate .
python -m docsystem read DOC-001 .
python -m docsystem read DOC-001 . --list
python -m docsystem read DOC-001 . --anchor purpose
python -m docsystem dependencies DOC-001 .
python -m docsystem dependencies DOC-001 . --reverse
```

`init` creates a project-local `.docsystem.toml` and the configured
documentation root. It does not create empty documentation hierarchies.

`catalog` lists Markdown source files under paths mapped by logical roles in
`[areas]`. `catalog --explain` classifies every Markdown file as included,
excluded or unmapped. Unmapped Markdown is a validation error rather than a
silent omission.

Catalog exclusions are optional, ordered POSIX globs relative to the
documentation root:

```toml
[catalog]
exclude = ["templates/*-template.md"]
```

The first matching pattern is reported as the exclusion reason. An area mapped
to `.` owns root documents and acts as a fallback when a more specific area
does not match. `validate` requires each included document to be linked from
the nearest `README.md` or `index.md`; nested indexes must themselves be linked
from the nearest parent index. `doctor` includes membership, navigation and
metadata validation.

Every cataloged Markdown document starts with YAML front matter containing a
stable `id` and positive `revision`. Semantic relations use stable IDs:
`derived_from`, `depends_on`, `related` and `supersedes` contain ID lists;
`validated_against` contains `ID@revision` freshness pins. Unknown fields are
preserved for project-specific policy. Duplicate YAML mapping keys are invalid
at every nesting level.

`read` resolves a whole document, navigation prefix or ATX section by stable
ID. `read --list` emits `anchor`, `Hn`, `start:end` and `title` as tab-separated
fields in document order.

A heading may declare a stable canonical anchor on the immediately preceding
line:

```html
<a id="stable-section"></a>
## Section title
```

`name` is also accepted, as are single quotes. The standalone tag may contain
only the `id` or `name` attribute. Anchor values start with a Unicode
alphanumeric character and then use Unicode alphanumerics or `-_.:`. The value
is preserved exactly. Malformed, orphaned, multiple, duplicate or colliding
anchors are errors rather than silently repaired.

Navigation may extend the default prefix through the furthest matching H2:

```toml
[navigation]
extend_through = ["summary", "contents"]
```

If no configured anchor exists in a document, the original prefix before the
first H2 is returned. A configured anchor resolving to another heading level
is an error.

`dependencies` reports deterministic forward or reverse semantic edges.
It fails without partial stdout when metadata errors make the requested graph
incomplete; stale revision warnings remain non-blocking.
Stale revision pins are visible warnings rather than blocking errors because
historical snapshot policy is not part of the initial metadata contract.

## Status

The first milestones establish configuration, project bootstrapping,
provider-neutral Markdown discovery, hierarchical navigation, stable metadata,
addressable sections and dependency graphs. Sharded projections, bounded
context packets and provider integrations remain subsequent milestones.
