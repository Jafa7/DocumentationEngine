# Bounded execution admission

Documentation Engine can validate a workstream intent before another agent or
orchestrator executes it. Admission connects a stable workstream mandate to
bounded target addresses, actions, risk, verification and authorization
evidence. It returns either `admitted` or an explainable `blocked` result.

The initial contract deliberately covers autonomy levels A0 through A2 only:

| Level | Supported actions |
| --- | --- |
| A0 | `inspect` |
| A1 | `plan` |
| A2 | `edit-local`, `run-checks` |

It does not run workers, generate semantic changes, write Markdown, commit,
push, merge or release. Those remain separate execution and external-action
boundaries.

## Project-authored policy

Admission is disabled unless a project defines a versioned criterion:

```toml
[[admission.criteria]]
id = "bounded-local"
revision = 1
max_autonomy = "A2"
allowed_actions = ["inspect", "plan", "edit-local", "run-checks"]
required_authorizations = ["edit-local"]
allowed_verification = ["focused", "full"]
max_risk = "medium"
max_targets = 12
required_sections = ["mandate", "boundaries", "return-protocol", "review-gate"]
require_source_scope_for = ["edit-local"]
safe_fallback = "blocked"
```

The `id` and positive `revision` form an immutable reference such as
`bounded-local@1`. `max_autonomy` is `A0`, `A1` or `A2`; every allowed action
must fit that level. Required authorizations must be a subset of allowed
actions. Verification values are `structural`, `focused` and `full`; risk is
ordered `low`, `medium`, `high`; `max_targets` is 1 through 100. Required
sections are canonical anchors that must exist in the workstream mandate.
The only safe fallback is `blocked`.

## Bounded intent

The agent or orchestration runtime prepares a UTF-8 JSON request with schema
version `1`. See
[`examples/execution-admission-request.json`](../examples/execution-admission-request.json).

```json
{
  "schema_version": 1,
  "workstream_id": "WS-001",
  "criterion": "bounded-local@1",
  "intake_request_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "outcome": "Make one bounded local change.",
  "targets": ["DOC-010#contract"],
  "actions": ["inspect", "plan", "edit-local", "run-checks"],
  "risk": "medium",
  "verification": "focused",
  "boundaries": {
    "authored_deletion": false,
    "privacy_boundary": false,
    "permission_expansion": false,
    "external_commitment": false
  },
  "authorizations": [
    {
      "action": "edit-local",
      "authority": "project-owner",
      "evidence": "user-current-task"
    }
  ],
  "assumptions": ["The mandate remains authoritative."],
  "source_scope": [
    {"path": "src/example.py", "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"},
    {"path": "tests/new_test.py", "sha256": null}
  ]
}
```

The file is limited to 128 KiB, individual strings to 4096 characters and
lists to 100 items. Targets, actions, authorizations and assumptions are
normalized for deterministic hashes and output. Target document/section
addresses must resolve in a fully valid catalog. The selected document must
have `type: workstream`, must not declare terminal status `completed`,
`cancelled` or `failed`, and must contain every section required by the
criterion.

`source_scope` is optional unless the selected criterion requires it for a
requested action. Each entry uses one normalized relative POSIX path. A
lowercase SHA-256 binds an existing file; `null` binds the expectation that a
new path is absent. Absolute, escaping, duplicate, missing or stale entries
fail closed before handoff generation.

The intake hash links admission to the earlier semantic request without
copying its outcome body. Documentation Engine checks that it is a lowercase
SHA-256 value; it cannot prove which external actor produced it. A legacy or
manually created workstream with no intake request must set the field to
`null`, preserving an explicit absence instead of inventing provenance.

## Inspect admission

```bash
docsystem admission WS-001 PROJECT --request admission.json
docsystem admission WS-001 PROJECT --request admission.json --json
```

Invalid configuration, malformed/unbounded input, an ID mismatch, incomplete
mandate or unresolved target exits `1`, emits diagnostics on stderr and emits
no partial stdout. Policy denials are valid evaluations: they exit `0` with
`decision: blocked`, exact reason codes and `blocked: true`.

An intent is blocked when it exceeds configured action, autonomy, risk or
verification policy; omits a required authorization; or declares any of these
A0–A2 escalation boundaries:

- deletion of authored documentation;
- a privacy boundary;
- permission-scope expansion;
- an external commitment.

An admitted result includes the required autonomy level, normalized intent,
request/outcome hashes and a catalog guard. It is admission evidence for the
calling workflow, not an execution command. The caller must compare the guard
before acting and re-evaluate after catalog or mandate changes.

For a portable pre-execution boundary, turn the admitted request into an
[immutable execution handoff](execution-handoff.md). That packet binds the
admission to exact mandate, target, section and graph evidence and can be
reverified immediately before an external executor acts.

## Trust boundary

Authorization objects are signed-off assertions, not authenticated identity.
The core verifies that project policy received an authority/evidence pointer
for each required action and preserves it in output. The calling agent or
orchestrator must authenticate the actor, enforce filesystem/runtime
permissions and prevent prompt text from expanding the admitted scope.

Admission does not replace workstream completion evidence. Validate the
pre-execution intent here, execute elsewhere, then use the
[bounded workstream evidence](workstream-evidence.md) contract to prove checks,
review, correction and return.
