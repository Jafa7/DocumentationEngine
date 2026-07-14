"""Thin MCP (Model Context Protocol) adapter over the `docsystem` CLI.

The adapter translates MCP tool calls into the same CLI invocations the
agent contract documents, running them in a subprocess so it can never
bypass the core's validation, projection-fallback or output contracts.
Only read-only commands are exposed; mutating operations (`init`,
`migrate --apply`, `index --write`) stay with the human or calling system,
matching `docs/agent-contract.md`.

Structured (object) tools surface any successful-exit CLI stderr -- most
importantly the `projection stale/corrupt; using direct Markdown` fallback
warning -- under a `diagnostics` key, so a client never loses that signal.
Text tools (`read_document`, `impact`) keep returning the CLI stdout unchanged
for compatibility. Their packet variants (`read_document_packet`,
`impact_packet`) expose the same stdout together with successful-exit
diagnostics in a structured envelope.

The `mcp` package is an optional dependency: install `documentation-engine[mcp]`
to run the server (`docsystem-mcp` or `python -m docsystem.mcp_server`). The
tool functions themselves are plain Python and work without it.
"""

from __future__ import annotations

import json
import subprocess
import sys


def _invoke(
    arguments: list[str], *, allow_failure_payload: bool = False
) -> tuple[str, str]:
    """Run a CLI command, returning its stdout and stderr.

    A non-zero exit raises `RuntimeError` carrying the CLI diagnostics, so a
    client never mistakes a failure for data. The one exception is a command
    that prints a payload while exiting non-zero (for example `readiness`
    reporting a legitimate "not ready" state): with `allow_failure_payload`
    that payload is returned instead of raising.
    """

    result = subprocess.run(
        [sys.executable, "-m", "docsystem", *arguments],
        capture_output=True,
        encoding="utf-8",
        text=True,
    )
    if result.returncode != 0 and not (allow_failure_payload and result.stdout.strip()):
        message = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(message or f"docsystem {' '.join(arguments)} failed")
    return result.stdout, result.stderr


def _run_cli(arguments: list[str], *, allow_failure_payload: bool = False) -> str:
    stdout, _ = _invoke(arguments, allow_failure_payload=allow_failure_payload)
    return stdout


def _text_packet(arguments: list[str]) -> dict:
    """Run a text CLI command and preserve successful-exit diagnostics."""

    stdout, stderr = _invoke(arguments)
    payload = {"schema_version": 1, "text": stdout}
    diagnostics = _diagnostics(stderr)
    if diagnostics:
        payload["diagnostics"] = diagnostics
    return payload


def _diagnostics(stderr: str) -> list[str]:
    """Return non-empty successful-exit stderr lines, in CLI order."""

    return [line for line in stderr.splitlines() if line.strip()]


def _selection_arguments(
    source: str | None, workspace: str | None
) -> list[str]:
    """Return the workspace selection flags, empty when neither is given.

    Appending nothing on omission keeps every existing invocation's argv
    byte-identical, so adding these parameters cannot change the behavior of
    a client that never sets them.
    """

    arguments: list[str] = []
    if source is not None:
        arguments.extend(["--source", source])
    if workspace is not None:
        arguments.extend(["--workspace", workspace])
    return arguments


def _json_tool(
    arguments: list[str], *, allow_failure_payload: bool = False
) -> dict:
    """Run a `--json` CLI command and decode its structured payload.

    On a successful exit the CLI can still print diagnostics to stderr -- most
    importantly the `WARNING: projection ...; using direct Markdown` note when
    a `context`/read command falls back from a stale or corrupt projection.
    Those lines would otherwise be lost, so they are surfaced deterministically
    under a `diagnostics` key. The key is present only when the CLI emitted
    such diagnostics, keeping every other payload byte-identical to the CLI's
    `--json` output.
    """

    stdout, stderr = _invoke(arguments, allow_failure_payload=allow_failure_payload)
    payload = json.loads(stdout)
    diagnostics = _diagnostics(stderr)
    if diagnostics:
        payload["diagnostics"] = diagnostics
    return payload


def readiness(
    project: str, source: str | None = None, workspace: str | None = None
) -> dict:
    """Report adoption readiness for a project as a structured object.

    Read-only. `ready` is false while blocking errors remain; `next_command`
    names the single safe next step. Always pass the project root explicitly.
    Set `source` to run against a named source from the local workspace
    registry instead, optionally overriding the registry root with
    `workspace`; the payload then names the project by its reusable
    `--source NAME` selector rather than a local path.
    """

    arguments = ["readiness", project, "--json", *_selection_arguments(source, workspace)]
    return _json_tool(arguments, allow_failure_payload=True)


def catalog(
    project: str,
    explain: bool = False,
    source: str | None = None,
    workspace: str | None = None,
) -> dict:
    """List cataloged Markdown documents and their logical roles.

    Read-only. With `explain`, classifies every Markdown source under the
    documentation root as included, excluded or unmapped. `source` and
    `workspace` select a registered workspace source instead of `project`.
    """

    arguments = ["catalog", project, "--json"]
    if explain:
        arguments.insert(2, "--explain")
    arguments.extend(_selection_arguments(source, workspace))
    return _json_tool(arguments)


def migration_report(
    project: str, source: str | None = None, workspace: str | None = None
) -> dict:
    """Report resolvable legacy relation migrations and explicit boundaries.

    Read-only dry run: `resolved` rows are safe path-to-ID migration
    candidates; `boundaries` (external URLs, resources) are human decisions
    and must never be converted into invented document IDs. `source` and
    `workspace` select a registered workspace source instead of `project`.
    """

    arguments = [
        "migration-report",
        project,
        "--json",
        *_selection_arguments(source, workspace),
    ]
    return _json_tool(arguments)


def changes(
    project: str, source: str | None = None, workspace: str | None = None
) -> dict:
    """Report documents and sections changed since the selected projection.

    `source` and `workspace` select a registered workspace source instead of
    `project`.
    """

    arguments = ["changes", project, "--json", *_selection_arguments(source, workspace)]
    return _json_tool(arguments)


def workspace_list(project: str, workspace: str | None = None) -> dict:
    """List the selectable sources in the local workspace registry.

    Read-only. Reports each registered source's name, visibility,
    availability and a fixed reason slug when it cannot be selected. Never
    emits a local path or any document content. The registry is discovered
    from `workspace`, the `DOCSYSTEM_WORKSPACE` environment variable, or a
    `.docsystem.local.toml` pointer in `project`, in that order.
    """

    arguments = ["workspace", "list", project, "--json"]
    if workspace is not None:
        arguments.extend(["--workspace", workspace])
    return _json_tool(arguments)


def context(
    project: str,
    document_id: str,
    depth: int | None = None,
    include_related: bool = False,
    include: list[str] | None = None,
    anchor: str | None = None,
    outline: bool = False,
    assume_known: list[str] | None = None,
    since: str | None = None,
    source: str | None = None,
    workspace: str | None = None,
    view: str | None = None,
) -> dict:
    """Build a deterministic, inspectable context packet for a document.

    Read-only. The packet reports exactly what was included and omitted —
    never a silent token-budget truncation. Expand coverage with `depth`,
    `include_related` or explicit `include` selections (`ID` or `ID#anchor`)
    instead of assuming omitted material is irrelevant. Set `view` to a
    project-authored purpose view; it supplies delivery, direction, depth and
    authored relation filters and returns every filtered/depth-stopped edge in
    `view_omissions`. A view cannot combine with the manual `depth`,
    `include_related` or `outline` controls. Set `outline` for a
    map-first packet of per-section `lines`/`bytes` sizes with no document
    content, to budget tokens before fetching; `outline` cannot combine with
    `anchor` or `include`. Declare documents already held with
    `assume_known` (`ID@REV`, repeatable): a document at the declared
    revision has its content omitted, a mismatch keeps it. Request a delta
    against a retained projection generation with `since` (full hash or
    unique >=12-char prefix): unchanged documents are omitted and changed
    ones carry only their changed sections. `since` cannot combine with
    `assume_known`, and neither combines with `outline`. If the CLI serves
    the packet by falling back from a stale or corrupt projection to direct
    Markdown, the fallback warning is surfaced under `diagnostics`. `source`
    and `workspace` select a registered workspace source instead of `project`.
    """

    if view is not None and (depth is not None or include_related or outline):
        raise ValueError(
            "view cannot combine with depth, include_related or outline"
        )
    arguments = ["context", document_id, project, "--json"]
    if view is not None:
        arguments.extend(["--view", view])
    elif depth is not None:
        arguments.extend(["--depth", str(depth)])
    if include_related:
        arguments.append("--include-related")
    if anchor is not None:
        arguments.extend(["--anchor", anchor])
    for item in include or []:
        arguments.extend(["--include", item])
    if outline:
        arguments.append("--outline")
    for item in assume_known or []:
        arguments.extend(["--assume-known", item])
    if since is not None:
        arguments.extend(["--since", since])
    arguments.extend(_selection_arguments(source, workspace))
    return _json_tool(arguments)


def _read_arguments(
    project: str,
    document_id: str,
    anchor: str | None,
    navigation: bool,
    list_sections: bool,
    source: str | None,
    workspace: str | None,
) -> list[str]:
    arguments = ["read", document_id, project]
    if anchor is not None:
        arguments.extend(["--anchor", anchor])
    elif navigation:
        arguments.append("--navigation")
    elif list_sections:
        arguments.append("--list")
    arguments.extend(_selection_arguments(source, workspace))
    return arguments


def read_document(
    project: str,
    document_id: str,
    anchor: str | None = None,
    navigation: bool = False,
    list_sections: bool = False,
    source: str | None = None,
    workspace: str | None = None,
) -> str:
    """Read a Markdown document, navigation prefix or section by stable ID.

    Read-only. `anchor` returns one section; `navigation` returns the
    navigation prefix; `list_sections` returns tab-separated section rows
    (`anchor`, `Hn`, `start:end`, `title`) in document order. `source` and
    `workspace` select a registered workspace source instead of `project`.
    """

    return _run_cli(
        _read_arguments(
            project, document_id, anchor, navigation, list_sections, source, workspace
        )
    )


def read_document_packet(
    project: str,
    document_id: str,
    anchor: str | None = None,
    navigation: bool = False,
    list_sections: bool = False,
    source: str | None = None,
    workspace: str | None = None,
) -> dict:
    """Read a document and return text plus successful-exit diagnostics.

    This is the structured counterpart to `read_document`: it preserves the
    exact CLI stdout under `text` and adds `diagnostics` when the CLI emitted
    non-fatal stderr, such as projection fallback warnings.
    """

    return _text_packet(
        _read_arguments(
            project, document_id, anchor, navigation, list_sections, source, workspace
        )
    )


def dependencies(
    project: str,
    document_id: str,
    reverse: bool = False,
    source: str | None = None,
    workspace: str | None = None,
) -> list[dict]:
    """List forward (or, with `reverse`, incoming) semantic dependency edges.

    Read-only. Fails closed with an error instead of returning a silently
    incomplete graph when metadata errors affect the answer. `source` and
    `workspace` select a registered workspace source instead of `project`.
    """

    arguments = ["dependencies", document_id, project]
    if reverse:
        arguments.append("--reverse")
    arguments.extend(_selection_arguments(source, workspace))
    rows: list[dict] = []
    for line in _run_cli(arguments).splitlines():
        relation, peer_id, expected = line.split("\t")
        rows.append(
            {
                "relation": relation,
                "peer_id": peer_id,
                "expected_revision": None if expected == "-" else int(expected),
            }
        )
    return rows


def impact(
    project: str,
    document_id: str,
    source: str | None = None,
    workspace: str | None = None,
) -> str:
    """Report reverse metadata impact for a document as a Markdown table.

    Read-only. Distinguishes semantic, related-navigation, freshness-pin and
    configured historical-snapshot relations. `source` and `workspace` select
    a registered workspace source instead of `project`.
    """

    return _run_cli(
        ["impact", document_id, project, *_selection_arguments(source, workspace)]
    )


def impact_packet(
    project: str,
    document_id: str,
    source: str | None = None,
    workspace: str | None = None,
) -> dict:
    """Report reverse metadata impact with text plus non-fatal diagnostics."""

    return _text_packet(
        ["impact", document_id, project, *_selection_arguments(source, workspace)]
    )


def agent_instructions(
    project: str, source: str | None = None, workspace: str | None = None
) -> dict:
    """Return the deterministic agent-rules snippet for AGENTS.md/CLAUDE.md.

    Read-only: derived from the project's `.docsystem.toml` plus the
    engine's stable agent contract, never from parsing
    `docs/setup-guide.md`, so the pasted snippet cannot drift from the
    project's actually configured areas and identifiers. Works even when
    the documentation root itself is missing, since only configuration is
    read. Under a selected `source` the snippet addresses the project as
    `--source NAME`, never by its local path, so it stays safe to commit.
    """

    arguments = [
        "agent-instructions",
        project,
        "--json",
        *_selection_arguments(source, workspace),
    ]
    return _json_tool(arguments)


_TOOLS = (
    readiness,
    catalog,
    migration_report,
    changes,
    context,
    read_document,
    read_document_packet,
    dependencies,
    impact,
    impact_packet,
    agent_instructions,
    workspace_list,
)


def build_server():
    """Create the FastMCP server with every read-only tool registered."""

    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as error:
        raise RuntimeError(
            "the MCP adapter requires the optional 'mcp' dependency; "
            "install it with: pip install 'documentation-engine[mcp]'"
        ) from error
    server = FastMCP("docsystem")
    for tool in _TOOLS:
        server.tool()(tool)
    return server


def main() -> int:
    build_server().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
