# Deterministic idea intake

Documentation Engine can place an agent-interpreted idea into an existing
documentation graph without making the engine itself an intent classifier.
The human supplies the idea, an AI agent prepares a bounded semantic request,
and project-authored policy maps that request to exactly one result:

- `update-existing`: update the one sufficient existing owner;
- `create-draft`: propose a new ordinary document;
- `create-workstream`: propose a separately governed workstream;
- `blocked`: stop because the evidence is ambiguous, contradictory or not
  authorized by policy.

`docsystem intake` is read-only. It never creates a document, reserves an ID,
edits Markdown or approves the agent's semantic assertions.

## Project-authored policy

Intake is disabled unless the project defines at least one versioned
criterion:

```toml
[[intake.criteria]]
id = "idea-placement"
revision = 1
allowed_decisions = ["update-existing", "create-draft", "create-workstream"]
max_candidates = 8
safe_fallback = "blocked"
draft = { area = "architecture", type = "architecture", identifier = "document", width = 3 }
workstream = { area = "roadmap", type = "workstream", identifier = "roadmap", width = 3 }
```

The `id` and positive `revision` form a pinned reference such as
`idea-placement@1`. `allowed_decisions` may contain the three non-blocked
results; `blocked` is always available as the safe fallback. A criterion may
accept from 1 through 50 candidates. `draft` and `workstream` name configured
areas and identifier roles, define the proposed metadata type, and define the
minimum numeric width for a new stable ID. Every field is required; unknown
fields fail configuration loading.

## Bounded request

The request is a UTF-8 JSON object with schema version `1`. See
[`examples/idea-intake-request.json`](../examples/idea-intake-request.json).

```json
{
  "schema_version": 1,
  "idea_id": "IDEA-001",
  "criterion": "idea-placement@1",
  "outcome": "Add a bounded capability for placing new ideas.",
  "source": "human-idea:current-task",
  "candidates": [
    {"address": "DOC-010#ownership", "authority": "owner"}
  ],
  "signals": {
    "authority_conflict": false,
    "incompatible_outcomes": false,
    "independent_lifecycle": false,
    "existing_owner_sufficient": true
  },
  "assumptions": ["DOC-010 remains the canonical owner."]
}
```

The file is limited to 64 KiB, individual strings to 4096 characters, and
lists to 50 items. Candidate addresses must resolve to catalog documents or
sections. Candidate authority is either `owner` or `related`. Duplicate and
unknown values fail before stdout is emitted. Candidates and assumptions are
normalized into deterministic order before the request hash is computed.

The engine applies these rules:

1. authority conflict, incompatible outcomes, or contradictory owner and
   independent-lifecycle signals produce `blocked`;
2. `existing_owner_sufficient` requires exactly one `owner` candidate and
   produces `update-existing`;
3. otherwise, `independent_lifecycle` produces `create-workstream`;
4. otherwise the result is `create-draft`;
5. a result not allowed by the pinned criterion becomes `blocked`.

## Inspect a decision

```bash
docsystem intake PROJECT --request idea-request.json --json
```

Malformed input, invalid configuration and unresolved addresses fail with
exit code `1`, diagnostics on stderr and no partial stdout. `blocked` is a
valid, inspectable evaluation and therefore exits `0` with `blocked: true`.
The JSON result includes reasons, normalized candidates and assumptions,
hashes of the request and outcome, and either an existing address or a
proposed ID/path.

A new-target result also includes an `allocation_guard`: a SHA-256 digest of
the stable-ID catalog used to allocate the proposal. It is evidence, not a
reservation. Before a later authorized creation, the caller must evaluate the
request again and require the same guard; a changed catalog requires a new
proposal.

## Trust boundary

The AI agent, not Documentation Engine, determines what the idea means and
sets the candidate authority and semantic signals. The engine validates the
bounded shape, stable addresses and pinned project policy, then makes the
placement decision mechanically. A human or higher-level workflow remains
responsible for reviewing semantic truth and authorizing any source change.

Do not paste private source bodies into the request. Use stable addresses,
short assumptions and a bounded outcome statement. If the graph cannot
support one unambiguous placement, choose evidence that produces `blocked`
rather than inventing ownership.

When intake leads to a separately governed workstream, create and review its
mandate before passing a bounded intent to
[execution admission](execution-admission.md). An intake proposal is not
execution permission.
