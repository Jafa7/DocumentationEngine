# MCP adapter

`docsystem.mcp_server` is a thin, provider-neutral [Model Context Protocol]
adapter over the `docsystem` CLI. It lets any MCP-capable client (Claude Code,
Codex, IDE agents, custom hosts) call Documentation Engine as typed tools
instead of shelling out and parsing text.

[Model Context Protocol]: https://modelcontextprotocol.io

## Design

The adapter is deliberately a wrapper, not a second implementation:

- every tool call runs the same CLI (`python -m docsystem ...`) in a
  subprocess, so the adapter can never bypass the core's validation,
  projection verification or fallback behavior;
- structured tools return the CLI's `--json` payloads (including
  `schema_version`); when a command exits successfully but still prints
  diagnostics to stderr — most importantly the
  `WARNING: projection stale/corrupt; using direct Markdown` note emitted when
  `context` falls back from a stale or corrupt projection — those lines are
  surfaced deterministically under a `diagnostics` array. The key is present
  only when such diagnostics exist, so every other payload stays byte-identical
  to the CLI output;
- the `context` tool takes an `outline: bool = False` parameter that maps to
  `--outline`: it returns a map-first packet of per-section `lines`/`bytes`
  sizes with no document content, for an agent that wants to budget tokens
  before fetching. `outline` cannot combine with `anchor` or `include` (the
  CLI rejects that combination, so the tool call raises like any other
  non-zero exit);
- the `context` tool takes an optional `view` name. It maps to the project's
  authored purpose view and returns `purpose_view` plus `view_omissions`.
  `view` cannot combine with manual `depth`, `include_related` or `outline`;
  explicit `anchor`/`include` remains available only when the view's delivery
  is `navigation`;
- the `context` tool also takes `assume_known: list[str] | None = None`
  (each value `ID@REV`, mapping to a repeated `--assume-known`) and `since:
  str | None = None` (mapping to `--since GENERATION`). `assume_known`
  declares documents the client already holds so their content is omitted
  while the declared revision still matches (a mismatch keeps full content and
  is reported under `assume_known_mismatches`); `since` requests a delta
  against a retained projection generation so unchanged documents are omitted
  and changed ones carry their changed sections plus explicit removed-section,
  metadata-change and outside-section signals. Explicit `anchor`/`include`
  selections still win for an otherwise unchanged document. The retained
  generation is fully integrity-verified before it can authorize an omission.
  The CLI rejects combining `since` with `assume_known`, or either with
  `outline`, so those
  tool calls raise like any other non-zero exit;
- the `context` tool takes `compact: bool = False`, mapping to `--compact`.
  Compact packets merge overlapping source ranges and retain address/reason
  mappings; `compact` cannot combine with outline delivery;
- text tools (`read_document`, `impact`) return the CLI's stdout unchanged for
  compatibility. Structured packet variants (`read_document_packet`,
  `impact_packet`) return the same stdout under `text` and add `diagnostics`
  when successful commands emitted non-fatal stderr, such as projection
  fallback warnings;
- only read-only commands are exposed. Mutating operations (`init`,
  `migrate --apply`, `index --write`, `maintenance --write` and
  `maintenance-recover`) intentionally have no tools and stay
  with the human or calling system, matching
  [the agent contract](agent-contract.md);
- a non-zero CLI exit becomes a tool error carrying the CLI's stderr
  diagnostics, so a client never mistakes a failure for data. The one
  exception is `readiness`, whose "not ready" state is a legitimate answer:
  it returns the payload with `"ready": false` instead of raising.
- `criteria`, `workstream`, `intake`, `admission`, `execution_handoff`,
  `execution_result`, `lifecycle` and `finish_handoff` expose the same read-only
  versioned workstream policy, record validation and optional strict finish
  gate plus idea-placement and A0–A2 admission policy as the CLI. Request and
  record paths are local to the MCP server host.
- `metadata_inventory` returns body-free field coverage and document graph
  facts. Additional values remain hidden unless the caller supplies both one
  `field` and `values: true`.
- `profile_check` returns project-authored profile assignments and structured
  violations without document bodies. A profile-invalid result is a valid
  payload with `valid: false`, not an MCP transport failure.

## Tools

| Tool | Returns | Wraps |
|---|---|---|
| `readiness` | object | `docsystem readiness PROJECT --json` |
| `catalog` | object | `docsystem catalog PROJECT [--explain] --json` |
| `migration_report` | object | `docsystem migration-report PROJECT --json` |
| `changes` | object | `docsystem changes PROJECT --json` |
| `metadata_inventory` | object | `docsystem metadata-inventory PROJECT [--field NAME --values] --json` |
| `profile_check` | object | `docsystem profile-check PROJECT --json` |
| `delivery_map` | object | `docsystem delivery-map PROJECT [--contract ID#anchor ...] --json` |
| `change_plan` | object | `docsystem change-plan ID[#anchor] PROJECT [--reverse] [--transitive] [--with-delivery] --json` |
| `criteria` | object | `docsystem criteria PROJECT --json` |
| `workstream` | object | `docsystem workstream ID PROJECT --record RECORD --json` |
| `intake` | object | `docsystem intake PROJECT --request REQUEST --json` |
| `admission` | object | `docsystem admission ID PROJECT --request REQUEST --json` |
| `execution_handoff` | object | `docsystem execution-handoff ID PROJECT --admission REQUEST [--verify PACKET] --json` |
| `execution_result` | object | `docsystem execution-result ID PROJECT --packet PACKET --result RESULT --json` |
| `lifecycle` | object | `docsystem lifecycle ID PROJECT --admission REQUEST --packet PACKET --result RESULT --record RECORD --json` |
| `finish_handoff` | object | `docsystem finish ID PROJECT [--workstream-record RECORD] --json` |
| `context` | object | `docsystem context ID PROJECT --json ...` |
| `read_document` | text | `docsystem read ID PROJECT [--anchor/--navigation/--list]` |
| `read_document_packet` | object | `docsystem read ID PROJECT [--anchor/--navigation/--list]` |
| `dependencies` | list | `docsystem dependencies ID PROJECT [--reverse]` |
| `impact` | text (Markdown table) | `docsystem impact ID PROJECT` |
| `impact_packet` | object | `docsystem impact ID PROJECT` |
| `agent_instructions` | object | `docsystem agent-instructions PROJECT --json` |
| `workspace_list` | object | `docsystem workspace list PROJECT --json` |

Every tool takes the project root explicitly; none relies on the server
process working directory.

Project-oriented tools also accept optional `source` and `workspace`
parameters for [workspace source selection](workspace-sources.md). They append
the corresponding CLI flags only when provided, so existing MCP calls remain
unchanged. `workspace_list` reports registry metadata without reading document
bodies. The adapter does not federate sources or expose workspace mutations.

The packet tools use this envelope:

```json
{
  "schema_version": 1,
  "text": "exact CLI stdout",
  "diagnostics": ["optional successful-exit stderr line"]
}
```

`diagnostics` is omitted when the CLI emitted no non-fatal stderr.

`agent_instructions` returns the same `{"schema_version": 1, "text": ...}`
envelope, but it comes directly from `docsystem agent-instructions PROJECT
--json`: the CLI itself emits that shape, so the adapter only decodes it and
adds `diagnostics` like any other `--json` tool.

## Run and configure

Install the optional SDK through the README's
[CLI with MCP support](../README.md#cli-with-mcp-support) path. This guide owns
adapter behavior and host configuration, not package installation.

```bash
docsystem-mcp
```

or, in a development checkout:

```bash
uv run python -m docsystem.mcp_server
```

The server speaks stdio. A typical host configuration:

```json
{
  "mcpServers": {
    "docsystem": {
      "command": "docsystem-mcp"
    }
  }
}
```

Without the `mcp` package installed, the tool functions in
`docsystem.mcp_server` still work as plain Python (they only need the
`docsystem` CLI importable); only starting the server requires the SDK.
