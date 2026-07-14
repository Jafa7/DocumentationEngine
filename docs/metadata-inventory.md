# Metadata and graph inventory

`docsystem metadata-inventory PROJECT [--json]` is a read-only, body-free view
of the metadata actually authored across a valid documentation catalog. It
helps an agent discover the project's vocabulary and graph shape before it
reads document content or proposes a schema change.

## Default privacy boundary

The default report contains:

- every core, relation and additional metadata field name;
- document coverage, missing count and observed YAML value types per field;
- the document types on which each field occurs and whether its observed type
  is inconsistent;
- each document's stable ID, revision, catalog role, relative path, lifecycle
  fields, additional-field names, document-level incoming/outgoing edge counts
  and unresolved-boundary count.

It never includes Markdown bodies or additional metadata values. The inventory
is observed evidence, not a schema: field frequency does not make a field
required, and an observed edge never grants permission to edit its endpoints.

Use an explicit field drill-down when its values are necessary:

```bash
docsystem metadata-inventory . --field owner --values --json
```

`--values` requires `--field`; it cannot dump every additional value in one
command. The selected values remain local command output and can contain
private metadata, so agents must not paste them into public reports without
sanitization.

## Determinism and failures

Text output is stable tab-separated rows. JSON output has `schema_version: 1`
and ordered `fields` and `documents`; an optional `values` array appears only
after explicit drill-down. Field types use stable names such as `string`,
`integer`, `number`, `boolean`, `date`, `datetime`, `sequence`, `mapping` and
`null`.

The command validates the catalog and graph before emitting data. Duplicate or
missing IDs, unknown relation targets, ambiguous metadata and other blocking
catalog errors produce diagnostics on stderr, exit `1`, and no partial stdout.
The command does not write Markdown, configuration, projections or local
state.

`graph-health` remains the broader structural diagnostic: it aggregates edge
authority/relation, components, orphans, pins and configured advisory signals.
`metadata-inventory` instead answers which metadata vocabulary exists and how
individual documents participate in the graph.
