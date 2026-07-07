# Setup guide

Audience: an AI agent (or a human) asked to connect Documentation Engine to an
existing project. Follow the steps in order. Every step ends with a check; do
not continue past a failing check — fix it or report the blocker to the user.

## What you are setting up

Documentation Engine gives a project a stable Markdown documentation contract:

- project-local `.docsystem.toml` configuration;
- a configured documentation root such as `plan/`;
- stable document IDs and relation metadata;
- deterministic validation, context packets and projection cache;
- local-only backup policy for private/ignored documentation state.

The engine does not own the target project's private planning content and does
not choose a user's backup destination. It provides the contract and CLI; the
adopting project owns its local policy.

## Step 0 — Gather facts

Establish these before touching files. Ask the user rather than guessing.

1. **Project root** — absolute path of the project to adopt.
2. **Documentation root** — where the project's Markdown knowledge should live
   (for example `plan/`, `docs/plan/` or an existing private docs folder).
3. **Language** — language of private/local documentation.
4. **Public vs. private split** — which docs are allowed to be committed and
   which must remain local/ignored.
5. **Backup destination** — where local disaster-recovery snapshots should be
   stored for ignored documentation/configuration/runtime files.

Ask the backup question explicitly:

```text
Where should this project store local disaster-recovery backups for ignored
documentation/configuration/runtime files?
```

Do not invent this path. Do not copy a path from another project. Do not write
the user's concrete backup path into tracked public documentation.

## Step 1 — Install the engine

If the user gave only the repository URL, clone and install it:

```bash
git clone https://github.com/Jafa7/DocumentationEngine.git
cd DocumentationEngine
pip install .
```

Use `pip install -e .` if the user wants to track engine updates from git.

**Check:**

```bash
docsystem --help
```

If `docsystem` is not on PATH, use the same interpreter that installed the
package:

```bash
python -m docsystem --help
```

Prefer a real install over ad-hoc `PYTHONPATH`; adopting projects should call
the installed console script, not import this repository's `src/` directly.

If you are contributing changes to the Documentation Engine repository itself
from Windows, stage and commit from inside WSL. Do not use Windows Git over
`\\wsl.localhost`; it can drop executable bits on scripts such as
`scripts/installed_cli_smoke.sh` and break CI. Verify:

```bash
git ls-files --stage scripts/installed_cli_smoke.sh
test -x scripts/installed_cli_smoke.sh
```

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
printf '\n/.agents/local/\n' >> /path/to/project/.git/info/exclude
```

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

For an existing tree that still uses relative path relations, add the
compatibility bridge only after understanding the migration boundary:

```toml
[relations]
legacy_paths = "resolve-with-warning"
snapshot_types = ["review", "experiment"]
```

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
Keep it project-local and adapt paths/placeholders:

```markdown
## Documentation with Documentation Engine

Use `docsystem` for structured Markdown documentation. Always pass the project
root explicitly; do not rely on the current working directory.

Start with read-only commands:

- `docsystem readiness <project-root> --json`
- `docsystem validate <project-root>`
- `docsystem doctor <project-root>`
- `docsystem context DOCUMENT_ID <project-root> --depth 1`

Before mutating ignored/local-only documentation, `.docsystem.toml`,
`.docsystem/` or runtime state, read `.agents/local/backup-policy.md` and run
the local backup command. If the policy is missing, stop and ask the user where
backups should be stored.

Do not commit private planning content or local backup paths unless the user
explicitly says those files are public/tracked.
```

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
