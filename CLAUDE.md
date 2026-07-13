# Claude Code instructions

Read and follow [`AGENTS.md`](AGENTS.md) before changing this repository.
`AGENTS.md` is the provider-neutral source of agent rules; this file is only a
thin Claude Code adapter and must not duplicate project architecture.

Use these sources for task context:

- [`README.md`](README.md) — public product behavior and CLI surface;
- [`docs/architecture.md`](docs/architecture.md) — architectural contracts;
- [`docs/adoption.md`](docs/adoption.md) — existing-project adoption workflow;
- the task contract supplied by the orchestrator — scope, acceptance criteria,
  worktree and required checks for the current task.

Work only in the assigned checkout or detached worktree. Do not commit, push,
merge, rebase, or modify another checkout unless the task explicitly requires
it. Do not add Claude-specific behavior to `src/docsystem/`.

Before handing work off, use the risk level and checks required by `AGENTS.md`
and the task contract. Do not default to the full suite for structural-only
documentation changes, and do not broaden a focused gate without a stated
risk reason.

Report changed files, checks, assumptions and blockers. Your report is not
acceptance evidence by itself: an independent reviewer verifies the actual
diff and test results.
