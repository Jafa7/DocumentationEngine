## Summary

<!-- What changed and why. Link related issues if any. -->

## Testing

<!--
Verification level: structural | focused | full
Risk reason:
Commands run and results:
Checks not run: N/A with a reason; do not mark an unrun check as passing.
See AGENTS.md and CONTRIBUTING.md for the canonical policy.
-->

```bash
# Replace with the checks selected for this change.
git diff --check
```

## Checklist

- [ ] This change stays within a single described scope (no unrelated
      refactors bundled in).
- [ ] The verification level and risk reason are recorded above.
- [ ] Relevant structural checks, focused tests or the full gate pass; every
      unrun check is marked N/A with a reason.
- [ ] Configuration-behavior changes include and run configuration tests, or
      this change does not affect configuration behavior.
- [ ] `git diff --check` passes.
- [ ] `uv lock --check` passes, or this change does not touch dependencies
      (N/A with reason otherwise).
- [ ] `./scripts/installed_cli_smoke.sh` passes, or this change does not
      affect packaging/entry points/CLI surface (N/A with reason otherwise).
- [ ] `CHANGELOG.md` and relevant docs are updated, or this change has no
      public-contract impact (N/A with reason otherwise).
- [ ] The core package (`src/docsystem/`) remains provider-neutral — no
      Claude-, Copilot- or other agent-specific behavior added there.
- [ ] No private or local generated state is committed (root-local
      `.docsystem.toml`, `.docsystem/`, or other adopter-local artifacts;
      intentional public example fixtures are reviewed separately).
- [ ] Executable bits on any added/modified shell scripts are preserved
      (`git ls-files --stage <script>` shows `100755`).
