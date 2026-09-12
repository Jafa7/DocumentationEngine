# Interrupted journal recovery design

Date: 2026-09-12
Status: architecture accepted; implemented and verified in the current W06 candidate

## Problem and boundary

The journal already rolls a bounded write back when the creating Python
process handles an exception, and `maintenance-recover` can undo a completed,
verified generation when every edited path still contains the recorded after
bytes. Neither path handles abrupt process termination after one or more
source replacements but before completion evidence is published.

W06 adds a separate interrupted-attempt recovery protocol. It does not weaken
completed-generation verification and does not claim multi-file atomic
visibility or power-loss durability.

The guarantees remain distinct:

- **exception rollback:** the creating process catches a failure and restores
  the admitted before state;
- **process-interruption recovery:** a later explicit command verifies a
  prepared attempt and monotonically restores its known source state;
- **atomic visibility:** only each individual file replacement is atomic;
- **power-loss persistence:** not claimed without filesystem-level evidence.

## Journal schema transition

New generations use journal schema 2. Their `manifest.json` is an immutable
prepared record with status `prepared`. It is written only after all before and
after byte copies and patch evidence exist and before any source replacement.
The manifest binds:

- workstream and generation identity;
- creation time and optional selected-source authority;
- ordered operations and provider-root-relative paths;
- before and after hashes;
- allowed ranges and read guards;
- semantic and mechanical patch hashes.

`verification.json` becomes the separate terminal record. It binds the
immutable manifest hash and declares `applied` or `rolled-back`. Terminal
records are fully written and flushed under a temporary name, then published
with one atomic same-directory replace. A staging remnant is not terminal
evidence. Existing
schema-1 completed generations remain readable and recoverable through the
existing completed-generation path; they are not rewritten.

Terminal state has three fail-closed classifications:

- `absent`: no published `verification.json`; the prepared generation is
  eligible for interrupted recovery;
- `valid`: the completed-generation path owns the generation and the
  interrupted route rejects it;
- `present-invalid`: malformed, mismatched or corrupt terminal evidence; both
  routes refuse it as an integrity failure rather than downgrading it to an
  interruption.

A schema-2 manifest with an absent terminal is an interrupted attempt,
provided the manifest, copies and patch evidence verify. A missing or corrupt
prepared manifest is not recoverable because no trusted mutation inventory
exists. Since source replacement begins only after preparation, an incomplete
preparation is expected to leave source unchanged; the engine reports it as
orphan/incomplete evidence and does not guess.

## Separate command and state machine

The proposed command is:

```bash
docsystem maintenance-recover-interrupted GENERATION PROJECT
```

The existing `maintenance-recover` remains limited to completed generations.
The new command rejects a generation with valid terminal evidence and points
to the completed recovery command when applicable.

For every admitted path, recovery classifies current source as:

- `before`: exact recorded before hash, or absence for a `create` operation;
- `after`: exact recorded after hash;
- `unknown`: any other bytes, unexpected absence or incompatible object.

The source state is then one of:

- all-before: record the interrupted attempt as recovered without source
  mutation;
- all-after or mixed before/after: restore only after-state paths to before;
- any-unknown: refuse with the exact paths and make no source change.

The recovery creates a separate immutable record below
`recoveries/<generation>/<timestamp>/`. Its prepared manifest binds the source
generation manifest hash, requested outcome `before`, initial per-path states,
and selected-source authority. Recovery progresses monotonically toward the
before state. If the process is interrupted again, rerunning the command
detects and verifies the one existing prepared recovery record, reclassifies
every path and continues. Multiple pending records are ambiguous and fail
closed; invalid records are never skipped in favor of the latest timestamp.
Unknown bytes still refuse. A handled write failure leaves the recovery record
pending and known source state resumable; it does not attempt a second
best-effort rollback that could itself lose evidence.

On success, an immutable recovery verification binds the recovery manifest and
the restored path inventory. The source generation remains an interrupted
attempt; its original evidence is never relabeled as a successfully applied or
automatically rolled-back transaction. A valid completed recovery record
closes the operation permanently: a repeat returns `already-recovered` only
while source is still exactly before, and otherwise refuses as post-recovery
drift. It must not reinterpret bytes that later happen to equal the old after
hash as permission to replay a create deletion or bounded restore.

## Authority and safety

- The source and journal roots must remain separate and symlink-safe.
- The existing non-blocking source-journal lock covers both the original write
  and interrupted recovery.
- Selected-source recovery requires the same source, workspace-manifest,
  project-config and write-policy authority checks as completed recovery.
- Recovery writes only recorded before bytes or removes a recorded `create`
  whose current bytes exactly match this attempt's after hash.
- Read guards are evidence of original admission, not mutable recovery
  targets. Recovery does not overwrite them.
- Before any mutation, the command completes a full admission scan of every
  target. Immediately before each replace or removal it revalidates path
  containment, object type, exact current hash and external authority. It
  verifies the complete final before state before publishing recovery success.
- Bytes outside the recorded before/after states are never intentionally
  overwritten. Drift detected before the first mutation produces no write;
  drift detected after earlier restores leaves a visible pending recovery with
  partial progress that a later invocation may resume only if every path still
  has a known state.
- Prepared and terminal records contain hashes, paths and policy evidence, not
  Markdown bodies beyond the existing private before/after journal copies.

The supported concurrency model assumes no uncoordinated source writer during
recovery. The journal lock excludes cooperating Documentation Engine commands;
per-path revalidation detects many external races but cannot prove the absence
of an intervening same-byte edit (the ABA case). Stronger arbitrary-writer
coordination or lineage is outside this bounded protocol.

## Recovery matrix

| Current source | Prepared evidence | Recovery record | Outcome |
| --- | --- | --- | --- |
| all before | valid | none | record recovered; no source write |
| all after | valid | none | restore all paths to before |
| mixed before/after | valid | none | restore only after-state paths |
| any unknown/newer | valid | any | refuse; no additional source write |
| create target absent | valid create | none/pending | already before |
| create target exact after | valid create | none/pending | remove tool-created path |
| create target other bytes/object | valid create | any | refuse |
| valid completed terminal evidence | valid | none | reject interrupted route |
| published terminal present but invalid | valid | any | refuse as integrity failure |
| missing/corrupt manifest or backup | invalid | any | refuse as untrusted evidence |
| changed selected-source authority | valid | any | refuse before source mutation |
| symlink substitution | valid | any | refuse before source mutation |
| pending recovery, known mixed state | valid | valid pending | resume toward before |
| pending recovery, unknown state | valid | valid pending | refuse without guessing |
| one completed recovery, source before | valid | valid completed | already recovered; no mutation |
| one completed recovery, source not before | valid | valid completed | refuse as post-recovery drift |
| multiple pending recovery records | valid | ambiguous | refuse; do not select by timestamp |
| invalid recovery record beside a valid one | valid | mixed validity | refuse integrity/ambiguity |

## Verification plan

Use subprocess fault injection after preparation, after each source
replacement, during validation, before terminal recording and during
interrupted recovery. Cover bounded edits and creates, mixed known states,
unknown/newer bytes, corrupt evidence, selected-source authority, symlink
substitution, lock contention and deterministic replay. Existing schema-1
completed recovery and undo tests must remain green.

Fault injection around terminal publication must prove that the published
state is either absent or valid. Corrupt and hash-mismatched published terminal
records must refuse interrupted recovery. Recovery tests must include repeated
success, post-recovery drift (including bytes equal to an old after state),
multiple pending records, per-target drift between admission and mutation, and
an interruption after each recovery replacement.

Public documentation may describe the protocol only after these tests pass.
