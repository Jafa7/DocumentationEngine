# Documentation map

Each topic below has one canonical owner. Other documents should summarize and
link to that owner rather than repeat commands or policy.

## Start here

- [README installation](../README.md#installation) — consumer, MCP and
  contributor installation choices.
- [Setup guide](setup-guide.md) — safely connect Documentation Engine to an
  existing project.
- [Adoption guide](adoption.md) — migrate and operate an existing Markdown
  tree after setup.

## Product contracts

- [Architecture](architecture.md) — boundaries, layers, configuration,
  projection and catalog model.
- [Agent contract](agent-contract.md) — safe command behavior for AI clients.
- [Context measurement](context-efficiency.md) — measured reduction method,
  quality guard and limitations.

## Integrations and project structure

- [MCP adapter](mcp-adapter.md) — read-only stdio adapter and host
  configuration.
- [Workspace source selection](workspace-sources.md) — address independent
  local profiles by stable source name.
- [Paradigmarium integration](paradigmarium-integration.md) — real adopter
  compatibility profile and wrapper contract.
- [Workstream branching](workstream-branching.md) — preserve context when work
  splits into another chat, module or repository.

## Safety and reporting

- [Local state safety](local-state-safety.md) — private state and backup
  requirements.
- [Adopter reporting](adopter-reporting.md) — privacy-safe issue taxonomy and
  evidence contract.
- [Security policy](../SECURITY.md) — vulnerability reporting.

## Contributing and releases

- [Contributing](../CONTRIBUTING.md) — development workflow and risk-based
  verification.
- [Agent instructions](../AGENTS.md) — provider-neutral repository rules and
  orchestration behavior.
- [Release guide](releasing.md) — immutable build, TestPyPI and PyPI process.
- [Changelog](../CHANGELOG.md) — published release history.
