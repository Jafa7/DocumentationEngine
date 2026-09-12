# Documentation Engine remediation plan for Sol

Date: 2026-09-12
Intended implementer: Sol
Source baseline: `3f5f9cc42e1ad4da4b9d6b9fd7387e9f3dcc01f4`
Package baseline: 0.5.0; internal projection schema: 5
Status: implementation plan prepared; implementation has not started.

## Mandate and outcome

Retain the Markdown source model and provider-neutral core. Address the architectural reviews through bounded changes that improve semantic correctness, regeneration, recovery and explicit delivery guarantees. Use those changes to guide later extraction of application logic from the CLI.

This document plans future work. Creating it does not start a worker, authorize commits or authorize changes to private project material. When the user requests implementation, execute the authorized work packages below and preserve unrelated working-tree changes. Follow the current [agent instructions](AGENTS.md) if they differ from this planning snapshot.

Success means that confirmed defects have regression coverage, contract limitations have explicit dispositions, and implemented changes preserve the existing retrieval and evidence guarantees. A clarified limitation is not a repaired runtime defect; a design decision is not a completed implementation.

Review inputs:

- [First review: AR-01 through AR-06](ARCHITECTURE_REVIEW_2026-09-12.md).
- [Second review: SP-01 through SP-04](ARCHITECTURE_REVIEW_2026-09-12_SECOND_PASS.md).
- [Third review: TP-01 through TP-03](ARCHITECTURE_REVIEW_2026-09-12_THIRD_PASS.md).

The source commit and relevant implementation branches were checked while preparing this plan. They remain at the reviewed baseline. Earlier reproductions are supporting evidence; Sol must reproduce the behavior addressed by each fix before changing it. Do not rerun all reviews or all diagnostics before beginning the first bounded fix.

## Scope and working constraints

- Preserve authored Markdown, IDs, formatting and existing files. Use synthetic test projects and native temporary directories under `/tmp`.
- Keep documentation and comments in English. Keep public fixtures and examples adopter-neutral.
- Do not inspect or modify the private `plan/` tree for this work. Store any additional public design notes outside it.
- Preserve the pre-existing `AGENTS.md` edit and the three review reports. Do not overwrite their historical conclusions with implementation status.
- Keep provider-specific behavior outside the core. Do not add forum, wiki, external community integration, a database or a mandatory service as part of this remediation.
- Preserve complete-or-fail responses, explicit omissions, authority checks and direct/projected parity. Never fix performance by silently omitting context or skipping safety-relevant validation.
- Do not stage, commit, push, merge, rebase or publish unless separately requested. If commits are later requested, use WSL and perform the executable-mode checks in `AGENTS.md`.
- Use one implementation package at a time in the shared checkout. A broad CLI refactor must not be bundled with semantic fixes.

## Finding disposition and execution order

| Finding | Work package | Planned disposition |
| --- | --- | --- |
| SP-01: qualified relations missed by profiles | W01 | Fix relation inventory consumption |
| TP-02: corrupt generation survives successful rebuild | W02 | Fix rebuild success and corrupt-object handling |
| SP-03: duplicate JSON members accepted | W03 | Reject ambiguity at the verified artifact boundary |
| SP-02: readiness and quality scopes differ | W04 | Preserve scope differences; make coverage explicit |
| TP-03: revision-only omission | W04 | Clarify revision discipline; retain current lightweight mode |
| AR-05: eager optional-configuration validation | W04 | Retain fail-closed behavior; document the deliberate tradeoff |
| AR-03: physical bytes versus normalized text identity | W05 | Decide and implement versioned identity semantics |
| TP-01: incomplete journal recovery | W06 | Design and implement separate interrupted-attempt recovery |
| AR-02: volatile migration rollback | W06, W07 | Clarify guarantee, then reuse proven recovery primitives where suitable |
| SP-04: repeated complete-export preparation | W08 | Prepare immutable export queries once |
| AR-04: whole-catalog cost for small reads | W09 | Measure first; optimize only an evidenced bottleneck |
| AR-06: MCP execution limits | W10 | Define and implement transport execution policy |
| AR-01: application behavior inside CLI | W11 | Extract one stable retrieval service first; stage later extractions |

Recommended sequence: **W00 → W01 → W02 → W03 → W04 → W05 → W06 → W07 → W08 → W09 → W10 → W11**.

W01–W03 form the first deliverable batch and can be accepted independently. W08 has no dependency on journal implementation and may move ahead of W06/W07 if recovery design remains unresolved. W09 requires W08 for the export baseline. W11 follows the semantic and contract changes affecting the code being extracted. No package is allowed to claim closure of a downstream package merely by documenting it.

Do not estimate elapsed time from line counts. W01–W03 are bounded fixes; W05–W07 contain compatibility or recovery design work and should be split into reviewable candidates.

## W00 — Establish the execution baseline

**Verification: structural.**

Record the current commit, package/projection versions and working-tree status. If source has advanced, inspect only the relevant changes and revise the affected package rather than assuming historical line numbers still apply. Read the current instructions and the review section for W01.

Keep a small execution ledger beside this plan or in the authorized task's handoff evidence. Use the fields `package`, `state`, `baseline`, `change`, `checks`, `remaining_risks` and `next_step`. Valid states are `not-started`, `in-progress`, `implemented`, `verified`, `accepted`, `deferred-with-reason` and `blocked-with-reason`.

**Done when:** the first task has a bounded scope, baseline and verification declaration. No full suite is required for this inventory step. No finding may be marked fixed from historical evidence alone.

## W01 — Make profile checks see every authored relation form

**Finding: SP-01. Verification: full on the finished candidate.**

Start with [profiles](src/docsystem/profiles.py), [metadata](src/docsystem/metadata.py) and [profile tests](tests/test_profiles.py). Both `_metadata_fields` and `_document_relations` omit `federated_references` at this baseline.

Implement a shared relation-name/presence accessor at the smallest appropriate semantic layer. Preserve target qualification and pin information in the underlying model. Profile allowlist and required-field checks must not depend on remote availability or resolution of a target body.

**Acceptance:**

- `allowed_relations = []` rejects the same relation whether its target is local, qualified or validly pinned.
- A valid qualified value satisfies a required relation field. Empty or malformed values do not become valid merely because the field exists.
- Legacy handling follows the configured legacy policy; it does not silently change.
- Exercise `profile-check`, ordinary validation, selected-source behavior and MCP exposure where supported. Preserve existing failure payload semantics.

Focused development checks: `tests/test_profiles.py`, relevant cases in `tests/test_metadata.py`, `tests/test_workspace.py` and `tests/test_mcp_adapter.py`. Extend tests around observable semantic outcomes, not the accessor's implementation shape.

## W02 — Make projection rebuild success verifiable

**Finding: TP-02. Verification: full.**

Start with [projection publication and verification](src/docsystem/projection.py), `index_projection` in [CLI](src/docsystem/cli.py) and [vertical tests](tests/test_vertical.py).

Reproduce a valid generation whose manifest is damaged while source/configuration stay unchanged. Define valid reuse versus a corrupt occupied content address before editing the writer. Preferred outcome: the ordinary rebuild path repairs derived state through a controlled publication protocol and reports success only after verification.

Keep valid generations immutable. Do not patch individual shards in place under a pointer that readers may already hold. Validate resolved cache paths before any quarantine, move or cleanup; never touch authored source or journal evidence. Preserve the documented single-writer assumption and fallback behavior.

**Acceptance:**

- Corrupt manifest, missing shard, malformed shard and invalid pointer each have a deterministic recovery outcome with unchanged source.
- A successful rebuild is followed by successful projection status and verified reads. A failure returns nonzero and a precise supported next action.
- An already valid generation is reused idempotently; direct/projected output remains equal.
- A reader encountering recovery/retention races either receives verified content or visible direct-source fallback.
- Source bytes remain unchanged, including when repair itself fails.

If safe repair cannot be delivered in one candidate, a truthful nonzero failure is an acceptable first slice, but leave TP-02 open until a supported repair path exists. Re-run [federated projection tests](tests/test_federated_projection.py) if shared publication primitives change; do not automatically extend repair to unrelated caches.

## W03 — Reject ambiguous artifact JSON before verification

**Finding: SP-03. Verification: full.**

Start with [artifact loading](src/docsystem/provider_artifact.py), the existing duplicate-key policy in [shared finish](src/docsystem/shared_finish.py), and [artifact tests](tests/test_provider_artifact.py).

Reject duplicate object members recursively during decoding, before schema checks and digest canonicalization. A small shared decoder is appropriate if it preserves each consumer's public error mapping. The first implementation scope is the provider artifact boundary and any helper actually extracted for it; inventory other integrity-bearing loaders for later bounded adoption instead of changing every JSON reader.

**Acceptance:** root, nested, same-value and different-value duplicates are rejected; malformed/invalid-encoding inputs have deterministic errors; valid exports still verify; unique unknown optional fields retain the compatibility allowed by the artifact contract. Test the CLI verification route as well as the loader. Do not reinterpret this change as authentication or a new digest algorithm.

## W04 — Specify what readiness and cached knowledge prove

**Findings: SP-02, TP-03, AR-05. Verification: full for shared contract/output changes; structural for the initial decision note only.**

Start with [readiness](src/docsystem/readiness.py), [configuration](src/docsystem/config.py), [agent contract](docs/agent-contract.md), [knowledge promotion](docs/knowledge-promotion.md) and the context handler.

Use these recommended decisions to keep the change bounded:

| Question | Recommended decision |
| --- | --- |
| Does `ready` mean all project policies passed? | Keep its structural meaning and current exit semantics. Expose an explicit scope and the checks actually evaluated. Never represent an unevaluated check as passing. |
| Should indexing require all governance checks? | Preserve the present indexability boundary. Document how it differs from `validate`; do not add unrelated gates to reads. |
| Does `ID@REV` prove unchanged content? | It relies on revision-to-content discipline. State that directly, including existing living-knowledge revision guidance. Recommend retained-generation deltas when that discipline cannot be assured. |
| Should malformed unused capability settings be ignored? | Keep eager fail-closed validation for this tranche. Document it and record lazy capability loading as deferred, with explicit reasons. |

Before changing readiness JSON, specify the exact fields, versioning/additive-compatibility treatment, text rendering and MCP mapping. Keep `--assume-known` behavior stable; a new content-bound cache token is not required in this tranche. Narrow unconditional safety wording in README/architecture without claiming revision enforcement has been added.

**Acceptance:** fixtures distinguish valid structure with forbidden profile relations, invalid program-plan policy, graph diagnostics and absent optional policy. An unchanged-revision content edit demonstrates the documented assumption. Test explicit section inclusion and `--since` separately. Preserve rejection of invalid required configuration; any configuration behavior change requires new focused configuration tests.

Development owners: [readiness tests](tests/test_readiness.py), [context tests](tests/test_context_cli.py), [configuration tests](tests/test_config.py) and [MCP tests](tests/test_mcp_adapter.py).

## W05 — Separate physical source identity from parsed text identity

**Finding: AR-03. Verification: full. Depends on W02 and W04.**

Write a short decision note before implementation. The recommended target is raw-byte identity for fields claiming exact source identity, while keeping deliberate text normalization for parsing and section presentation. Inventory all producers/consumers of source hashes, retained generations, delta comparison, federation and exported identity. Do not alter one hash function in isolation.

At this baseline the projection schema is 5. Decide whether the change requires a new schema or explicit algorithm identity. Never silently reinterpret an old generation's hashes. Define how old caches fall back/rebuild and how pinned historical consumers report unsupported identity; do not relabel or destroy retained evidence to hide incompatibility. If normalized identity is intentionally retained instead, explicitly defer byte-exact identity and correct every affected public claim.

**Acceptance:** CRLF/LF, terminal newline, Unicode and ordinary content edits have explicit identity outcomes; raw-byte claims match bytes; section rendering stays correct; direct/projected output parity holds; config/source drift is detected; old-generation behavior is deterministic. Include delta and federation coverage in [context](tests/test_context_cli.py), [vertical](tests/test_vertical.py), [provider](tests/test_provider.py) and [federated projection](tests/test_federated_projection.py) tests as affected.

## W06 — Recover interrupted journal attempts through a separate protocol

**Finding: TP-01; prerequisite for durable AR-02 remediation. Verification: structural for a design-only candidate, full for implementation.**

First distinguish exception rollback, process interruption, atomic visibility and power-loss persistence in a short design note. Correct overbroad migration/maintenance wording as part of the relevant contract candidate. This work targets recoverable process interruption; do not claim multi-file atomic visibility or power-loss durability from Python exception tests.

Design a prepared record binding source authority, operations, paths, before/after hashes and backups before source replacement. Define states and transitions for preparation, partial apply, completed apply, interrupted recovery and completed recovery. Keep completed-generation verification strict. Pending attempts require their own validated recovery path and separate evidence.

Specify a recovery matrix: all-before, all-after, mixed known states, unknown/newer bytes, create-target absence/presence, corrupt evidence, changed authority and symlink substitution. Unknown state must fail closed. Recovery may restore only bytes owned by the verified attempt; an interrupted recovery must itself have a deterministic continuation. Preserve pending evidence and orphan staging diagnostics.

Perform a bounded review of this design against the matrix before implementation. Resolve technical choices in the decision record; escalate only when a proposed scope change exceeds the user's implementation authorization. Do not turn this into another general repository review.

**Acceptance:** subprocess fault injection after preparation, after each source replacement, during validation, around final recording and during recovery. Recovery restores known before-state or explains a safe refusal; original backups survive; unknown/newer content is not overwritten; replay is deterministic; completed undo remains compatible. Cover local lock behavior and selected-source authority in [journal](tests/test_journal.py), [maintenance](tests/test_maintenance.py) and [federated write](tests/test_federated_write.py) tests. Power-loss guarantees require separate evidence if later added.

## W07 — Bring migration onto an explicit preservation contract

**Finding: AR-02. Verification: full. Depends on accepted W06 primitives for crash recovery.**

Start with [migration](src/docsystem/migration.py) and [migration tests](tests/test_migration.py). Reproduce process interruption and rollback failure independently; the original review established this control-flow risk statically rather than executing a crash test.

Reuse W06's transaction primitives where they fit migration's whole-file, formatting-preserving transformation. Do not force migration into managed-block semantics or duplicate a second recovery engine. Bind planned before bytes and preserve scalar formatting, encoding, file modes and idempotence. Define stale-plan rejection and recovery diagnostics at the CLI boundary.

**Acceptance:** ordinary migration fixtures stay unchanged; caught write failure, partial replacement, interruption, stale source and failed recovery each have explicit supported outcomes; a recovered/retried migration is deterministic; authored files and unrelated content survive. If integration is deferred, document the narrower exception-only guarantee and leave durable migration recovery open in the ledger.

## W08 — Prepare complete exports once

**Finding: SP-04. Verification: full. Depends on W03; reconcile with W05 if identity changes have landed.**

Start with [provider queries](src/docsystem/provider.py), [artifact assembly](src/docsystem/provider_artifact.py), [provider tests](tests/test_provider.py) and [artifact tests](tests/test_provider_artifact.py).

Prepare one immutable observation inventory per snapshot export, and one inventory per side plus one comparison per comparison export. Share paging semantics through a prepared-query abstraction. Preserve cursor binding, ordering, byte limits, completeness and deterministic artifact encoding. Do not introduce a persistent/global cache or bypass page validation merely to reduce call counts.

**Acceptance:** empty, single-page and multi-page exports equal their paged equivalents; byte-limited pages and invalid cursors retain behavior; complete exports verify; inventory/comparison preparation counts stay constant with increasing page count. Reuse the synthetic 1,104-observation regression scenario from SP-04. Assert meaningful work counts, not wall-clock thresholds in ordinary unit tests.

## W09 — Measure retrieval cost before narrowing materialization

**Finding: AR-04. Verification: focused for an isolated measurement script; full for retrieval changes. Depends on W08 for export measurement.**

Create a deterministic synthetic corpus generator and measurement runner with configurable document count, body size and graph density. Record environment, revision, cold/repeated execution, wall time, peak-memory measurement method, bytes read/returned and parsing/materialization counts. Measure a single section, broad context and complete export separately; do not read private corpora. Distinguish the first process invocation from a genuinely cold filesystem cache.

Produce a compact before/after table and choose the next change from evidence. A likely candidate is query-scoped materialization after existing freshness verification, but this is not preselected as a guaranteed optimization. Keep full-source hashing and complete-graph requirements unless a separately specified freshness contract replaces them.

**Acceptance:** the runner is reproducible on synthetic data; any optimization shows reduced targeted work without altered output, missed source/config drift or incomplete graph answers. A measurement-only result leaves runtime optimization deferred with evidence. Do not promise arbitrary latency or memory targets before obtaining the baseline.

## W10 — Bound MCP subprocess execution

**Finding: AR-06. Verification: full. Use W09 measurements when selecting execution defaults.**

Start with [MCP executor](src/docsystem/mcp_server.py), [adapter contract](docs/mcp-adapter.md), [MCP tests](tests/test_mcp_adapter.py) and [platform tests](tests/test_cli_platform.py).

Specify deadline, cancellation and output handling at the adapter boundary. Define configuration ownership and defaults, cleanup of a timed-out/cancelled child, and structured transport failure versus valid domain-negative payload. Check what the installed host/SDK actually propagates before promising cancellation support. Keep one semantic implementation behind the adapter; a reusable executor around the CLI is sufficient.

**Acceptance:** a stalled child terminates under the configured deadline; cancellation cleans up where supported; excessive output produces an explicit failure or supported delivery mode, never a silently truncated success; Unicode diagnostics and legitimate nonzero payloads remain intact. Unsupported host/platform cancellation must be stated precisely. Add configuration tests if execution policy introduces settings. Verify relevant Linux/Windows boundaries without claiming an unrun platform test passed.

## W11 — Extract application services after behavior stabilizes

**Finding: AR-01. Verification: full. Depends on relevant earlier semantic/contract packages.**

First extract only retrieval view loading, selection and packet construction from [CLI](src/docsystem/cli.py) into internal application services returning structured results/diagnostics. Keep argument syntax, rendering and exit-code mapping in adapters. Preserve the existing CLI/MCP interface; an internal seam does not need to become a supported public Python API.

**Acceptance:** direct/projected and CLI/MCP parity, unchanged omission and graph-validity behavior, service tests that do not capture stdout, and no application/domain dependency on CLI presentation. Use an import-boundary check if it protects the new architectural rule. Do not make file length the success metric.

Treat evidence assembly and managed maintenance as subsequent extractions with their own contracts. The retrieval pilot reduces AR-01 but does not close all concentration findings. Reconsider further splits after measuring whether the first seam actually simplifies callers and tests.

## Verification and acceptance protocol

During implementation, run the narrow tests named by the work package and lint touched Python. Every configuration behavior change must include and run focused configuration tests. Verification declarations above refer to the finished candidate, not a requirement to run the whole suite after every edit.

For each finished candidate classified full, run once from WSL:

```bash
TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run pytest
TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run ruff check .
git diff --check
```

If several small packages are intentionally accepted as one candidate, use focused checks while implementing and one full gate after the final combined change. If they are delivered independently, each full-classified candidate needs its own gate. A later structural-only edit does not invalidate a previously passing gate. Release/package changes additionally require the checks in [releasing](docs/releasing.md).

For every package, record the reproduced before-state, implemented behavior, exact checks/results, compatibility impact, remaining limits and ledger state. Distinguish `passed`, `failed` and `not run`. Validate new untracked Markdown separately, since `git diff --check` alone does not inspect it. Mark a finding accepted only when its stated completion criteria have evidence; record retained tradeoffs and deferred parts explicitly.

## Orchestration and handoff to Sol

This plan is an input to Sol, not a reason to spawn a new task automatically. If execution is delegated, use OrchestratorEngine under the current project instructions. Check that the binding belongs to the executing host task and that the selected enabled profile's executable is available in its execution environment. The previous failed review dispatch does not establish current worker availability.

Write the complete bounded task contract and `WORKER_TASK_INTENT` below `.orchestrator/prompts/`, declaring role, permissions, risk, verification and authorizations. Preserve existing bindings and audit state. Dispatch once with a stable task ID; use a deterministic bounded wait instead of model polling. Inspect compact result/evidence first, then referenced details as necessary. A timeout does not authorize duplicate dispatch. Do not alter worker profiles or substitute models silently to make a launch succeed.

Suggested first implementation assignment, to use when execution is requested:

> Use this plan and the current AGENTS.md. Complete W00, then implement W01 only. Reproduce SP-01 on synthetic local and qualified relations; fix profile relation presence/allowlist handling without changing target resolution or unrelated validation policy. Preserve the working tree and private material. Run focused checks during development and the full gate on the finished candidate. Record before/after evidence and remaining risks. Do not commit or start W02 until W01 has been delivered for acceptance.

The first delivery should contain the W01 patch and its verification evidence. Subsequent assignments should name the next package and its dependencies rather than replay all three reviews into every worker prompt.

## Plan preparation checks

This document is a structural planning change. Implementation, fault-injection and test-suite execution were not performed while authoring it. Local file links, Markdown whitespace, finding coverage and preservation of existing reports/instructions are checked together with `git diff --check` before delivery. The private `plan/` tree and product code remain unchanged.
