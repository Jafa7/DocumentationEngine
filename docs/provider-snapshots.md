# Pinned provider snapshots

Pinned provider snapshots let an external consumer reconcile two exact states
of a Documentation Engine catalog without importing package internals or
reading `.docsystem/cache` directly. The surface is read-only, body-free and
provider-neutral.

This contract answers a different question from `docsystem changes`:

- `changes` compares live Markdown with the generation selected by
  `current.json`;
- `provider snapshot` exports observations from one explicitly named immutable
  generation;
- `provider compare` compares two explicitly named immutable generations and
  does not substitute the current pointer for either operand.

## Opt-in provider identity

Configure a stable identity before building generations intended for external
reconciliation:

```toml
[provider]
id = "example-docs"
visibility = "private"

[projection]
format = "sharded-json"
keep_generations = 4
```

`provider.id` accepts 1–128 lowercase letters, digits, `.`, `_` and `-`.
`visibility` is `private` by default and may be set to `public`; it is an
exported classification, not an access-control mechanism. Filesystem,
sandbox, service and transport policy still control who can invoke the CLI.

Provider configuration is optional for all existing commands. The provider
commands fail closed with `provider-not-configured` when the ID is absent.
Never derive the ID from an absolute path, host name or repository remote.

Run `docsystem index PROJECT --write` after configuring the provider. Each new
projection generation binds the provider ID, visibility, capabilities,
catalog-completeness evidence, coverage and fixed export boundaries into its
content hash. Generations created by older projection schemas do not acquire
those claims retroactively and are reported as `generation-unsupported`.

## Export one pinned generation

```bash
docsystem provider snapshot GENERATION PROJECT --json
docsystem provider snapshot GENERATION PROJECT --json \
  --page-size 100 --cursor CURSOR
```

`GENERATION` is a full retained generation hash or an unambiguous prefix of at
least twelve characters. It is required; the command never selects
`current.json` implicitly.

The versioned response identifies the provider and generation, advertises
capabilities, states coverage/scope/boundaries, and returns a page of stable
document and section observations. Each observation contains only:

- `document_id` and canonical `anchor` (`null` for a document);
- `anchor_kind` (`explicit` or `generated`) for a section;
- exact content hash;
- documentation-root-relative POSIX path;
- one-based inclusive line hints;
- configured visibility.

Markdown bodies, titles, metadata values, relations, excluded paths, absolute
paths and projection locations are not exported. Explicit anchors provide the
strongest stable section identity. A generated anchor remains canonical for
that generation, but changing its heading may produce a missing/added pair
rather than an inferred match; the engine does not use title similarity.

## Compare two pinned generations

```bash
docsystem provider compare BEFORE AFTER PROJECT --json
docsystem provider compare BEFORE AFTER PROJECT --json \
  --page-size 100 --cursor CURSOR
```

Both operands are verified independently and may be retained historical
generations. They do not need to match live Markdown or the current pointer.
They must belong to the configured provider and advertise the same supported
capability.

The comparison uses the exact key `(kind, document_id, canonical_anchor)`:

| Classification | Meaning |
| --- | --- |
| `relocated` | Identity and content hash are unchanged, but path or line hints changed |
| `changed` | Identity exists on both sides and its content hash changed |
| `missing` | Identity exists only in `BEFORE` |
| `added` | Identity exists only in `AFTER` |

Every change carries the exact body-free `before` and `after` observation when
that side exists. A content change takes precedence over relocation, while the
two observations still expose any simultaneous location change. The summary
also counts unchanged entities. Valid Documentation Engine catalogs prevent
duplicate stable keys; ambiguity is never used to disguise corruption or an
incomplete catalog.

## Integrity and availability

Pinned export verifies the selector, projection schema, manifest-to-generation
hash, provider capability descriptor, completeness evidence, document coverage
and every stored document/reverse/reference shard hash. It deliberately does
not compare historical source hashes with live Markdown: a retained generation
is immutable historical evidence and is expected to differ from current files.

Failures return exit code `1`, write no stdout, and use a stable code in stderr,
for example `ERROR [generation-corrupt]: ...`. Important distinctions include:

- `provider-unavailable`: generation storage cannot be read;
- `generation-unknown`: the requested retained generation does not exist;
- `generation-selector-ambiguous`: a prefix selects more than one generation;
- `generation-corrupt`: bound manifest or shard evidence is invalid;
- `generation-unsupported`: the generation lacks this versioned capability;
- `generation-incomplete`: catalog or coverage completeness cannot be proved;
- `provider-mismatch`: generation identity differs from configured identity;
- `page-invalid` / `page-too-large`: pagination evidence is invalid or cannot
  fit the bounded response.

Provider unavailability and corrupt/incomplete snapshots are never represented
as missing entities.

## Pagination and deterministic bytes

Pages default to 100 entities, accept 1–500, and never exceed 262,144 UTF-8
bytes. The response may return fewer than the requested count to respect the
byte limit. If one observation cannot fit, the command fails instead of
truncating a field. `next_cursor` is bound to the response kind and exact
generation operand(s); using it for another query fails closed.

Following `next_cursor` until `null` reconstructs the complete ordered result
without loss or duplication. Repeating the same pinned request produces the
same sorted JSON bytes with one trailing newline.

Retention controls availability. Set `projection.keep_generations` high enough
for the consumer's reconciliation window, or archive the body-free exported
responses in the consumer. If either comparison generation has been evicted,
the provider fails with `generation-unknown`/unavailable. The consumer must
perform an explicit full rebaseline from a newly selected complete generation
and record that the earlier delta chain ended. Retention loss is never evidence
that an entity was removed, and the engine does not reconstruct the missing
operand heuristically.

## Deferred follow-up: portable snapshot artifacts

Portable comparison after provider retention expiry is a named follow-up, not
part of the first contract. A future command may accept complete exported
snapshot artifacts as operands, but must bind and verify their schema, provider
and generation identity, query/scope, coverage/completeness, complete ordered
observations and canonical artifact digest before applying the same comparison
semantics. It must reject partial page sets, mixed-query pages, tampering,
unsupported schemas and incompatible providers. Persisted pages from this
version are evidence for a consumer; they are not yet accepted back as
Documentation Engine comparison operands.

For v1, entity identity is exactly `(kind, document_id, canonical_anchor)`.
Moving the same document ID to another provider-relative path can be
`relocated`. Moving a section across document IDs is `missing` plus `added`,
even when its anchor and content match; cross-document similarity is not safe
structural identity and is not inferred.

The first contract is CLI-only. A service or MCP adapter may wrap these exact
read-only commands later, but it must preserve their integrity, privacy,
pagination and failure semantics rather than inspect projection files itself.

[`examples/provider-snapshots/`](../examples/provider-snapshots/) is a public
synthetic corpus with explicit anchors and an opt-in provider profile. Copy it
to a temporary directory, run `index --write`, make one source change and write
a second generation to exercise the commands without exposing adopter data.
