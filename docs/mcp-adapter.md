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
- text tools (`read_document`, `impact`) return the CLI's stdout unchanged for
  compatibility. Structured packet variants (`read_document_packet`,
  `impact_packet`) return the same stdout under `text` and add `diagnostics`
  when successful commands emitted non-fatal stderr, such as projection
  fallback warnings;
- only read-only commands are exposed. Mutating operations (`init`,
  `migrate --apply`, `index --write`) intentionally have no tools and stay
  with the human or calling system, matching
  [the agent contract](agent-contract.md);
- a non-zero CLI exit becomes a tool error carrying the CLI's stderr
  diagnostics, so a client never mistakes a failure for data. The one
  exception is `readiness`, whose "not ready" state is a legitimate answer:
  it returns the payload with `"ready": false` instead of raising.

## Tools

| Tool | Returns | Wraps |
|---|---|---|
| `readiness` | object | `docsystem readiness PROJECT --json` |
| `catalog` | object | `docsystem catalog PROJECT [--explain] --json` |
| `migration_report` | object | `docsystem migration-report PROJECT --json` |
| `changes` | object | `docsystem changes PROJECT --json` |
| `context` | object | `docsystem context ID PROJECT --json ...` |
| `read_document` | text | `docsystem read ID PROJECT [--anchor/--navigation/--list]` |
| `read_document_packet` | object | `docsystem read ID PROJECT [--anchor/--navigation/--list]` |
| `dependencies` | list | `docsystem dependencies ID PROJECT [--reverse]` |
| `impact` | text (Markdown table) | `docsystem impact ID PROJECT` |
| `impact_packet` | object | `docsystem impact ID PROJECT` |

Every tool takes the project root explicitly; none relies on the server
process working directory.

The packet tools use this envelope:

```json
{
  "text": "exact CLI stdout",
  "diagnostics": ["optional successful-exit stderr line"]
}
```

`diagnostics` is omitted when the CLI emitted no non-fatal stderr.

## Install and run

The MCP SDK is an optional dependency:

```bash
pip install "docsystem[mcp]"
docsystem-mcp
```

or, in a development checkout:

```bash
python -m docsystem.mcp_server
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
