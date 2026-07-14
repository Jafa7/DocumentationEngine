# Compact context delivery

`docsystem context ID PROJECT --compact [--json]` is the agent-oriented,
lossless content form. It reduces repeated source bytes inside one packet; it
does not summarize, paraphrase, rank away or hide documentation.

## Source-range contract

The engine first selects documents and sections exactly as ordinary `context`
does. Within each document it then unions overlapping or adjacent requested
line ranges: navigation, explicit parent sections, explicit child sections and
delta-added sections. The resulting `content_fragments` are disjoint, remain
in source order, contain original Markdown, and carry their exact line range
and SHA-256. Consequently, a child section nested inside a selected parent is
not copied a second time.

`content_manifest` separately records every requested stable address:

```json
{
  "address": "DOC-002#principles",
  "start_line": 20,
  "end_line": 34,
  "reasons": ["explicit include"],
  "fragment_id": "DOC-002:1-34",
  "delivery": "covered-by-fragment"
}
```

`covered-by-fragment` means that a wider emitted source range already contains
the complete address. No content was dropped. Document entries also expose
ordered `inclusion_reasons` with the relation, direction and document through
which each graph path selected them. Existing `relations`, H2 omissions,
freshness, boundaries and purpose-view omissions remain present.
To avoid sending the same map twice, compact packets omit the full `sections`
size map: use the recommended outline-first call or `read --list` when that map
is needed.

## Diagnostics

Graph-blocking errors still fail closed before a packet is emitted. Compact
text keeps stale/historical pins and unresolved/resource boundaries as
individual rows. Repeated nonblocking adoption mappings and purpose-view
omissions may be grouped by relation/reason/count, with an explicit instruction
to rerun the same command with `--json`. JSON always retains the complete
deterministic row-level lists.

Compact delivery cannot combine with outline delivery because outline contains
no source content to deduplicate. It remains compatible with navigation
purpose views, explicit anchors, `--include`, `--assume-known` and `--since`.
Every omitted section remains listed and available through another context
request or a full `read`.
