# Existing-project adoption

Complete the README's canonical [Installation](../README.md#installation)
section and the fact-gathering and backup steps in the
[setup guide](setup-guide.md) before adopting an existing tree. This document
starts at the profile and migration layer; it does not redefine installation.

This profile adopts a documentation tree without changing its Markdown first.
It keeps strict stable-ID semantics as the product default while making legacy
relative relations inspectable and migratable.

```toml
version = 1

[documentation]
root = "plan"
language = "en"

[areas]
workspace = "."
architecture = "architecture"
decisions = "decisions"
reviews = "reviews"
experiments = "experiments"

[identifiers]
document = "DOC"
decision = "DEC"
roadmap = "RM"

[catalog]
exclude = ["templates/*-template.md"]

[navigation]
extend_through = ["summary", "contents"]

[relations]
legacy_paths = "resolve-with-warning"
snapshot_types = ["review", "experiment"]
snapshot_rules = [
  { source_type = "roadmap", source_status = "completed" },
]

[projection]
format = "sharded-json"
keep_generations = 2

[context.views.map]
tier = 1
delivery = "outline"
direction = "both"
depth = 0
relations = []
layers = ["authored"]

[context.views.task]
tier = 2
delivery = "navigation"
direction = "forward"
depth = 1
relations = ["depends_on", "derived_from", "validated_against"]
layers = ["authored"]
```

Area `.` owns root-level Markdown and is the fallback below the documentation
root; more specific areas win. Exclusions are evaluated before parsing, so
templates cannot create metadata or duplicate-ID errors.

Context views are optional and must reflect the adopter's own task model. Use
the lowest suitable tier, inspect `view_omissions`, and expand on demand. They
do not replace full access or authorize an agent to ignore a filtered edge.

Snapshot rules are also project policy. Use a type-wide `snapshot_types` entry
only when every document of that type is historical; use `snapshot_rules` to
match a narrower type/status lifecycle. Do not classify active planning pins
as historical merely to remove useful freshness diagnostics.

Run the adoption sequence without editing source files:

```bash
docsystem catalog . --explain
docsystem readiness .
docsystem validate .
docsystem validate . --verbose-adoption
docsystem migration-report .
docsystem migrate .
docsystem context DOC-001 . --depth 1
docsystem context DOC-001 . --view map --json
docsystem context DOC-001 . --view task --json
docsystem impact DOC-001 .
docsystem finish DOC-001 .
docsystem report draft . --project-name "Adopter Project" --type adoption-finding --source codex
docsystem report context-gap . --project-name "Adopter Project" --type adoption-finding --source codex --reason missing_dependency --initial DOC-001#summary --expanded DOC-002#constraints --impact decision
docsystem index . --write
docsystem index .
docsystem changes .
```

`catalog --explain`, `readiness`, `migration-report`, `changes` and `context`
also accept `--json` for a deterministic, machine-readable form of the same
report; see [the AI client integration guide](client-integration.md) and
[`examples/generic-adopter/`](../examples/generic-adopter/) for a runnable
synthetic profile and a wrapper-oriented walkthrough.

The inspection commands and `migrate` preview above do not edit Markdown.
`index --write` creates only a disposable derived projection. `readiness` is
the single compact entry point: it distinguishes blocking structural or
configuration errors, resolvable legacy relation migrations, explicit
unresolved/resource boundaries, stale freshness pins and projection state
(absent/stale/current), and prints the one safe next command — it never writes
Markdown, configuration or the projection cache itself.

The migration report uses tab-separated records:

```text
resolved	SOURCE_ID	relation	legacy/path.md	TARGET_ID
boundary	SOURCE_ID	relation	value	reason
```

`resolved` rows are safe candidates for a reviewed path-to-ID migration.
`boundary` rows require a human decision: external provenance and resources
must not be converted into invented document IDs. `migration-report` reports
both regardless of the current `relations.legacy_paths` mode, so it is useful
even before opting into `resolve-with-warning`.

## Applying the migration

`docsystem migrate .` previews, without writing, the exact rewrite for every
`resolved` row above:

```text
would-migrate	SOURCE_ID	relation	legacy/path.md	TARGET_ID	path/to/document.md
```

Only `docsystem migrate . --apply` writes. It re-validates the same plan
against a scratch copy of the documentation tree first, then rewrites just the
resolved scalar inside `derived_from`, `depends_on`, `related` or
`supersedes` for each affected document — front matter formatting, comments,
unknown fields, the document body and every `boundary` value are left
byte-for-byte untouched. A multi-file run is all-or-nothing: if validation or
any write fails, no file is left partially migrated, and re-running
`migrate --apply` after a successful run reports
`No resolvable legacy relation migrations found.`

Once every resolvable legacy relation has been migrated and the remaining
`boundary` rows are genuinely external URLs or resources — never document
relations — the project can drop `relations.legacy_paths =
"resolve-with-warning"` and use `strict` mode: boundaries never require the
compatibility mode, only unmigrated document relations do.

Default `validate` and `doctor` output compacts these expected adoption rows
into counts so operational diagnostics remain small. Both accept
`--verbose-adoption` when the full warning context is needed. Stale pins and
other non-adoption warnings are always printed individually.

After an index is written, `read`, `context` and `impact` serve from it: the
projection is verified against the current sources (every included file is
re-hashed against the generation manifest), against the active configuration
fingerprint (so a semantic config change such as `relations.legacy_paths` or
`navigation.extend_through` invalidates it), and against a manifest-root
generation hash that binds every shard hash (so semantic shard tampering is caught). When
current, shards replace Markdown parsing while producing byte-identical output.
An absent, stale, corrupt or incompatible projection produces a stderr warning
and falls back to direct source reads. `changes` compares the current
Markdown-derived state with the selected generation.

## Local-only state and backups

An adopting project may intentionally keep its documentation root,
`.docsystem.toml` and `.docsystem/` cache out of git. That is supported, but
it means git cannot recover those files after an accidental recursive copy,
delete or migration mistake. Define a local backup command before letting an
agent perform broad or mutating work on ignored documentation. The reusable
contract is described in [local state safety](local-state-safety.md).

When several independent public or private profiles live under one local
documentation directory, register them with
[workspace source selection](workspace-sources.md). The registry selects one
ordinary profile by name; it does not merge catalogs or authorize deleting an
original `plan/` after a copy-only migration.

Project-specific registry synchronization, `finish` orchestration, private
history/backup, and provider adapters remain outside this adoption profile.

When an adopted project splits work into a new chat, module, repository or
long-running idea, use the [workstream branching pattern](workstream-branching.md)
to preserve inherited context and define how the result returns to the parent
project.

If adoption exposes a DocumentationEngine runtime problem, compatibility gap,
core defect or reusable documentation pattern, file it through the
[adopter reporting](adopter-reporting.md) policy. Reports should use compact
diagnostics, sanitized profile/config excerpts and stable IDs or anchors rather
than private document bodies. Start with `docsystem report draft` for a general
finding. If additional reading materially changes the work because task-sized
delivery missed a reproducible relation, section or profile rule, use
`docsystem report context-gap`; normal progressive reading is not an issue.
Both commands are read-only. Complete the expected, actual and requested-action
fields before creating the GitHub issue.
