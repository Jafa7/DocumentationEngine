# Paradigmarium-style integration profile

This directory is a small, public, runnable fixture. It shows the
`.docsystem.toml` shape and Markdown tree a Paradigmarium-style downstream
project adopts, without containing any private Paradigmarium plan content.
See [`docs/paradigmarium-integration.md`](../../docs/paradigmarium-integration.md)
for the full integration guidance this fixture demonstrates.

Run the adoption sequence against it (from the repository root, in a
development checkout):

```bash
uv run python -m docsystem readiness examples/paradigmarium-profile --json
uv run python -m docsystem migration-report examples/paradigmarium-profile --json
uv run python -m docsystem catalog examples/paradigmarium-profile --explain --json
```

`architecture/README.md` intentionally uses a legacy relative-path
`depends_on` value instead of a stable ID, so `readiness` and
`migration-report` have a real, resolvable migration to report. `doctor` and
`validate` correctly fail against this fixture in the default `strict` mode
until that value is migrated with `docsystem migrate --apply` or the project
opts into `relations.legacy_paths = "resolve-with-warning"`; this is the same
behavior any adopting project sees, not a bug in the fixture.

This fixture is not modified by the test suite or by
`scripts/installed_cli_smoke.sh`, which build their own temporary fixtures
instead.
