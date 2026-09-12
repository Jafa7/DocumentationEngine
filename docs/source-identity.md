# Source identity

Documentation Engine separates physical file identity from parsed Markdown
identity. The distinction keeps integrity checks exact without making ordinary
section addressing depend on operating-system line endings.

## Versioned algorithms

Projection schema 6 records both algorithms in `hash_algorithms` and includes
that declaration in the immutable generation digest:

| Field | Algorithm | Purpose |
| --- | --- | --- |
| document `source_sha256` | `sha256-raw-source-bytes-v1` | Exact bytes stored in the Markdown file |
| section `sha256` | `sha256-normalized-section-text-v1` | UTF-8 Markdown after universal-newline normalization, sliced by the section's inclusive line range |

Changing LF to CRLF, adding or removing a terminal newline, changing a UTF-8
encoding sequence or editing ordinary content therefore changes document source
identity. A line-ending-only edit does not change section identity because the
parser and rendered Markdown operate on normalized lines. A semantic text edit
changes every section slice that contains it.

The provider scope advertises the same names as `document_hash` and
`section_hash`. Consumers should compare hash values only when their enclosing
scope is compatible; they must not infer the algorithm from a 64-character
value alone.

## Serving and delta behavior

Freshness verification hashes the source bytes before decoding. The decoded
text is normalized exactly as the direct catalog path, so a verified projection
and direct Markdown render the same content. If physical bytes change while the
normalized Markdown is otherwise identical, the projection is stale and the
read visibly falls back. A `context --since` comparison reports that physical
change as `source_changed_outside_sections` when no normalized section hash
changed.

Revision declarations are a separate lightweight contract.
`--assume-known ID@REV` trusts the project's revision discipline and does not
compare a retained source hash; use `--since GENERATION` when the omission must
be content-bound.

## Compatibility

Projection schema 5 and older generations used normalized document text for
`source_sha256`. Schema 6 does not reinterpret or rewrite them. Current-pointer
reads report the old cache as incompatible and may rebuild it through explicit
`index --write`. Pinned provider export reports an old retained generation as
`generation-unsupported`; loss of a supported retained operand remains a full
rebaseline, never an entity-absence conclusion.

The federated derived cache uses schema 2 because serialized document objects
now carry exact source identity. Its existing source inventory already hashes
raw bytes. Old federated cache generations are disposable and fall back to a
direct rebuild without changing any registered source.
