# Delivery traceability

Delivery traceability is an optional, project-authored reverse map from an
exact source contract to the roadmap or other delivery document that owns its
implementation. It answers two bounded questions without reading document
bodies:

- which delivery document claims `DOC-ID#canonical-anchor`;
- where that delivery document records its completion evidence.

This is not inferred from prose, Markdown links or similarity. Generated
output is disposable inspection data; Markdown metadata remains authoritative.

## Configuration

The policy binds one additional metadata field to document profiles and a
semantic evidence role:

```toml
[profiles.roadmap]
document_types = ["roadmap"]
required_roles = ["completion"]

[profiles.roadmap.roles]
completion = ["completion-evidence", "completion"]

[traceability]
metadata_field = "delivers"
document_types = ["roadmap"]
evidence_role = "completion"
terminal_statuses = ["completed"]
```

Every configured document type must belong to a profile that defines the
chosen evidence role. The metadata field must be an additional field rather
than `id`, `revision`, `type`, `status` or a semantic relation field.

A delivery document claims exact, canonical section addresses:

```yaml
---
id: RM-021
revision: 1
type: roadmap
status: active
delivers:
  - DOC-019#delivery-traceability
---
```

Document-only IDs are rejected: the contract is deliberately section-sized.
The target ID and anchor must exist in the current catalog. A delivery document
cannot claim one of its own sections, and duplicate claims inside one metadata
list are invalid.

The configured evidence role must resolve to exactly one canonical anchor in
the delivery document. A status listed in `terminal_statuses` produces a
`delivered` disposition; every other status produces `active`. This classifies
the mapping but does not change lifecycle state or prove that the implementation
is correct by itself.

## Inspection and validation

```bash
docsystem delivery-map .
docsystem delivery-map . --json
docsystem delivery-map . --contract DOC-019#profiles --json
```

Output is deterministic and body-free. Each mapping contains the source
address, owner ID/path/status, exact evidence address and disposition. Multiple
delivery documents may claim the same source address; such overlaps are shown
explicitly instead of being silently selected or treated as automatic write
authority.

Configured delivery documents that omit the metadata field are listed as
`untracked_documents`, not rejected. This supports gradual adoption. Malformed
authored mappings and missing or ambiguous evidence roles make the report
invalid and exit `1`. They also fail ordinary `validate` and `doctor`.

`--contract ID#anchor` is repeatable and provides task-sized inspection. The
requested addresses are normalized into deterministic order, output mappings
are limited to those addresses, and unrelated `untracked_documents` are
omitted. A valid requested contract with no configured mapping is returned in
`unowned_contracts`; this is evidence that no owner was found, not permission
to infer one. Document-only, duplicate, unknown-document and unknown-anchor
requests fail with empty stdout and a precise stderr diagnostic.

Targeted mode still validates the complete authored traceability inventory. An
unrelated malformed claim can conceal ownership, so it blocks a completeness
claim instead of being silently ignored. Full mode remains available for
inventory and adoption work.

For planning a change, `docsystem change-plan ID#anchor PROJECT --with-delivery
--json` embeds the same targeted evidence as a separate
review-only layer. Owner and evidence addresses never become mandatory reads,
graph relations or write targets. Use standalone `delivery-map` when no graph
change plan is needed.

An absent or empty `[traceability]` table preserves existing behavior. The
command never edits Markdown, resolves implementation state from source code,
or authorizes a mechanical change. A later automation layer may use this map
to prepare a change plan, but authored policy and explicit review must define
any write authority.

The read-only MCP tool `delivery_map` exposes the same JSON contract through
its optional `contracts` list.
