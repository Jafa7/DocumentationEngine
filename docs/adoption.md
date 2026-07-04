# Existing-project adoption

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
document = "PDOC"
decision = "PDEC"
roadmap = "PRM"

[catalog]
exclude = ["templates/*-template.md"]

[navigation]
extend_through = ["резюме", "содержание", "summary", "contents"]

[relations]
legacy_paths = "resolve-with-warning"
snapshot_types = ["review", "experiment"]

[projection]
format = "sharded-json"
keep_generations = 2
```

Area `.` owns root-level Markdown and is the fallback below the documentation
root; more specific areas win. Exclusions are evaluated before parsing, so
templates cannot create metadata or duplicate-ID errors.

Run the adoption sequence without editing source files:

```bash
docsystem catalog . --explain
docsystem validate .
docsystem validate . --verbose-adoption
docsystem migration-report .
docsystem context PDOC-001 . --depth 1
docsystem impact PDOC-001 .
docsystem index . --write
docsystem index .
docsystem changes .
```

The migration report uses tab-separated records:

```text
resolved	SOURCE_ID	relation	legacy/path.md	TARGET_ID
boundary	SOURCE_ID	relation	value	reason
```

`resolved` rows are safe candidates for a reviewed path-to-ID migration.
`boundary` rows require a human decision: external provenance and resources
must not be converted into invented document IDs. This milestone intentionally
does not bulk-edit Markdown.

Default `validate` and `doctor` output compacts these expected adoption rows
into counts so operational diagnostics remain small. Both accept
`--verbose-adoption` when the full warning context is needed. Stale pins and
other non-adoption warnings are always printed individually.

After an index is written, `read`, `context` and `impact` validate it before
serving the same Markdown semantics. An absent, stale, corrupt or incompatible
projection produces a stderr warning and falls back to direct source reads.
`changes` compares the current Markdown-derived state with the selected
generation.

Project-specific registry synchronization, `finish` orchestration, private
history/backup, and provider adapters remain outside this adoption profile.
