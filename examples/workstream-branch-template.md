---
id: WS-001
revision: 1
type: workstream
status: active
parent: DOC-010
derived_from: [DOC-003]
returns_to: [DOC-020]
---

# Workstream: <name>

<a id="why-this-branch-exists"></a>
## Why this branch exists

Explain the problem, opportunity or split that made this workstream necessary.
Name the parent project need in one or two paragraphs.

<a id="inherited-context"></a>
## Inherited context

List the parent documents, decisions and constraints that the child workstream
must preserve. Prefer stable document IDs and section anchors over copied
history.

<a id="mandate"></a>
## Mandate

Define the concrete outcome this branch is allowed to pursue. Include the
expected artifact shape: code, documentation, research notes, migration plan,
prototype, pull request or review report.

<a id="boundaries"></a>
## Boundaries / non-goals

State what the workstream must not change or decide. Include repository,
module, product and process boundaries when relevant.

<a id="return-protocol"></a>
## Return protocol

Define how the result returns to the parent project:

- where the final report, commit or artifact should be sent;
- which checks must pass;
- which assumptions need parent approval;
- whether the branch may push/open a pull request or only hand off findings.

<a id="current-artifacts"></a>
## Current artifacts

List active files, branches, threads, tasks or external resources created by
the workstream.

<a id="review-gate"></a>
## Review gate

Define the evidence required before the parent accepts the workstream result.
Include known compatibility checks and nonblocking backlog items.
