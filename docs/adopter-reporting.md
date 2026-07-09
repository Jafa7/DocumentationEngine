# Adopter reporting

DocumentationEngine is meant to be reused inside projects that may contain
private planning documents, generated projection state and local-only
configuration. Reports from adopter projects should therefore be actionable
without copying private document bodies into GitHub issues.

This guide defines the shared reporting policy for humans and AI agents that
find a DocumentationEngine problem while adopting or operating it in another
project.

## Report types

Use one of four issue templates:

- `runtime-report` — local setup, installation, PATH, virtual environment,
  MCP adapter, projection cache, readiness, doctor, validate, index, changes
  or other `docsystem` execution problems in an adopter project.
- `adoption-finding` — compatibility and workflow gaps discovered during
  adoption: catalog include/exclude policy, legacy relation mapping,
  navigation expectations, anchors, direct-vs-indexed behavior, snapshot
  assumptions, profile configuration, migration guidance or context/impact
  workflow fit.
- `core-bug` — deterministic DocumentationEngine defects reproducible outside
  the private adopter corpus, such as parser, metadata, section, dependency
  graph, projection, context, MCP or CLI bugs.
- `docs-pattern-request` — reusable documentation patterns, templates,
  workflow conventions or privacy-safe reporting guidance that should become
  product documentation. This is intentionally separate from defects.

When in doubt, use `adoption-finding` for real-project fit and `core-bug` only
when a minimal public or synthetic fixture can reproduce the defect.

## Privacy rules

Every adopter-facing report must follow these rules:

- Do not paste private document bodies.
- Do not paste private scratch, review, roadmap or planning content.
- Sanitize profile and configuration excerpts; remove secrets and private
  personal paths unless the path itself is the bug.
- Prefer stable IDs, anchors, counts and short synthetic snippets over private
  prose.
- Local artifact paths may be included as pointers for the reporter's audit
  trail, but maintainers must not be expected to access files from another
  machine.
- Full generated projections, MCP context payloads and unbounded logs should
  not be pasted. Include compact counts or the smallest relevant excerpt.

For a `core-bug`, prefer a minimal synthetic fixture. If the issue was found
inside a private adopter project, reduce it to the smallest public-safe
Markdown/config example before filing.

## Start with compact diagnostics

Run only the commands needed to characterize the problem. The preferred first
step is the read-only draft command:

```bash
docsystem report draft /path/to/project \
  --project-name "Example Project" \
  --type adoption-finding \
  --source codex \
  --component projection \
  --output /tmp/docsystem-report.md
```

The command gathers compact local diagnostics, writes or prints a GitHub issue
body draft, and leaves expected behavior, actual behavior and requested action
for the reporter to complete. It does not create the GitHub issue and does not
mutate Markdown, configuration or generated projection state.

For targeted evidence, prefer JSON where a machine-readable form exists, and
include exit codes:

```bash
docsystem readiness /path/to/project --json
docsystem doctor /path/to/project
docsystem validate /path/to/project
docsystem index /path/to/project
docsystem changes /path/to/project --json
docsystem read DOC-001 /path/to/project --navigation
docsystem context DOC-001 /path/to/project --depth 1 --json
docsystem impact DOC-001 /path/to/project
```

A good report starts with compact machine-readable diagnostics or counts, then
lists exact commands and minimal sanitized excerpts. Avoid narrative-only bug
reports: the maintainer should be able to see what was requested, what failed,
and which information was intentionally omitted for privacy.

## Required fields

Each report should include:

- project or adopter name;
- source host or agent: `codex`, `claude`, `vscode` or `other`;
- DocumentationEngine version or git commit;
- exact command(s) run and exit codes;
- compact diagnostics or counts from relevant commands;
- sanitized profile/config excerpt such as areas, identifiers, catalog policy,
  exclusions, relation policy, navigation settings or projection settings;
- affected stable IDs, anchors, section IDs or sanitized paths when safe;
- expected behavior;
- actual behavior;
- whether private content was omitted or sanitized;
- runtime/local-state changes made after the failure, or `none`;
- requested DocumentationEngine action.

Optional but useful fields include a component, a minimal synthetic fixture,
related generated artifact paths as pointers only, and whether generated state
was modified after the failure.

## Label convention

Reports should use stable, low-cardinality labels:

- report type: `runtime-report`, `adoption-finding`, `core-bug`,
  `docs-pattern-request`;
- lifecycle: `triage`;
- origin: `project:<slug>`, `source:codex`, `source:claude`,
  `source:vscode`;
- components: `component:catalog`, `component:metadata`,
  `component:sections`, `component:relations`, `component:graph`,
  `component:navigation`, `component:anchors`, `component:projection`,
  `component:context`, `component:mcp`, `component:cli`,
  `component:adoption`, `component:profiles`, `component:readiness`,
  `component:reporting`, `component:setup`, `component:local-state`,
  `component:privacy`.

Do not create labels for every command, file name, local path or one-off
adopter detail. Keep the labels useful for triage across many projects.

## What this policy deliberately avoids

The reporting templates do not depend on any orchestration runtime, worker
state, wakeup event, receipt or local audit model. A report may come from a
human, Codex, Claude, VS Code or another environment; GitHub author identity is
transport, while `project:<slug>` and `source:<host>` describe the origin.

The report draft command intentionally stays small and deterministic. It is not
an AI summarizer, does not inspect private document bodies beyond compact
diagnostic counts, and does not create GitHub issues on its own:

```bash
docsystem report draft PROJECT \
  --project-name NAME \
  --type runtime-report \
  --source codex \
  --component projection \
  --output /tmp/report.md
```

Issue templates remain the canonical reporting interface; the CLI only drafts a
privacy-safe body for those templates.
