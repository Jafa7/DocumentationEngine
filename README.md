# Documentation Engine

[![CI](https://github.com/Jafa7/DocumentationEngine/actions/workflows/ci.yml/badge.svg)](https://github.com/Jafa7/DocumentationEngine/actions/workflows/ci.yml)

Documentation Engine is a provider-neutral toolkit for maintaining structured
Markdown knowledge that remains usable by humans and AI clients as a project
grows.

The published `documentation-engine` package is pre-1.0. Paradigmarium remains
the first real-project integration fixture for its adoption contracts.

## Complete access. Task-sized reads.

> **Every cataloged document remains fully available to the agent.**
> Documentation Engine reads original Markdown in structured, task-relevant
> fragments and expands to related sections or complete documents on demand.

"Task-sized" means selected according to the current task, document
relationships, and explicit requests—not compressed, summarized, or truncated
to fit an arbitrary token budget. Sections outside the initial read are listed
explicitly and remain available by anchor or as complete documents. An
operation that claims a complete dependency answer fails closed when that
completeness cannot be established.

The graph measures how much original documentation had to be loaded initially
for three predefined tasks over a real 6.41 MB legacy Markdown corpus
containing 292 documents. Each task retained access to the entire catalog and
passed its predefined document and section coverage requirements. Lower
initial reading is better when those quality requirements remain satisfied.

![Documentation context read for one task](docs/assets/context-reduction.svg)

The efficiency comes from structured, on-demand reading rather than rewriting
source material:

| Read on demand instead of upfront | Quality safeguard |
| --- | --- |
| Unrelated documents | Selected source Markdown verbatim |
| Unrequested sections | Explicit omitted-section list and addressable anchors |
| Repeated full-corpus reads | Stable IDs, revisions and dependency coverage |
| Unproven dependency results | A fail-closed error instead of silent incompleteness |

Omitted content is neither destroyed nor hidden. It remains in the Markdown
source of truth and can be requested by section anchor or as a complete
document.

| Scenario | Required documents | Required sections | Packet | Corpus read | Reduction |
| --- | ---: | ---: | ---: | ---: | ---: |
| Architecture analysis | 4 | 6 | 120.7 KB | 1.88% | 98.12% |
| Roadmap phase | 4 | 7 | 123.6 KB | 1.93% | 98.07% |
| Research continuation | 4 | 9 | 60.2 KB | 0.94% | 99.06% |

Each task passed a quality guard: every predefined required document and
section was present, and every selected document carried an explicit coverage
line. Navigation excerpts and requested sections remained verbatim Markdown.
The smaller packet comes from excluding unrelated documents and unrequested
sections, not from rewriting or degrading the selected material.
The baseline is deliberately specific: reading every Markdown byte, not every
possible manual or competing retrieval strategy. UTF-8 bytes are a
deterministic provider-neutral proxy for context volume, not tokenizer-specific
token counts or a claim about total engineering productivity.

See [the measurement methodology](docs/context-efficiency.md) for the corpus,
shadow-overlay, formulas, quality checks and limitations. The chart is a
static measured snapshot; it does not publish an unqualified forecast for
larger corpora.

## Get started

### Installation

Choose one installation path. Do not run both published-package commands.

#### CLI and Python package

For local command-line use and Python imports:

```bash
pip install documentation-engine
```

#### CLI with MCP support

If an MCP host will launch Documentation Engine as a local stdio server, use
the optional extra instead:

```bash
pip install "documentation-engine[mcp]"
```

The MCP extra includes the base package. It does not create or host a shared
documentation service; it adds the SDK needed for an MCP client to launch the
local `docsystem-mcp` adapter. See
[the MCP adapter guide](docs/mcp-adapter.md) for host configuration and the
security boundary.

#### Contributor or unreleased checkout

Contributors and anyone intentionally tracking unreleased development should
use the repository checkout rather than the published package:

```bash
git clone https://github.com/Jafa7/DocumentationEngine.git
cd DocumentationEngine
uv sync
```

Run development commands through `uv run`; do not mix this checkout with an
unrelated globally installed `docsystem` executable.

#### Verify the selected installation

For a published-package installation:

```bash
docsystem --help
```

For a contributor checkout:

```bash
uv run python -m docsystem --help
```

The distribution is `documentation-engine`; the import package, `docsystem`
and `docsystem-mcp` console scripts, and `.docsystem.toml`/`.docsystem/`
project files keep their existing names. Installation only makes the commands
available and does not modify a project. Continue with
[Connecting Documentation Engine to your project](#connecting-documentation-engine-to-your-project)
to configure one deliberately.

### Connecting Documentation Engine to your project

Connecting is project configuration, not package installation. It means
choosing the documentation root and privacy boundary, reviewing or creating
`.docsystem.toml`, validating the existing Markdown, and then writing the
disposable projection. It does not require MCP.

#### Agent-guided setup

**If you are an AI agent** asked to connect a project: follow
[docs/setup-guide.md](docs/setup-guide.md) step by step. It contains the
connection/adoption flow, required user questions, backup-policy setup and
checks.
Do not improvise a local backup path or commit private planning paths.

**If you are a human using an AI agent**, paste this in the project you want to
connect:

```text
Connect Documentation Engine to this project.
Repository: https://github.com/Jafa7/DocumentationEngine
Read docs/setup-guide.md in that repository and follow it exactly.
Ask me where local disaster-recovery backups should be stored before touching
ignored/private documentation or local configuration.
```

#### Manual setup

For a manual adoption, follow the same
[setup guide](docs/setup-guide.md). Start with its fact-gathering and backup
steps rather than running `init` blindly against an existing documentation
tree. The compact readiness command will then report the next safe action:

```bash
docsystem readiness /path/to/project
```

#### Independent local documentation sources

If public documentation stays in product repositories while private profiles
live in one local directory, use [workspace source selection](docs/workspace-sources.md)
to register those independent profiles and address one by name. This removes
machine-specific paths from routine commands without claiming a federated
cross-project graph.

## Principles

- Markdown is the editable source of truth.
- Stable IDs survive file moves and title changes.
- Generated indexes are deterministic projections, never a second truth.
- Human navigation and machine retrieval use the same dependency model.
- Context selection exposes omissions instead of silently truncating meaning.
- Mechanical maintenance is automated; semantic decisions remain reviewable.
- AI integrations are adapters around a provider-neutral core.

For work that splits into a new chat, module, repository or long-running idea,
use the [Workstream / Idea Branching](docs/workstream-branching.md) pattern so
the child context carries its inherited context, boundaries and return
protocol.

For problems found while adopting Documentation Engine in another project, use
the [adopter reporting](docs/adopter-reporting.md) policy and issue templates.
Reports start with compact diagnostics and sanitized evidence, not private
document bodies or unbounded logs.

## Documentation

Use the [documentation map](docs/README.md) to find the canonical guide for
setup, adoption, architecture, agent behavior, integrations, safety, reporting
and releases. Specialized guides link back to their owning contract instead of
repeating it.

## Development and release verification

Contributor workflow and risk-based checks are defined in
[CONTRIBUTING.md](CONTRIBUTING.md). Packaging and publication are defined in
[the release guide](docs/releasing.md). The contributor checkout uses
`uv run python -m docsystem ...`; published-package consumers use the installed
`docsystem` command. `scripts/installed_cli_smoke.sh` verifies that boundary.

## CLI overview

```bash
docsystem init .
docsystem doctor .
docsystem show-config .
docsystem catalog .
docsystem catalog . --explain
docsystem catalog . --explain --json
docsystem validate .
docsystem validate . --verbose-adoption
docsystem read DOC-001 .
docsystem read DOC-001 . --list
docsystem read DOC-001 . --anchor purpose
docsystem dependencies DOC-001 .
docsystem dependencies DOC-001 . --reverse
docsystem references DOC-001 .
docsystem references DOC-001#purpose . --reverse
docsystem references DOC-001 . --transitive
docsystem references DOC-001 . --json
docsystem change-plan DOC-001 .
docsystem change-plan DOC-001#purpose . --reverse
docsystem change-plan DOC-001 . --transitive
docsystem change-plan DOC-001 . --json
docsystem maintenance install-version . --check
docsystem maintenance install-version . --preview
docsystem maintenance install-version . --check --json
docsystem maintenance install-version . --preview --expect-source-hash SHA256
docsystem maintenance install-version . --write --expect-source-hash SHA256 --workstream-id WS-001
docsystem maintenance-recover 20260714T100000Z-WS-001 .
docsystem context DOC-001 . --depth 1
docsystem context DOC-001 . --depth 1 --json
docsystem context DOC-001 . --compact --json
docsystem context DOC-001 . --outline
docsystem context DOC-001 . --outline --json
docsystem context DOC-001 . --view task --json
docsystem context DOC-001 . --assume-known DOC-001@3
docsystem context DOC-001 . --since 0a1b2c3d4e5f
docsystem impact DOC-001 .
docsystem graph-health .
docsystem graph-health . --json
docsystem criteria .
docsystem criteria . --json
docsystem workstream WS-001 . --record workstream-record.json
docsystem workstream WS-001 . --record workstream-record.json --json
docsystem intake . --request idea-intake-request.json --json
docsystem migration-report .
docsystem migration-report . --json
docsystem readiness .
docsystem readiness . --json
docsystem finish DOC-001 .
docsystem finish DOC-001 . --json
docsystem finish DOC-001 . --context-expansion material-gap --context-gap-report drafted
docsystem finish WS-001 . --workstream-record workstream-record.json --json
docsystem report draft . --project-name "My Project" --type adoption-finding --source codex
docsystem report context-gap . --project-name "My Project" --type adoption-finding --source codex --reason missing_dependency --initial DOC-001#summary --expanded DOC-002#constraints --impact decision
docsystem migrate .
docsystem migrate . --apply
docsystem index . --write
docsystem changes .
docsystem changes . --json
docsystem agent-instructions .
docsystem agent-instructions . --json
docsystem workspace list . --workspace /path/to/workspace
docsystem workspace doctor . --workspace /path/to/workspace
docsystem context DOC-001 . --source example-project
```

`init` creates a project-local `.docsystem.toml` and the configured
documentation root. It does not create empty documentation hierarchies.

`catalog` lists Markdown source files under paths mapped by logical roles in
`[areas]`. `catalog --explain` classifies every Markdown file as included,
excluded or unmapped. Unmapped Markdown is a validation error rather than a
silent omission.

Catalog exclusions are optional, ordered POSIX globs relative to the
documentation root:

```toml
[catalog]
exclude = ["templates/*-template.md"]
```

The first matching pattern is reported as the exclusion reason. An area mapped
to `.` owns root documents and acts as a fallback when a more specific area
does not match. `validate` requires each included document to be linked from
the nearest `README.md` or `index.md`; nested indexes must themselves be linked
from the nearest parent index. `doctor` includes membership, navigation and
metadata validation.

Every cataloged Markdown document starts with YAML front matter containing a
stable `id` and positive `revision`. Semantic relations use stable IDs:
`derived_from`, `depends_on`, `related` and `supersedes` contain ID lists;
`validated_against` contains `ID@revision` freshness pins. Unknown fields are
preserved for project-specific policy. Duplicate YAML mapping keys are invalid
at every nesting level.

`read` resolves a whole document, navigation prefix or ATX section by stable
ID. `read --list` emits `anchor`, `Hn`, `start:end` and `title` as tab-separated
fields in document order.

A heading may declare a stable canonical anchor on the immediately preceding
line:

```html
<a id="stable-section"></a>
## Section title
```

`name` is also accepted, as are single quotes. The standalone tag may contain
only the `id` or `name` attribute. Anchor values start with a Unicode
alphanumeric character and then use Unicode alphanumerics or `-_.:`. The value
is preserved exactly. Malformed, orphaned, multiple, duplicate or colliding
anchors are errors rather than silently repaired.

Navigation may extend the default prefix through the furthest matching H2:

```toml
[navigation]
extend_through = ["summary", "contents"]
```

If no configured anchor exists in a document, the original prefix before the
first H2 is returned. A configured anchor resolving to another heading level
is an error.

Projects may define progressive, purpose-specific authored context views:

```toml
[context.views.map]
tier = 1
delivery = "outline"
direction = "both"
depth = 0
relations = []
layers = ["authored"]

[context.views.task]
tier = 2
delivery = "navigation"
direction = "forward"
depth = 1
relations = ["depends_on", "derived_from", "validated_against"]
layers = ["authored"]
```

`context ID PROJECT --view NAME` applies that policy and emits the selected
view plus every edge stopped by `relation-filter` or `depth-limit`. View names
and tiers are stable project policy. The only currently supported layer is
`authored`; observed links and generated containment remain available through
`references` but cannot silently enter a context view. A view replaces manual
`--depth`, `--include-related` and `--outline`; navigation views still allow
explicit `--include ID#anchor`. A view controls the initial packet, not access:
agents can always request another section or the full Markdown source.

`dependencies` reports deterministic forward or reverse semantic edges.
It fails without partial stdout when metadata errors make the requested graph
incomplete; stale revision warnings remain non-blocking.

`graph-health PROJECT [--json]` gives an inspectable whole-catalog inventory:
document and section counts, edges by authority/relation, explicit boundaries,
weak components, orphans, stale/historical pins and missing configured
metadata. Metrics are facts; smell signals such as hubs, concentrated
boundaries or disconnected components appear only when the project enables a
threshold in `[graph_health]`; objectively missing Markdown anchors are always
identified as dead references. Signals are advisory and never grant write
authority. A structurally ambiguous graph fails closed with no partial stdout.
See [graph health](docs/graph-health.md) for the configuration and output
contract.

`references ID[#anchor] PROJECT` is a read-only inspection of the section and
reference graph: authored metadata relations, observed Markdown links, and
generated section containment, each tagged with its `authority` so an
observed link or a generated containment edge is never mistaken for write
permission. `--reverse` lists incoming edges, `--transitive` expands beyond
direct neighbors, and `--json` carries the same rows structurally. Unknown
addresses and metadata errors that make the requested graph ambiguous fail
closed with no stdout; unresolved targets (external URLs, non-document
resources, and missing anchors) remain visible, non-blocking boundaries.
Relation-specific cycle diagnostics (`depends_on`, `derived_from` and
`supersedes` are errors; `related` and observed `references` cycles are
allowed) surface through `doctor`/`validate`, which cover the whole graph,
rather than blocking an individual `references` call. It prefers a verified
projection and reads only the shards a query touches, falling back to direct
Markdown with one diagnostic on any staleness or corruption — either path
produces byte-identical stdout.

`change-plan ID[#anchor] PROJECT` is a read-only explainable change plan built
on the same graph: the requested document or section is always a `read` item
at distance 0; an authored `depends_on` edge adds its target as `read`;
everything else an agent should look at before changing the target — observed
Markdown references, `--reverse` incoming impact, and authored `related`,
`derived_from`, `supersedes` or `validated_against` edges — is `review`, never
a promoted `read`. Generated section containment can prove a transitive path
to a section-owned reference, but generated sections are not emitted as plan
items and never expand a whole document by default. When more
than one edge reaches the same address, the plan keeps one item and lists
every distinct reason instead of picking one arbitrarily. `--transitive`
expands both directions with a deterministic minimal proving path per reason.
Completeness is reported per graph layer (`authored`, `observed`, `generated`)
instead of one collapsing flag, and unresolved targets remain visible
boundaries with their source address. There is no `--write`/`--apply`: like
`references`, this command never edits Markdown.

`maintenance TARGET PROJECT --check|--preview|--write` manages one
project-declared `[[maintenance]]` target: a canonical
`source_document`/`source_anchor` block and its bounded `occurrences`, each
with an opt-in role (`current`, `historical`, `example`, `snapshot` or
`unmanaged`). Only a `current` occurrence is preview eligible; every other
role is reported as excluded evidence, never diffed. The block is delimited
by exact `<!-- docsystem:source target=NAME -->`/
`<!-- docsystem:managed target=NAME -->` HTML-comment markers, one per line
and inert inside fenced code. `--check` and `--preview` are read-only and
print the same deterministic result; `--check` exits `0` clean or
`2` on drift (a documented, non-error code), while `--preview` always exits
`0` for a valid target. Invalid config, an unknown target/address or a
graph-blocking error exit `1` with empty stdout. Reports include exact line
ranges and content hashes for review and stale-input guards.
Pass a previously reported source `block_hash` with `--expect-source-hash` to
fail closed if the canonical block changed between inspection steps.
`--write` additionally requires `--workstream-id`; it changes only drifted
`current` block interiors in one journaled transaction, validates the rebuilt
catalog/graph, and rolls back every touched file on failure. Other roles are
never written. A successful write refreshes the disposable projection;
refresh failure is a visible warning and direct Markdown remains authoritative.
`maintenance-recover GENERATION` restores verified before
bytes only when current files still equal that generation's after state, so
newer authored work is never overwritten.

Existing projects may opt into a migration bridge for relative path relations:

```toml
[relations]
legacy_paths = "resolve-with-warning"
snapshot_types = ["review", "experiment"]
snapshot_rules = [
  { source_type = "roadmap", source_status = "completed" },
]
```

Strict stable-ID relations remain the default. In the compatibility mode,
resolvable paths become canonical graph edges and emit migration warnings.
External URLs, resources and paths outside the catalog are never document
relations, so they remain explicit, non-blocking boundaries in both `strict`
and `resolve-with-warning` mode. A relative path that *does* resolve to a
cataloged document is a real document relation: in `strict` mode it is a
blocking error until it is migrated to a stable ID or the project opts into
`resolve-with-warning`. `migration-report` reports both resolved mappings and
boundaries as a deterministic dry-run, independent of the current
`relations.legacy_paths` mode, without editing Markdown.

`snapshot_types` classifies every pin owned by the listed document types as
historical. `snapshot_rules` is the narrower alternative: each rule matches
the pin-owning document by optional `source_type`, `source_status`, or both.
Matched stale pins remain visible as `historical snapshot` in context, impact
and finish packets, but are not freshness warnings in `validate`, `doctor` or
`readiness`. Unmatched pins retain normal stale-warning behavior.

By default, `validate` and `doctor` summarize expected resolved mappings and
resource boundaries by count while printing stale pins and other warnings
individually. Pass `--verbose-adoption` to either command for every row-level
adoption warning. `migration-report` always remains the complete deterministic
inventory.

`readiness` is a read-only report for adopting an existing Markdown project.
It distinguishes blocking structural/configuration errors, resolvable legacy
relation migrations, explicit unresolved/resource boundaries, stale freshness
pins and projection state (absent/stale/current), and prints the single safe
next command. It never writes to Markdown, configuration or the projection
cache.

`finish` produces a compact handoff packet for returning a workstream or
document-focused task to its parent context. It summarizes included context,
omitted H2 sections, migration boundaries and stale versus historical snapshot
pins. A project can opt into a stricter, versioned completion contract with
`workstreams.criteria`, inspect it with `criteria`, validate a bounded JSON
lifecycle/evidence record with `workstream` and require that record through
`finish --workstream-record`. The strict gate preserves corrective attempts,
requires configured check/review/change/omission/risk/return evidence and
fails without a handoff when the completion claim is incomplete. See
[bounded workstream evidence](docs/workstream-evidence.md).

`intake` evaluates a bounded, agent-prepared semantic request against a
versioned project criterion. It deterministically selects one existing owner,
proposes an ordinary draft or workstream, or returns an explainable blocked
decision. The command is read-only: a proposed ID/path is not reserved and no
Markdown is created. The engine validates addresses and policy but does not
pretend to infer the human idea or verify an agent's semantic claims. See
[deterministic idea intake](docs/idea-intake.md).

`report draft` produces a privacy-safe GitHub issue body for adopter
runtime reports, adoption findings, core bugs or documentation pattern
requests; it is read-only and leaves expected/actual/requested-action fields
for the reporter to fill in.

When progressive reading exposes a reproducible context-coverage gap that
materially changes the work, `report context-gap` adds body-free initial and
expanded address evidence to the same adopter report format. Ordinary
full-review, follow-up and precautionary reads remain normal behavior and are
not reported. The agent always retains complete access to authored Markdown;
the feature measures gaps in task-sized delivery rather than limiting source
access.

An agent can carry only the classification back in `finish`: `normal` requires
no report, while `material-gap` requires a `drafted` or `filed` report state.
The default handoff stays byte-compatible when no classification is supplied.

`agent-instructions` prints a deterministic Markdown snippet — naming the
configured documentation root, language, areas and identifier namespaces plus
the configured purpose views and core agent rules (pass the project root
explicitly, start with `readiness --json`, prefer `--json`, use the lowest
suitable view, expand context deliberately, never run a mutating command
without approval, follow local backup policy) — for pasting
into an adopting project's `AGENTS.md`/`CLAUDE.md`. It is read-only, reads
only project configuration and works even when the documentation root itself
is missing, so the pasted snippet can never drift from
`docs/setup-guide.md` Step 7 by hand-copying.

`readiness`, `migration-report`, `catalog --explain`, `changes`, `context`,
`criteria`, `workstream`, `intake` and `agent-instructions` accept `--json` and
print one
deterministic JSON value
(sorted keys, stable field names) instead of text, carrying the same
information the text form prints plus what it sends to stderr, so a machine
client never has to parse human prose. Every `--json` root is an object
carrying `"schema_version": 1`; the version is bumped only on a breaking
change to an existing field, so a consumer can detect format evolution
without guessing. Exit codes are unchanged by `--json`. `context --outline`
(with or without `--json`) prints section size maps instead of content for
the same selected document set, so an agent can budget tokens with a cheap
map before fetching.

An MCP adapter exposes the read-only commands as typed tools for any
MCP-capable client; see [the MCP adapter guide](docs/mcp-adapter.md). It is a
thin wrapper over this CLI contract and requires the optional MCP installation
described in [Installation](#cli-with-mcp-support). Text tools keep exact CLI
stdout for compatibility; packet variants add non-fatal diagnostics such as
projection fallback warnings.

`migrate` previews, by default, every legacy relation value that
`migration-report` already classifies as unambiguously resolved. Preview is
read-only. `migrate --apply` re-validates the same plan against a scratch copy
of the documentation tree and then rewrites only the exact resolved scalar in
`derived_from`, `depends_on`, `related` or `supersedes` for each affected
document — front matter formatting, comments, unknown fields, the document
body and unresolved boundaries are left byte-for-byte untouched. Multi-file
runs are all-or-nothing: if validation or a write fails, no file is left
partially migrated. Re-running `migrate --apply` after a successful migration
reports no further changes. Once every resolvable legacy relation has been
migrated, a project whose remaining legacy values are all boundaries (URLs and
resources) can drop `relations.legacy_paths = resolve-with-warning` and use
`strict` mode without those boundaries becoming errors.

`context` emits a deterministic Markdown packet containing navigation excerpts,
semantic dependencies, explicit section selections, H2 coverage, omissions,
stale pins and unresolved boundaries. It never silently truncates to a token
budget. The packet ends with a `Packet stats` section reporting how many
documents and explicit sections were included, how many H2 sections were
omitted, and the line/byte size of the packet body above it, so a client can
budget a follow-up `--depth` or `--include` expansion without re-measuring
the output. `context --json` additionally exposes each document's typed
`revision` and lists `"sections"`: each section's `anchor`, `title`, `level`,
`lines` and exact UTF-8 `bytes` size, in document order, so a client can retain
the revision for a later `--assume-known` call and budget without fetching
content first. The existing `navigation` field keeps its complete Markdown
prefix, including YAML front matter. `context --outline` prints the same
document set (`--depth` and `--include-related` still apply) with those
section size tables instead of navigation excerpts or content — a cheap
"map first, fetch second" packet.
`context --compact` is the lossless content-delivery form for agents. It merges
overlapping navigation, parent-section and child-section line ranges, so each
source range appears once. Every requested address remains in
`content_manifest` with its reasons, source line range, carrier fragment and
delivery classification; `content_fragments` contains the original Markdown
and its SHA-256. Document-level `inclusion_reasons` preserve every graph path
that selected the document. Compact text aggregates repeated nonblocking
adoption/view diagnostics and points to `--json`, which retains every row;
stale/historical pins and unresolved boundaries remain individual. Compact
delivery does not summarize content, impose a token budget or restrict later
reads, and it cannot combine with outline delivery. Compact JSON assumes the
outline-first workflow and omits the repeated full `sections` size map; it
remains available through `--outline` or `read --list`.
When `--view NAME` is selected, JSON adds `purpose_view` and deterministic
`view_omissions`; text packets show the same policy and omission rows. Reverse
view inclusions are labeled `reverse:RELATION`, so downstream context cannot be
mistaken for an authored forward dependency. Reverse and bidirectional views
fail closed unless the complete catalog semantic graph is valid; a broken
document elsewhere could otherwise hide an incoming edge. Without `--view`,
existing text and JSON output remain unchanged.
`--outline` combines with `--json` for the structured form, but not with
`--anchor` or `--include`, which select content the outline never returns.
`context --assume-known ID@REV` (repeatable) lets an agent declare a document
it already holds: when that document lands in the packet and its current
revision still equals `REV`, its navigation excerpt is omitted and its coverage
line becomes `content omitted — declared known at revision REV (current)`,
while `--include ID#anchor` still forces those explicit sections. A stale
declaration (revision moved on) keeps full content and records a mismatch note,
so a declared cache never silently hides a change. `context --since GENERATION`
requests a delta against a retained projection generation (full hash or an
unambiguous prefix of at least twelve characters): unchanged documents are
omitted with an `unchanged since GEN12` coverage line, changed documents keep
navigation and gain a `### Changed section` block per changed H2 not already
covered by navigation (a changed H1 or `navigation.extend_through` H2 is
already served by the navigation excerpt), and a document absent from that
generation is reported as new and served in full. Removed anchors and semantic
metadata before/after values are reported separately, and a source change
outside addressable sections is explicit. A requested `--anchor` or `--include`
still wins over unchanged-content omission. The retained generation is
verified against its manifest, document and reverse shards, active
configuration fingerprint and reconstructed generation hash before it can
authorize any omission. `--since` cannot combine with `--assume-known`, and
neither combines with `--outline`; every rejected combination fails closed
with no packet. `impact` reports reverse
metadata dependencies and distinguishes
semantic, related-navigation, freshness and configured historical-snapshot
relations.

`index --write` derives immutable generations below `.docsystem/cache`,
hashed over both the derived content and a fingerprint of the projection-
relevant configuration, then atomically selects the current generation.
`index` checks freshness and `changes` reports changed documents and sections.
`read`, `context` and `impact` serve from the verified projection when it is
current: verification re-hashes every included source byte-for-byte, checks the
configuration fingerprint, and reconstructs the generation hash from the shards,
so a served read can never disagree with the Markdown truth or the active
configuration, while Markdown, metadata and link parsing plus graph
reconstruction are skipped. When the projection is absent, stale, corrupt or
incompatible, reads visibly fall back to direct Markdown with a stderr
diagnostic and identical output. Markdown remains the only editable truth.

See [the adoption guide](docs/adoption.md) for a complete profile and migration
sequence, [the Paradigmarium integration guide](docs/paradigmarium-integration.md)
for downstream consumer guidance, and [the agent contract](docs/agent-contract.md)
for how an AI client should safely drive this CLI.
Projects that keep private documentation or local configuration outside git
should also define a local backup command; see
[local state safety](docs/local-state-safety.md).

## Deliberate project-local boundaries

Registry synchronization, finish orchestration, private history/backup and
provider-specific adapters are not generalized by this vertical slice. They
remain project-local until reusable contracts are proven.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for contributor setup and required
checks, and [SECURITY.md](SECURITY.md) to report a vulnerability.

## License

Documentation Engine is available under the [MIT License](LICENSE).
Copyright (c) 2026 Oleg Synelnykov (Jafa7).
