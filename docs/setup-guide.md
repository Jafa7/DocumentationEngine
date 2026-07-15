# Setup guide

Audience: an AI agent (or a human) asked to connect Documentation Engine to an
existing project. Follow the steps in order. Every step ends with a check; do
not continue past a failing check — fix it or report the blocker to the user.

## What you are setting up

Documentation Engine gives a project a stable Markdown documentation contract:

- project-local `.docsystem.toml` configuration;
- a configured documentation root inside or outside the repository;
- stable document IDs and relation metadata;
- deterministic validation, context packets and projection cache;
- local-only backup policy for private/ignored documentation state.

The engine does not own the target project's private planning content and does
not choose a user's backup destination. It provides the contract and CLI; the
adopting project owns its local policy.

## Step 0 — Gather facts

Establish these before touching files. Ask the user rather than guessing.

1. **Project root** — absolute path of the project to adopt.
2. **Documentation root** — whether private Markdown should remain inside the
   repository or use a dedicated external project directory, and its exact
   absolute path. Recommend the external option for private documentation, but
   do not invent or create a shared workspace path.
3. **Language** — language of private/local documentation.
4. **Public vs. private split** — which docs are allowed to be committed and
   which must remain local/ignored.
5. **Backup destination** — where local disaster-recovery snapshots should be
   stored for ignored documentation/configuration/runtime files.

Before creating or moving private documentation, ask these placement questions
explicitly and in English:

```text
Where should this project's private documentation be stored?

The recommended option is a dedicated directory outside the project repository,
for example:

<external-documentation-root>/projects/<project-slug>/

Please provide the exact path. I will not create, move, replace, or delete any
documentation until the location and migration procedure are explicitly
confirmed.

Does private documentation already exist inside the project, for example in
plan/? If so, I will copy and verify it first. The original directory will
remain untouched until you explicitly authorize its removal.
```

The path above is a neutral placeholder, not a directory to create literally.
The user may select an in-repository, local, mounted or network location. Do
not treat a parent directory as readable authority: an external project root
authorizes only that exact scope unless the user separately authorizes another
path.

Ask the backup question separately:

```text
Where should this project store local disaster-recovery backups for ignored
documentation/configuration/runtime files?
```

Do not invent this path. Do not copy a path from another project. Do not write
the user's concrete backup path into tracked public documentation.

## Step 1 — Install the engine

Choose and complete exactly one path from the README's canonical
[Installation](../README.md#installation) section. Ordinary adopters use the
published CLI package. MCP is optional and does not turn Documentation Engine
into a shared remote service. Contributor and unreleased checkouts use the
separate source path documented there.

Do not mix a source checkout with an unrelated globally installed `docsystem`,
and do not use an ad-hoc `PYTHONPATH` as an installation substitute.

**Check:** complete the verification command for the selected installation
path in the README and record whether this adoption uses the published CLI,
the MCP extra or a contributor checkout.

## Step 2 — Inspect the target project

Run read-only inspection first:

```bash
git -C /path/to/project status --short --ignored=matching
find /path/to/project -maxdepth 3 -type f -name '*.md' | sort
```

Identify whether the intended documentation root already exists and whether it
is tracked or ignored. If important local-only files already exist, make a
backup before any copy/move/delete/migration command.

**Check:** report to the user:

- selected project root;
- selected documentation root;
- whether the documentation root is tracked or ignored;
- whether a backup policy already exists.

## Step 3 — Configure local backup policy

Before mutating ignored documentation or local configuration, create a
project-local ignored policy file such as:

```text
/path/to/project/.agents/local/backup-policy.md
```

Also ensure `.agents/local/` is ignored, preferably through the local git
exclude file so the personal path cannot be committed by accident:

```bash
mkdir -p /path/to/project/.agents/local
grep -qx '/.agents/local/' /path/to/project/.git/info/exclude 2>/dev/null ||
  printf '/.agents/local/\n' >> /path/to/project/.git/info/exclude
```

The `grep` guard keeps the step idempotent: re-running setup never appends
duplicate exclude lines.

Write the user's selected backup command/path into that local policy, not into
tracked public docs. A project may use any command as long as it creates a
timestamped snapshot that includes private/ignored documentation,
`.docsystem.toml`, `.docsystem/`, runtime/orchestration state and uncommitted
tracked work.

**Check:**

```bash
git -C /path/to/project check-ignore -v .agents/local/backup-policy.md
```

Then smoke-test the backup command with a dry run if available, followed by one
real timestamped snapshot before risky work. See
[`docs/local-state-safety.md`](local-state-safety.md) for the portable backup
contract.

## Step 4 — Create or review `.docsystem.toml`

If the project has no configuration and the user approves bootstrapping:

```bash
docsystem init /path/to/project
```

If the project already has Markdown, prefer writing/reviewing
`.docsystem.toml` deliberately rather than accepting defaults blindly. Typical
shape:

```toml
version = 1

[documentation]
root = "plan"
language = "en"

[areas]
workspace = "."
foundation = "foundation"
architecture = "architecture"
roadmap = "roadmap"

[identifiers]
document = "DOC"
decision = "DEC"
roadmap = "RM"

[projection]
format = "sharded-json"
keep_generations = 2
```

Whole-graph smell policy is optional and should reflect the adopting project,
not copied thresholds. With no thresholds, `graph-health` still reports the
complete deterministic inventory and emits only intrinsic dead-link evidence:

```toml
[graph_health]
hub_in_degree = 12
boundary_count = 5
max_weak_components = 1
required_metadata = ["type", "status"]
report_orphans = true
```

Thresholds are positive integers. `required_metadata` accepts only `type` and
`status`; omitted settings remain disabled. Signals are advisory and do not
weaken validation or authorize an automated rewrite.

Optional AI-agent context tiers are authored project policy. Start small; do
not copy view names or relations from another project without validating its
workflow:

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

Every view requires all six fields. Names use lowercase letters, digits and
hyphens; tiers are unique integers from 1 through 99; depth is 0 through 5;
direction is `forward`, `reverse` or `both`; delivery is `outline` or
`navigation`. Supported relations are `derived_from`, `depends_on`,
`validated_against`, `related` and `supersedes`. `layers` must currently be
exactly `["authored"]`; this prevents a project from assuming observed or
generated edges are semantic context authority before that layer is supported.

Projects that want deterministic placement of agent-interpreted ideas may add
a versioned `[intake]` policy after the base catalog is stable. The canonical
configuration and trust boundary are in
[deterministic idea intake](idea-intake.md); intake remains disabled when no
criterion is configured.

After a project has stable workstream mandates, it may separately enable the
read-only [bounded execution admission](execution-admission.md) policy for
A0–A2 intents. Do not copy an admission criterion until its actions, risk and
authorization evidence match the adopting project's actual permissions.

For an existing tree that still uses relative path relations, add the
compatibility bridge only after understanding the migration boundary:

```toml
[relations]
legacy_paths = "resolve-with-warning"
snapshot_types = ["review", "experiment"]
snapshot_rules = [
  { source_type = "roadmap", source_status = "completed" },
]
```

`snapshot_types` is a type-wide historical classification. Prefer a
`snapshot_rules` entry when only a particular lifecycle state is historical.
Every rule is a table with non-empty optional `source_type` and
`source_status`; at least one must be present. Rules match the document that
owns `validated_against`, not the pinned target. A matched stale pin remains
inspectable as historical evidence but does not produce a freshness warning.

**Check:**

```bash
docsystem show-config /path/to/project
docsystem catalog /path/to/project --explain
```

Unmapped Markdown is a validation error; either map the area or intentionally
exclude non-source templates with `[catalog].exclude`.

## Step 5 — Run adoption readiness

Start with the machine-readable report:

```bash
docsystem readiness /path/to/project --json
```

Follow its `next_command`. Do not assume a fixed sequence and do not run
mutating commands unless the user approved them and the backup policy is in
place.

Useful read-only commands during adoption:

```bash
docsystem validate /path/to/project --verbose-adoption
docsystem doctor /path/to/project
docsystem migration-report /path/to/project --json
docsystem migrate /path/to/project
```

Only this form rewrites Markdown:

```bash
docsystem migrate /path/to/project --apply
```

Run it only after backup and user approval.

**Check:** `readiness --json` eventually reports `"ready": true` or reports a
specific blocker you can explain.

## Step 6 — Write the projection

Once validation is clean and migrations are settled:

```bash
docsystem index /path/to/project --write
docsystem index /path/to/project
docsystem changes /path/to/project --json
```

`index --write` writes `.docsystem/cache`. If `.docsystem/` is local-only,
ensure the backup policy includes it.

**Check:** expect `Projection is current` and no unexpected changes.

## Step 7 — Teach future agents

Add a thin instruction to the adopted project's agent instructions file
(`AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md`, or equivalent).
Keep it project-local and adapt paths/placeholders. Generate the snippet
instead of hand-copying it, so it reflects this project's actual configured
areas and identifiers and cannot drift from this guide:

```bash
docsystem agent-instructions /path/to/project
```

Paste its output into the agent instructions file.

When the project configures intake, admission or governed-workstream
criteria, the generated snippet also carries the applicable chronological
cycle: idea placement, admission, saved-packet verification, completion
evidence and strict finish. It omits unconfigured gates instead of inventing
project policy.

**Check:** confirm the instruction file is either tracked intentionally or is a
local provider-specific file the project already uses.

## Step 8 — Final report

Tell the user:

- package/install method;
- project root and documentation root;
- whether local-only state is tracked or ignored;
- backup destination/policy file location (without committing it);
- validation/readiness/index status;
- any remaining migration boundaries or stale pins;
- exact commands future agents should use.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `configuration not found` | Wrong project root or `.docsystem.toml` missing; pass the project root explicitly. |
| `Markdown is not mapped to a configured area` | Add an `[areas]` mapping or exclude non-source Markdown with `[catalog].exclude`. |
| Duplicate or malformed metadata errors | Fix YAML front matter before migration/indexing. |
| Relative path relations fail in strict mode | Use `migration-report`; migrate real document relations to stable IDs or temporarily opt into `resolve-with-warning`. |
| `Projection is stale` | Run `docsystem index PROJECT --write` after validating source changes. |
| Agent wants to edit ignored `plan/` without backup | Stop. Ask for backup destination or run the configured local backup command first. |
