# Program plans and deterministic next work

Documentation Engine can derive an ordered execution view from one authored
program-roadmap document. This optional contract is intended for genuinely
multi-stage work. It answers three bounded questions without reading roadmap
bodies:

- what is already delivered, active, ready, waiting or deferred;
- which milestone should be continued or started next;
- which prerequisites, source contracts and downstream milestones explain that
  recommendation.

The program remains Markdown source of truth. There is no task database,
hidden AI ranking or automatic execution. Routine fixes and one-step changes
belong in the project's ordinary issue, change and Git workflow rather than in
`program_plan`.

## Authored contract

Add `program_plan` to the YAML front matter of the `type: roadmap` document
that owns the overall program:

```yaml
---
id: RM-001
revision: 4
type: roadmap
status: proposed
program_plan:
  version: 1
  milestones:
    - id: documentation-foundation
      title: Documentation foundation
      order: 10
      priority: 10
      roadmap: RM-010
      source_contracts: [DOC-003#requirements]
    - id: automated-validation
      title: Automated documentation validation
      order: 20
      priority: 10
      state: planned
      prerequisites: [documentation-foundation]
      source_contracts: [DOC-004#quality-contract]
    - id: organization-rollout
      title: Organization-wide adoption
      order: 30
      state: deferred
      reopen_when: local project adoption provides sufficient evidence
      source_contracts: [DOC-005#adoption]
---
```

`version` is currently `1`. Each milestone has a unique lowercase slug `id`,
non-empty `title`, positive unique `order`, and optional positive `priority`
(defaulting to `order`). Lower priority numbers are recommended first.

IDs, titles, ordering, priorities, source contracts and reopen conditions are
project-authored policy. Documentation Engine validates and explains that
policy; it does not infer product priority from document text, similarity,
provider behavior or model preference.

Before a bounded roadmap document exists, the program owns `state`:

- `planned` becomes `ready` after every prerequisite is delivered;
- `waiting` requires a non-empty `waiting_for` condition;
- `deferred` requires a non-empty `reopen_when` condition.

After the bounded roadmap exists, replace `state` with `roadmap: RM-NNN`.
The target must be a different cataloged document with `type: roadmap`, and one
bounded roadmap may belong to only one program. Lifecycle state is then derived
from that document's metadata and must not be duplicated in the program entry.
Supported roadmap statuses normalize as
follows: `proposed`/`planned`, `waiting`, `ready`, `active`, `blocked`,
`completed`/`delivered`, `deferred`, `cancelled`, and `failed`.
If a roadmap-derived state is `waiting` or `deferred`, the program entry still
supplies its visible `waiting_for` or `reopen_when` condition. Those condition
fields are invalid for every other effective state.

`prerequisites` contains milestone slugs from the same program. Cycles,
unknown/self prerequisites, duplicate owners and an active or delivered
milestone whose prerequisites are not delivered fail closed. `source_contracts`
contains exact existing `ID#anchor` scope/evidence addresses. They are
non-exclusive because one architecture section may justify several phases;
they do not claim delivery ownership. Proven ownership remains the separate
`delivers`/`delivery-map` contract after a bounded roadmap exists. `unlocks` is
derived from reverse prerequisites and is never authored separately.

## Inspection

```bash
docsystem roadmap status PROJECT
docsystem roadmap next PROJECT
docsystem roadmap explain automated-validation PROJECT
docsystem roadmap explain RM-010 PROJECT
```

All commands accept `--json`, workspace source selection, and `--program ID`
when a catalog intentionally contains more than one program. Without
`--program`, zero programs or multiple programs are explicit errors.

`status` returns every milestone in authored `order`. `next` behaves as a
deterministic continuation gate:

1. if any roadmap is `active`, recommend continuing active work;
2. otherwise recommend every ready milestone at the lowest authored priority;
3. retain other ready alternatives and blocked reasons in the result;
4. return `complete` only when every entry is delivered or cancelled;
5. return `deferred` when no actionable work remains but at least one idea is
   waiting for its explicit reopen condition.

`explain` resolves either the local milestone slug or its assigned roadmap ID
and reports prerequisites, exact source contracts, derived unlocks and the
reason for its effective state. Text output is stable tab-separated data;
JSON uses the ordinary Documentation Engine schema envelope.

Malformed authored plans are reported by `validate` and `doctor` as well as by
the roadmap commands. A roadmap query emits no partial stdout when the catalog
or selected program is invalid.

## Agent behavior

An agent should run `roadmap next` before creating another bounded roadmap.
The recommendation is planning evidence, not execution or write authority.
The agent still needs the normal intake, admission, change-plan, verification
and completion evidence appropriate to the selected milestone, plus any local
permissions and verification policy defined by the adopter project. An agent
must not add milestones merely to create a more detailed history.
