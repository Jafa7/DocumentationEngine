# Knowledge promotion

Knowledge promotion moves a reviewed claim toward its canonical owner without
turning drafts, reviews or experiments into a second source of current truth.
Documentation Engine plans this transition; it does not generate the semantic
merge or write Markdown.

## Authored authority

The destination document declares one or more stable authority slugs in YAML:

```yaml
type: canonical
authority_for: [installation-policy]
```

Its document type must belong to a configured
[`[profiles]`](document-profiles.md) entry. The profile's `history_mode`
determines the safe operation:

| History mode | Planned action |
| --- | --- |
| `living` | revise the current owner and increment its revision |
| `append-only` | append a new record without rewriting earlier entries |
| `immutable-after-state` | create a superseding document |

If another document declares the same authority slug, promotion blocks with a
visible conflict. Documentation Engine does not infer semantic equivalence
between differently named authority keys.

## Request and command

```json
{
  "schema_version": 1,
  "source": "DOC-020#finding",
  "destination": "DOC-003#installation",
  "authority_key": "installation-policy",
  "knowledge_state": "fact",
  "disposition": "accepted",
  "evidence": ["EXP-004#result"]
}
```

```bash
docsystem promotion . --request promotion-request.json
docsystem promotion . --request promotion-request.json --json
```

Addresses must identify exact canonical sections. Supported knowledge states
are `fact`, `inference`, `hypothesis`, `decision` and `reviewer-position`;
review dispositions are `accepted`, `partial`, `rejected`, `deferred` and
`contested`. Only accepted/partial facts, inferences and decisions are
promotable, and they require exact evidence sections. Other combinations are
retained as candidates rather than silently rewritten as truth.

The body-free result reports the action, source/destination revisions and
policy, source/evidence revision pins, conservative destination-document
metadata consumers, authority conflicts and explicit omissions. It does not
claim that every document-level consumer depends on the selected section. A
ready plan exits 0. A valid but blocked plan is emitted
with exit 2. Malformed requests, invalid catalogs, missing authority, unknown
sections and unprofiled destination types fail with exit 1, stderr only.

## Boundaries

The command parses Markdown structure but does not include section bodies in
the plan, decide whether prose is correct, authenticate the reviewer, allocate
a new document ID, modify Markdown or delete/silence historical material. The
AI agent performs the semantic edit
under normal project authorization, preserves the source through provenance,
reviews reported consumers and runs the project's verification policy.
