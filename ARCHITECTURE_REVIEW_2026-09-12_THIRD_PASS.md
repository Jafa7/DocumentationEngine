# Documentation Engine architecture review — third pass

Date: 2026-09-12
Package version: 0.5.0
Reviewed source baseline: `3f5f9cc42e1ad4da4b9d6b9fd7387e9f3dcc01f4`
Previous reports: [first pass](ARCHITECTURE_REVIEW_2026-09-12.md), [second pass](ARCHITECTURE_REVIEW_2026-09-12_SECOND_PASS.md)

## Assessment

**The architecture remains suitable for a Markdown knowledge and document engine. Its next priority should be complete recovery paths and precise consistency guarantees.** This pass found two reproducible operational gaps and one conditional cache-contract risk. None requires replacing Markdown, introducing a database into the core, or moving retrieval into a mandatory background service.

The strongest architectural choices remain the separation of authored source from disposable projections, explicit context coverage, provider-neutral mechanics, and optional governed mutation. However, those choices create obligations at their boundaries: disposable state must be rebuildable, journaled mutation must define what happens after interruption, and an omitted document must have a clearly stated basis for being considered known.

For read-oriented project knowledge, these findings support retaining the design. For unattended maintenance of several documents, interrupted-transaction handling needs attention before treating the journal as a complete recovery mechanism. For recurring agents, revision declarations need an explicit trust contract or a content-bound alternative.

| Finding | Priority | Evidence | Relation to earlier reviews |
| --- | --- | --- | --- |
| TP-01: interrupted journal generations cannot use normal recovery | High within optional mutation | Two child-process termination diagnostics | Extends AR-02 into the separate journal subsystem; not the same migration implementation |
| TP-02: rebuilding an unchanged corrupt projection reports success without repairing it | Medium | Corrupt, rebuild, verify sequence | New recovery-path finding; distinct from projection hashing and performance |
| TP-03: revision-only cache declarations can omit changed text | Medium when revisions are not strictly managed | Content change with unchanged revision | New conditional contract risk; expected equality behavior, not a parser defect |

Priorities describe engineering attention, not security severity. The unchanged source baseline means this pass does not establish remediation of earlier findings.

## Scope and method

This pass inspected the journal state transitions, projection publication and verification, context omission planning, and their public contracts. It also checked whether live source reads promise a point-in-time multi-file snapshot. The review used published product goals; it did not use private planning documents, adopter content or external services.

Runtime diagnostics ran against disposable synthetic projects under `/tmp`, using the existing Python environment. For indexing, validation and context, diagnostics called the Python command handlers behind the CLI and captured their return codes and output. Journal interruption used separate Python processes, terminated deliberately with `os._exit(33)`, followed by recovery from the parent process. The CLI recovery route was traced statically to the same journal entry point. These are targeted review experiments, not a full test-suite result or a power-loss test.

An independent review was initially dispatched through OrchestratorEngine using isolated local state and a binding to the current task. It failed before model execution because the selected worker executable was unavailable in WSL. Its compact failure evidence was inspected and retained; no repeated dispatch or status polling was used. A bounded built-in read-only reviewer then completed a static challenge of the three interpretations. That challenge supported the two recovery findings and narrowed the cache finding to a conditional contract risk; it did not independently rerun the experiments. Final findings and experiment acceptance remain the host reviewer's responsibility.

No implementation, configuration or test files were changed. Existing reports and the pre-existing `AGENTS.md` change were preserved. The only new public repository artifact is this report; orchestration prompts and audit evidence remain in ignored local state. No Git staging, commit or publication was performed.

## TP-01 — Interrupted journal generations cannot use normal recovery

**Priority: high for unattended bounded mutation. Confidence: high. Classification: reproduced recovery coverage gap.**

The journal preserves useful recovery material before mutation, but its recovery reader admits only completed attempts. The distinction matters because a process can stop after changing source and before recording completion.

### Evidence and reproduced sequence

[`run_bounded_transaction`](src/docsystem/journal.py#L706) prepares before/after copies and patches, then writes a manifest with `status = "pending"` at [line 804](src/docsystem/journal.py#L804). Source files are replaced individually at [line 858](src/docsystem/journal.py#L858). Validation follows those replacements at [line 891](src/docsystem/journal.py#L891), with ordinary exceptions handled by rollback.

The recovery path calls [`_load_and_verify_generation`](src/docsystem/journal.py#L1366). That reader requires both `manifest.json` and `verification.json` at [line 1107](src/docsystem/journal.py#L1107) and accepts only mutually consistent `applied` or `rolled-back` completion states at [line 1130](src/docsystem/journal.py#L1130). The [maintenance recovery handler](src/docsystem/cli.py#L7839) uses this same path.

Two synthetic files, `a.md` and `b.md`, started with `before\n`. One transaction declared a line-1 edit of both files to `after\n`, supplying their original SHA-256 values and valid line bounds.

| Deliberate termination point | Source after child exit | Persisted journal | Parent recovery result |
| --- | --- | --- | --- |
| Immediately after the first real source replacement | `a.md`: after; `b.md`: before | Pending manifest; both original backups present; no verification file | `generation is incomplete or missing` |
| Inside the validation callback, after all source replacements | Both files: after | Pending manifest; both original backups present; no verification file | Same refusal |

Both child processes exited with code 33. For the first scenario, the child wrapped `Path.replace`, performed the actual replacement, then exited when its target was synthetic `a.md`. For the second, the validation callback exited directly. No production implementation was patched.

### Interpretation and limits

The current [agent contract](docs/agent-contract.md#L477) describes explicit recovery from a verified generation whose current files still match recorded after-state. Refusing incomplete or unknown evidence is appropriate for that operation. The finding is the absence of a separate recovery path for interrupted attempts, rather than an instruction to weaken completed-generation verification.

The existing design supports rollback for handled failures and explicit undo of completed writes. It does not provide general crash recovery. Catching additional Python exceptions would not resolve abrupt termination, because the process cannot execute rollback after it has exited. The experiment establishes process-interruption behavior on this environment; it does not establish filesystem persistence after power loss.

Original bytes remained available in the journal, so irreversible data loss was not demonstrated. An operator could inspect and restore them with separate authority. The operational risk is a partially applied document set and the lack of a supported, verifiable route back from that state. That risk is concentrated in the optional mutation capability, not ordinary read-only knowledge retrieval.

### Recommended architectural direction

Define an interrupted-attempt recovery protocol separately from completed-generation undo. A prepared record should bind the admitted paths, before/after hashes, operation types and authority before source mutation begins. Recovery should verify that record, classify each current path as before, after or unknown, and refuse unknown content. Known partial states need an explicit recovery policy, with separate evidence for the recovery attempt.

If crash durability is intended, specify the persistence ordering for the prepared record, backups, source replacements and final completion evidence. Until that exists, document the narrower guarantee and provide an operator procedure for preserved pending generations. Preserve the original attempt as evidence; do not manufacture a successful completion record to make it admissible.

**Future acceptance evidence:** child-process termination after preparation, after each replacement, during validation and around completion recording; completed-generation recovery unchanged; unknown/newer source still refused; interrupted recovery itself remains recoverable. Power-loss guarantees, if claimed, require separate filesystem-level evidence.

## TP-02 — Rebuilding an unchanged corrupt projection reports success without repairing it

**Priority: medium. Confidence: high. Classification: reproduced operational defect in the rebuild path.**

[`index_projection`](src/docsystem/cli.py#L4314) constructs a projection from authored source and checks the current cache. When `write=True`, it calls the writer even if the status check found corruption, then prints a successful generation-written message and returns 0.

[`write_projection`](src/docsystem/projection.py#L871) only creates shards and a manifest when the directory for that content-derived generation does not already exist. If an existing directory is damaged but source and configuration are unchanged, its generation name remains the same. The writer skips generation construction and republishes the pointer to the damaged directory.

### Observed behavior

A valid one-document synthetic project was indexed. The generated `manifest.json` was then replaced with the invalid JSON text `{`, while authored Markdown and configuration remained unchanged.

| Operation | Handler return code | Observed result |
| --- | --- | --- |
| Initial `index --write` | 0 | Valid generation created |
| `index` after manifest damage | 1 | `projection unreadable` |
| `index --write` with unchanged source | 0 | `Projection generation written` with the original generation ID |
| `index` after that write | 1 | Same unreadable-projection diagnostic |
| `context DOC-001 --json` | 0 | Direct Markdown fallback remained available |

The manifest still contained `{` after the reported successful write. The experiment used malformed manifest content; related missing-shard scenarios were not separately executed.

### Architectural cause and impact

The [projection architecture](docs/architecture.md#L168) deliberately never rewrites existing generation directories. That is a useful immutability rule for readers and pinned consumers. The missing piece is the lifecycle of a corrupt object occupying an otherwise valid content address.

Source-based regeneration and content addressing do not by themselves provide repair. Directory existence is insufficient evidence that the addressed object is complete and valid. As a result, a repair loop can repeatedly receive successful writes while remaining on fallback. Consumers requiring verified retained generations cannot substitute live-source fallback in the same way as ordinary reads.

This does not demonstrate incorrect content being served: [`_load_views`](src/docsystem/cli.py#L2630) rejected the invalid cache and used authored Markdown. The defect is unsuccessful repair reported as success, with operational and performance consequences.

### Recommended architectural direction

Make reuse conditional on successful verification of the existing generation. Define explicit handling for a corrupt occupied address: either return a precise failure with a supported repair operation, or rebuild through a controlled protocol that preserves the documented reader and writer assumptions. Do not silently overwrite files inside a generation that active readers may have selected.

The repair design should distinguish a valid existing generation, an interrupted staging area, a corrupt finalized generation and an invalid pointer. The existing single-writer assumption can keep the design small, provided the success postcondition is explicit: the published generation must be readable and verifiable.

**Future acceptance evidence:** corrupt manifest, absent shard, malformed shard and invalid pointer, each with unchanged source/configuration. A repair must either produce a verified generation or return an actionable nonzero result. Reusing an already valid generation should remain idempotent.

## TP-03 — Revision-only cache declarations can omit changed text

**Priority: medium where revision discipline is not enforced. Confidence: high for behavior. Classification: conditional contract risk, not a violation of the documented equality rule.**

The [declared-cache contract](docs/agent-contract.md#L557) presents context reduction as provably safe, then defines `--assume-known ID@REV` in terms of equality with the current authored revision. That equality proves the label matches; it does not establish that the client has the current content associated with that label.

[`_build_packet_plans`](src/docsystem/cli.py#L2884) intentionally omits navigation when `view.revision == declared`. This branch does not compare a previously observed content hash. The separate `--since` branch compares source hashes at [line 2835](src/docsystem/cli.py#L2835).

### Observed behavior

The diagnostic created `DOC-001` at revision 1, indexed it, and retained the generation ID. It then changed its navigation from `Old guidance.` to `Updated guidance.` without changing the revision.

| Operation after that source edit | Observed result |
| --- | --- |
| `validate` | Return 0; valid Markdown navigation |
| Fresh `context DOC-001 --json` | Includes `Updated guidance.` |
| `context DOC-001 --assume-known DOC-001@1 --json` | Omits navigation with `reason: assumed-known`; mismatch list empty |
| `context DOC-001 --since <previous-generation> --json` | Includes `Updated guidance.` |

The engine saw the changed source; this was not a stale projection serving an old document. The omission decision was based on the unchanged revision value.

### Interpretation and limits

The output correctly identifies the omission as `assumed-known`. The command is behaving as its explicit equality rule describes. A project that reliably changes revisions whenever relevant content changes can deliberately accept this lightweight contract.

The required assumption is an immutable association between a document revision and its content. Revision discipline is not entirely undocumented: the [knowledge promotion policy](docs/knowledge-promotion.md#L23) prescribes incrementing the revision when revising living knowledge. However, the inspected cache path does not enforce that historical association, and ordinary validation accepted the synthetic edit. An agent can truthfully declare the revision it previously cached while holding outdated content if an author reused that revision. This is therefore a limitation in what the token proves, not necessarily misuse of the CLI. The unconditional [README safety wording](README.md#L754) should be read with that assumption made explicit.

This matters for the goal of reusable knowledge consumed by recurring agents: recommendations can change between runs, and omitted guidance may be the information the agent needed to refresh. The risk is conditional on revision management; it should not be counted as a universal failure of context delivery.

### Recommended architectural direction

State explicitly that `--assume-known` trusts the project's revision-to-content discipline. Distinguish that guarantee from content-hash comparison in both integration guidance and packet semantics. A future content-bound token could identify the document plus a content digest or verified generation, while retaining revision declarations as the lightweight mode.

The existing `--since` mechanism already provides a stronger content-comparison option when the prior generation is retained. Its separately reported text-normalization limitation in AR-03 still applies; this pass only demonstrates detection of an ordinary textual edit. A global revision registry is not required merely to clarify the contract.

**Future acceptance evidence:** unchanged revision with changed navigation, changed H2 content, and changed semantic metadata; explicit section inclusion; same-label declarations under any future content-bound mode. Tests should make the accepted trust boundary visible rather than merely repeat revision equality.

## Snapshot consistency: boundary checked, no additional defect claimed

[`build_catalog`](src/docsystem/catalog.py#L226) reads source files sequentially. Projection freshness verification likewise reads included source files sequentially in [`load_verified_projection`](src/docsystem/projection.py#L667). Atomic publication of a derived generation is distinct from taking a transactional snapshot of several mutable source files.

The public architecture already assigns coordination of multiple projection writers to the caller and documents the fallback path during retention races. This review did not reproduce an invalid mixed packet or a concurrency failure, and does not claim that the local engine must provide filesystem-wide transactions. The source-read boundary should nevertheless be explicit for a future application with simultaneous authors: an immutable checkout or caller-controlled read window can supply stronger consistency without moving orchestration into this core.

## Implications for product boundaries

The current core is well placed to supply documents, stable references, context and evidence to another product. A discussion or recommendation application can own deliberation, practical-experience aggregation, recommendation status and external-source provenance. An orchestrator can own execution and coordination. None of the three findings is a reason to make the document engine decide whether advice is correct or to embed external community behavior in its core.

The useful next separation is between precise guarantees: authored truth, verified derived content, client-declared knowledge, completed-write undo and interrupted-write recovery. Keeping these distinct makes the existing package easier to compose without implying stronger guarantees than each operation provides.

## Verification and change record

| Check | Result |
| --- | --- |
| Abrupt child termination after first replacement | Reproduced pending generation, partial source state and recovery refusal |
| Abrupt child termination during validation | Reproduced pending generation, fully changed source and recovery refusal |
| Corrupt projection followed by unchanged-source rebuild | Reproduced return 0 with persistent corruption |
| Content edit retaining revision | Reproduced assumed-known omission; fresh and generation-delta reads included the edit |
| Independent static challenge | Completed; recovery findings supported, cache finding qualified; experiments not rerun |
| Report links, source-line references and whitespace | Passed |
| `git diff --check` | Passed |
| Pre-existing reports, `AGENTS.md` and original orchestration binding | Preserved; SHA-256 comparisons matched |
| Full pytest suite | Not run; no implementation change |
| Ruff lint | Not run; no Python source change |
| Packaging, provider integration and external-service checks | Not run; outside this report's scope |
| Power-loss and concurrent-writer stress tests | Not run; no claim of those guarantees |

Structural checks covered the new, untracked report as well as `git diff --check` for tracked changes. Suggested future tests and architectural changes above are recommendations, not implemented fixes.
