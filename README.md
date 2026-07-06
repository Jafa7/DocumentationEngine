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

## Development setup vs. consumer install

Development work in this checkout runs the CLI as a module against `src/`,
either via `python -m docsystem ...` in an editable install or with
`PYTHONPATH=src`. Downstream consumers (such as Paradigmarium) instead depend
on `docsystem` as an ordinary installed package and invoke the `docsystem`
console script produced by the build, with no `PYTHONPATH` and no direct
import of this repository's sources.

`uv.lock` pins the resolved dependency graph for this checkout.
`uv lock --check` verifies the lockfile matches `pyproject.toml`.

`scripts/installed_cli_smoke.sh` is the reproducible check for the consumer
path: it builds a wheel from the current checkout, installs it into an
isolated venv, and runs the installed `docsystem` entry point against a fresh
fixture project from an unrelated working directory. It requires no API
credentials, does not modify this repository, and cleans up all temporary
files on exit.

```bash
./scripts/installed_cli_smoke.sh
```

## Initial CLI

```bash
python -m docsystem init .
python -m docsystem doctor .
python -m docsystem show-config .
python -m docsystem catalog .
python -m docsystem catalog . --explain
python -m docsystem validate .
python -m docsystem validate . --verbose-adoption
python -m docsystem read DOC-001 .
python -m docsystem read DOC-001 . --list
python -m docsystem read DOC-001 . --anchor purpose
python -m docsystem dependencies DOC-001 .
python -m docsystem dependencies DOC-001 . --reverse
python -m docsystem context DOC-001 . --depth 1
python -m docsystem impact DOC-001 .
python -m docsystem migration-report .
python -m docsystem readiness .
python -m docsystem migrate .
python -m docsystem migrate . --apply
python -m docsystem index . --write
python -m docsystem changes .
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

Existing projects may opt into a migration bridge for relative path relations:

```toml
[relations]
legacy_paths = "resolve-with-warning"
snapshot_types = ["review", "experiment"]
```

Strict stable-ID relations remain the default. In the compatibility mode,
resolvable paths become canonical graph edges and emit migration warnings.
External URLs, resources and paths outside the catalog are never document
relations, so they remain explicit, non-blocking boundaries in both `strict`
and `resolve-with-warning` mode. A relative path that *does* resolve to a
cataloged document is a real document relation: in `strict` mode it is a
blocking error until it is migrated to a stable ID or the project opts into
`resolve-with-warning`. `migration-report` reports both resolved mappings and
boundaries as a deterministic dry-run, independent of the current
`relations.legacy_paths` mode, without editing Markdown.

By default, `validate` and `doctor` summarize expected resolved mappings and
resource boundaries by count while printing stale pins and other warnings
individually. Pass `--verbose-adoption` to either command for every row-level
adoption warning. `migration-report` always remains the complete deterministic
inventory.

`readiness` is a read-only report for adopting an existing Markdown project.
It distinguishes blocking structural/configuration errors, resolvable legacy
relation migrations, explicit unresolved/resource boundaries, stale freshness
pins and projection state (absent/stale/current), and prints the single safe
next command. It never writes to Markdown, configuration or the projection
cache.

`migrate` previews, by default, every legacy relation value that
`migration-report` already classifies as unambiguously resolved. Preview is
read-only. `migrate --apply` re-validates the same plan against a scratch copy
of the documentation tree and then rewrites only the exact resolved scalar in
`derived_from`, `depends_on`, `related` or `supersedes` for each affected
document — front matter formatting, comments, unknown fields, the document
body and unresolved boundaries are left byte-for-byte untouched. Multi-file
runs are all-or-nothing: if validation or a write fails, no file is left
partially migrated. Re-running `migrate --apply` after a successful migration
reports no further changes. Once every resolvable legacy relation has been
migrated, a project whose remaining legacy values are all boundaries (URLs and
resources) can drop `relations.legacy_paths = resolve-with-warning` and use
`strict` mode without those boundaries becoming errors.

`context` emits a deterministic Markdown packet containing navigation excerpts,
semantic dependencies, explicit section selections, H2 coverage, omissions,
stale pins and unresolved boundaries. It never silently truncates to a token
budget. `impact` reports reverse metadata dependencies and distinguishes
semantic, related-navigation, freshness and configured historical-snapshot
relations.

`index --write` derives immutable content-hash generations below
`.docsystem/cache`, then atomically selects the current generation.
`index` checks freshness and `changes` reports changed documents and sections.
Read operations verify the projection and visibly fall back to direct Markdown
when it is absent, stale or incompatible. Markdown remains the only editable
truth.

See [the adoption guide](docs/adoption.md) for a complete profile and migration
sequence.

## Deliberate project-local boundaries

Registry synchronization, finish orchestration, private history/backup and
provider-specific adapters are not generalized by this vertical slice. They
remain project-local until reusable contracts are proven.
