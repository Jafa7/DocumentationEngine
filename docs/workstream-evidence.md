# Bounded workstream evidence

Documentation Engine can validate the lifecycle and completion claim of a
long-running workstream without becoming the system that executes it. An AI
agent, CI job or provider-neutral orchestrator performs the work and writes a
small JSON record. Documentation Engine checks that record against versioned
criteria authored by the project.

This is an opt-in contract. Existing `finish` behavior is unchanged unless
`--workstream-record` is supplied.

For a pre-execution gate, first validate scope, risk and local permissions with
[bounded execution admission](execution-admission.md). Admission does not
predict completion; this document validates the evidence produced afterward.

## Project-authored criteria

Declare one or more versions in `.docsystem.toml`:

```toml
[[workstreams.criteria]]
id = "verified-delivery"
revision = 1
required_sections = ["mandate", "boundaries", "return-protocol", "review-gate"]
required_evidence = ["changes", "checks", "review", "omissions", "risks", "returns"]
max_attempts = 3
safe_fallback = "blocked"
```

The exact reference is `verified-delivery@1`. A later policy change adds
revision 2 instead of changing the meaning of records that cited revision 1.
Criterion IDs are lowercase kebab-case. Section values use canonical Markdown
anchors. Supported evidence fields are:

- `changes`: stable document or section addresses changed by the workstream;
- `checks`: named checks, status and a compact evidence pointer;
- `review`: review status, reviewer identity, evidence pointer and whether the
  review was independent;
- `omissions`: explicit work intentionally not performed;
- `risks`: residual risks, including an explicit empty list;
- `returns`: stable parent document or section addresses receiving the result.

`max_attempts` is between 1 and 20. The only safe fallback is `blocked`:
exhausting attempts must never turn into an implicit success.

Inspect normalized policy without reading prose:

```bash
docsystem criteria PROJECT
docsystem criteria PROJECT --json
```

## Bounded record

The record schema is JSON so an agent or orchestration runtime can update it
mechanically. It is evidence, not an alternate documentation source. Keep
semantic intent and boundaries in the Markdown workstream mandate.

See [`examples/workstream-record.json`](../examples/workstream-record.json).
The top-level fields are:

- `schema_version`: currently `1`;
- `workstream_id`: the stable ID of a catalog document whose type is
  `workstream`;
- `criterion`: exact `id@revision`;
- `history`: ordered lifecycle states with attempt number and a compact
  evidence pointer;
- `findings`: correction provenance;
- `evidence`: only the bounded fields enabled by the criterion.

The supported lifecycle is:

```text
mandated -> planned -> implementing -> validating -> reviewing
reviewing -> accepted -> finishing -> completed
reviewing/validating/accepted/finishing -> correcting -> validating
```

`blocked`, `cancelled` and `failed` are terminal in one record. Resuming a
blocked workstream should create a new record/lineage rather than rewriting
the old evidence. A `correcting -> validating` transition increments the
attempt. Every correction attempt requires at least one finding, and every
finding in a completed record must name the later attempt that resolved it.

History entries should be compact pointers such as a stable address, check
run, review artifact or commit. Do not copy full logs or chat transcripts into
every state. A record is limited to 256 KiB, individual strings to 4,096
characters and lists to 1,000 items. Store larger evidence elsewhere and keep
only its stable pointer in the record.

## Validate and finish

Both commands are read-only:

```bash
docsystem workstream WS-001 PROJECT --record workstream-record.json
docsystem workstream WS-001 PROJECT --record workstream-record.json --json

docsystem finish WS-001 PROJECT --workstream-record workstream-record.json
docsystem finish WS-001 PROJECT --workstream-record workstream-record.json --json
```

`workstream` can inspect a valid in-progress record and reports
`ready_to_finish: false`. `finish --workstream-record` is stricter: it emits no
handoff unless the final state is `completed`, required mandate sections exist,
all required evidence is present, checks pass, required independent review is
accepted, all findings are resolved, addresses resolve and the attempt limit
was respected.

`independent: true` is a signed-off assertion in the record, not proof of
reviewer identity. The core validates that the criterion received an accepted
independent-review declaration and preserves its reviewer/evidence fields. The
calling agent, CI system or orchestrator must authenticate that reviewer and
prevent an implementation role from falsely self-attesting independence.

Without `--workstream-record`, `finish` retains its existing lightweight
context handoff contract. Projects can therefore adopt strict evidence only
for milestones where the additional assurance is worth its documentation
cost.

An [immutable execution handoff](execution-handoff.md) packet hash may be
recorded as provenance for what was authorized and verified before execution.
It is not completion evidence: checks, review, corrections and returned
artifacts must still be recorded here after the work.

## Responsibility boundary

Documentation Engine does not choose product semantics, run workers, approve
its own changes, commit, push or release. It validates a portable record
against authored policy. The agent or orchestrator remains responsible for
execution; the project owner remains responsible for decisions that the
mandate reserves for a human.
