## Summary

<!-- What changed and why. Link related issues if any. -->

## Testing

<!-- Commands run and their result. Use "N/A: <reason>" for anything skipped. -->

```bash
TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run pytest
TMPDIR=/tmp TMP=/tmp TEMP=/tmp uv run ruff check .
```

## Checklist

- [ ] This change stays within a single described scope (no unrelated
      refactors bundled in).
- [ ] `uv run pytest` passes, and configuration-behavior changes have tests.
- [ ] `uv run ruff check .` passes.
- [ ] `uv lock --check` passes, or this change does not touch dependencies
      (N/A with reason otherwise).
- [ ] `./scripts/installed_cli_smoke.sh` passes, or this change does not
      affect packaging/entry points/CLI surface (N/A with reason otherwise).
- [ ] `CHANGELOG.md` and relevant docs are updated, or this change has no
      public-contract impact (N/A with reason otherwise).
- [ ] The core package (`src/docsystem/`) remains provider-neutral — no
      Claude-, Copilot- or other agent-specific behavior added there.
- [ ] No private or local generated state is committed (`.docsystem.toml`,
      `.docsystem/`, or other adopter-local artifacts).
- [ ] Executable bits on any added/modified shell scripts are preserved
      (`git ls-files --stage <script>` shows `100755`).
