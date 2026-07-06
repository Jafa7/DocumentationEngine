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
