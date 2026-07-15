# Workspace source selection

Documentation Engine normally operates on one project root passed directly to
each command. A local documentation workspace can register several independent
project profiles and let a caller select one by a stable source name instead
of repeating a machine-specific absolute path.

Ordinary `--source` use is source selection: one command sees one catalog,
dependency graph and projection. The separate read-only
[`federation` commands](federation.md) can build a qualified graph across all
registered sources without changing this single-source behavior.

## Ownership model

Keep public product documentation in the repository that owns it. A private
workspace may hold ignored planning documents, internal reviews and other
local-only profiles outside public repositories. Each registered source is a
complete Documentation Engine project root with its own `.docsystem.toml`,
Markdown root and disposable `.docsystem/` projection.

The workspace registry does not copy, move, synchronize or delete documents.
Markdown ownership remains with the selected source. In particular, moving a
profile into a workspace is a separate, owner-controlled, copy-only migration:
the engine never authorizes removal of the original source tree.

## Registry

Create `workspace.toml` at the root of the local workspace:

```toml
version = 1

[[sources]]
name = "example-project"
root = "projects/example-project"
visibility = "private"
write = "managed-maintenance"

[[sources]]
name = "shared-guides"
root = "projects/shared-guides"
visibility = "public"
```

Every source root is relative to the workspace, must remain inside it after
symlink resolution, and must name a complete project profile. Its configured
documentation root and projection cache must also resolve inside that source;
a writable symlink escape makes the source unavailable. Source names use
lowercase ASCII letters, digits and hyphens, starting with a letter. Names and
resolved roots must be unique and non-overlapping. `visibility` is required
and is either `private` or `public`.

`write` is optional and defaults to `none`. The only opt-in value is
`managed-maintenance`; it authorizes the selected-source form of the existing
bounded `maintenance --write` and `maintenance-recover` commands. It does not
authorize arbitrary edits, migration, projection writes, cross-source
transactions or deletion. Keep `none` unless the owning project deliberately
enables this narrow maintenance contract.

Visibility is inspectable metadata in this milestone. It does not implement
authorization or redaction: selecting a private source explicitly means the
local caller is allowed to read it. Do not expose a workspace through an
untrusted service or publish generated output without applying the
organization's access policy.

Inspect the registry without reading document bodies:

```bash
docsystem workspace list . --workspace /path/to/documentation-workspace
docsystem workspace list . --workspace /path/to/documentation-workspace --json
docsystem workspace doctor . --workspace /path/to/documentation-workspace
```

The listing contains only source name, visibility, write policy, availability
and a fixed reason slug. Missing roots, missing or invalid configurations and
unsafe local paths remain visible; selecting any unavailable source fails
closed.

## Local project pointer

For routine use, place this ignored file in the consuming checkout:

```toml
# .docsystem.local.toml
workspace = "/path/to/documentation-workspace"
```

The pointer is local machine wiring, not public project policy. It accepts one
absolute path and is ignored by the Documentation Engine repository template.
Do not commit it or paste its value into public agent instructions.

Workspace discovery uses this precedence:

1. `--workspace PATH`;
2. `DOCSYSTEM_WORKSPACE`;
3. `.docsystem.local.toml` in the positional project root.

An explicit source never falls back to the positional project when discovery,
manifest validation or source availability fails.

## Selecting a source

Commands that operate on an existing project profile accept `--source NAME`
and `--workspace PATH`:

```bash
docsystem readiness . --source example-project --json
docsystem catalog . --source example-project
docsystem context DOC-001 . --source example-project --depth 1
docsystem impact DOC-001 . --source example-project
docsystem index . --source example-project --write
docsystem maintenance install-version . --source example-project --preview --json
```

`--workspace` is normally omitted after installing the local pointer. On a
project command it is valid only together with `--source`. The `report draft`
and `report context-gap` commands already use `--source` for the reporting
host, so their workspace selector is spelled `--workspace-source`:

```bash
docsystem report draft . \
  --project-name "Example" \
  --type adoption-finding \
  --source codex \
  --workspace-source example-project
```

`init` remains a direct-path bootstrap command. Create a new profile with
`docsystem init PATH`, then register that valid project root in
`workspace.toml`; source selection never creates or repairs workspace entries.

When no source selector is present, workspace state is not loaded or validated
and the existing single-project behavior is unchanged. Selected-source
readiness and agent-instruction output identify the project by the caller's
positional discovery root plus `--source NAME`, never by the private source
root. Generated next commands therefore remain directly executable while the
same local pointer or environment wiring is active.

Mutating commands retain their existing authorization boundary. Source
selection only changes the one project root they target. `init`,
`migrate --apply` and `index --write` retain their command-specific guards.
Selected-source managed maintenance has an additional fail-closed boundary:
the source must opt into `write = "managed-maintenance"`.

Review a selected-source preview, then bind the exact reviewed state into the
write:

```bash
docsystem maintenance install-version . \
  --source example-project --preview --json
docsystem maintenance install-version . \
  --source example-project --write \
  --expect-source-hash SOURCE_BLOCK_SHA256 \
  --expect-preview-hash PREVIEW_SHA256 \
  --workstream-id WS-001
```

The preview hash binds the selected source name, workspace manifest, write
policy, project configuration, maintenance declaration, source and occurrence
bytes/ranges, expected after hashes and graph completeness. A change detected
before transaction admission is refused before journal creation; a race
detected after the immutable generation begins causes automatic rollback and
retains bounded failure evidence. The transaction uses a source-local journal
and a non-blocking per-journal lock. Recovery is also
source-qualified and requires the exact journal manifest hash:

```bash
docsystem maintenance-recover GENERATION . \
  --source example-project \
  --expect-manifest-hash JOURNAL_MANIFEST_SHA256
```

The journal records body-free authority evidence: source name, workspace
manifest hash, project-config hash, write policy and preview hash. Selected
recovery requires the current workspace manifest and project configuration to
match that authority; restore the reviewed policy wiring first or use an
explicit direct-project recovery under separate operator authority. The same
workstream ID may occur independently in different sources; there is no
cross-source atomic commit or rollback.

## MCP

The read-only MCP tools accept optional `source` and `workspace` parameters.
They forward the same CLI flags and preserve the old invocation exactly when
both are omitted. `workspace_list` exposes the body-free registry listing.
MCP exposes the same read-only federated catalog/dependency/context/impact
queries. It does not expose workspace creation, synchronization, migration or
source mutation.

## Deliberate non-goals

This milestone does not provide:

- remote/network sources, authentication or authorization;
- cross-source transactions or locking across distinct source journals;
- Git synchronization, import, copy or deletion;
- a documentation web server or UI.

Remote-service writes require a separate bounded design. Federated queries may
use a verified workspace-owned projection and fall back visibly to direct
Markdown; both paths fail closed unless every registered source is available
and valid.
