# Workstream / Idea Branching

Projects do not only grow by editing existing documents. They also grow by
splitting off a new idea, module, experiment, repository or long-running chat.
That split is risky for AI-assisted work: the child context often loses why it
exists, what it inherits, what it must not change and how its result should
return to the parent project.

A workstream branch is a lightweight documentation pattern for that split.
It is not a Git branch. It is a semantic project branch: a documented mandate
for a focused line of work that may happen in a new chat, package, repository
or local planning area.

## When to create one

Create a workstream mandate when work is separated enough that a future human
or agent could reasonably ask:

- why this branch exists;
- which parent documents or decisions it inherits;
- what it is allowed to change;
- what is explicitly out of scope;
- where its output should be reviewed or merged back;
- what evidence is required before the parent project trusts the result.

Do not create one for every small task. A good threshold is: if the work will
outlive the current conversation, cross a repository/module boundary or need a
separate agent to continue safely, write a mandate.

## Why it helps AI agents

Chat memory is operational context, not durable project memory. A branch
mandate gives an agent a compact, inspectable starting point before it begins
work in the child context. It reduces token use because the parent does not
need to restate the whole project history, and it reduces accidental scope
creep because the boundaries are part of the source material.

The mandate should be read before implementation in a child chat or module,
and updated when the branch's scope or return protocol changes.

## What a branch mandate contains

A mandate is ordinary Markdown with front matter. Use your project's normal
stable ID prefix policy; the `WS-001` example in the template is only a
placeholder.

Recommended fields:

- `id` and `revision`: stable identity for the mandate itself.
- `type: workstream`: makes the document easy to discover.
- `status`: for example `proposed`, `active`, `blocked`, `reviewing`,
  `accepted` or `closed`.
- `parent`: the parent document, decision or project anchor that authorized
  the branch. This is project metadata, not a core dependency relation.
- `derived_from`: source documents whose context the branch inherits.
- `returns_to`: parent documents, decisions or review gates that should receive
  the result. This is project metadata unless your profile gives it stronger
  semantics.

Recommended sections:

- why this branch exists;
- inherited context;
- mandate;
- boundaries and non-goals;
- return protocol;
- current artifacts;
- review gate.

Use explicit canonical anchors for sections referenced by project completion
criteria. The reusable template does this so a heading wording improvement
does not silently change the evidence address.

## Return protocol

The return protocol is the handoff contract. It should say what the branch must
provide before the parent project acts on it:

- commits, documents, diagrams or generated artifacts;
- checks or acceptance evidence;
- migration assumptions;
- known incompatibilities;
- decisions that still belong to the parent;
- whether the child branch may push, open a pull request or only report back.

This keeps child work useful without letting it silently redefine the parent
project.

## Keep it lightweight

The pattern fails if it becomes paperwork. Keep the mandate short enough that a
new agent can read it before doing real work. Prefer concrete boundaries and
return evidence over long narrative history. Link to source documents instead
of copying them, and use `context` or `read` commands to expand only the parts
needed for the current task.

Good mandates are not bureaucracy; they are a map label: "this tunnel starts
here, carries this context, and comes back there."

For long-running or higher-risk branches, the optional
[bounded workstream evidence](workstream-evidence.md) contract validates
lifecycle transitions, corrective attempts, checks, independent review and the
final return without requiring chat memory. Small tasks can keep using this
lightweight mandate alone.

Before creating a new branch mandate, a project may use
[deterministic idea intake](idea-intake.md) to determine whether an existing
document already owns the outcome, an ordinary draft is sufficient, or a
separate workstream lifecycle is justified. Intake proposes placement; it
does not create the mandate.
