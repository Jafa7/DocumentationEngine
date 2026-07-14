# Immutable execution handoff

After an A0–A2 intent is admitted, Documentation Engine can produce a compact,
immutable packet for an external agent or orchestrator. The packet freezes the
admission decision, workstream mandate, exact target hashes/ranges and
explainable graph impact without embedding authored document bodies.

This is a transport contract, not an executor. Documentation Engine does not
dispatch workers, grant filesystem permissions, apply changes or perform Git
and release operations.

## Build a packet

```bash
docsystem execution-handoff WS-001 PROJECT \
  --admission admission.json \
  --json > execution-packet.json
```

Generation re-evaluates the admission request against current project policy.
A blocked intent, invalid catalog, incomplete/terminal mandate or unknown
target fails with exit `1`, diagnostics on stderr and empty stdout.

The schema-version-1 packet contains:

- the normalized admitted intent, hashes, required autonomy and catalog guard;
- a mandate snapshot with revision, status, relative path, document hash and
  hashes/ranges for every policy-required section;
- one exact target snapshot per stable document/section address;
- normalized local source paths with expected pre-edit hashes or absence;
- direct, transitive, forward and reverse change-plan evidence;
- a deduplicated `read`/`review` context manifest;
- a SHA-256 integrity seal over the complete packet except the seal field.

For a section target, the packet includes both its exact section plan and the
owning document's plan. This preserves section precision without losing
document-level semantic dependencies or reverse consumers.

`content_embedded` is always `false`. The executor retains complete access to
original Markdown and expands the listed stable addresses on demand with
`read` or `context`. The packet saves tokens by transporting identity,
coverage and integrity evidence, not by summarizing or truncating required
content.

Graph plan dispositions remain planning evidence. `read` means the address is
part of the initial semantic read set; `review` means it is visible impact or
navigation evidence. Neither disposition grants write permission. Boundaries
and per-layer completeness remain explicit in every target plan.

Packet output is bounded to 2 MiB. If a real scope exceeds that bound, narrow
the workstream or split it through authored policy; the engine does not
silently omit targets or graph evidence.

## Verify before execution

```bash
docsystem execution-handoff WS-001 PROJECT \
  --admission admission.json \
  --verify execution-packet.json \
  --json
```

Verification first checks the packet's self-contained integrity hash, then
rebuilds the entire packet from current Markdown and the supplied admission
request. Success returns `current: true`. Any changed source bytes, revisions,
sections, graph edges, boundaries, mandate, policy or admission evidence fails
closed as stale. Verification never repairs or refreshes the supplied packet;
generate and review a new packet instead.

An external runtime should verify immediately before acting and keep the
packet hash with its execution evidence. It must still authenticate the
authorization assertions, enforce the admitted action/target scope and stop
if its own permissions or task prompt are broader than the packet.

## Privacy boundary

Packets contain stable IDs, relative paths, titles, hashes, graph relations,
assumptions and authorization evidence pointers. They omit authored bodies but
may still reveal private project structure. Keep a private-project packet in
private runtime state and sanitize it before using it in a public report.

After execution, the packet does not prove success. Deterministic checks,
independent review, corrective lineage and return evidence belong in the
[bounded workstream evidence](workstream-evidence.md) record and `finish`
handoff.

## Validate returned source evidence

An executor or authoritative host writes a bounded result:

```json
{
  "schema_version": 1,
  "workstream_id": "WS-001",
  "packet_sha256": "...",
  "changed_files": [
    {"path": "src/example.py", "sha256": "..."}
  ]
}
```

Use `sha256: null` for a deleted admitted file. Then validate it read-only:

```bash
docsystem execution-result WS-001 PROJECT \
  --packet execution-packet.json --result execution-result.json --json
```

The command rejects unadmitted paths, omitted changes within the admitted
scope, unchanged paths declared as changed, wrong after-hashes and packet/ID
mismatches. It can verify only the structured inventory it receives; it does
not monitor filesystem writes or discover changes outside the declared scope.
The host or orchestration runtime must produce the changed-file inventory from
an authoritative diff/audit source. Worker prose is not sufficient evidence.
