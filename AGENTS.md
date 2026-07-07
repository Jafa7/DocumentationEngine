# Agent instructions

- Keep product documentation and code comments in English.
- Treat Markdown as source of truth and generated data as disposable.
- Do not add provider-specific behavior to the core package.
- Prefer deterministic scripts for mechanical documentation maintenance.
- Preserve existing project files during bootstrap and migration.
- Every change to configuration behavior requires tests.
- Run `TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run pytest` before handing work off.
  WSL sessions may inherit Windows temp directories; native `/tmp` keeps
  pytest capture deterministic.
- Run git stage/commit operations from inside WSL for this checkout. Do not
  stage or commit from Windows Git over `\\wsl.localhost`; it can drop
  executable bits such as `scripts/installed_cli_smoke.sh` from `100755` to
  `100644` and break CI with `Permission denied`.
- Before committing, verify executable scripts that CI runs:

```bash
git ls-files --stage scripts/installed_cli_smoke.sh
test -x scripts/installed_cli_smoke.sh
```

Expected git mode for `scripts/installed_cli_smoke.sh` is `100755`.
