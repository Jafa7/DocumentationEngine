# Documentation Engine architecture review — second pass

Date: 2026-09-12
Package version: 0.5.0
Reviewed source baseline: `3f5f9cc42e1ad4da4b9d6b9fd7387e9f3dcc01f4`
Related assessment: [first architectural review](ARCHITECTURE_REVIEW_2026-09-12.md)

## Assessment

The second pass supports retaining the existing architecture, but changes the immediate emphasis: **consistency between capabilities should take priority over broad code reorganization**. The Markdown source model, explicit graph relationships, verifiable projections and provider-neutral adapters remain appropriate for the published goals. Their most important weakness is that different consumers reconstruct parts of the same semantic or validation model independently.

This review confirmed one profile-validation defect, one ambiguity in command-level validation guarantees, one inconsistent transport-decoding boundary, and repeated full-inventory work during complete provider export. These are more specific than the first report's concerns about module size. They show where architectural boundaries already affect observable behavior.

The findings do not justify a rewrite, a database in the document core, or a mandatory background server. They support shared semantic accessors, explicitly named validation scopes, consistent artifact decoding and preparation of immutable export queries once per operation.

## Scope and independence

This is a second pass by the same reviewer, with new hypotheses and fresh synthetic diagnostics. It is not an independent-agent endorsement of the first report. The source commit is unchanged. Existing changes to `AGENTS.md` and the first report were preserved.

The review concentrated on composition: local versus source-qualified relations, readiness versus the normal validation gate, artifact serialization versus canonical verification, and paged versus complete exports. Relevant implementation and public contracts were inspected. Private planning documents, adopter data and external services were not used.

No implementation, configuration or test files were changed. All runtime diagnostics used disposable synthetic projects under `/tmp`. This report is the only additional repository deliverable. No full test suite or performance benchmark was run.

Priority below describes recommended engineering attention, not security severity. A confirmed behavior can be a deliberate contract choice rather than a defect; those cases are identified explicitly.

## SP-01 — Source-qualified relations disappear from document-profile validation

**Priority: high. Confidence: high. Classification: confirmed behavioral defect.**

The metadata parser stores source-qualified references separately in `DocumentMetadata.federated_references`. However, the profile helpers [`_metadata_fields`](src/docsystem/profiles.py#L59) and [`_document_relations`](src/docsystem/profiles.py#L73) inspect only ordinary references and legacy references. Consequently, profile constraints depend on how a relation target is addressed, even when the authored relation is the same.

The [profile contract](docs/document-profiles.md) states that an explicit empty `allowed_relations` list allows no values and that a required semantic field must contain a valid relation value. It does not exempt qualified references from those rules.

### Observed behavior

A synthetic document of type `spec` was checked against a profile with `allowed_relations = []`:

| Authored metadata | Result of `profile-check --json` |
| --- | --- |
| `depends_on: [DOC-001]` | Exit 1; `relation-not-allowed` |
| `depends_on: ["peer::DOC-001"]` | Exit 0; `valid: true`; no violations |
| Qualified value above, with `required_metadata = ["depends_on"]` also configured | Exit 1; `missing-metadata` for the authored `depends_on` field |

The diagnostic did not connect to a real second project. It established that a syntactically accepted source-qualified relation is absent from the profile's relation inventory. Resolution or availability of its remote target is a separate question; the relation should not silently disappear from local policy evaluation.

### Architectural cause and impact

[`_references`](src/docsystem/metadata.py#L166) has a richer relation model than profile evaluation consumes. A capability added to the common metadata representation has not been incorporated into every downstream semantic helper.

This is directly relevant to the goals of project-authored policy and multi-source documentation. A project can prohibit a relation but receive a successful profile check after only changing the target's address form. Conversely, a required field can be reported absent when it is authored.

**Recommendation:** expose one authoritative relation inventory that preserves relation name, target qualification, resolution state and pin information. Profile presence and allowlist checks should consume that inventory without requiring disclosure of the target body. Keep unresolved-target diagnostics separate from field absence and relation prohibition.

**Future acceptance evidence:** a matrix covering local, qualified, pinned, legacy, empty and malformed relation values against required-field and allowlist rules, including CLI/MCP and ordinary validation entry points. The existing [profile tests](tests/test_profiles.py) exercise local relations and workspace selection, but those are not equivalent to qualified relation values inside a document.

## SP-02 — Readiness and the normal quality gate validate different scopes without naming the distinction in the readiness result

**Priority: medium. Confidence: high. Classification: reproduced contract ambiguity, not proof that all reads or index writes should be blocked.**

[`validate`](src/docsystem/cli.py#L1514) combines catalog validation, graph diagnostics, document profiles, delivery policy and program-plan checks. In contrast, [`evaluate_readiness`](src/docsystem/readiness.py#L68) evaluates a narrower set of catalog/metadata/navigation conditions. [`index_projection`](src/docsystem/cli.py#L4314) also uses the narrower catalog validator.

### Observed behavior

For a structurally sound synthetic catalog containing a local `depends_on` relation forbidden by its configured profile:

| Command | Observed outcome |
| --- | --- |
| `readiness --json` | Exit 0, `ready: true` |
| `validate` | Exit 1, `profile spec: relation-not-allowed` |
| `index --write` | Exit 0, projection created |

The source did not change between those calls. The same authored profile violation therefore coexists with successful readiness and a current projection.

### Interpretation

Adoption readiness, catalog indexability and policy compliance can legitimately differ. Indexing useful documents despite a policy violation may be desirable. The problem is that the machine-facing `ready` flag and suggested next action do not disclose which configured validation dimensions were omitted. A downstream agent may treat readiness or projection freshness as a broader quality endorsement than it is.

The same distinction applies to cycle diagnostics. The [agent contract](docs/agent-contract.md) explicitly makes those corpus-wide checks available through `doctor`/`validate` rather than blocking every individual reference query. That is a deliberate boundary, not a reason to reject all cyclic-document reads.

**Recommendation:** define named validation scopes and expose their coverage. For example, a readiness result could distinguish structural readiness from profile/graph/delivery checks that passed, failed or were not evaluated. Decide deliberately whether a recommendation to index should carry outstanding policy diagnostics. Do not silently widen every query to the full quality gate.

**Future acceptance evidence:** valid structure with invalid profile, invalid program plan, graph-cycle diagnostics and absent optional policy; verify both command outcomes and explicit coverage of the checks that justify each outcome.

## SP-03 — Artifact verification accepts duplicate JSON members before checking the digest

**Priority: medium. Confidence: high. Classification: reproduced interoperability and decoding-policy gap.**

[`load_and_verify_artifact`](src/docsystem/provider_artifact.py#L745) calls ordinary `json.loads` and then verifies the resulting object. Repeated JSON object members are resolved by the parser before [`verify_artifact`](src/docsystem/provider_artifact.py#L703) evaluates the schema and canonical content digest.

### Observed behavior

A valid snapshot artifact was exported. A second `schema_version` member was inserted before the genuine member:

```json
{
  "schema_version": 999,
  "schema_version": 1,
  "artifact_kind": "provider-snapshot-artifact"
}
```

The snippet illustrates the duplicate members; the actual diagnostic retained all other required fields from the complete artifact. The existing digest was not changed. `load_and_verify_artifact` still returned `valid: true`, because the parsed object retained the last `schema_version` value and therefore had the same canonical digest as before.

This is not a SHA-256 collision or signature bypass. The [artifact contract](docs/provider-artifacts.md) correctly describes a digest of canonical content and states that authentication and provenance are separate responsibilities. Whitespace differences also legitimately do not change that digest. The narrower concern is acceptance of a transport representation whose duplicate members can be interpreted differently or rejected by another consumer.

There is already a stricter precedent in the package: [`load_shared_finish_record`](src/docsystem/shared_finish.py#L59) uses [`_reject_duplicate_keys`](src/docsystem/shared_finish.py#L44). YAML metadata also rejects repeated keys. Several other JSON loaders, including [execution packets](src/docsystem/execution.py#L65), use ordinary decoding; this review did not reproduce every loader.

**Recommendation:** define a shared unambiguous JSON input policy for integrity-bearing artifacts and requests. Reject duplicate members recursively before canonicalization. Keep this separate from the documented allowance for unknown optional fields in future-compatible artifacts: unique optional fields and duplicate keys are different compatibility questions.

**Future acceptance evidence:** root and nested duplicates, duplicate digest or scope members, malformed input, and unique unknown optional fields. Confirm that strict decoding preserves the intended additive compatibility policy and deterministic error classes.

## SP-04 — Complete export rebuilds the complete entity inventory for every page

**Priority: medium. Confidence: high for repeated work; no measured latency threshold. Classification: confirmed scalability inefficiency.**

[`snapshot_artifact`](src/docsystem/provider_artifact.py#L150) assembles pages through [`_collect_pages`](src/docsystem/provider_artifact.py#L95). Each callback invokes [`snapshot_response`](src/docsystem/provider.py#L384), which rebuilds `observations(snapshot)` for the whole immutable generation before slicing a page.

The analogous [comparison response](src/docsystem/provider.py#L413) reconstructs both inventories and their comparison each time [complete comparison export](src/docsystem/provider_artifact.py#L200) asks for another page.

### Observed behavior

A synthetic snapshot with 1,104 observations required three pages at the maximum page size of 500. Instrumentation around the existing `observations` function recorded three full inventory constructions during one `snapshot_artifact` call.

| Measurement | Result |
| --- | ---: |
| Complete artifact observations | 1,104 |
| Maximum requested page size | 500 |
| Full inventory constructions during one export | 3 |

For N entities across P pages, inventory work is repeated P times. With a fixed page-size ceiling, that component can approach quadratic growth in N. This is a work-count observation, not a runtime benchmark or a claim that memory consumption is quadratic. Generation loading itself was performed once in this diagnostic.

The current [artifact regression](tests/test_provider_artifact.py#L105) covers complete multi-page output and deterministic verification. That is valuable correctness evidence, but does not constrain how often the same immutable query is prepared.

**Recommendation:** prepare and validate the immutable inventory or comparison once per complete export, then expose a page view over that prepared query. Reuse public contract semantics without repeatedly rebuilding the entire result merely to keep an internal page-shaped call boundary. Preserve ordering, cursor binding, byte limits and complete-or-fail assembly.

**Future acceptance evidence:** operation counts over increasing entity totals, complete/paged result equality, byte-limited pages, empty results and multi-page comparisons. Performance benchmarks can then determine whether additional streaming or memory changes are necessary.

## Reassessment of the first report

| First-pass finding | Second-pass disposition |
| --- | --- |
| AR-01: application logic in the CLI | Supported, with a sharper remedy. Extract ownership of shared semantics and validation before performing a mechanical file split. SP-01 and SP-02 illustrate why. |
| AR-02: migration recovery model | Retained as a static failure-model concern. No new crash or rollback-failure experiment was run; the second pass does not upgrade it to a reproduced crash incident. |
| AR-03: normalized-text hashes | Retained with the existing qualification: physical file identity and normalized text identity differ. This is not evidence of lost prose or an insecure hash algorithm. |
| AR-04: whole-catalog work for small reads | Supported. SP-04 identifies a separate repeated-work issue in complete provider export. Neither review establishes actual latency limits. |
| AR-05: shared configuration failure boundary | Treat as a design tradeoff, not an instruction to ignore invalid optional policy. Address only through an explicit validation-dependency contract. |
| AR-06: MCP execution limits | Supported as an adapter-operability concern. The absence of a timeout does not establish observed host failure and does not justify making a daemon mandatory. |

The first review's positive observations remain valid: a small base dependency set, no explicit intra-package import cycles in the inspected AST graph, useful domain modules, explicit context omissions and strong direct/projected parity intent in tests. These are foundations to preserve, not reasons to overlook cross-capability inconsistencies.

## Recommended order of attention

1. Correct qualified-relation handling in profile checks and add semantic-form parity coverage.
2. Define a consistent strict decoder for integrity-bearing JSON; retain additive field compatibility where the contract allows it.
3. Name and expose the validation scope behind readiness, quality gates and projection status.
4. Prepare complete-export inventories once; measure work counts before broad optimization.
5. Use these concrete boundaries to guide the application-service extraction proposed in the first review.

Migration durability and hash-identity decisions from the first report remain parallel prerequisites for expanding write automation or external traceability guarantees. No implementation or repository reorganization is authorized by this report.

## Verification record

| Activity | Outcome |
| --- | --- |
| Source baseline and existing working-tree inspection | Completed; source commit unchanged |
| Local versus qualified relation diagnostic | Completed; SP-01 reproduced |
| Qualified required-field diagnostic | Completed; SP-01 reproduced |
| Readiness / validate / index comparison | Completed; SP-02 reproduced on unchanged synthetic source |
| Duplicate-member artifact diagnostic | Completed; SP-03 reproduced with the original canonical digest |
| Complete-export inventory-call instrumentation | Completed; SP-04 reproduced, 3 full inventories for 1,104 observations |
| Relevant existing tests and public contracts | Inspected; not claimed to have passed |
| Report links, cited line bounds and Markdown structure | Passed |
| `git diff --check` and new-report whitespace validation | Passed |
| Preservation hashes of `AGENTS.md` and the first report | Unchanged |
| Full pytest / Ruff gate | **Not run** — no implementation or configuration changes |
| Build, lock and installed-consumer checks | **Not run** — packaging inputs unchanged |
| Timing, memory, cancellation and crash experiments | **Not run** |

The diagnostic examples contain only synthetic content. Their temporary files and projections were removed after execution. Model-worker orchestration was not dispatched because the existing project binding still targeted a different host task; its binding and delivery state were left unchanged. This is a scope limitation of reviewer independence, not a defect in Documentation Engine.

## Reproduction recipes

These recipes describe the isolated diagnostics, rather than proposing changes to repository fixtures. Use an existing contributor environment and disposable directories under `/tmp`.

### Profile and validation scope

Create a minimal valid catalog with `DOC-001` as its index linking to `DOC-002`. Map a root area to `.`. Give `DOC-002` the type `spec` and configure:

```toml
[profiles.spec]
document_types = ["spec"]
history_mode = "living"
allowed_relations = []
```

Run `profile-check --json` first with `depends_on: [DOC-001]`, then with `depends_on: ["peer::DOC-001"]`. Add `required_metadata = ["depends_on"]` to the same profile and repeat the qualified case. Finally, restore the local relation and the profile without the required-field rule, then compare `readiness --json`, `validate` and `index --write` on the same temporary project. Expected observed results are recorded in SP-01 and SP-02.

### Complete export and duplicate members

Create a provider-enabled synthetic catalog containing an index and a target with 1,100 H2 sections. Build and retain one generation, then load it once with `load_pinned_projection`. Instrumentation used the existing implementation without changing a source file:

```python
from unittest.mock import patch
import docsystem.provider as provider
from docsystem.provider_artifact import (
    encode_artifact,
    load_and_verify_artifact,
    snapshot_artifact,
)

# snapshot is the pinned synthetic generation; temporary_path is under /tmp.
with patch.object(provider, "observations", wraps=provider.observations) as counter:
    artifact = snapshot_artifact(snapshot)
    print(len(artifact["observations"]), counter.call_count)  # 1104, 3

encoded = encode_artifact(artifact)
ambiguous = encoded.replace("{", '{\n  "schema_version": 999,', 1)
temporary_path.write_text(ambiguous, encoding="utf-8")
print(load_and_verify_artifact(temporary_path)["valid"])  # True
```

The code is a contributor-side diagnostic of implementation behavior, not a recommendation for external consumers to import internal package modules. External integrations should continue to use the documented CLI/artifact contracts.
