# End-to-end workstream lifecycle evidence

Documentation Engine can validate one completed workstream across the artifacts
that previously had to be checked separately:

- the bounded admission request;
- the immutable pre-execution packet;
- the authoritative changed-file result;
- the completed lifecycle and review record.

The command is a read-only evidence gate. It does not dispatch an agent,
authenticate a permission, perform a review, change source or claim that the
implemented product semantics are correct.

The gate requires a source-scoped admission and the exact packet persisted by
the trusted host before execution. The packet seal detects alteration; because
it is an ordinary unkeyed SHA-256 rather than a signature, it does not prove
who created or retained the packet. The host must record its hash at handoff
time and compare it with the `packet_sha256` returned by `lifecycle`.

## Validate the bundle

After execution, deterministic checks and independent review have completed:

```bash
docsystem lifecycle WS-001 PROJECT \
  --admission execution-admission-request.json \
  --packet execution-packet.json \
  --result execution-result.json \
  --record workstream-record.json \
  --json
```

The command validates all existing per-artifact contracts, then proves these
cross-artifact invariants:

- every artifact names the requested stable workstream ID;
- the packet embeds the exact normalized admission request hash, criterion,
  targets, actions, risk, verification level and source scope;
- the execution result references the packet integrity hash;
- every changed file is within the packet scope, every change relative to the
  host-persisted packet baseline is declared, and every declared after-hash
  matches the current file;
- the lifecycle record is `completed`, stays within its attempt policy,
  preserves every corrective finding and resolves it in a later attempt;
- all admitted stable targets occur in the record's `changes` evidence;
- configured checks pass and accepted independent review evidence is present;
- the resulting bundle is ready for the existing strict `finish` gate.

The intended order is `admission`, packet generation and verification,
external execution, `execution-result`, deterministic checks, independent
review, completed `workstream` record, `lifecycle`, then `finish`. A host may
repeat the read-only validation commands, but must not regenerate the packet
after execution or reorder review ahead of the actual result.

Success returns body-free hashes, stable target addresses, changed relative
paths and bounded counts. It does not return document bodies, check logs or
review prose. Any malformed, stale, missing or mismatched artifact exits `1`,
writes a precise diagnostic to stderr and emits no partial stdout.

## Why the packet is not rebuilt after execution

`execution-handoff --verify` is a pre-execution drift guard. Run it immediately
before the executor starts. Once admitted files change, rebuilding the packet
would correctly report drift and would destroy the distinction between the
authorized before-state and the observed after-state.

`lifecycle` therefore verifies the saved packet's integrity and lineage, then
checks the result against the packet's original source scope and current
after-hashes. The immutable before-state remains evidence instead of being
silently refreshed.

## Output contract

JSON output has `schema_version: 1` and five top-level evidence groups:

- `admission`: policy reference and request lineage hashes;
- `execution`: packet hash, changed paths and scope completeness;
- `workstream`: completion criterion, attempts, findings and review state;
- `coverage`: admitted stable targets and their coverage state;
- `ready_to_finish` plus `authority: evidence-validation-only`.

The tab-separated text form prints the same terminal decision as compact
operator evidence: criteria and packet hash, counts for targets, changed paths,
attempts and findings, then the coverage/scope/review/finish states. Use JSON
when exact addresses, changed paths, lineage hashes or resolved-finding counts
are needed programmatically.

The output is suitable for a CI job or provider-neutral orchestration runtime.
It is not a permission token. The host remains responsible for producing the
changed-file inventory from an authoritative diff and authenticating the
declared independent reviewer.

## Relationship to `finish`

`lifecycle` proves the execution lineage. `finish --workstream-record` builds
the compact return packet with context, omissions, risks and return addresses.
Use the same completed record for both commands. A successful lifecycle check
does not publish, commit or return anything by itself.

Projects may continue using each command independently. Existing `admission`,
`execution-handoff`, `execution-result`, `workstream` and `finish` behavior is
unchanged.
