# Changelog

All notable changes to Documentation Engine are documented in this file.

## [Unreleased]

### Added

- An ignored `.docsystem.project.local.toml` pointer can route ordinary
  project commands to one validated external documentation root without
  exposing its absolute path in generated agent instructions. The generated
  contract limits trusted agents to that exact private scope and forbids
  parent/sibling discovery unless separately authorized.

- Source-qualified bounded maintenance for local workspaces: sources default
  to `write = "none"` and may opt into `managed-maintenance`; selected writes
  require a deterministic reviewed preview hash, selected recovery requires
  the immutable journal manifest hash, authority evidence is source-local,
  and a non-blocking journal lock prevents concurrent transactions without
  claiming cross-source atomicity.

- Authored `program_plan` sequencing with fail-closed `roadmap status`,
  `roadmap next` and `roadmap explain` CLI/MCP inspection. Recommendations
  derive lifecycle state from bounded roadmap documents, prerequisites and
  explicit priority without reading bodies or granting execution authority;
  deferred ideas remain visible and never produce a false complete state.
- Workspace-owned federated projection generations reuse unchanged per-source
  objects, verify all registered source/config/Markdown inputs and accelerate
  complete federation queries without writing source caches.

- Read-only multi-catalog federation with qualified `source::ID[#anchor]`
  identities, authored cross-source relations, complete catalog/dependency/
  reference/context/impact CLI queries and matching thin MCP tools.
- Projection schema 4 preserves qualified relation boundaries on the
  single-source direct and projected paths; older generations fall back safely
  and can be rebuilt from Markdown.

- Versioned, project-authored workstream completion criteria.
- Read-only `criteria` and `workstream` commands for deterministic lifecycle,
  correction and bounded evidence validation.
- Optional `finish --workstream-record` gate and matching read-only MCP tools.
- Versioned idea-intake placement policy, bounded request validation and the
  read-only `intake` CLI/MCP command with explainable blocked decisions.
- Versioned A0–A2 execution-admission policy and read-only `admission` CLI/MCP
  evaluation for bounded workstream targets, actions, risk and authorization
  evidence.
- Read-only `execution-handoff` CLI/MCP packet generation and verification with
  mandate/target hashes, section ranges, graph impact, visible completeness and
  no embedded authored bodies.
- Generated agent instructions now present configured intake, admission,
  immutable handoff verification and evidence-gated finish in execution order.
- Optional admission source scopes bind local pre-edit paths/hashes, while the
  read-only `execution-result` CLI/MCP contract validates structured returned
  changed-file evidence without claiming to observe external writes.
- Read-only `metadata-inventory` CLI/MCP inspection reports observed metadata
  coverage, YAML types and body-free per-document graph facts while hiding
  additional values unless one field is explicitly requested.
- Optional project-authored document profiles and read-only `profile-check`
  CLI/MCP validation cover metadata, semantic anchor roles, relation/status
  allowlists and history-mode evidence without inferred policy or source writes.
- Optional delivery traceability metadata and read-only `delivery-map` CLI/MCP
  inspection connect exact source-section contracts to delivery ownership and
  completion evidence without reading or returning authored bodies.
- Repeatable targeted delivery-contract lookup reports bounded mappings and
  explicit unowned contracts, with matching MCP and generated-agent guidance.
- Opt-in delivery-aware change plans add owner and completion evidence as a
  separate review-only layer, with default-output compatibility and MCP parity.
- Read-only `lifecycle` CLI/MCP validation composes an admission request,
  host-persisted execution packet, authoritative changed-file result and
  completed workstream record into one fail-closed, body-free evidence lineage.

### Changed

- `workspace list` rows and JSON now expose each source's body-free write
  policy so operators can inspect the default-deny boundary.

- Replaced the named adopter integration guide and CI profile with a synthetic
  client-integration contract and generic adopter fixture.

## [0.2.0] - 2026-07-13

### Highlights

- Local workspace source selection lets one checkout address an independent
  public or private Documentation Engine profile by a stable source name,
  without committing machine-specific absolute paths.
- A strict `workspace.toml` registry and ignored `.docsystem.local.toml`
  pointer provide deterministic discovery through explicit CLI options, an
  environment variable or local project wiring.
- New read-only `workspace list` and `workspace doctor` commands report source
  visibility and availability without reading document bodies or exposing
  local paths.
- Existing project commands and MCP tools can select one registered source;
  explicit selection fails closed and never falls back to the positional
  project.
- Source roots must be contained, unique and non-overlapping. Writable
  documentation and projection paths cannot escape through symlinks, and
  malformed or looping paths produce bounded diagnostics instead of stack
  traces.
- Existing single-project CLI and MCP behavior remains unchanged when no
  source is selected. This release deliberately does not claim cross-source
  graph federation, remote storage, synchronization or authorization.

## [0.1.2] - 2026-07-13

### Fixed

- The release gate now removes the `.gitignore` marker generated by `uv build`
  before asserting and uploading the exact wheel-and-sdist artifact set. The
  `v0.1.1` candidate stopped at this gate before any artifact was uploaded to
  TestPyPI or PyPI; version `0.1.2` is the replacement release candidate.

## [0.1.1] - 2026-07-12

### Highlights

- Canonical PyPI distribution identity: the distribution to be published is
  named `documentation-engine`. The import package (`docsystem`), console
  commands (`docsystem`, `docsystem-mcp`) and project files
  (`.docsystem.toml`, `.docsystem/`) are unchanged.
- Single source of truth for the package version: `pyproject.toml` now
  derives `version` dynamically from `src/docsystem/__init__.py` instead of
  duplicating it.
- Release automation prepared: a tag-triggered Trusted Publishing workflow
  builds the distributions once, verifies that the tag matches the package
  version, publishes to TestPyPI, checks the published SHA-256 digests against
  the built artifact, installs the result, and only then offers the same bytes
  to PyPI behind a manual approval gate. See
  [the release guide](docs/releasing.md).
- CI runs on Node 24-capable action majors, declares least-privilege
  permissions, and checks the sdist as well as the wheel.

This entry documents packaging identity and release automation. Documentation
Engine has not been published to any package index yet; `pip install
documentation-engine` starts working only after the first successful release.

## [0.1.0] - 2026-07-11

### Highlights

- Structured Markdown catalogs with stable document IDs, revisions, typed
  dependency graphs and deterministic section addressing.
- Selective `read`, `context` and `impact` workflows that preserve verbatim
  source text and expose omitted context instead of silently truncating it.
- Deterministic sharded projections with integrity checks, change detection and
  safe direct-Markdown fallback.
- Adoption support for existing documentation trees, including catalog
  membership policies, legacy relation diagnostics and migration reports.
- Read-only MCP adapter and structured JSON output for AI-agent integrations.
- Workstream branching, adopter reporting and project handoff patterns.
- Measured context-reduction methodology and a reproducible consumer-install
  smoke test.

[0.2.0]: https://github.com/Jafa7/DocumentationEngine/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/Jafa7/DocumentationEngine/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/Jafa7/DocumentationEngine/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Jafa7/DocumentationEngine/releases/tag/v0.1.0
