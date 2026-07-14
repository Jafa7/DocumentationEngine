# Agent instructions

## Working rules

- Keep product documentation and code comments in English.
- Treat Markdown as source of truth and generated data as disposable.
- Do not add provider-specific behavior to the core package.
- Prefer deterministic scripts for mechanical documentation maintenance.
- Preserve existing project files during bootstrap and migration.
- Every change to configuration behavior requires tests.
- Do not modify, move, delete or replace the private `plan/` tree unless the
  user explicitly authorizes that exact operation. Treat it as local source
  material, not generated state.
- Do not commit, push, merge, rebase or perform destructive Git operations
  unless the user explicitly asks.
- Run git stage/commit operations from inside WSL for this checkout. Do not
  stage or commit from Windows Git over `\\wsl.localhost`; it can drop
  executable bits such as `scripts/installed_cli_smoke.sh` from `100755` to
  `100644` and break CI with `Permission denied`.

## Risk-based verification

Choose the narrowest verification level that covers the change:

- **Structural only:** prose documentation, comments, badges or repository
  metadata with no runtime, contract, packaging or generated-output effect.
  Do not run a test suite. Run relevant structural checks such as validating
  links or TOML/JSON when applicable and always run `git diff --check`.
- **Focused:** an isolated implementation or test change with a clear owning
  module. Run directly affected tests and lint touched code. Configuration
  behavior changes must include and run focused configuration tests.
- **Full:** shared contracts or schemas, CLI behavior, catalog/graph/section
  semantics, projection or workspace safety, dependencies, build/packaging/CI,
  cross-module behavior, release candidates, an explicit user request, or
  uncertainty remaining after focused checks. During implementation use
  focused checks; run the full gate once on the finished candidate:

  ```bash
  TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run pytest
  TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run ruff check .
  git diff --check
  ```

WSL sessions may inherit Windows temp directories; native `/tmp` keeps pytest
capture deterministic. Packaging and release changes additionally require the
lock, build, distribution and installed-consumer checks documented in
`docs/releasing.md`. Do not repeat an already-passing full gate after a later
structural-only edit unless it changes generated artifacts, packaging inputs
or test expectations. Report checks deliberately not run as `not run`; never
imply that an unrun check passed.

Before committing, verify executable scripts that CI runs:

```bash
git ls-files --stage scripts/installed_cli_smoke.sh
test -x scripts/installed_cli_smoke.sh
```

Expected git mode for `scripts/installed_cli_smoke.sh` is `100755`.

## Orchestration with OrchestratorEngine

- Delegate only concrete, bounded work where an AI worker adds value. Use a
  deterministic script or check runner for mechanical work and test execution.
- Before dispatch, confirm that the project binding targets the current host
  chat, classify the task as structural, focused or full, select an appropriate
  enabled worker profile, and write both the complete task contract and a
  `WORKER_TASK_INTENT` JSON file below `.orchestrator/prompts/`.
- Set `intent.verification` before dispatch and pass the intent with
  `worker run --intent-file`. The declared verification level is authoritative:
  generic or copied task prose must not broaden it. If scope changes enough to
  require another level, stop and dispatch corrected intent rather than
  silently changing the gate.
- Dispatch once with a stable task id. Do not spend model turns polling worker
  state and do not dispatch a duplicate merely because a wait timed out.
- For a bounded wait that fits the active Codex turn, prefer one direct
  deterministic wait:

  ```bash
  orchestrator-engine --project-root <project-root> \
    worker wait --task-id TASK-ID --json
  ```

- If ending a Codex turn while work remains active, show the user this
  terminal command before handing off:

  ```bash
  orchestrator-engine --project-root <project-root> \
    worker wait --task-id TASK-ID
  ```

  It reads local durable state without invoking a model. For parallel workers,
  repeat `--task-id` and use one `--mode all` or `--mode any` wait.
- Use a low-cost relay subagent only when its native wait is materially more
  reliable than the direct command wait. The parent must remain active in one
  bounded native wait; a relay must not edit, test, review or poll repeatedly.
- Codex Desktop completion delivery is durable history, not guaranteed live
  refresh of an already-open chat. Use the terminal wait for unknown or long
  work. Claude uses the stream delivery documented by OrchestratorEngine and
  does not need the Codex manual fallback.
- On completion, inspect compact `result.json` and `evidence.json` first. Open
  only referenced failure logs when necessary. Worker output is evidence; the
  host agent owns final review and acceptance.
- Preserve `.orchestrator` events, signals, results and evidence as the audit
  trail. For OrchestratorEngine runtime/core problems, start with compact
  `status`, use targeted diagnostics, and create a sanitized structured report
  instead of pasting private documents or unbounded logs.
