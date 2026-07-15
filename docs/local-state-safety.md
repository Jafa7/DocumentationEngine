# Local state safety

Documentation Engine intentionally works with project-local files that may be
outside git. A downstream project can keep private planning, local
configuration, projection caches and orchestration state out of the public
repository, but those files are still part of the real working state. They
must be protected before broad or risky operations.

## Local-only state

Common local-only paths include:

- the configured documentation root when it is private and ignored, such as
  `plan/`;
- `.docsystem.toml`, when a project keeps its Documentation Engine profile
  local;
- `.docsystem/`, including projection cache generations;
- project-local orchestration/runtime directories such as `.orchestrator/` or
  `.project-runtime/`;
- provider-local files such as `.claude/`, `.codex/` or other adapter state;
- uncommitted work in tracked files.

Git does not protect ignored files. A clean git status is therefore not enough
evidence that a destructive operation is safe.

## Required project backup command

An adopting project should define its own backup command in local agent policy,
outside tracked public documentation. The command must create a timestamped
snapshot of the project before risky work starts.

During installation or adoption, an AI agent must ask the user where local
backups should be stored before it writes any local backup policy. The agent
must not invent a personal path, reuse a path from another project, or embed a
machine-specific path in tracked documentation. Once the user chooses a
location, the agent should create a project-local, ignored policy file for
future agents, for example `.agents/local/backup-policy.md`.

Recommended setup question:

```text
Where should this project store local disaster-recovery backups for ignored
documentation/configuration/runtime files?
```

The answer should be recorded only in local, non-versioned agent policy.

Example local policy text:

```text
Before risky or broad filesystem operations, run:

backup-project PROJECT_NAME

The backup must include private/ignored documentation, local configuration,
projection cache, orchestration state and uncommitted tracked changes. It may
exclude reproducible caches and environments such as .venv/, __pycache__/,
.pytest_cache/, .ruff_cache/, node_modules/, dist/ and build/.
```

The concrete backup destination is local infrastructure. It should not be
embedded in a reusable repository unless that repository is private to one
machine/user. Public documentation should describe the contract, not a personal
path.

## When backup is mandatory

An AI client must create or request a backup before:

- running recursive copy, move, delete or sync commands;
- applying migrations or generated rewrites to local-only documentation;
- changing `.docsystem.toml` or the configured documentation root;
- deleting or recreating projection/runtime directories;
- running shell snippets whose argument quoting crosses OS boundaries;
- modifying multiple files when any affected file is ignored by git.

Read-only commands such as `validate`, `doctor`, `catalog`, `read`,
`dependencies`, `context`, `impact`, `migration-report`, `readiness`,
`changes` and `index` without `--write` do not require a backup by themselves.

## Snapshot requirements

A safe backup should:

- be timestamped rather than an in-place mirror;
- avoid delete/mirror semantics unless the user explicitly asks for them;
- write to a temporary/incomplete directory first, then publish the completed
  snapshot;
- include a small manifest with source path, timestamp, git HEAD, git status
  and excluded patterns;
- be restorable without needing network APIs or AI-provider state.

Documentation Engine validates documentation structure and projection
freshness, but it is not a backup system. The adopting project or orchestrator
is responsible for providing the concrete backup command and teaching its
agents to run it before risky local-state changes.

## Adoption checklist

Before an agent treats Documentation Engine as installed for a project with
local-only state, it should verify:

- the user selected a backup destination;
- a local, ignored backup policy exists;
- the policy names the command future agents should run;
- the backup command has been smoke-tested at least once, preferably with a
  dry run followed by one real timestamped snapshot;
- tracked public documentation contains only the portable contract, not the
  user's private backup path.
