# Interrupted journal recovery

Documentation Engine uses one journal protocol for bounded managed maintenance
and legacy-relation migration. Completed undo and interrupted-attempt recovery
are distinct operations and must not be substituted for each other:

```bash
# Undo a completed, verified write whose source still equals recorded after bytes.
docsystem maintenance-recover GENERATION PROJECT

# Restore a prepared schema-2 attempt that stopped before terminal evidence.
docsystem maintenance-recover-interrupted GENERATION PROJECT

# Restore an interrupted legacy-relation migration to its exact before state.
docsystem migrate-recover-interrupted GENERATION PROJECT
```

Both commands are mutating operations and require explicit user approval.

## Immutable preparation and terminal evidence

Journal schema 2 writes an immutable `manifest.json` with status `prepared`
only after all before/after copies and patches exist, and before replacing any
source file. A separately published `verification.json` binds that manifest
and records terminal status `applied` or `rolled-back`. Terminal JSON is fully
written under a temporary name and then atomically published in the same
directory.

The interrupted command distinguishes three states:

- no published terminal: eligible for interrupted recovery;
- valid terminal: reject this route and use completed recovery when an undo is
  intended;
- present but invalid terminal: fail closed as corrupt evidence.

Temporary publication remnants are not terminal evidence. Missing or corrupt
prepared evidence is never reconstructed heuristically. Existing completed
journal schema-1 generations remain valid for inspection, backup and ordinary
completed recovery; they are not rewritten.

## Known-state recovery

Every admitted source path is classified by exact byte hash:

- `before`: recorded original bytes, or absence for a recorded create;
- `after`: exact bytes created by the interrupted attempt;
- `unknown`: anything else, including unexpected absence or object type.

All-before needs no source mutation. An all-after or mixed before/after state
restores only after-state paths. Any unknown state refuses. A create target is
removed only while it is still byte-identical to this attempt's recorded after
copy.

Interrupted recovery has its own immutable record under
`recoveries/GENERATION/TIMESTAMP`. If recovery itself stops, rerunning the
command resumes the one verified pending record toward before state. Multiple
or invalid records are not resolved by picking the latest timestamp. Once a
recovery terminal is published, it permanently fences replay: a repeat returns
`already-recovered` only while source remains before, and later drift refuses
even if bytes happen to equal the old after copy.

Recovery-record preparation uses a non-authoritative `.staging-*` directory.
Only after its complete manifest exists is that directory atomically renamed
to the public timestamp name. Empty recovery containers and unpublished
staging remnants therefore do not block a safe retry and are retained as audit
evidence; malformed or incomplete records already published under a timestamp
remain authoritative ambiguity and fail closed.

The command performs a complete preflight, then revalidates path containment,
object type, source hash and selected-source authority before each mutation.
Detected mid-run drift leaves visible pending evidence and known partial
progress for an explicit retry; the engine never guesses over unknown bytes.

## Exact guarantees

- Exception rollback handles failures caught by the creating process.
- Interrupted recovery handles abrupt process termination after a valid
  preparation was published.
- Each individual file replacement is atomic; a multi-file source tree is not
  claimed to be atomically visible to concurrent readers.
- The journal lock coordinates Documentation Engine writers. Operators must
  prevent uncoordinated external writes during recovery. Exact hashes cannot
  detect an intervening edit that returns to identical bytes (the ABA case).
- Power-loss durability is not claimed without separate filesystem-level
  evidence.

Managed-maintenance recovery validates the rebuilt catalog and refreshes its
disposable projection. Migration recovery instead restores the exact
pre-migration bytes. That legacy state may intentionally contain path
relations, so recovery does not pretend it satisfies strict post-migration
validation; a later migration retry recomputes and validates a fresh plan.
The interrupted migration command accepts only a generation whose immutable
workstream identity is `MIGRATION-RELATIONS`.

Selected workspace sources retain the stricter authority contract:
`write = "managed-maintenance"`, the exact prepared manifest hash, and matching
workspace-manifest/project-config evidence are required. Recovery records are
body-free metadata, but the private journal continues to contain the byte-exact
before and after copies needed for restoration. Migration uses the same
source-local journal and lock, emits its generation ID before applying, and
preserves each existing file's mode as well as its raw bytes and line endings.
