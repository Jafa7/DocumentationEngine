# Agent contract

This document describes how an AI client (a coding agent, an MCP adapter, or
a project-local wrapper) should safely drive the `docsystem` CLI. It is
provider-neutral: it describes the core CLI's contract, not any specific
provider's orchestration.

## Read-only vs. mutating commands

Five operations write anything:

- `docsystem init PROJECT` creates `.docsystem.toml` and the documentation
  root. It refuses to overwrite an existing configuration, but it is a
  bootstrap/mutating command, not a read-only one: an agent must not run it
  without explicit approval, the same as any other command that creates
  files in the target tree.
- `docsystem migrate PROJECT --apply` rewrites resolved legacy relation
  values in place.
- `docsystem index PROJECT --write` writes a new projection generation
  below `.docsystem/cache`.
- `docsystem federation index PROJECT --workspace PATH --write` writes a new
  disposable projection generation below the workspace root, without writing
  any registered source.
- `docsystem maintenance TARGET PROJECT --write` applies only declared,
  drifted `current` managed blocks through an immutable journal.
- `docsystem maintenance-recover GENERATION PROJECT` restores verified before
  bytes and refuses to overwrite any source that no longer equals the
  generation's recorded after state.

Every other command — `doctor`, `show-config`, `catalog`, `validate`, `read`,
`dependencies`, `references`, `change-plan`, `graph-health`, `maintenance` with `--check` or
`--preview`, `context`,
`impact`, `migration-report`, `migrate` without `--apply`, `readiness`,
`finish`, `report draft`, `report context-gap`, and `index`/`changes` without
`--write` — is
read-only. An agent may call any read-only command freely to inspect project
state before deciding whether a mutating command is warranted.

`graph-health` is intended for broad planning, documentation-architecture
review and graph diagnosis. It is not mandatory overhead for every small edit.
Its inventory values are facts; configured smell signals are advisory, do not
authorize edits, and require an agent or human to interpret them in task
context. On graph-blocking structural errors it exits `1`, writes no partial
stdout and reports the blocking diagnostics on stderr. Direct Markdown and a
verified projection produce byte-identical stdout.

`docsystem migrate` without `--apply` is always a preview: it computes and
prints the same plan `--apply` would write, but touches nothing. An agent
should treat `--apply`, `--write`, and the explicit `maintenance-recover`
command as mutating authority signals, and surface that distinction to the
human or calling system before using them, exactly like any other
hard-to-reverse action.

## Protect local-only state before risky work

An adopting project may keep its documentation root, `.docsystem.toml`,
`.docsystem/` projection cache and orchestration/runtime state outside git.
Those files are still source-of-truth operational state. An agent must not
use a clean git status as evidence that broad filesystem operations are safe:
ignored files can be destroyed while git remains clean.

Before recursive copy, move, delete or sync commands; generated migrations;
bulk rewrites; cross-OS shell snippets; or any task that touches ignored
local-only documentation, the agent must run the project's local backup
command or stop and ask the user to run it. The backup command and destination
are project-local policy and should live outside reusable public
documentation. See [`docs/local-state-safety.md`](local-state-safety.md) for
the portable contract a downstream project should implement.

## Preserve branch context when work splits

When a new chat, module, repository or long-running idea is split from a
parent project, create or request a workstream mandate document. Do not rely
on chat memory alone. The mandate should explain why the branch exists, which
context it inherits, its boundaries and non-goals, and how results return to
the parent project. An agent entering a child chat or module must read the
mandate before implementation work. See
[`docs/workstream-branching.md`](workstream-branching.md) and the reusable
template in [`examples/workstream-branch-template.md`](../examples/workstream-branch-template.md).

For a long-running or higher-risk workstream, inspect project-authored
completion policy with `docsystem criteria PROJECT --json`. If the project
uses a bounded record, validate it with `docsystem workstream ID PROJECT
--record RECORD --json` before claiming completion and pass the same record to
`finish --workstream-record`. A valid in-progress record is not completion:
`ready_to_finish` must be true. Never remove failed attempts or findings to
make the record pass; correction lineage is evidence. See
[bounded workstream evidence](workstream-evidence.md).

When a human supplies a new idea, the agent may prepare a bounded intake
request after inspecting plausible stable document or section owners. It must
not claim that Documentation Engine inferred the idea: candidate authority,
semantic signals and assumptions remain agent assertions. Run `docsystem
intake PROJECT --request REQUEST --json`, respect an explainable `blocked`
result, and treat a proposed ID/path as non-reserved. Before any separately
authorized document creation, evaluate again and require the same
`allocation_guard`. See [deterministic idea intake](idea-intake.md).

Before executing an admitted workstream, prepare a bounded intent and run
`docsystem admission ID PROJECT --request REQUEST --json`. Do not execute a
`blocked` result. Treat the returned catalog guard as continuity evidence and
re-evaluate if the catalog or mandate changed. Authorization entries are
assertions that the calling runtime must authenticate; they are not permission
tokens minted by Documentation Engine. The A0–A2 contract never authorizes
commit, push, merge, release, authored deletion or scope expansion. See
[bounded execution admission](execution-admission.md).

After an admitted result, build an immutable execution packet with
`docsystem execution-handoff ID PROJECT --admission REQUEST --json`. The
packet is body-free planning and integrity evidence, not permission to act or
a substitute for reading listed source addresses. Verify the exact saved
packet immediately before execution with `--verify PACKET`; stop if current
Markdown, graph, mandate, policy or admission evidence no longer reproduces
it. Enforce write permissions and authorization outside Documentation Engine.
See [immutable execution handoff](execution-handoff.md).

When the packet carries local source scope, the executor must not change any
other path. After execution, validate an authoritative machine-readable
changed-file inventory with `docsystem execution-result ID PROJECT --packet
PACKET --result RESULT --json` before accepting completion evidence. The core
checks admitted paths and hashes but does not observe writes; caller-declared
inventory must come from a trusted host/runtime diff, not worker prose.

Before the strict finish handoff, validate the complete lineage with
`docsystem lifecycle ID PROJECT --admission REQUEST --packet PACKET --result
RESULT --record RECORD --json`. Use the same immutable packet and completed
record already reviewed by the host. A successful result proves artifact
identity, source-scope completeness, admitted-target coverage and declared
independent review; it does not authenticate the reviewer or approve product
semantics. Stop on any mismatch and never regenerate the pre-execution packet
to fit an after-state. See
[end-to-end workstream lifecycle evidence](workstream-lifecycle.md).

When installing or adopting Documentation Engine for a project that will keep
important state outside git, the agent must ask the user where backups should
be stored and then write that choice only to local ignored policy, such as
`.agents/local/backup-policy.md`. It must not assume a default personal path
or commit the user's backup location to public documentation.

## Always pass the project root explicitly

Every command accepts the project root as a positional argument and only
falls back to the current working directory when it is omitted. That
fallback exists for interactive human use. An agent must always pass the
project root explicitly and must never rely on its own working directory
matching the target project: agent processes are routinely started in
unrelated directories, and an implicit-cwd invocation against the wrong
directory produces a confusing `configuration not found` error at best and
operates on an unintended project at worst.

For a profile registered in a local documentation workspace, an agent must
instead pass the positional discovery root and `--source NAME`. Workspace
resolution is fail-closed: an unknown or unavailable source must never be
replaced with the positional project. The local pointer and absolute workspace
path are private machine wiring and must not be committed. See
[workspace source selection](workspace-sources.md).

## Prefer `--json` over parsing text

`readiness`, `migration-report`, `catalog --explain`, `changes` and
`context` accept `--json` and print one deterministic JSON value instead of
tab-separated or prose text. An agent should use `--json` wherever it needs
to branch on the result programmatically, rather than parsing the default
human-readable output, which is free to add explanatory text over time.
Every `--json` root is an object carrying `"schema_version": 1`; an agent
should check it before assuming field semantics, since it is bumped only on
breaking changes while new fields may appear without a bump. The public
documentation for each command defines its command-specific payload. MCP
clients get the same read-only commands as typed tools via
[the MCP adapter](mcp-adapter.md); it never exposes a mutating command. For
text commands whose exact stdout matters, MCP clients should prefer the
structured packet variants when they also need non-fatal diagnostics such as
projection fallback warnings.

## Exit codes and diagnostics

Every command returns `0` on success (or, for `readiness`, when the project
is ready) and `1` otherwise. Human text output writes `ERROR` and `WARNING`
diagnostics to stderr while stdout carries only the command's data. In
`--json` mode, diagnostics may be carried structurally in the JSON payload
and human stderr diagnostics may be suppressed. An agent should treat a
non-zero exit code as authoritative and should not infer failure or success
from stdout text alone.

## Adoption sequence

For an existing project, `docsystem readiness PROJECT --json` is the
starting point. Its `next_command` field names the single safe next
command for the project's current state — never a source-mutating default.
An agent driving adoption should call `readiness`, follow `next_command`,
and re-check `readiness` after each step, rather than assuming a fixed
command order.

## Boundaries and stable IDs are human decisions

`migration-report` and `readiness` distinguish `resolved` legacy relations
(a relative path that unambiguously maps to a cataloged stable ID) from
`boundary` values (external URLs and resources that are not, and must never
become, document relations). An agent must not invent a stable ID for a
boundary value or silently resolve one; `docsystem migrate` only ever
touches values the engine has already classified as unambiguously resolved.
Stable IDs themselves are project-assigned identity, not something an agent
should generate as a side effect of an unrelated task.

## `references` is navigation evidence, not write authority

`docsystem references ID[#anchor] PROJECT [--reverse] [--transitive] [--json]`
inspects the read-only section/reference graph: authored metadata relations
(`depends_on`, `derived_from`, `related`, `supersedes`), observed Markdown
links, and generated section containment. It never edits Markdown and has no
`--apply`/write variant in this milestone.

Default text output is deterministic, tab-separated rows with a fixed nine
column schema:

```text
kind    relation  authority  origin  distance  class  address  path  reason
```

`kind` is `edge` for a traversal result or `boundary` for a visible unresolved
target; a boundary row puts its source address in `address`, its unresolved raw
target in `path`, and `category: reason` in `reason`. `class` is `direct` (distance 1)
or `transitive` (only present with `--transitive`); `path` is the
proving-path address chain, source-to-target, joined with ` -> `.
`authority` is one of `authored` (explicit metadata), `observed` (a Markdown
link), or `generated` (section containment); none of them grants write
permission by itself, and observed/generated edges never imply that a
target should be changed. `--json` prints one object carrying
`"schema_version": 1` plus `address`, `reverse`, `transitive`, `results`
(one entry per edge, mirroring the
text columns), `boundaries`, and `completeness`. The authored layer is
`complete` after validation. The observed layer is `complete` when all targets
resolve, `bounded` when every unresolved target is explicitly listed, and
`unknown` for reverse queries because an unresolved link cannot prove its
intended target. An agent should treat `boundaries` and non-`error`
diagnostics as visible context, not as a reason to invent an edge or a
write target.

An unknown document ID, an unknown or malformed anchor, or a metadata error
that makes the requested graph ambiguous (for example a duplicate document ID
or an unresolved reference target, mirroring `dependencies`) fails closed:
exit `1`, no stdout, and one precise `ERROR` line per blocking diagnostic on
stderr. Relation-specific
cycle diagnostics are a corpus-wide check, not a per-query one, so they
surface through `doctor`/`validate` rather than blocking an individual
`references` call: `depends_on`, `derived_from` and `supersedes` cycles are
reported there as errors, while a `related` cycle or an observed `references`
cycle is allowed navigation evidence.

`references` prefers a verified, generation-bound projection and reads only
the shards its query actually touches; it transparently falls back to
direct Markdown when the projection is absent, stale, incompatible with the
current configuration, or fails per-shard integrity verification. Either
path produces byte-identical stdout for the same query. A fallback always
prints exactly one `NOTE` diagnostic to stderr — an agent should treat that
note as informational, not as a query failure.

## Delivery ownership is task-sized evidence, not permission

`docsystem delivery-map PROJECT --contract ID#anchor [--contract ...] --json`
looks up project-authored delivery ownership for exact source sections. Prefer
this bounded form before opening roadmap documents or the complete delivery
inventory. It returns only matching owner/evidence rows plus explicit
`unowned_contracts`; an unowned result does not authorize the agent to invent
an owner, mapping or implementation status.

Every request must name an existing canonical section address. Invalid
requests fail with exit `1`, a precise stderr diagnostic and no stdout.
Malformed authored traceability anywhere in the catalog also blocks targeted
results because the engine cannot prove that a broken claim is unrelated.
Neither full nor targeted delivery inspection reads bodies, modifies Markdown
or promotes evidence into write authority.

## `change-plan` is a read/review plan, never write authority

`docsystem change-plan ID[#anchor] PROJECT [--reverse] [--transitive]
[--with-delivery] [--json]`
builds an explainable, read-only change plan on top of the same section/
reference graph `references` inspects. It has no `--write`/`--apply` variant
in this milestone: every item is either `read` or `review`, and no edge --
however authored -- grants write permission by itself.

For an exact section address, `--with-delivery` adds a separate delivery review
layer without changing ordinary plan items. The layer reports `unconfigured`,
`unowned`, `owned` or `overlap` and lists each owner document and exact evidence
section with disposition `review`. Active and completed owners remain
review-only; historical evidence is not refreshed or rewritten. Document-only
targets are rejected because delivery contracts are section addresses. Invalid
authored traceability blocks the combined plan with no partial stdout.

The requested document or section is always a `read` item at distance 0. An
authored `depends_on` edge from the target adds its direct or transitive
target as `read`. Everything else stays `review`: observed Markdown
references, `--reverse` incoming impact (including an authored `depends_on`
edge pointing *at* the target -- being depended upon is impact, not a
mandatory read), and authored `related`, `derived_from`, `supersedes` or
`validated_against` edges, which are provenance, lineage or opt-in navigation
rather than a semantic dependency. `--reverse` *adds* incoming scope on top of
the forward plan rather than replacing it; `--transitive` expands whichever
direction(s) are active beyond direct neighbors, and the two flags combine.
Generated section containment may appear inside a transitive proving path so
section-owned references remain discoverable, but generated sections are not
emitted as plan items and never expand a whole document into the plan by
default.

When more than one edge reaches the same address, `change-plan` keeps exactly
one plan item and lists every distinct `(relation, authority, origin)` reason
that reached it, instead of discarding alternate evidence the way a plain
shortest-path search would. An item is `read` if any of its reasons alone
would make it `read`.

Default text output is deterministic, tab-separated rows with a fixed
eleven-column schema:

```text
kind    address  disposition  scope  relation  authority  origin  distance  class  path  reason
```

`kind` is `item` for one plan-item reason, `boundary` for a visible unresolved
target, or `completeness` for one graph-layer state. With delivery enabled,
`delivery-state` reports its classification and `delivery` reports review-only
owner/evidence addresses; a row's unused columns
are `-`. An `item` row's `scope` is `target` (the distance-0 request),
`forward` or `reverse`; `class` is `target`, `direct` (distance 1) or
`transitive`. A `boundary` row mirrors `references`: `address` carries the
source, `path` the unresolved raw target, and `reason` a `category: reason`
pair. A `completeness` row reuses `address` for the layer name (`authored`,
`observed` or `generated`) and `reason` for its state. `--json` prints one
object carrying `"schema_version": 1` plus `address`, `reverse`, `transitive`,
`items` (one entry per address with `address`, `disposition` and a `reasons`
list carrying `scope`, `relation`, `authority`, `origin`, `distance`, `direct`,
`path` and `detail`), `boundaries` (same shape as `references`), and
`completeness`. The optional JSON `delivery` object carries `contract`,
`configured`, `state` and body-free `items`; it is absent without the flag so
the default payload remains compatible.

`completeness` never collapses to one boolean: `authored`, `observed` and
`authored` and `observed` use `complete`, `bounded` (visible, every unresolved
target explicitly listed as a boundary), or `unknown` (reverse-observed
evidence cannot prove there is no further incoming link). `generated` is
`not-enumerated`: containment can prove a transitive path, but generated
section nodes are intentionally absent from plan items. An agent that needs
full section navigation should follow up with `references` or `context`.
An unknown document ID, an
unknown or malformed anchor, or a metadata error that makes the requested
graph ambiguous fails closed exactly like `references`: exit `1`, no stdout,
one precise `ERROR` line per blocking diagnostic on stderr.

`change-plan` shares `references`' projection strategy: it prefers a verified,
generation-bound projection and reads only the shards its query actually
touches, falling back to direct Markdown with exactly one `NOTE` diagnostic on
staleness, incompatibility or corruption. Either path produces byte-identical
stdout for the same query.

## Managed maintenance requires bounded write authority

`docsystem maintenance TARGET PROJECT --check|--preview|--write [--json]
[--expect-source-hash SHA256] [--expect-preview-hash SHA256]` reports
drift between one project-declared canonical source block and its declared
occurrences. Check and preview are read-only. Write is an explicit mutating
mode and must not be inferred from drift alone.

A target is declared in `.docsystem.toml` as `[[maintenance]]`: one
`source_document`/`source_anchor` owns the canonical block, and a bounded
`occurrences` list names every document/section that may hold a replica, each
with one role — `current`, `historical`, `example`, `snapshot` or
`unmanaged`. Only a `current` occurrence is preview eligible and can be
`clean` or `drifted`; every other role is reported as excluded evidence with
its role as the reason and is never diffed, however similar its text. The
canonical block and every replica are delimited by exact
`<!-- docsystem:source target=NAME -->`/`<!-- docsystem:managed target=NAME -->`
HTML-comment marker pairs (each on its own line; inert inside fenced code);
markers themselves are never part of the compared payload. Comparison is
exact over the engine's decoded Markdown text. The shared Markdown reader
canonicalizes platform line endings to `\n`; it does not summarize, reflow or
otherwise rewrite the block's semantic content.

`--check` and `--preview` report the identical deterministic result; only the
exit code differs. `--check` returns `0` for a clean target and `2` — a
documented, non-error drift code — when any current occurrence has drifted,
so it composes as a CI gate. `--preview` always returns `0` for a valid
target, clean or drifted, since previewing is inspection, not a pass/fail
check. Invalid configuration, an unknown target, an unknown or ambiguous
document/section/marker address, and a graph-blocking metadata error are all
errors: exit `1`, one diagnostic line per blocker on stderr, and empty
stdout — an agent should never try to parse stdout after a non-zero,
non-`2` exit.

Each report carries exact section, marker and content line ranges. It also
carries document/section/block hashes for the source and every eligible
occurrence, plus a deterministic unified diff for a drifted occurrence. This
is the evidence `--write` acts on. Every item also links to a
read-only `change-plan` view of the canonical source address, the same
explainable graph evidence `change-plan` exposes elsewhere; an agent should
treat it as planning context, not as permission to edit the occurrence
directly. `maintenance` shares `change-plan`'s projection strategy: it prefers
a verified projection and falls back to direct Markdown with exactly one
visible stderr diagnostic on absence, staleness or corruption, producing
byte-identical stdout either way.

For a multi-step continuation, pass the source `block_hash` from the previous
report as `--expect-source-hash`. A malformed hash or a source block that has
changed since that report fails closed with exit `1`, stderr diagnostics and
empty stdout. This guard does not grant write authority by itself.

`--write` requires both that hash and `--workstream-id`. When `--source NAME`
selects a workspace source, the source must additionally declare
`write = "managed-maintenance"`, and the write requires the exact
`preview_sha256` returned by a reviewed selected-source preview. That hash
binds the source, workspace manifest, policy, project configuration,
declaration, source/occurrence evidence, expected after hashes and graph
completeness. Any drift fails with empty stdout before journal creation.

The write re-reads raw files,
admits only marker interiors owned by drifted `current` occurrences, and
applies all admitted files in one immutable journal generation. The canonical
source file is a read guard: if it changes during the transaction, every
occurrence edit is rolled back. Post-write catalog, metadata, section and graph
validation is mandatory; validation failure also restores every touched file
byte-for-byte. Historical, example, snapshot and unmanaged occurrences remain
excluded even when their text matches the source.

After a successful write, the command refreshes the disposable projection.
Projection failure does not undo a validated Markdown transaction: it emits a
warning and subsequent reads safely use direct Markdown until projection state
can be regenerated. Recovery follows the same refresh rule.

The journal normally lives below project-local `.docsystem/journal`. If the
documentation root is the project root, it uses the user state directory
instead so journal evidence never overlaps authored source. Recovery is
explicit: `maintenance-recover GENERATION PROJECT` verifies the immutable
generation and restores only when every current file still equals recorded
after bytes. A newer or unknown state is a refusal, not a forced rollback.
Selected-source recovery also requires `--expect-manifest-hash` with the exact
journal manifest hash and exact source, workspace-manifest, project-config and
opt-in policy authority recorded by that generation. Journal generations carry
this body-free authority plus the preview hash and use a non-blocking
source-local lock. Separate sources never form one atomic transaction.

## Context is explicit, never silently truncated

`docsystem context ID PROJECT` reports exactly what it included (navigation,
explicit sections, dependency traversal) and what it omitted, instead of
truncating to a token budget. An agent that needs more context should expand
the request with `--depth`, `--include-related`, or `--include ID#anchor`
rather than assuming omitted material is irrelevant. The packet's final
`Packet stats` section reports included/omitted counts and the body size in
lines and UTF-8 bytes; an agent should use it to decide whether an expanded
follow-up request fits its budget instead of re-measuring the output.

When the project declares `[context.views.NAME]`, prefer the lowest tier whose
purpose fits the task:

```bash
docsystem context DOC-001 PROJECT --view map --json
docsystem context DOC-001 PROJECT --view task --json
```

A view is authored query policy, not an access boundary. It fixes initial
delivery (`outline` or `navigation`), authored semantic relation filters,
direction and depth. Inspect every `view_omissions` row: `relation-filter`
means the policy excluded that edge, while `depth-limit` means traversal
stopped before following it. Reverse inclusions are labeled
`reverse:RELATION`. Expand with an explicit section or full read whenever
omitted evidence may matter; a navigation view may use `--include ID#anchor`
directly.

Reverse and bidirectional views fail closed on graph-affecting errors anywhere
in the catalog. This is required to prove that no malformed document hid an
incoming authored edge; forward-only views retain source-scoped validation.

`--view` cannot combine with manual `--depth`, `--include-related` or
`--outline`. An outline-delivery view also rejects content selectors and
declared-cache/delta flags. The currently supported view layer is exactly
`authored`; do not infer observed links or generated containment as semantic
dependencies. Default context behavior and output remain unchanged when no
view is selected.

An agent should budget outline-first: run `docsystem context ID PROJECT
--outline` (add `--json` for the structured form) before requesting content.
Outline mode selects the same document set as a normal call (`--depth` and
`--include-related` still apply) but replaces navigation and section content
with a per-section size map — `anchor`, `title`, `level`, `lines` and exact
UTF-8 `bytes` — for every included document, so an agent can see a
document's shape and cost before fetching anything. Once the map shows what
is actually needed, follow up with `--include ID#anchor` or a full (non-
outline) `context` call; `--outline` itself never combines with `--anchor`
or `--include`, since it never returns content. `context --json` (without
`--outline`) also carries this same `sections` size map alongside full
navigation and explicit sections, so a client already fetching content does
not need a separate outline call just to learn section sizes.

After choosing content, prefer `docsystem context ID PROJECT --compact --json`.
Compact delivery merges overlapping navigation/parent/child source ranges and
emits each resulting range once. The client must inspect `content_manifest`:
it preserves every requested stable address, all inclusion reasons and the
fragment carrying its original Markdown bytes. A `covered-by-fragment` entry
means a wider merged range already contains the address; it does not mean the
section was summarized or discarded. `inclusion_reasons`, omissions,
boundaries and freshness rows remain explicit. Compact text may aggregate
repeated nonblocking adoption/view rows, but identifies `--json` as the full
deterministic drill-down; stale/historical pins and unresolved boundaries stay
individual. `--compact` cannot combine with outline delivery and never limits
subsequent `read` or context expansion. Because the agent already chose content
after the outline step, compact JSON omits the repeated full `sections` size
map; request `--outline` or `read --list` again only when that map is needed.

## Declared cache and delta briefings must stay honest

A recurring agent can shrink packets further, but only in ways the engine can
prove safe. When an agent still holds a document from an earlier packet, it may
declare it with `--assume-known ID@REV` (repeatable). The engine omits that
document's content only while its current revision still equals `REV`; if the
document has since moved to a newer revision, the packet includes full content
and a mismatch note instead. An agent must therefore pass the exact revision it
cached and treat a mismatch note as a signal to refresh, never assume a
declared document was omitted for being stale. An explicit `--include
ID#anchor` still wins over a declaration, so an agent can re-request specific
sections it needs verbatim.

When an agent consumed a projection generation earlier and wants only what
changed, it should record that generation hash and later run `--since
GENERATION` (the full hash, or an unambiguous prefix of at least twelve
characters). Before an omission, the engine verifies the retained manifest
against its document and reverse shards, active configuration fingerprint and
reconstructed generation hash. It then omits every document whose source is
byte-identical to that verified generation. For a changed document, navigation
is always served — it already covers everything before the first H2 plus any
`navigation.extend_through` H2s — and every changed H2 outside that prefix is
attached as a full `### Changed section` block, carrying its H3+ descendants
with it (a nested change always changes its enclosing H2's own hash, so
nothing nested is ever served in isolation). A changed H1 or a changed
`extend_through` H2 is never re-emitted as a block since navigation already
carries it, but every changed anchor at any level is still listed in
`changed_sections`, plus a summary of how many documents changed versus were
omitted. Removed anchors are listed separately, semantic metadata changes carry
their previous and current values, and a source change outside addressable
sections is explicitly marked. A document the generation never saw is served
in full. An explicit `--anchor` or `--include ID#anchor` still wins when the
selected document is otherwise unchanged. An agent should trust the omissions
— they are hash-verified against a retained
manifest, not a heuristic — and refetch a full packet only when it needs
context the delta deliberately left out. `--since` and
`--assume-known` cannot be combined, and neither combines with `--outline`;
every rejected combination fails closed with no packet, so an agent never acts
on a partially applied request.

## Report product issues without leaking adopter context

When an agent finds a DocumentationEngine problem while working inside another
project, it should use the shared
[adopter reporting policy](adopter-reporting.md). Start with compact command
diagnostics and counts, include exact commands and exit codes, and sanitize
profile/config excerpts before filing a GitHub issue.

Use `runtime-report` for adopter-side setup or execution problems,
`adoption-finding` for real-project compatibility and workflow gaps,
`core-bug` only for deterministic defects reproducible with a minimal public or
synthetic fixture, and `docs-pattern-request` for reusable documentation
patterns. Do not paste private document bodies, private scratch/review/roadmap
content, full generated projections, MCP context payloads or unbounded logs.

`docsystem report draft PROJECT --project-name NAME --type TYPE --source HOST`
is the preferred first step when available. It is read-only: it gathers compact
diagnostic counts and emits a GitHub issue body draft, but it does not create
the issue or mutate the adopter project.

Context economy never limits access to authored source. If the initial packet
is insufficient, expand immediately with an exact section, explicit include,
greater dependency depth or the full document/corpus required for correctness.
Complete the primary task before entering a non-blocking reporting workflow.

An additional read is normal when the task requires full review, the agent is
checking whole-document consistency, the selected omissions contain the next
needed section, or the read is merely precautionary. Do not report those reads.
If expansion instead exposes a reproducible missing dependency, section,
authority, profile rule, reverse reference, navigation path or runtime context
failure and materially changes the plan, scope, decision, verification or
result, draft body-free evidence with:

```bash
docsystem report context-gap PROJECT \
  --project-name NAME \
  --type adoption-finding \
  --source codex \
  --reason missing_dependency \
  --initial DOC-001#summary \
  --expanded DOC-002#constraints \
  --impact decision
```

The command validates stable addresses and records revisions, section ranges
and projection generation, but never copies document bodies. It deliberately
rejects `task_requires_full_review`, `agent_uncertainty` and
`manual_inspection` as standalone report reasons. Fill only compact coverage
counts, the material effect and a sanitized reproduction before filing an
issue, and create the issue only when external write policy permits it.

Preserve the outcome in the parent handoff without copying the report body:

```bash
docsystem finish DOC-001 PROJECT \
  --context-expansion material-gap \
  --context-gap-report drafted
```

`--context-expansion` accepts `not-observed` (default), `normal` or
`material-gap`. `--context-gap-report` accepts `not-needed` (default),
`drafted` or `filed`. A material gap requires a drafted/filed report; every
other classification requires `not-needed`. Invalid combinations fail before
packet output. Default finish output remains unchanged, while an explicit
classification appears in both Markdown and JSON handoffs.

## Balanced documentation policy

Routine corrective or internal-history detail (a bug fix, a small refactor,
what changed and why for its own sake) belongs in Git history and tests, not
in prose documentation. An observable contract change (CLI surface, output
schema, projection format, public behavior) updates the one existing document
that already owns it, instead of spawning a parallel description. A durable,
hard-to-reverse choice (an identity model, a safety invariant, a naming or
schema policy) is recorded as a decision, not folded into an architecture or
roadmap document. Genuinely multi-step work that needs its own scope,
acceptance and verification uses exactly one bounded roadmap entry, not a
running log. A project owner may opt into recording more detail than this
baseline for their own needs, but that choice can only add detail — it can
never weaken the safety or public-contract documentation this contract and
`AGENTS.md` already require.

## Roadmap lifecycle and delivery evidence

A program roadmap should retain each admitted idea until its disposition is
explicit. Use the lifecycle `planned`, `waiting`, `ready`, `active`,
`delivered` or `deferred`. A `waiting` row names the prerequisite and the
condition that makes it `ready`; an `active` row links its bounded delivery
roadmap; a `delivered` row links the exact stable completion-evidence section.
Do not erase the original idea or replace its delivery evidence with narrative
history.

Use authored `depends_on` for document-level prerequisites and an ordinary
Markdown link to the exact completion anchor for human and observed-graph
navigation. Keep `validated_against` pins at the revisions actually reviewed.
If completed roadmap pins are historical evidence, configure a narrow
`relations.snapshot_rules` type/status match instead of rewriting revisions or
silencing active-roadmap freshness warnings.

Before creating a new bounded roadmap, inspect the authored program with
`docsystem roadmap next PROJECT`. Continue an active milestone before starting
another one. If there is no active milestone, use the lowest project-authored
priority among ready entries; equal-priority entries remain explicit choices
rather than a hidden model preference. Use `roadmap explain MILESTONE PROJECT`
to verify prerequisites, exact source contracts and downstream unlocks.

The recommendation is read-only planning evidence, not permission to execute
or write. A malformed program plan, unknown prerequisite, cycle or inconsistent
roadmap state fails closed. Do not bypass that result by reconstructing an
informal sequence from chat memory.

## Federated workspace reads

When a task depends on more than one registered workspace source, use
`docsystem federation catalog/context/impact` and qualified
`source::ID[#anchor]` addresses. Do not run separate single-source queries and
claim that their union is a complete cross-project graph. A qualified metadata
relation is an explicit boundary in single-source mode and becomes an edge
only after the federation resolves its named source and target ID.

Prefer `docsystem federation index PROJECT --workspace PATH` as a read-only
freshness check. The `--write` form is an explicit derived-state mutation and
requires the same caller authority as single-source `index --write`.
`federation changes` is read-only and reports source-level drift. A current
workspace projection skips repeated parsing but does not relax the all-source
freshness and completeness gate; stale or corrupt state falls back visibly to
the byte-identical direct result.

Federated context preserves source Markdown, lists omitted H2 anchors and
allows exact qualified `--include` expansion. Treat unavailable sources,
invalid catalogs and unresolved qualified relations as hard completeness
failures. A read-only federated result grants no write permission; any future
change must be authorized and journaled independently by the owning source.
