# Graph health

`docsystem graph-health PROJECT [--json]` is a read-only whole-catalog view for
planning and diagnosing documentation structure. It measures the graph that
Documentation Engine already builds; it does not create inferred semantic
relations, edit Markdown or decide that a document should change.

## Factual inventory

Every successful report includes:

- document and addressable-section counts;
- edge counts by authority (`authored`, `observed`, `generated`) and relation;
- unresolved boundaries by category;
- weakly connected component sizes and orphan document IDs;
- stale freshness pins and policy-classified historical pins;
- missing metadata counts for fields selected by project policy.

The text form is deterministic Markdown. `--json` emits one object with
`schema_version`, `metrics`, and ordered `signals`. A verified projection is
used when current; otherwise the command prints one fallback warning to stderr
and derives the same facts from Markdown. The two paths produce byte-identical
stdout. If metadata, identity or graph errors prevent a complete inventory,
the command exits `1`, prints diagnostics to stderr and writes no partial
stdout.

## Advisory policy

The inventory is always available. Smell thresholds are disabled unless the
project defines them:

```toml
[graph_health]
hub_in_degree = 12
hub_out_degree = 12
boundary_count = 5
stale_pin_count = 3
max_weak_components = 1
required_metadata = ["type", "status"]
report_orphans = true
```

All numeric values are positive integers. A degree or concentration signal is
emitted when a document's value is greater than or equal to its threshold;
`weak-components` is emitted when the component count is greater than
`max_weak_components`. `required_metadata` accepts only `type` and `status`.
Missing-anchor Markdown links are reported as `dead-reference` even without a
threshold because their target is objectively absent. Other boundaries remain
inventory until project policy gives them a threshold.

Signals are observations for review, never failures and never write authority.
A hub can be an intentional index, an orphan can be a valid standalone note,
and historical snapshot pins are not stale freshness debt. An agent should use
the report for broad planning or diagnosis, inspect the cited documents, and
make a semantic decision before proposing a change. It should not run this
whole-catalog command as mandatory overhead for every small edit.
