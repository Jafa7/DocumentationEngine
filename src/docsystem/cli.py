"""Command-line interface for Documentation Engine."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from docsystem import __version__
from docsystem.admission import (
    AdmissionError,
    AdmissionEvaluation,
    AdmissionRequest,
    normalize_source_path,
)
from docsystem.admission import (
    evaluate_request as evaluate_admission_request,
)
from docsystem.admission import (
    load_request as load_admission_request,
)
from docsystem.catalog import (
    MarkdownCatalog,
    RelationBoundary,
    RelationMigration,
    ValidationIssue,
    build_catalog,
    build_dependency_graph,
    document_section_issues,
    find_document,
    validate_adoption,
    validate_catalog,
    validate_membership,
    validate_metadata,
)
from docsystem.change_plan import (
    ChangePlan,
    Completeness,
    InclusionReason,
    PlanItem,
    build_change_plan,
)
from docsystem.config import (
    CONFIG_FILENAME,
    DEFAULT_CONFIG,
    AdmissionCriterion,
    ContextView,
    IntakeCriterion,
    IntakePlacement,
    ProjectConfig,
    WorkstreamCriterion,
    is_historical_snapshot,
    load_config,
)
from docsystem.delivery import DeliveryReport, evaluate_delivery
from docsystem.execution import (
    ExecutionPacketError,
    load_execution_result,
    load_packet,
    seal_packet,
)
from docsystem.graph import (
    Address,
    Boundary,
    GraphEdge,
    ProjectionUnavailable,
    SectionReferenceGraph,
    TraversalResult,
    build_reference_graph,
    graph_validation_issues,
    parse_address,
)
from docsystem.graph import (
    traverse as graph_traverse,
)
from docsystem.graph import (
    traverse_reasons as graph_traverse_reasons,
)
from docsystem.health import (
    GraphHealthReport,
    evaluate_graph_health,
    facts_from_catalog,
    facts_from_projection,
)
from docsystem.intake import (
    IntakeError,
    IntakeEvaluation,
    IntakeRequest,
)
from docsystem.intake import (
    evaluate_request as evaluate_intake_request,
)
from docsystem.intake import (
    load_request as load_intake_request,
)
from docsystem.inventory import (
    FieldOccurrence,
    MetadataInventory,
    build_metadata_inventory,
    field_occurrences,
)
from docsystem.journal import (
    FileEdit,
    FileGuard,
    JournalError,
    LineRange,
    recover_generation,
    run_bounded_transaction,
    validate_workstream_id,
)
from docsystem.maintenance import (
    CLEAN,
    CURRENT,
    DRIFTED,
    EXCLUDED,
    MANAGED,
    SOURCE,
    block_lines,
    block_text,
    resolve_marker,
    resolve_marker_in_section,
    scan_markers,
    sha256_text,
    span_within_section,
    unified_block_diff,
)
from docsystem.migration import apply_migration_plan, build_migration_plan, validate_plan
from docsystem.profiles import ProfileReport, evaluate_profiles
from docsystem.projection import (
    LoadedProjection,
    build_projection,
    evaluate_changes,
    load_verified_projection,
    open_targeted_projection,
    projection_status,
    resolve_generation_manifest,
    targeted_forward_edges,
    targeted_reverse_edges,
    write_projection,
)
from docsystem.projection import (
    changes as projection_changes,
)
from docsystem.readiness import evaluate_readiness
from docsystem.sections import MarkdownSection, extract_navigation, extract_section
from docsystem.workspace import (
    WorkspaceError,
    resolve_source_root,
    resolve_workspace,
    source_statuses,
)
from docsystem.workstream import (
    WorkstreamError,
    WorkstreamEvaluation,
    WorkstreamRecord,
    evaluate_record,
    load_record,
)

# Version of every `--json` root object. Bump only on a breaking change to
# an existing field; adding new fields is compatible and does not bump it.
JSON_SCHEMA_VERSION = 1

REPORT_TYPES = {
    "runtime-report": "Runtime Report",
    "adoption-finding": "Adoption Finding",
    "core-bug": "Core Bug",
    "docs-pattern-request": "Docs Pattern Request",
}
REPORT_SOURCES = ("codex", "claude", "vscode", "other")
REPORT_COMPONENTS = (
    "catalog",
    "metadata",
    "sections",
    "relations",
    "graph",
    "navigation",
    "anchors",
    "projection",
    "context",
    "mcp",
    "cli",
    "adoption",
    "profiles",
    "readiness",
    "reporting",
    "setup",
    "local-state",
    "privacy",
)
CONTEXT_GAP_REASONS = (
    "task_requires_full_review",
    "missing_dependency",
    "missing_section",
    "poor_section_granularity",
    "unresolved_authority",
    "stale_relation",
    "missing_reverse_reference",
    "navigation_insufficient",
    "profile_gap",
    "external_boundary",
    "agent_uncertainty",
    "manual_inspection",
)
CONTEXT_GAP_LOCAL_REASONS = frozenset(
    {
        "task_requires_full_review",
        "agent_uncertainty",
        "manual_inspection",
    }
)
CONTEXT_GAP_IMPACTS = ("plan", "scope", "decision", "verification", "result")
CONTEXT_EXPANSION_STATES = ("not-observed", "normal", "material-gap")
CONTEXT_GAP_REPORT_STATES = ("not-needed", "drafted", "filed")


@dataclass(frozen=True)
class _Selection:
    """The one project a command runs against, and how it was addressed.

    Without `--source` this is exactly the positional project root and every
    command behaves as before. With `--source` the root comes from the local
    workspace registry, and `selector` replaces the root in any output a
    reader could paste elsewhere, so a private workspace path never leaves the
    machine that holds it.
    """

    project_root: Path
    source: str | None = None
    discovery_root: Path | None = None

    @property
    def project_argument(self) -> Path:
        """Return the caller's public/discovery root, not the private source."""

        return self.discovery_root or self.project_root

    @property
    def selector(self) -> str:
        if self.source is None:
            return str(self.project_argument)
        return f"{self.project_argument} --source {self.source}"

    @property
    def report_selector(self) -> str:
        """Render source selection without colliding with report host source."""

        if self.source is None:
            return str(self.project_argument)
        return f"{self.project_argument} --workspace-source {self.source}"


@dataclass(frozen=True)
class _ReferencesOutput:
    results: tuple[TraversalResult, ...]
    boundaries: tuple[Boundary, ...]
    observed_completeness: str


@dataclass(frozen=True)
class _ContextGapEvidence:
    """Body-free evidence for one agent-declared material context gap."""

    reason: str
    impacts: tuple[str, ...]
    initial: tuple[str, ...]
    expanded: tuple[str, ...]
    projection: str


@dataclass(frozen=True)
class _ContextViewOmission:
    """One authored edge a purpose view deliberately did not traverse."""

    source_id: str
    direction: str
    relation: str
    peer_id: str
    reason: str


class _ReferenceGraphInvalid(Exception):
    """Graph-affecting diagnostics that make a query unsafe to answer."""

    def __init__(self, issues: tuple[ValidationIssue, ...]) -> None:
        super().__init__("reference graph is invalid")
        self.issues = issues


def _resolve_selection(args: argparse.Namespace) -> _Selection | None:
    """Resolve the effective project, or print one diagnostic and fail closed.

    Workspace state is neither loaded nor validated when `--source` is absent,
    so an unrelated broken workspace can never affect a plain project command.
    """

    source = getattr(args, "workspace_source", None)
    if source is None:
        if getattr(args, "workspace", None) is not None:
            print(
                "ERROR: --workspace requires --source/--workspace-source",
                file=sys.stderr,
            )
            return None
        return _Selection(args.project)
    try:
        project_root = resolve_source_root(
            source,
            workspace_option=getattr(args, "workspace", None),
            project_root=args.project,
        )
    except WorkspaceError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return None
    return _Selection(project_root, source, args.project)


def _print_json(payload: dict[str, object]) -> None:
    print(
        json.dumps(
            {"schema_version": JSON_SCHEMA_VERSION, **payload},
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def _write_text_or_stdout(text: str, output: Path | None) -> int:
    if output is None:
        sys.stdout.write(text)
        return 0
    try:
        output.write_text(text, encoding="utf-8")
    except OSError as error:
        print(f"ERROR: failed to write report draft: {error}", file=sys.stderr)
        return 1
    print(f"Report draft written: {output}")
    return 0


def _label_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "unknown"


def _migration_json(item: RelationMigration) -> dict[str, object]:
    return {
        "source_id": item.source_id,
        "relation": item.relation,
        "value": item.value,
        "target_id": item.target_id,
    }


def _boundary_json(item: RelationBoundary) -> dict[str, object]:
    return {
        "source_id": item.source_id,
        "relation": item.relation,
        "value": item.value,
        "reason": item.reason,
    }


def _with_graph_issues(
    issues: tuple[ValidationIssue, ...],
    markdown_catalog: MarkdownCatalog,
    config: ProjectConfig,
) -> tuple[ValidationIssue, ...]:
    """Fold relation-specific cycle and dead-reference diagnostics into validate/doctor."""

    return tuple(
        sorted(
            (*issues, *graph_validation_issues(markdown_catalog, config)),
            key=lambda issue: (issue.path.as_posix(), issue.severity, issue.message),
        )
    )


def _print_validation_issues(
    issues: tuple[ValidationIssue, ...],
    *,
    verbose_adoption: bool,
) -> bool:
    adoption_counts = {
        "adoption-resolved": 0,
        "adoption-boundary": 0,
    }
    visible: list[ValidationIssue] = []
    for issue in issues:
        compactable = (
            issue.severity == "warning" and issue.category in adoption_counts
        )
        if compactable and not verbose_adoption:
            adoption_counts[issue.category] += 1
        else:
            visible.append(issue)

    summaries = (
        (
            adoption_counts["adoption-resolved"],
            "legacy relation values resolve to stable IDs",
        ),
        (
            adoption_counts["adoption-boundary"],
            "legacy relation values remain resource/outside boundaries",
        ),
    )
    for count, description in summaries:
        if count:
            print(
                f"WARNING: {count} {description}; run "
                "`docsystem migration-report PROJECT` for row-level details.",
                file=sys.stderr,
            )
    for issue in visible:
        level = "WARNING" if issue.severity == "warning" else "ERROR"
        print(
            f"{level}: {issue.path.as_posix()}: {issue.message}",
            file=sys.stderr,
        )
    return any(issue.severity != "warning" for issue in issues)


def _workspace_status_rows(
    project_root: Path, workspace_option: Path | None
) -> list[dict[str, object]]:
    workspace = resolve_workspace(
        workspace_option=workspace_option, project_root=project_root
    )
    return [
        {
            "name": status.name,
            "visibility": status.visibility,
            "available": status.available,
            "reason": status.reason,
        }
        for status in source_statuses(workspace)
    ]


def workspace_list(
    project_root: Path,
    *,
    workspace_option: Path | None = None,
    json_output: bool = False,
) -> int:
    """List registered sources, sorted by name, with availability.

    Read-only and body-free: only the registered name, its declared
    visibility, whether it can be selected and a fixed reason slug are
    reported. No local path and no document content is ever emitted, so a
    listing is safe to share.
    """

    try:
        rows = _workspace_status_rows(project_root, workspace_option)
    except WorkspaceError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if json_output:
        _print_json({"sources": rows})
        return 0
    for row in rows:
        state = "available" if row["available"] else "unavailable"
        print(f"{row['name']}\t{row['visibility']}\t{state}\t{row['reason'] or '-'}")
    return 0


def workspace_doctor(
    project_root: Path,
    *,
    workspace_option: Path | None = None,
    json_output: bool = False,
) -> int:
    """Report whether every registered source is currently selectable."""

    try:
        rows = _workspace_status_rows(project_root, workspace_option)
    except WorkspaceError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    unavailable = [row for row in rows if not row["available"]]
    if json_output:
        _print_json({"sources": rows, "ready": not unavailable})
        return 1 if unavailable else 0
    print(f"Workspace manifest is valid: {len(rows)} source(s).")
    print(f"- Available sources: {len(rows) - len(unavailable)}")
    print(f"- Unavailable sources: {len(unavailable)}")
    for row in unavailable:
        print(
            f"ERROR: workspace source is unavailable: {row['name']} "
            f"({row['reason']})",
            file=sys.stderr,
        )
    return 1 if unavailable else 0


def initialize(project_root: Path) -> int:
    root = project_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / CONFIG_FILENAME
    if config_path.exists():
        print(f"Refusing to overwrite existing configuration: {config_path}", file=sys.stderr)
        return 1
    config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    config = load_config(root)
    config.documentation_root.mkdir(parents=True, exist_ok=True)
    print(f"Created {config_path}")
    print(f"Created documentation root: {config.documentation_root}")
    return 0


def doctor(project_root: Path, *, verbose_adoption: bool = False) -> int:
    try:
        config = load_config(project_root)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if not config.documentation_root.is_dir():
        print(
            f"ERROR: documentation root does not exist: "
            f"{config.documentation_root}",
            file=sys.stderr,
        )
        return 1
    markdown_catalog = build_catalog(config)
    issues = _with_graph_issues(
        validate_catalog(markdown_catalog, config), markdown_catalog, config
    )
    if _print_validation_issues(
        issues, verbose_adoption=verbose_adoption
    ):
        return 1
    profile_report = evaluate_profiles(markdown_catalog, config)
    if profile_report.violations:
        _print_profile_violations(profile_report)
        return 1
    delivery_report = evaluate_delivery(markdown_catalog, config)
    if delivery_report.violations:
        _print_delivery_violations(delivery_report)
        return 1
    print("Configuration is valid.")
    print(f"Documentation root: {config.documentation_root}")
    print(f"Language: {config.language}")
    print(f"Projection: {config.projection_format}")
    return 0


def catalog(project_root: Path, *, explain: bool = False, json_output: bool = False) -> int:
    try:
        config = load_config(project_root)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    markdown_catalog = build_catalog(config)
    if explain:
        if json_output:
            _print_json(
                {
                    "memberships": [
                        {
                            "state": membership.state,
                            "path": membership.path.as_posix(),
                            "role": membership.role,
                            "reason": membership.reason,
                        }
                        for membership in markdown_catalog.memberships
                    ]
                }
            )
            return 0
        for membership in markdown_catalog.memberships:
            detail = membership.role or membership.reason or "-"
            print(f"{membership.state}\t{detail}\t{membership.path.as_posix()}")
        return 0
    if json_output:
        _print_json(
            {
                "documents": [
                    {"role": document.role, "path": document.path.as_posix()}
                    for document in markdown_catalog.documents
                ]
            }
        )
        return 0
    for document in markdown_catalog.documents:
        print(f"{document.role}\t{document.path.as_posix()}")
    return 0


def validate(project_root: Path, *, verbose_adoption: bool = False) -> int:
    try:
        config = load_config(project_root)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    markdown_catalog = build_catalog(config)
    issues = _with_graph_issues(
        validate_catalog(markdown_catalog, config), markdown_catalog, config
    )
    if _print_validation_issues(
        issues, verbose_adoption=verbose_adoption
    ):
        return 1
    profile_report = evaluate_profiles(markdown_catalog, config)
    if profile_report.violations:
        _print_profile_violations(profile_report)
        return 1
    delivery_report = evaluate_delivery(markdown_catalog, config)
    if delivery_report.violations:
        _print_delivery_violations(delivery_report)
        return 1
    print("Markdown navigation is valid.")
    return 0


def _graph_health_json(report: GraphHealthReport) -> dict[str, object]:
    return {
        "metrics": {
            "documents": report.document_count,
            "sections": report.section_count,
            "edges": report.edge_count,
            "edges_by_authority": dict(report.edges_by_authority),
            "edges_by_relation": dict(report.edges_by_relation),
            "boundaries": report.boundary_count,
            "boundaries_by_category": dict(report.boundaries_by_category),
            "weak_component_sizes": list(report.weak_component_sizes),
            "orphan_documents": list(report.orphan_documents),
            "stale_pins": report.stale_pin_count,
            "historical_pins": report.historical_pin_count,
            "missing_metadata": dict(report.missing_metadata),
        },
        "signals": [
            {
                "code": signal.code,
                "severity": "advisory",
                "documents": list(signal.documents),
                "value": signal.value,
                "threshold": signal.threshold,
                "detail": signal.detail,
            }
            for signal in report.signals
        ],
    }


def _emit_graph_health_text(report: GraphHealthReport) -> None:
    print("# Graph health")
    print()
    print(f"- Documents: {report.document_count}")
    print(f"- Sections: {report.section_count}")
    print(f"- Edges: {report.edge_count}")
    print(f"- Boundaries: {report.boundary_count}")
    print(
        "- Weak components: "
        + (", ".join(str(item) for item in report.weak_component_sizes) or "none")
    )
    print(
        "- Orphan documents: "
        + (", ".join(report.orphan_documents) or "none")
    )
    print(f"- Stale pins: {report.stale_pin_count}")
    print(f"- Historical pins: {report.historical_pin_count}")
    print()
    print("## Inventory")
    print()
    print("| Kind | Name | Count |")
    print("| --- | --- | ---: |")
    inventory = (
        *(("authority", name, count) for name, count in report.edges_by_authority),
        *(("relation", name, count) for name, count in report.edges_by_relation),
        *(("boundary", name, count) for name, count in report.boundaries_by_category),
        *(("missing-metadata", name, count) for name, count in report.missing_metadata),
    )
    if inventory:
        for kind, name, count in inventory:
            print(f"| {kind} | {name} | {count} |")
    else:
        print("| - | - | 0 |")
    print()
    print("## Advisory signals")
    print()
    print("| Code | Documents | Value | Threshold | Detail |")
    print("| --- | --- | ---: | ---: | --- |")
    if report.signals:
        for signal in report.signals:
            documents = ", ".join(signal.documents) or "-"
            threshold = str(signal.threshold) if signal.threshold is not None else "-"
            print(
                f"| {signal.code} | {documents} | {signal.value} | "
                f"{threshold} | {signal.detail} |"
            )
    else:
        print("| none | - | 0 | - | No configured advisory threshold was crossed. |")


def graph_health(project_root: Path, *, json_output: bool = False) -> int:
    """Report deterministic graph metrics without granting write authority."""

    try:
        config = load_config(project_root)
        loaded, reason = load_verified_projection(config, include_references=True)
        if loaded is not None:
            facts = facts_from_projection(loaded)
        else:
            markdown_catalog = build_catalog(config)
            issues = _with_graph_issues(
                validate_catalog(markdown_catalog, config),
                markdown_catalog,
                config,
            )
            blocking = tuple(issue for issue in issues if issue.severity != "warning")
            if blocking:
                _print_validation_issues(blocking, verbose_adoption=False)
                return 1
            print(f"WARNING: {reason}; using direct Markdown", file=sys.stderr)
            facts = facts_from_catalog(markdown_catalog, config)
        report = evaluate_graph_health(facts, config)
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if json_output:
        _print_json(_graph_health_json(report))
    else:
        _emit_graph_health_text(report)
    return 0


def _metadata_inventory_json(
    report: MetadataInventory,
    *,
    field_name: str | None,
    occurrences: tuple[FieldOccurrence, ...],
    show_values: bool,
) -> dict[str, object]:
    fields = [
        {
            "name": item.name,
            "category": item.category,
            "present_documents": item.present_documents,
            "missing_documents": item.missing_documents,
            "observed_types": list(item.observed_types),
            "document_types": list(item.document_types),
            "type_conflict": item.type_conflict,
        }
        for item in report.fields
        if field_name is None or item.name == field_name
    ]
    payload: dict[str, object] = {
        "schema_version": 1,
        "document_count": report.document_count,
        "fields": fields,
        "documents": [
            {
                "id": item.document_id,
                "revision": item.revision,
                "role": item.role,
                "path": item.path,
                "type": item.document_type,
                "status": item.status,
                "additional_fields": list(item.additional_fields),
                "graph": {
                    "incoming": item.incoming_edges,
                    "outgoing": item.outgoing_edges,
                    "boundaries": item.boundaries,
                },
            }
            for item in report.documents
        ],
    }
    if show_values:
        payload["values"] = [
            {
                "id": item.document_id,
                "path": item.path,
                "type": item.value_type,
                "value": item.value,
            }
            for item in occurrences
        ]
    return payload


def _emit_metadata_inventory_text(
    report: MetadataInventory,
    *,
    field_name: str | None,
    occurrences: tuple[FieldOccurrence, ...],
) -> None:
    print(f"summary\tdocuments\t{report.document_count}")
    for item in report.fields:
        if field_name is not None and item.name != field_name:
            continue
        observed_types = ",".join(item.observed_types) or "-"
        document_types = ",".join(item.document_types) or "-"
        print(
            f"field\t{item.name}\tcategory={item.category}\t"
            f"present={item.present_documents}\tmissing={item.missing_documents}\t"
            f"types={observed_types}\tdocument_types={document_types}\t"
            f"conflict={'true' if item.type_conflict else 'false'}"
        )
    for item in report.documents:
        additional = ",".join(item.additional_fields) or "-"
        print(
            f"document\t{item.document_id}\trevision={item.revision}\t"
            f"role={item.role}\tpath={item.path}\t"
            f"type={item.document_type or '-'}\tstatus={item.status or '-'}\t"
            f"additional={additional}\tincoming={item.incoming_edges}\t"
            f"outgoing={item.outgoing_edges}\tboundaries={item.boundaries}"
        )
    for item in occurrences:
        value = json.dumps(item.value, ensure_ascii=False, sort_keys=True)
        print(
            f"value\t{item.document_id}\tpath={item.path}\t"
            f"type={item.value_type}\tvalue={value}"
        )


def metadata_inventory(
    project_root: Path,
    *,
    field_name: str | None = None,
    show_values: bool = False,
    json_output: bool = False,
) -> int:
    """Inspect metadata coverage and document-level graph facts without bodies."""

    if show_values and field_name is None:
        print("ERROR: --values requires --field", file=sys.stderr)
        return 1
    try:
        config = load_config(project_root)
        catalog_value = build_catalog(config)
        issues = _with_graph_issues(
            validate_catalog(catalog_value, config), catalog_value, config
        )
        blocking = tuple(issue for issue in issues if issue.severity != "warning")
        if blocking:
            _print_validation_issues(blocking, verbose_adoption=False)
            return 1
        report = build_metadata_inventory(catalog_value, config)
        field_names = {item.name for item in report.fields}
        if field_name is not None and field_name not in field_names:
            raise ValueError(f"metadata field not found: {field_name}")
        occurrences = (
            field_occurrences(catalog_value, field_name)
            if show_values and field_name is not None
            else ()
        )
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if json_output:
        _print_json(
            _metadata_inventory_json(
                report,
                field_name=field_name,
                occurrences=occurrences,
                show_values=show_values,
            )
        )
    else:
        _emit_metadata_inventory_text(
            report, field_name=field_name, occurrences=occurrences
        )
    return 0


def _profile_report_json(report: ProfileReport) -> dict[str, object]:
    return {
        "schema_version": 1,
        "valid": report.valid,
        "profiles": [
            {
                "name": item.name,
                "document_types": list(item.document_types),
                "history_mode": item.history_mode,
                "documents": item.document_count,
                "violations": item.violation_count,
            }
            for item in report.profiles
        ],
        "documents": [
            {
                "id": item.document_id,
                "path": item.path,
                "type": item.document_type,
                "status": item.status,
                "profile": item.profile,
                "history_mode": item.history_mode,
                "valid": item.valid,
            }
            for item in report.documents
        ],
        "unprofiled_documents": list(report.unprofiled_documents),
        "violations": [
            {
                "id": item.document_id,
                "path": item.path,
                "profile": item.profile,
                "code": item.code,
                "subject": item.subject,
                "detail": item.detail,
            }
            for item in report.violations
        ],
    }


def _print_profile_violations(report: ProfileReport) -> None:
    for item in report.violations:
        print(
            f"ERROR: {item.path}: profile {item.profile}: "
            f"{item.code} ({item.subject}): {item.detail}",
            file=sys.stderr,
        )


def _emit_profile_report_text(report: ProfileReport) -> None:
    print(f"summary\tvalid\t{'true' if report.valid else 'false'}")
    for item in report.profiles:
        print(
            f"profile\t{item.name}\ttypes={','.join(item.document_types)}\t"
            f"history={item.history_mode}\tdocuments={item.document_count}\t"
            f"violations={item.violation_count}"
        )
    for item in report.documents:
        print(
            f"document\t{item.document_id}\tpath={item.path}\t"
            f"type={item.document_type or '-'}\tstatus={item.status or '-'}\t"
            f"profile={item.profile or '-'}\thistory={item.history_mode or '-'}\t"
            f"valid={('-' if item.valid is None else str(item.valid).lower())}"
        )
    for item in report.violations:
        print(
            f"violation\t{item.document_id}\tprofile={item.profile}\t"
            f"code={item.code}\tsubject={item.subject}\tdetail={item.detail}"
        )


def profile_check(project_root: Path, *, json_output: bool = False) -> int:
    """Validate documents against explicitly configured document profiles."""

    try:
        config = load_config(project_root)
        catalog_value = build_catalog(config)
        issues = _with_graph_issues(
            validate_catalog(catalog_value, config), catalog_value, config
        )
        blocking = tuple(issue for issue in issues if issue.severity != "warning")
        if blocking:
            _print_validation_issues(blocking, verbose_adoption=False)
            return 1
        report = evaluate_profiles(catalog_value, config)
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if json_output:
        _print_json(_profile_report_json(report))
    else:
        _emit_profile_report_text(report)
    return 0 if report.valid else 1


def _delivery_report_json(report: DeliveryReport) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "configured": report.configured,
        "valid": report.valid,
        "metadata_field": report.metadata_field,
        "mappings": [
            {
                "source": item.source,
                "owner_id": item.owner_id,
                "owner_path": item.owner_path,
                "owner_status": item.owner_status,
                "evidence": item.evidence,
                "disposition": item.disposition,
            }
            for item in report.mappings
        ],
        "untracked_documents": list(report.untracked_documents),
        "overlaps": list(report.overlaps),
        "violations": [
            {
                "id": item.document_id,
                "path": item.path,
                "code": item.code,
                "subject": item.subject,
                "detail": item.detail,
            }
            for item in report.violations
        ],
    }
    if report.requested_contracts:
        payload["requested_contracts"] = list(report.requested_contracts)
        payload["unowned_contracts"] = list(report.unowned_contracts)
    return payload


def _print_delivery_violations(report: DeliveryReport) -> None:
    for item in report.violations:
        print(
            f"ERROR: {item.path}: delivery {item.code} "
            f"({item.subject}): {item.detail}",
            file=sys.stderr,
        )


def _emit_delivery_report_text(report: DeliveryReport) -> None:
    print(f"summary\tconfigured\t{'true' if report.configured else 'false'}")
    print(f"summary\tvalid\t{'true' if report.valid else 'false'}")
    for source in report.requested_contracts:
        print(f"requested\t{source}")
    for item in report.mappings:
        print(
            f"mapping\t{item.source}\towner={item.owner_id}\t"
            f"status={item.owner_status or '-'}\tevidence={item.evidence}\t"
            f"disposition={item.disposition}"
        )
    for document_id in report.untracked_documents:
        print(f"untracked\t{document_id}")
    for source in report.overlaps:
        print(f"overlap\t{source}")
    for source in report.unowned_contracts:
        print(f"unowned\t{source}")
    for item in report.violations:
        print(
            f"violation\t{item.document_id}\tcode={item.code}\t"
            f"subject={item.subject}\tdetail={item.detail}"
        )


def delivery_map(
    project_root: Path,
    *,
    contracts: tuple[str, ...] = (),
    json_output: bool = False,
) -> int:
    """Build a reverse map from exact source contracts to delivery evidence."""

    try:
        config = load_config(project_root)
        catalog_value = build_catalog(config)
        issues = _with_graph_issues(
            validate_catalog(catalog_value, config), catalog_value, config
        )
        blocking = tuple(issue for issue in issues if issue.severity != "warning")
        if blocking:
            _print_validation_issues(blocking, verbose_adoption=False)
            return 1
        report = evaluate_delivery(catalog_value, config, contracts=contracts)
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if json_output:
        _print_json(_delivery_report_json(report))
    else:
        _emit_delivery_report_text(report)
    return 0 if report.valid else 1


def read_document(
    project_root: Path,
    document_id: str,
    *,
    anchor: str | None = None,
    navigation: bool = False,
    list_sections: bool = False,
) -> int:
    try:
        config = load_config(project_root)
        views, _, catalog_value = _load_views(config)
        if catalog_value is not None:
            document = find_document(catalog_value, document_id)
            section_issues = document_section_issues(document, config)
            if section_issues:
                for message in section_issues:
                    print(
                        f"ERROR: {document.path.as_posix()}: {message}",
                        file=sys.stderr,
                    )
                return 1
        view = views.get(document_id)
        if view is None:
            raise ValueError(f"document ID not found: {document_id}")
        if list_sections:
            output = "".join(
                f"{section.anchor}\tH{section.level}\t"
                f"{section.start_line}:{section.end_line}\t{section.title}\n"
                for section in view.sections
            )
        elif anchor is not None:
            section = next(
                (item for item in view.sections if item.anchor == anchor), None
            )
            if section is None:
                raise ValueError(f"anchor not found in {document_id}: {anchor}")
            output = extract_section(view.content, section)
        elif navigation:
            output = extract_navigation(
                view.content,
                view.sections,
                config.navigation_extend_through,
            )
        else:
            output = (
                view.content
                if view.content.endswith("\n")
                else f"{view.content}\n"
            )
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    sys.stdout.write(output)
    return 0


@dataclass(frozen=True)
class _EdgeView:
    """One dependency edge as served to a read command."""

    relation: str
    peer_id: str
    expected_revision: int | None


@dataclass(frozen=True)
class _DocumentView:
    """Document data required by `read`, `context` and `impact`.

    Both the direct-Markdown path and the verified-projection path reduce to
    this shape, so command output is byte-identical regardless of which path
    served it. `migrations` and `boundaries` are `(relation, value, target)`
    and `(relation, value, reason)` triples; `related_values` preserves the
    document-order raw values used by the "Related omitted" note.
    """

    document_id: str
    path: PurePosixPath
    content: str
    sections: tuple[MarkdownSection, ...]
    revision: int
    document_type: str | None
    status: str | None
    outgoing: tuple[_EdgeView, ...]
    migrations: tuple[tuple[str, str, str], ...]
    boundaries: tuple[tuple[str, str, str], ...]
    related_values: tuple[str, ...]


_Views = dict[str, _DocumentView]
_Incoming = dict[str, tuple[_EdgeView, ...]]


@dataclass(frozen=True, order=True)
class _ContextInclusionReason:
    """One exact reason a document entered a context selection."""

    via_id: str
    direction: str
    relation: str


_ContextReasons = dict[str, set[_ContextInclusionReason]]


def _freshness_rows(
    config,
    views: _Views,
    ordered: list[str],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for selected_id in ordered:
        view = views[selected_id]
        for edge in view.outgoing:
            if edge.expected_revision is None:
                continue
            dependency = views.get(edge.peer_id)
            if dependency is None or dependency.revision == edge.expected_revision:
                continue
            rows.append(
                {
                    "source_id": selected_id,
                    "target_id": edge.peer_id,
                    "pinned_revision": edge.expected_revision,
                    "current_revision": dependency.revision,
                    "classification": (
                        "historical snapshot"
                        if is_historical_snapshot(
                            config, view.document_type, view.status
                        )
                        else "stale"
                    ),
                }
            )
    return rows


def _context_selection(
    views: _Views,
    document_id: str,
    *,
    depth: int,
    include_related: bool,
) -> tuple[dict[str, set[str]], _ContextReasons]:
    included: dict[str, set[str]] = {document_id: {"target"}}
    reasons: _ContextReasons = {
        document_id: {_ContextInclusionReason(document_id, "self", "target")}
    }
    queue = deque([(document_id, 0)])
    expanded: set[str] = set()
    allowed = {"derived_from", "depends_on", "validated_against"}
    if include_related:
        allowed.update({"related", "supersedes"})
    while queue:
        source_id, current_depth = queue.popleft()
        if source_id in expanded or current_depth >= depth:
            continue
        expanded.add(source_id)
        for edge in views[source_id].outgoing:
            if edge.relation not in allowed:
                continue
            included.setdefault(edge.peer_id, set()).add(edge.relation)
            reasons.setdefault(edge.peer_id, set()).add(
                _ContextInclusionReason(source_id, "forward", edge.relation)
            )
            queue.append((edge.peer_id, current_depth + 1))
    return included, reasons


def _purpose_context_selection(
    views: _Views,
    incoming: _Incoming,
    document_id: str,
    purpose_view: ContextView,
) -> tuple[
    dict[str, set[str]],
    _ContextReasons,
    tuple[_ContextViewOmission, ...],
]:
    """Traverse one authored view while preserving every filtered/stopped edge."""

    included: dict[str, set[str]] = {document_id: {"target"}}
    reasons: _ContextReasons = {
        document_id: {_ContextInclusionReason(document_id, "self", "target")}
    }
    queue = deque([(document_id, 0)])
    expanded: set[str] = set()
    omissions: set[_ContextViewOmission] = set()
    allowed = set(purpose_view.relations)
    while queue:
        source_id, current_depth = queue.popleft()
        if source_id in expanded:
            continue
        expanded.add(source_id)
        candidates: list[tuple[str, _EdgeView]] = []
        if purpose_view.direction in {"forward", "both"}:
            candidates.extend(("forward", edge) for edge in views[source_id].outgoing)
        if purpose_view.direction in {"reverse", "both"}:
            candidates.extend(("reverse", edge) for edge in incoming.get(source_id, ()))
        for direction, edge in sorted(
            candidates,
            key=lambda item: (
                item[0],
                item[1].relation,
                item[1].peer_id,
                item[1].expected_revision or 0,
            ),
        ):
            if edge.relation not in allowed:
                omissions.add(
                    _ContextViewOmission(
                        source_id,
                        direction,
                        edge.relation,
                        edge.peer_id,
                        "relation-filter",
                    )
                )
                continue
            if current_depth >= purpose_view.depth:
                if edge.peer_id not in included:
                    omissions.add(
                        _ContextViewOmission(
                            source_id,
                            direction,
                            edge.relation,
                            edge.peer_id,
                            "depth-limit",
                        )
                    )
                continue
            if edge.peer_id == document_id:
                continue
            reason = (
                edge.relation
                if direction == "forward"
                else f"reverse:{edge.relation}"
            )
            included.setdefault(edge.peer_id, set()).add(reason)
            reasons.setdefault(edge.peer_id, set()).add(
                _ContextInclusionReason(source_id, direction, edge.relation)
            )
            queue.append((edge.peer_id, current_depth + 1))
    return (
        included,
        reasons,
        tuple(
            sorted(
                omissions,
                key=lambda item: (
                    item.source_id,
                    item.direction,
                    item.relation,
                    item.peer_id,
                    item.reason,
                ),
            )
        ),
    )


def _ordered_selection(included: dict[str, set[str]], document_id: str) -> list[str]:
    return [document_id, *sorted(item for item in included if item != document_id)]


def _views_from_catalog(catalog_value: MarkdownCatalog) -> tuple[_Views, _Incoming]:
    graph = build_dependency_graph(catalog_value)
    migrations: dict[str, list[tuple[str, str, str]]] = {}
    for item in catalog_value.relation_migrations:
        migrations.setdefault(item.source_id, []).append(
            (item.relation, item.value, item.target_id)
        )
    boundaries: dict[str, list[tuple[str, str, str]]] = {}
    for item in catalog_value.relation_boundaries:
        boundaries.setdefault(item.source_id, []).append(
            (item.relation, item.value, item.reason)
        )
    views: _Views = {}
    incoming: _Incoming = {}
    for document in catalog_value.documents:
        metadata = document.metadata
        if metadata is None:
            continue
        document_id = metadata.document_id
        related_values = [
            value
            for relation, value in metadata.legacy_references
            if relation == "related"
        ]
        related_values.extend(
            reference.target_id
            for reference in metadata.references
            if reference.relation == "related"
        )
        views[document_id] = _DocumentView(
            document_id=document_id,
            path=document.path,
            content=document.content,
            sections=document.sections,
            revision=metadata.revision,
            document_type=metadata.document_type,
            status=metadata.status,
            outgoing=tuple(
                _EdgeView(edge.relation, edge.target_id, edge.expected_revision)
                for edge in graph.outgoing(document_id)
            ),
            migrations=tuple(migrations.get(document_id, ())),
            boundaries=tuple(boundaries.get(document_id, ())),
            related_values=tuple(related_values),
        )
        incoming[document_id] = tuple(
            _EdgeView(edge.relation, edge.source_id, edge.expected_revision)
            for edge in graph.incoming(document_id)
        )
    return views, incoming


def _views_from_projection(loaded: LoadedProjection) -> tuple[_Views, _Incoming]:
    views: _Views = {}
    incoming: _Incoming = {}
    for document_id, shard in loaded.documents.items():
        # Shard JSON is written with sorted keys, so section order is
        # restored from line numbers rather than mapping order.
        sections = tuple(
            MarkdownSection(
                title=str(record["title"]),
                anchor=anchor,
                level=int(record["level"]),
                start_line=int(record["start_line"]),
                end_line=int(record["end_line"]),
            )
            for anchor, record in sorted(
                shard["sections"].items(),
                key=lambda item: item[1]["start_line"],
            )
        )
        path = str(shard["path"])
        views[document_id] = _DocumentView(
            document_id=document_id,
            path=PurePosixPath(path),
            content=loaded.contents[path],
            sections=sections,
            revision=int(shard["revision"]),
            document_type=shard.get("type"),
            status=shard.get("status"),
            outgoing=tuple(
                _EdgeView(
                    record["relation"], record["target"], record.get("expected_revision")
                )
                for record in shard.get("dependencies", ())
            ),
            migrations=tuple(
                (record["relation"], record["value"], record["target"])
                for record in shard.get("migrations", ())
            ),
            boundaries=tuple(
                (record["relation"], record["value"], record["reason"])
                for record in shard.get("boundaries", ())
            ),
            related_values=tuple(str(value) for value in shard.get("related_values", ())),
        )
        incoming[document_id] = tuple(
            _EdgeView(
                record["relation"], record["source"], record.get("expected_revision")
            )
            for record in loaded.reverse.get(document_id, ())
        )
    return views, incoming


def _load_views(config) -> tuple[_Views, _Incoming, MarkdownCatalog | None]:
    """Serve reads from the verified projection, else direct Markdown.

    Returns the catalog only on the direct path; callers use its presence to
    run the validation that a verified projection already guarantees (a
    generation is only written for a tree with no blocking errors, and the
    loader proves the sources are byte-identical to that tree).
    """

    loaded, reason = load_verified_projection(config)
    if loaded is not None:
        views, incoming = _views_from_projection(loaded)
        return views, incoming, None
    print(f"WARNING: {reason}; using direct Markdown", file=sys.stderr)
    catalog_value = build_catalog(config)
    views, incoming = _views_from_catalog(catalog_value)
    return views, incoming, catalog_value


def _selection(raw: str) -> tuple[str, str | None]:
    document_id, separator, anchor = raw.partition("#")
    if not document_id or (separator and not anchor):
        raise ValueError(f"invalid include selection: {raw!r}")
    return document_id, anchor if separator else None


def _section_size_maps(view: _DocumentView) -> list[dict[str, object]]:
    """Return per-section `{anchor, title, level, lines, bytes}` in order.

    `bytes` is the exact UTF-8 size of the slice `extract_section` hashes
    (before its trailing-newline normalization), so the value is identical
    on both serving paths regardless of trailing whitespace handling.
    """

    lines = view.content.splitlines()
    maps: list[dict[str, object]] = []
    for section in view.sections:
        slice_text = "\n".join(lines[section.start_line - 1 : section.end_line])
        maps.append(
            {
                "anchor": section.anchor,
                "title": section.title,
                "level": section.level,
                "lines": section.end_line - section.start_line + 1,
                "bytes": len(slice_text.encode("utf-8")),
            }
        )
    return maps


def _source_sha(view: _DocumentView) -> str:
    """Return the sha256 of a document's full source, matching the manifest."""

    return hashlib.sha256(view.content.encode()).hexdigest()


def _section_sha(view: _DocumentView, section: MarkdownSection) -> str:
    """Return a section's sha256 over the exact slice the manifest hashes."""

    lines = view.content.splitlines()
    slice_text = "\n".join(lines[section.start_line - 1 : section.end_line])
    return hashlib.sha256(slice_text.encode()).hexdigest()


def _changed_section_anchors(
    view: _DocumentView, previous_sections: dict[str, object]
) -> tuple[str, ...]:
    """Return every anchor whose content changed since a generation, in doc order.

    A section is changed when its per-section sha256 differs from the recorded
    one or when the section is new — any level, no filtering. This is the
    complete truth signal reported as `changed_sections`: an H1's slice spans
    everything beneath it and an H2's slice spans its H3+ descendants, so a
    change anywhere always bubbles up through every enclosing anchor as well.
    Which of these anchors are actually re-emitted as `### Changed section`
    content blocks is decided separately in `_packet_sections`, since the H1
    and any `navigation.extend_through` H2 are already served by navigation.
    """

    return tuple(
        section.anchor
        for section in view.sections
        if not isinstance(previous_sections.get(section.anchor), dict)
        or previous_sections[section.anchor].get("sha256") != _section_sha(view, section)
    )


def _removed_section_anchors(
    view: _DocumentView, previous_sections: dict[str, object]
) -> tuple[str, ...]:
    """Return removed anchors in their previous document order."""

    current = {section.anchor for section in view.sections}

    def previous_line(item: tuple[str, object]) -> tuple[int, str]:
        anchor, record = item
        if isinstance(record, dict) and isinstance(record.get("start_line"), int):
            return int(record["start_line"]), anchor
        return sys.maxsize, anchor

    return tuple(
        anchor
        for anchor, _ in sorted(previous_sections.items(), key=previous_line)
        if anchor not in current
    )


def _metadata_changes(
    view: _DocumentView, previous: dict[str, object]
) -> tuple[tuple[str, object, object], ...]:
    """Return deterministic semantic projection-field changes."""

    current: dict[str, object] = {
        "path": view.path.as_posix(),
        "revision": view.revision,
        "type": view.document_type,
        "status": view.status,
        "dependencies": [
            {
                "relation": edge.relation,
                "target": edge.peer_id,
                "expected_revision": edge.expected_revision,
            }
            for edge in view.outgoing
        ],
        "boundaries": [
            {"relation": relation, "value": value, "reason": reason}
            for relation, value, reason in view.boundaries
        ],
        "migrations": [
            {"relation": relation, "value": value, "target": target}
            for relation, value, target in view.migrations
        ],
        "related_values": list(view.related_values),
    }
    return tuple(
        (field, previous.get(field), value)
        for field, value in current.items()
        if previous.get(field) != value
    )


@dataclass(frozen=True)
class _DocPlan:
    """Per-document rendering plan for `--assume-known` / `--since` packets.

    Documents without a plan (neither flag active for them) render exactly as
    before, so the flagless packet stays byte-identical. `coverage_state`
    selects the coverage-line wording; `content_omitted` is the JSON marker
    and is present precisely when navigation is omitted; `changed_sections`
    is the complete truth signal — every anchor at any level whose slice
    changed — reported verbatim as the JSON `changed_sections` key; only the
    subset that `_packet_sections` selects (changed H2s outside
    `navigation.extend_through`) is actually rendered as content.
    `removed_sections` and `metadata_changes` make non-current-section
    changes explicit instead of forcing a client to infer them from an empty
    changed-section list.
    """

    omit_navigation: bool = False
    content_omitted: dict[str, object] | None = None
    coverage_state: str = "normal"
    declared_revision: int | None = None
    generation_short: str | None = None
    changed_sections: tuple[str, ...] = ()
    removed_sections: tuple[str, ...] = ()
    metadata_changes: tuple[tuple[str, object, object], ...] = ()
    source_changed_outside_sections: bool = False
    changed_document: bool = False


def _build_packet_plans(
    views: _Views,
    ordered: list[str],
    *,
    assumed: dict[str, int],
    since_manifest: dict[str, object] | None,
    generation_short: str | None,
) -> tuple[dict[str, _DocPlan], list[dict[str, object]], list[str], int]:
    """Compute per-document plans plus shared diagnostics for the new flags.

    Returns `(plans, mismatches, notes, assumed_known_omitted)`. `mismatches`
    feeds the JSON `assume_known_mismatches`; `notes` are extra text
    diagnostics (revision mismatches, `new since` and the delta summary).
    """

    plans: dict[str, _DocPlan] = {}
    mismatches: list[dict[str, object]] = []
    notes: list[str] = []
    assumed_known_omitted = 0
    changed_count = 0
    unchanged_omitted_count = 0
    for selected_id in ordered:
        view = views[selected_id]
        if since_manifest is not None:
            manifest_documents = since_manifest["documents"]
            previous = manifest_documents.get(selected_id)
            if not isinstance(previous, dict):
                plans[selected_id] = _DocPlan(
                    generation_short=generation_short,
                    changed_sections=_changed_section_anchors(view, {}),
                    changed_document=True,
                )
                notes.append(f"{selected_id}: new since {generation_short}")
                changed_count += 1
            elif _source_sha(view) == previous.get("source_sha256"):
                plans[selected_id] = _DocPlan(
                    omit_navigation=True,
                    content_omitted={
                        "reason": "unchanged-since",
                        "generation": generation_short,
                    },
                    coverage_state="unchanged-since",
                    generation_short=generation_short,
                )
                unchanged_omitted_count += 1
            else:
                previous_sections = previous.get("sections", {})
                if not isinstance(previous_sections, dict):
                    previous_sections = {}
                changed_sections = _changed_section_anchors(view, previous_sections)
                removed_sections = _removed_section_anchors(view, previous_sections)
                metadata_changes = _metadata_changes(view, previous)
                plans[selected_id] = _DocPlan(
                    generation_short=generation_short,
                    changed_sections=changed_sections,
                    removed_sections=removed_sections,
                    metadata_changes=metadata_changes,
                    source_changed_outside_sections=(
                        not changed_sections and not removed_sections
                    ),
                    changed_document=True,
                )
                if removed_sections:
                    notes.append(
                        f"{selected_id}: removed sections since {generation_short}: "
                        + ", ".join(removed_sections)
                    )
                for field, before, after in metadata_changes:
                    before_json = json.dumps(
                        before, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    )
                    after_json = json.dumps(
                        after, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    )
                    notes.append(
                        f"{selected_id}: metadata {field} changed: "
                        f"{before_json} -> {after_json}"
                    )
                if not changed_sections and not removed_sections:
                    notes.append(
                        f"{selected_id}: source changed outside addressable sections"
                    )
                changed_count += 1
        elif selected_id in assumed:
            declared = assumed[selected_id]
            if view.revision == declared:
                plans[selected_id] = _DocPlan(
                    omit_navigation=True,
                    content_omitted={
                        "reason": "assumed-known",
                        "declared_revision": declared,
                    },
                    coverage_state="assumed-known",
                    declared_revision=declared,
                )
                assumed_known_omitted += 1
            else:
                mismatches.append(
                    {
                        "id": selected_id,
                        "declared_revision": declared,
                        "current_revision": view.revision,
                    }
                )
                notes.append(
                    f"{selected_id}: assumed known at revision {declared}, "
                    f"current {view.revision} — content included"
                )
    if since_manifest is not None:
        notes.append(
            f"Delta vs generation {generation_short}: {changed_count} changed, "
            f"{unchanged_omitted_count} unchanged omitted"
        )
    return plans, mismatches, notes, assumed_known_omitted


def _packet_sections(
    config,
    view: _DocumentView,
    user_selected: list[str],
    plan: _DocPlan | None,
) -> tuple[list[str], set[str], list[str]]:
    """Return `(explicit_anchors, changed_set, omitted)` for one document.

    `explicit_anchors` is the ordered, de-duplicated set of section blocks to
    render: user `--anchor`/`--include` selections first, then auto-added
    `--since` changed sections. Auto-added anchors are restricted to changed
    H2s that are not already inside the navigation prefix
    (`navigation.extend_through`) — an H1 is always covered by the lead-in
    navigation serves, and an `extend_through` H2 is already inside it, so
    re-emitting either would duplicate content navigation already sent.
    `changed_set` marks the auto-added delta anchors so the text form can
    title them `### Changed section`. `omitted` is the usual coverage list of
    H2 anchors that are neither navigation extensions nor shown, computed
    against what the document actually renders, which makes it truthful by
    construction: every changed H2 is either emitted here or listed there.
    """

    user = list(dict.fromkeys(user_selected))
    changed = plan.changed_sections if plan is not None else ()
    h2_anchors = {item.anchor for item in view.sections if item.level == 2}
    changed_blocks = [
        anchor
        for anchor in changed
        if anchor in h2_anchors and anchor not in config.navigation_extend_through
    ]
    extra = [anchor for anchor in changed_blocks if anchor not in user]
    explicit_anchors = user + extra
    omitted = [
        item.anchor
        for item in view.sections
        if item.level == 2
        and item.anchor not in config.navigation_extend_through
        and item.anchor not in explicit_anchors
    ]
    return explicit_anchors, set(extra), omitted


@dataclass(frozen=True)
class _ContentRequest:
    address: str
    start_line: int
    end_line: int
    reasons: tuple[str, ...]


def _inclusion_reason_text(reason: _ContextInclusionReason) -> str:
    if reason.direction == "self":
        return "target"
    if reason.direction == "explicit":
        return f"explicit include from {reason.via_id}"
    prefix = "reverse:" if reason.direction == "reverse" else ""
    return f"{prefix}{reason.relation} from {reason.via_id}"


def _navigation_end_line(
    view: _DocumentView, extend_through: tuple[str, ...]
) -> int:
    matching = [
        section
        for section in view.sections
        if section.level == 2 and section.anchor in extend_through
    ]
    if matching:
        return max(section.end_line for section in matching)
    first_h2 = next((section for section in view.sections if section.level == 2), None)
    if first_h2 is None:
        return len(view.content.splitlines())
    return first_h2.start_line - 1


def _compact_content_delivery(
    config: ProjectConfig,
    view: _DocumentView,
    inclusion_reasons: set[_ContextInclusionReason],
    explicit_anchors: list[str],
    changed_set: set[str],
    anchor_reasons: dict[str, set[str]],
    plan: _DocPlan | None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Return merged source ranges plus an address-to-range manifest.

    Requests may overlap (navigation with an extended H2, or a parent section
    with its child). Their union is emitted as non-overlapping source ranges;
    every requested stable address remains in the manifest and points to the
    one merged fragment that carries its exact bytes.
    """

    requests: list[_ContentRequest] = []
    if plan is None or not plan.omit_navigation:
        end_line = _navigation_end_line(view, config.navigation_extend_through)
        if end_line > 0:
            requests.append(
                _ContentRequest(
                    view.document_id,
                    1,
                    end_line,
                    tuple(
                        sorted(_inclusion_reason_text(item) for item in inclusion_reasons)
                    ),
                )
            )
    sections = {section.anchor: section for section in view.sections}
    for anchor in explicit_anchors:
        section = sections[anchor]
        reasons = set(anchor_reasons.get(anchor, ()))
        if anchor in changed_set:
            generation = plan.generation_short if plan is not None else None
            reasons.add(f"changed since {generation or 'selected generation'}")
        if not reasons:
            reasons.add("explicit section")
        requests.append(
            _ContentRequest(
                f"{view.document_id}#{anchor}",
                section.start_line,
                section.end_line,
                tuple(sorted(reasons)),
            )
        )
    requests.sort(key=lambda item: (item.start_line, item.end_line, item.address))

    ranges: list[tuple[int, int]] = []
    for request in requests:
        if not ranges or request.start_line > ranges[-1][1] + 1:
            ranges.append((request.start_line, request.end_line))
        else:
            ranges[-1] = (ranges[-1][0], max(ranges[-1][1], request.end_line))

    lines = view.content.splitlines()
    fragments: list[dict[str, object]] = []
    for start_line, end_line in ranges:
        content = "\n".join(lines[start_line - 1 : end_line])
        fragment_id = f"{view.document_id}:{start_line}-{end_line}"
        fragments.append(
            {
                "id": fragment_id,
                "start_line": start_line,
                "end_line": end_line,
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
                "content": content,
            }
        )

    manifest: list[dict[str, object]] = []
    for request in sorted(requests, key=lambda item: item.address):
        fragment = next(
            item
            for item in fragments
            if int(item["start_line"]) <= request.start_line
            and int(item["end_line"]) >= request.end_line
        )
        direct = (
            int(fragment["start_line"]) == request.start_line
            and int(fragment["end_line"]) == request.end_line
        )
        manifest.append(
            {
                "address": request.address,
                "start_line": request.start_line,
                "end_line": request.end_line,
                "reasons": list(request.reasons),
                "fragment_id": fragment["id"],
                "delivery": "direct" if direct else "covered-by-fragment",
            }
        )
    return fragments, manifest


def _coverage_line(
    plan: _DocPlan | None, explicit_anchors: list[str], omitted: list[str]
) -> str:
    """Build the per-document `_Coverage_` line, honouring omitted content."""

    omitted_text = ", ".join(omitted) if omitted else "none"
    if plan is not None and plan.coverage_state == "assumed-known":
        body = (
            f"content omitted — declared known at revision "
            f"{plan.declared_revision} (current)"
        )
    elif plan is not None and plan.coverage_state == "unchanged-since":
        body = f"content omitted — unchanged since {plan.generation_short}"
    else:
        body = "navigation" + (" + explicit sections" if explicit_anchors else "")
    return f"_Coverage: {body}. Omitted H2: {omitted_text}._"


def _context_migrations_json(
    views: _Views, ordered: list[str]
) -> list[dict[str, object]]:
    return [
        {
            "source_id": selected_id,
            "relation": relation,
            "value": value,
            "target_id": target_id,
        }
        for selected_id in ordered
        for relation, value, target_id in views[selected_id].migrations
    ]


def _context_boundaries_json(
    views: _Views, ordered: list[str]
) -> list[dict[str, object]]:
    return [
        {
            "source_id": selected_id,
            "relation": relation,
            "value": value,
            "reason": reason,
        }
        for selected_id in ordered
        for relation, value, reason in views[selected_id].boundaries
    ]


def _context_related_omitted_json(
    views: _Views, document_id: str, *, include_related: bool
) -> list[str]:
    return [] if include_related else list(views[document_id].related_values)


def _emit_context_json(
    config,
    views: _Views,
    included: dict[str, set[str]],
    selected_anchors: dict[str, list[str]],
    ordered: list[str],
    document_id: str,
    *,
    depth: int,
    include_related: bool,
    plans: dict[str, _DocPlan] | None = None,
    mismatches: list[dict[str, object]] | None = None,
    assumed_known_omitted: int = 0,
    assume_known_used: bool = False,
    purpose_view: ContextView | None = None,
    view_omissions: tuple[_ContextViewOmission, ...] = (),
    compact: bool = False,
    inclusion_reasons: _ContextReasons | None = None,
    anchor_reasons: dict[str, dict[str, set[str]]] | None = None,
) -> int:
    """Print the context packet as one structured JSON object.

    The JSON form carries the same selection, coverage and diagnostics data
    as the Markdown packet, but structured (typed lists instead of prose
    notes) so a machine client never parses packet text. Declared-cache and
    delta documents drop `navigation` for a typed `content_omitted` marker;
    `--since` changed documents additionally carry `changed_sections`. Extra
    top-level keys and stats appear only when the matching flag is used, so
    every flagless payload stays byte-identical.
    """

    plans = plans or {}
    documents: list[dict[str, object]] = []
    explicit_count = 0
    omitted_count = 0
    source_fragment_count = 0
    source_fragment_bytes = 0
    inclusion_reasons = inclusion_reasons or {}
    anchor_reasons = anchor_reasons or {}
    for selected_id in ordered:
        view = views[selected_id]
        plan = plans.get(selected_id)
        explicit_anchors, changed_set, omitted = _packet_sections(
            config, view, selected_anchors[selected_id], plan
        )
        explicit_sections: list[dict[str, str]] = []
        if not compact:
            for selected_anchor in explicit_anchors:
                section = next(
                    item for item in view.sections if item.anchor == selected_anchor
                )
                explicit_sections.append(
                    {
                        "anchor": selected_anchor,
                        "content": extract_section(view.content, section).rstrip(),
                    }
                )
        explicit_count += len(explicit_anchors)
        omitted_count += len(omitted)
        entry: dict[str, object] = {
            "id": selected_id,
            "path": view.path.as_posix(),
            "revision": view.revision,
            "relations": sorted(included[selected_id]),
            "omitted_h2": omitted,
        }
        if compact:
            fragments, manifest = _compact_content_delivery(
                config,
                view,
                inclusion_reasons.get(selected_id, set()),
                explicit_anchors,
                changed_set,
                anchor_reasons.get(selected_id, {}),
                plan,
            )
            entry["inclusion_reasons"] = [
                {
                    "via_id": reason.via_id,
                    "direction": reason.direction,
                    "relation": reason.relation,
                }
                for reason in sorted(inclusion_reasons.get(selected_id, set()))
            ]
            entry["content_fragments"] = fragments
            entry["content_manifest"] = manifest
            source_fragment_count += len(fragments)
            source_fragment_bytes += sum(
                len(str(fragment["content"]).encode()) for fragment in fragments
            )
        else:
            entry["explicit_sections"] = explicit_sections
            entry["sections"] = _section_size_maps(view)
        if plan is not None and plan.omit_navigation:
            entry["content_omitted"] = plan.content_omitted
        elif not compact:
            entry["navigation"] = extract_navigation(
                view.content,
                view.sections,
                config.navigation_extend_through,
            ).rstrip()
        if plan is not None and plan.changed_document:
            entry["changed_sections"] = list(plan.changed_sections)
            entry["removed_sections"] = list(plan.removed_sections)
            entry["metadata_changes"] = [
                {"field": field, "before": before, "after": after}
                for field, before, after in plan.metadata_changes
            ]
            entry["source_changed_outside_sections"] = (
                plan.source_changed_outside_sections
            )
        documents.append(entry)
    freshness = _freshness_rows(config, views, ordered)
    stats: dict[str, object] = {
        "included_documents": len(ordered),
        "explicit_sections": explicit_count,
        "omitted_h2_sections": omitted_count,
    }
    if assume_known_used:
        stats["assumed_known_omitted"] = assumed_known_omitted
    if compact:
        stats["source_fragments"] = source_fragment_count
        stats["source_fragment_bytes"] = source_fragment_bytes
    payload: dict[str, object] = {
        "target": document_id,
        "depth": depth,
        "include_related": include_related,
        "outline": False,
        "documents": documents,
        "freshness": freshness,
        "migrations": _context_migrations_json(views, ordered),
        "boundaries": _context_boundaries_json(views, ordered),
        "related_omitted": _context_related_omitted_json(
            views, document_id, include_related=include_related
        ),
        "stats": stats,
    }
    if compact:
        payload["compact"] = True
    if assume_known_used:
        payload["assume_known_mismatches"] = mismatches or []
    if purpose_view is not None:
        payload["purpose_view"] = {
            "name": purpose_view.name,
            "tier": purpose_view.tier,
            "delivery": purpose_view.delivery,
            "direction": purpose_view.direction,
            "depth": purpose_view.depth,
            "relations": list(purpose_view.relations),
            "layers": list(purpose_view.layers),
        }
        payload["view_omissions"] = [
            {
                "source_id": item.source_id,
                "direction": item.direction,
                "relation": item.relation,
                "peer_id": item.peer_id,
                "reason": item.reason,
            }
            for item in view_omissions
        ]
    _print_json(payload)
    return 0


def _emit_context_outline_json(
    config,
    views: _Views,
    included: dict[str, set[str]],
    ordered: list[str],
    document_id: str,
    *,
    depth: int,
    include_related: bool,
    purpose_view: ContextView | None = None,
    view_omissions: tuple[_ContextViewOmission, ...] = (),
) -> int:
    """Print the map-first outline packet: section sizes, no content.

    Shares the same root shape as the full `context --json` packet
    (target/depth/include_related/diagnostics), but `documents[]` entries
    carry only `id`, `path`, `revision`, `relations` and `sections`, and
    `stats` counts listed sections and their total byte size instead of
    explicit/omitted H2 counts, so a client can budget a follow-up `--include`
    fetch while retaining the revision needed by `--assume-known`.
    """

    documents: list[dict[str, object]] = []
    listed_sections = 0
    total_section_bytes = 0
    for selected_id in ordered:
        view = views[selected_id]
        sections = _section_size_maps(view)
        listed_sections += len(sections)
        total_section_bytes += sum(int(item["bytes"]) for item in sections)
        documents.append(
            {
                "id": selected_id,
                "path": view.path.as_posix(),
                "revision": view.revision,
                "relations": sorted(included[selected_id]),
                "sections": sections,
            }
        )
    freshness = _freshness_rows(config, views, ordered)
    payload: dict[str, object] = {
        "target": document_id,
        "depth": depth,
        "include_related": include_related,
        "outline": True,
        "documents": documents,
        "freshness": freshness,
        "migrations": _context_migrations_json(views, ordered),
        "boundaries": _context_boundaries_json(views, ordered),
        "related_omitted": _context_related_omitted_json(
            views, document_id, include_related=include_related
        ),
        "stats": {
            "included_documents": len(ordered),
            "listed_sections": listed_sections,
            "total_section_bytes": total_section_bytes,
        },
    }
    if purpose_view is not None:
        payload["purpose_view"] = {
            "name": purpose_view.name,
            "tier": purpose_view.tier,
            "delivery": purpose_view.delivery,
            "direction": purpose_view.direction,
            "depth": purpose_view.depth,
            "relations": list(purpose_view.relations),
            "layers": list(purpose_view.layers),
        }
        payload["view_omissions"] = [
            {
                "source_id": item.source_id,
                "direction": item.direction,
                "relation": item.relation,
                "peer_id": item.peer_id,
                "reason": item.reason,
            }
            for item in view_omissions
        ]
    _print_json(payload)
    return 0


def _context_diagnostic_notes(
    config,
    views: _Views,
    ordered: list[str],
    document_id: str,
    *,
    include_related: bool,
    extra_notes: list[str] | None = None,
    view_omissions: tuple[_ContextViewOmission, ...] = (),
) -> list[str]:
    """Return the sorted "Diagnostics and boundaries" note lines.

    Shared between the full-content packet and `--outline`, which prints
    exactly the same notes ahead of its own closing action line. `extra_notes`
    carries declared-cache and delta-packet notes; it is empty for `--outline`
    (which cannot combine with `--assume-known`/`--since`), keeping that
    packet byte-identical.
    """

    notes: list[str] = list(extra_notes or [])
    freshness_found = False
    for row in _freshness_rows(config, views, ordered):
        mode = (
            "historical snapshot"
            if row["classification"] == "historical snapshot"
            else "STALE"
        )
        notes.append(
            f"{row['source_id']}: {row['target_id']}@"
            f"{row['pinned_revision']}, current "
            f"{row['current_revision']} — {mode}"
        )
        freshness_found = True
    if not freshness_found:
        notes.append("No stale revision pins among included documents.")
    for selected_id in ordered:
        for relation, value, target_id in views[selected_id].migrations:
            notes.append(f"{selected_id}: {relation} {value} -> {target_id}")
    boundary_found = False
    for selected_id in ordered:
        for relation, value, reason in views[selected_id].boundaries:
            notes.append(
                f"{selected_id}: unresolved/resource {relation} "
                f"{value} ({reason})"
            )
            boundary_found = True
    if not boundary_found:
        notes.append(
            "No unresolved/resource boundaries among included documents."
        )
    if not include_related:
        related = list(views[document_id].related_values)
        if related:
            notes.append("Related omitted: " + ", ".join(related))
    for item in view_omissions:
        notes.append(
            f"View omitted: {item.source_id} {item.direction} {item.relation} "
            f"{item.peer_id} ({item.reason})"
        )
    return sorted(set(notes))


def _context_compact_diagnostic_notes(
    config: ProjectConfig,
    views: _Views,
    ordered: list[str],
    document_id: str,
    *,
    include_related: bool,
    extra_notes: list[str] | None = None,
    view_omissions: tuple[_ContextViewOmission, ...] = (),
) -> list[str]:
    """Return blocker-first notes with lossless JSON drill-down summaries."""

    notes = list(dict.fromkeys(extra_notes or []))
    freshness = _freshness_rows(config, views, ordered)
    for row in freshness:
        mode = (
            "historical snapshot"
            if row["classification"] == "historical snapshot"
            else "STALE"
        )
        notes.append(
            f"{row['source_id']}: {row['target_id']}@{row['pinned_revision']}, "
            f"current {row['current_revision']} — {mode}"
        )

    boundaries = _context_boundaries_json(views, ordered)
    for row in boundaries:
        notes.append(
            f"{row['source_id']}: unresolved/resource {row['relation']} "
            f"{row['value']} ({row['reason']})"
        )
    if not freshness and not boundaries:
        notes.append("No stale pins or unresolved/resource boundaries.")

    if not include_related:
        related = list(views[document_id].related_values)
        if related:
            notes.append("Related omitted: " + ", ".join(related))

    migration_counts = Counter(
        row["relation"] for row in _context_migrations_json(views, ordered)
    )
    for relation, count in sorted(migration_counts.items()):
        notes.append(
            f"Adoption mappings: {count} {relation}; full rows: rerun with --json."
        )

    omission_counts = Counter(
        (item.direction, item.relation, item.reason) for item in view_omissions
    )
    for (direction, relation, reason), count in sorted(omission_counts.items()):
        notes.append(
            f"View omissions: {count} {direction} {relation} ({reason}); "
            "full rows: rerun with --json."
        )
    return notes


def _emit_context_compact_text(
    config: ProjectConfig,
    views: _Views,
    inclusion_reasons: _ContextReasons,
    selected_anchors: dict[str, list[str]],
    anchor_reasons: dict[str, dict[str, set[str]]],
    ordered: list[str],
    document_id: str,
    *,
    depth: int,
    include_related: bool,
    plans: dict[str, _DocPlan],
    extra_notes: list[str],
    assumed_known_omitted: int,
    assume_known_used: bool,
    purpose_view: ContextView | None = None,
    view_omissions: tuple[_ContextViewOmission, ...] = (),
) -> int:
    out = [f"# Compact context packet: {document_id}", ""]
    out.append(f"- Dependency depth: {depth}")
    out.append(f"- Related traversal: {'included' if include_related else 'omitted'}")
    if purpose_view is not None:
        out.append(
            f"- Purpose view: {purpose_view.name} (tier {purpose_view.tier}, "
            f"{purpose_view.direction}, authored)"
        )
    explicit_count = 0
    omitted_count = 0
    fragment_count = 0
    fragment_bytes = 0
    for selected_id in ordered:
        view = views[selected_id]
        plan = plans.get(selected_id)
        explicit_anchors, changed_set, omitted = _packet_sections(
            config, view, selected_anchors[selected_id], plan
        )
        fragments, manifest = _compact_content_delivery(
            config,
            view,
            inclusion_reasons.get(selected_id, set()),
            explicit_anchors,
            changed_set,
            anchor_reasons.get(selected_id, {}),
            plan,
        )
        explicit_count += len(explicit_anchors)
        omitted_count += len(omitted)
        fragment_count += len(fragments)
        fragment_bytes += sum(
            len(str(fragment["content"]).encode()) for fragment in fragments
        )
        out.extend(["", f"## {selected_id} — {view.path.as_posix()}", ""])
        reason_text = sorted(
            _inclusion_reason_text(item)
            for item in inclusion_reasons.get(selected_id, set())
        )
        out.append("Inclusion: " + ("; ".join(reason_text) or "explicit") + ".")
        for fragment in fragments:
            out.extend(
                [
                    "",
                    f"### Source fragment `{fragment['id']}`",
                    "",
                    str(fragment["content"]),
                ]
            )
        if manifest:
            out.extend(["", "Address manifest:"])
            for row in manifest:
                out.append(
                    f"- `{row['address']}` -> `{row['fragment_id']}` "
                    f"({row['delivery']}; {', '.join(row['reasons'])})"
                )
        out.extend(["", _coverage_line(plan, explicit_anchors, omitted)])

    out.extend(["", "## Diagnostics and boundaries", ""])
    for note in _context_compact_diagnostic_notes(
        config,
        views,
        ordered,
        document_id,
        include_related=include_related,
        extra_notes=extra_notes,
        view_omissions=view_omissions,
    ):
        out.append(f"- {note}")
    if purpose_view is None:
        out.append("- Expand with --depth, --include-related, or --include ID#anchor.")
    else:
        out.append(
            "- Expand with --include ID#anchor, another configured view, or an "
            "explicit/full read."
        )
    body = "\n".join(out) + "\n"
    sys.stdout.write(body)
    print()
    print("## Packet stats")
    print()
    print(f"- Included documents: {len(ordered)}")
    print(f"- Explicit section addresses: {explicit_count}")
    print(f"- Source fragments: {fragment_count}")
    print(f"- Source fragment bytes: {fragment_bytes}")
    print(f"- Omitted H2 sections: {omitted_count}")
    if assume_known_used:
        print(f"- Content omitted (assumed known): {assumed_known_omitted}")
    return 0


def _emit_context_outline_text(
    config,
    views: _Views,
    included: dict[str, set[str]],
    ordered: list[str],
    document_id: str,
    *,
    depth: int,
    include_related: bool,
    purpose_view: ContextView | None = None,
    view_omissions: tuple[_ContextViewOmission, ...] = (),
) -> int:
    """Print the map-first outline: section size tables, no content."""

    out: list[str] = []
    out.append(f"# Context outline: {document_id}")
    out.append("")
    out.append(f"- Dependency depth: {depth}")
    out.append(f"- Related traversal: {'included' if include_related else 'omitted'}")
    if purpose_view is not None:
        out.append(
            f"- Purpose view: {purpose_view.name} (tier {purpose_view.tier}, "
            f"{purpose_view.direction}, authored)"
        )
    listed_sections = 0
    total_bytes = 0
    for selected_id in ordered:
        view = views[selected_id]
        out.append("")
        out.append(f"## {selected_id} — {view.path.as_posix()}")
        out.append("")
        out.append(f"Relations: {', '.join(sorted(included[selected_id]))}.")
        out.append("")
        out.append("| Anchor | Level | Lines | Bytes | Title |")
        out.append("|---|---|---|---|---|")
        for section_map in _section_size_maps(view):
            out.append(
                f"| `{section_map['anchor']}` | H{section_map['level']} | "
                f"{section_map['lines']} | {section_map['bytes']} | "
                f"{section_map['title']} |"
            )
            listed_sections += 1
            total_bytes += int(section_map["bytes"])
    out.append("")
    out.append("## Diagnostics and boundaries")
    out.append("")
    for note in _context_diagnostic_notes(
        config,
        views,
        ordered,
        document_id,
        include_related=include_related,
        view_omissions=view_omissions,
    ):
        out.append(f"- {note}")
    if purpose_view is None:
        out.append(
            "- Fetch content with --include ID#anchor, or drop --outline for "
            "full navigation."
        )
    else:
        out.append(
            "- Expand with another configured view, an explicit read, or the full "
            "Markdown source."
        )
    out.append("")
    out.append("## Packet stats")
    out.append("")
    out.append(f"- Included documents: {len(ordered)}")
    out.append(f"- Listed sections: {listed_sections}")
    out.append(f"- Total section bytes: {total_bytes}")
    sys.stdout.write("\n".join(out) + "\n")
    return 0


def context(
    project_root: Path,
    document_id: str,
    *,
    anchor: str | None = None,
    depth: int | None = None,
    include_related: bool | None = None,
    includes: list[str] | None = None,
    json_output: bool = False,
    outline: bool | None = None,
    assume_known: list[str] | None = None,
    since: str | None = None,
    view_name: str | None = None,
    compact: bool = False,
) -> int:
    if view_name is not None and any(
        item is not None for item in (depth, include_related, outline)
    ):
        print(
            "ERROR: cannot combine --view with --depth, --include-related or --outline",
            file=sys.stderr,
        )
        return 1
    if since is not None and assume_known:
        print(
            "ERROR: cannot combine --since with --assume-known",
            file=sys.stderr,
        )
        return 1
    assumed: dict[str, int] = {}
    for raw in assume_known or []:
        document, separator, revision = raw.partition("@")
        if (
            not separator
            or not document
            or not (revision.isascii() and revision.isdigit())
            or int(revision) <= 0
        ):
            print(f"ERROR: invalid --assume-known value: {raw!r}", file=sys.stderr)
            return 1
        declared = int(revision)
        if document in assumed and assumed[document] != declared:
            print(
                f"ERROR: conflicting --assume-known declarations for {document}",
                file=sys.stderr,
            )
            return 1
        assumed[document] = declared
    since_manifest: dict[str, object] | None = None
    generation_short: str | None = None
    purpose_view: ContextView | None = None
    view_omissions: tuple[_ContextViewOmission, ...] = ()
    try:
        config = load_config(project_root)
        if view_name is not None:
            purpose_view = next(
                (item for item in config.context_views if item.name == view_name),
                None,
            )
            if purpose_view is None:
                raise ValueError(f"context view not found: {view_name}")
            depth = purpose_view.depth
            include_related = "related" in purpose_view.relations
            outline = purpose_view.delivery == "outline"
        else:
            depth = 1 if depth is None else depth
            include_related = False if include_related is None else include_related
            outline = False if outline is None else outline
        if outline and (anchor is not None or includes):
            if purpose_view is None:
                raise ValueError(
                    "cannot combine --outline with --anchor or --include"
                )
            raise ValueError("cannot combine outline delivery with --anchor or --include")
        if outline and compact:
            raise ValueError("cannot combine outline delivery with --compact")
        if outline and (assume_known or since is not None):
            if purpose_view is None:
                raise ValueError(
                    "cannot combine --outline with --assume-known or --since"
                )
            raise ValueError(
                "cannot combine outline delivery with --assume-known or --since"
            )
        if since is not None:
            resolved = resolve_generation_manifest(config, since)
            if resolved is None:
                print(
                    f"ERROR: unknown projection generation: {since}",
                    file=sys.stderr,
                )
                return 1
            generation, since_manifest = resolved
            generation_short = generation[:12]
        views, incoming, catalog_value = _load_views(config)
        if catalog_value is not None:
            find_document(catalog_value, document_id)
        elif document_id not in views:
            raise ValueError(f"document ID not found: {document_id}")
        if purpose_view is None:
            included, inclusion_reasons = _context_selection(
                views,
                document_id,
                depth=depth,
                include_related=include_related,
            )
        else:
            included, inclusion_reasons, view_omissions = _purpose_context_selection(
                views, incoming, document_id, purpose_view
            )
        forced: dict[str, list[str]] = {}
        anchor_reasons: dict[str, dict[str, set[str]]] = {}
        if anchor is not None:
            anchor_reasons.setdefault(document_id, {}).setdefault(anchor, set()).add(
                "target anchor"
            )
        for raw in includes or []:
            selected_id, selected_anchor = _selection(raw)
            if catalog_value is not None:
                find_document(catalog_value, selected_id)
            elif selected_id not in views:
                raise ValueError(f"document ID not found: {selected_id}")
            included.setdefault(selected_id, set()).add("explicit")
            inclusion_reasons.setdefault(selected_id, set()).add(
                _ContextInclusionReason(document_id, "explicit", "explicit")
            )
            if selected_anchor:
                forced.setdefault(selected_id, []).append(selected_anchor)
                anchor_reasons.setdefault(selected_id, {}).setdefault(
                    selected_anchor, set()
                ).add("explicit include")
        for assumed_id in assumed:
            # A declared document is validated even when it does not enter the
            # packet, so a stale declaration fails closed instead of silently
            # doing nothing. Declaring an ID never forces its inclusion.
            if catalog_value is not None:
                find_document(catalog_value, assumed_id)
            elif assumed_id not in views:
                raise ValueError(f"document ID not found: {assumed_id}")
        selected_anchors = {
            selected_id: [
                *([anchor] if selected_id == document_id and anchor else []),
                *forced.get(selected_id, []),
            ]
            for selected_id in included
        }
        if catalog_value is not None:
            # A verified projection is only ever written for a tree with no
            # blocking errors, so this validation runs on the direct path only.
            by_id = {
                document.metadata.document_id: document
                for document in catalog_value.documents
                if document.metadata is not None
            }
            relevant_paths = {by_id[item].path for item in included}
            graph_issues = (
                *validate_membership(catalog_value),
                *validate_metadata(catalog_value, config),
                *validate_adoption(catalog_value, config),
            )
            if purpose_view is not None and purpose_view.direction in {
                "reverse",
                "both",
            }:
                # An invalid document anywhere in the catalog can hide an
                # incoming authored edge. Reverse answers therefore require a
                # globally valid semantic graph, matching `dependencies
                # --reverse`; forward-only selection remains scoped to the
                # documents it actually traverses.
                blockers = [
                    issue
                    for issue in graph_issues
                    if issue.affects_graph and issue.severity != "warning"
                ]
            else:
                blockers = [
                    issue
                    for issue in graph_issues
                    if issue.affects_graph
                    and issue.severity != "warning"
                    and issue.path in relevant_paths
                ]
            for selected_id in included:
                document = by_id[selected_id]
                blockers.extend(
                    ValidationIssue(document.path, message)
                    for message in document_section_issues(document, config)
                )
            if blockers:
                for issue in blockers:
                    print(
                        f"ERROR: {issue.path.as_posix()}: {issue.message}",
                        file=sys.stderr,
                    )
                return 1
        for selected_id in included:
            known_anchors = {section.anchor for section in views[selected_id].sections}
            for selected_anchor in selected_anchors[selected_id]:
                if selected_anchor not in known_anchors:
                    raise ValueError(
                        f"anchor not found in {selected_id}: {selected_anchor}"
                    )
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    ordered = _ordered_selection(included, document_id)
    if outline:
        if json_output:
            return _emit_context_outline_json(
                config,
                views,
                included,
                ordered,
                document_id,
                depth=depth,
                include_related=include_related,
                purpose_view=purpose_view,
                view_omissions=view_omissions,
            )
        return _emit_context_outline_text(
            config,
            views,
            included,
            ordered,
            document_id,
            depth=depth,
            include_related=include_related,
            purpose_view=purpose_view,
            view_omissions=view_omissions,
        )
    plans, mismatches, extra_notes, assumed_known_omitted = _build_packet_plans(
        views,
        ordered,
        assumed=assumed,
        since_manifest=since_manifest,
        generation_short=generation_short,
    )
    if json_output:
        return _emit_context_json(
            config,
            views,
            included,
            selected_anchors,
            ordered,
            document_id,
            depth=depth,
            include_related=include_related,
            plans=plans,
            mismatches=mismatches,
            assumed_known_omitted=assumed_known_omitted,
            assume_known_used=bool(assume_known),
            purpose_view=purpose_view,
            view_omissions=view_omissions,
            compact=compact,
            inclusion_reasons=inclusion_reasons,
            anchor_reasons=anchor_reasons,
        )
    if compact:
        return _emit_context_compact_text(
            config,
            views,
            inclusion_reasons,
            selected_anchors,
            anchor_reasons,
            ordered,
            document_id,
            depth=depth,
            include_related=include_related,
            plans=plans,
            extra_notes=extra_notes,
            assumed_known_omitted=assumed_known_omitted,
            assume_known_used=bool(assume_known),
            purpose_view=purpose_view,
            view_omissions=view_omissions,
        )
    out: list[str] = []
    explicit_count = 0
    omitted_count = 0
    out.append(f"# Context packet: {document_id}")
    out.append("")
    out.append(f"- Dependency depth: {depth}")
    out.append(f"- Related traversal: {'included' if include_related else 'omitted'}")
    if purpose_view is not None:
        out.append(
            f"- Purpose view: {purpose_view.name} (tier {purpose_view.tier}, "
            f"{purpose_view.direction}, authored)"
        )
    for selected_id in ordered:
        view = views[selected_id]
        plan = plans.get(selected_id)
        out.append("")
        out.append(f"## {selected_id} — {view.path.as_posix()}")
        out.append("")
        out.append(f"Relations: {', '.join(sorted(included[selected_id]))}.")
        explicit_anchors, changed_set, omitted = _packet_sections(
            config, view, selected_anchors[selected_id], plan
        )
        if plan is None or not plan.omit_navigation:
            out.append("")
            out.append(
                extract_navigation(
                    view.content,
                    view.sections,
                    config.navigation_extend_through,
                ).rstrip()
            )
        for selected_anchor in explicit_anchors:
            section = next(
                (
                    item
                    for item in view.sections
                    if item.anchor == selected_anchor
                ),
                None,
            )
            explicit_count += 1
            title = (
                "Changed section"
                if selected_anchor in changed_set
                else "Explicit section"
            )
            out.append("")
            out.append(f"### {title} `{selected_anchor}`")
            out.append("")
            out.append(extract_section(view.content, section).rstrip())
        omitted_count += len(omitted)
        out.append("")
        out.append(_coverage_line(plan, explicit_anchors, omitted))
    out.append("")
    out.append("## Diagnostics and boundaries")
    out.append("")
    for note in _context_diagnostic_notes(
        config,
        views,
        ordered,
        document_id,
        include_related=include_related,
        extra_notes=extra_notes,
        view_omissions=view_omissions,
    ):
        out.append(f"- {note}")
    if purpose_view is None:
        out.append("- Expand with --depth, --include-related, or --include ID#anchor.")
    else:
        out.append(
            "- Expand with --include ID#anchor, another configured view, or an "
            "explicit/full read."
        )
    body = "\n".join(out) + "\n"
    line_count = body.count("\n")
    byte_count = len(body.encode("utf-8"))
    sys.stdout.write(body)
    print()
    print("## Packet stats")
    print()
    print(f"- Included documents: {len(ordered)}")
    print(f"- Explicit sections: {explicit_count}")
    print(f"- Omitted H2 sections: {omitted_count}")
    if assume_known:
        print(f"- Content omitted (assumed known): {assumed_known_omitted}")
    print(f"- Body size: {line_count} lines, {byte_count} UTF-8 bytes")
    return 0


def impact(project_root: Path, document_id: str) -> int:
    try:
        config = load_config(project_root)
        views, incoming, catalog_value = _load_views(config)
        if catalog_value is not None:
            find_document(catalog_value, document_id)
            blockers = [
                issue
                for issue in (
                    *validate_membership(catalog_value),
                    *validate_metadata(catalog_value, config),
                    *validate_adoption(catalog_value, config),
                )
                if issue.affects_graph and issue.severity != "warning"
            ]
            if blockers:
                for issue in blockers:
                    print(
                        f"ERROR: {issue.path.as_posix()}: {issue.message}",
                        file=sys.stderr,
                    )
                return 1
        target = views.get(document_id)
        if target is None:
            raise ValueError(f"document ID not found: {document_id}")
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"# Impact analysis: {document_id}")
    print()
    print(f"- Path: `{target.path.as_posix()}`")
    print(f"- Type/status: {target.document_type} / {target.status}")
    print(f"- Current revision: {target.revision}")
    print()
    print("| Downstream | Relation | Pin | Classification |")
    print("|---|---|---|---|")
    edges = incoming.get(document_id, ())
    for edge in edges:
        if edge.relation == "related":
            classification = "related navigation"
        elif edge.relation == "validated_against":
            source = views[edge.peer_id]
            if is_historical_snapshot(
                config, source.document_type, source.status
            ):
                classification = "historical snapshot"
            elif edge.expected_revision == target.revision:
                classification = "freshness pin (current)"
            else:
                classification = "freshness pin (already stale)"
        elif edge.relation == "supersedes":
            classification = "lineage"
        else:
            classification = "semantic"
        pin = str(edge.expected_revision) if edge.expected_revision else "—"
        print(
            f"| `{edge.peer_id}` | {edge.relation} | {pin} | "
            f"{classification} |"
        )
    if not edges:
        print("| — | — | — | no reverse metadata dependencies |")
    return 0


def migration_report(project_root: Path, *, json_output: bool = False) -> int:
    try:
        config = load_config(project_root)
        catalog_value = build_catalog(config)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if json_output:
        _print_json(
            {
                "resolved": [
                    _migration_json(item)
                    for item in catalog_value.relation_migrations
                ],
                "boundaries": [
                    _boundary_json(item)
                    for item in catalog_value.relation_boundaries
                ],
            }
        )
        return 0
    for item in catalog_value.relation_migrations:
        print(
            f"resolved\t{item.source_id}\t{item.relation}\t"
            f"{item.value}\t{item.target_id}"
        )
    for item in catalog_value.relation_boundaries:
        print(
            f"boundary\t{item.source_id}\t{item.relation}\t"
            f"{item.value}\t{item.reason}"
        )
    return 0


def migrate(project_root: Path, *, apply: bool = False) -> int:
    """Preview, by default, or (with `apply`) write resolved legacy relations.

    Preview is entirely read-only. `apply` computes the same plan, validates
    it against a scratch copy of the documentation tree, and only then
    rewrites the affected Markdown files atomically.
    """

    try:
        config = load_config(project_root)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if not config.documentation_root.is_dir():
        print(
            f"ERROR: documentation root does not exist: {config.documentation_root}",
            file=sys.stderr,
        )
        return 1
    try:
        catalog_value = build_catalog(config)
        plan = build_migration_plan(config, catalog_value)
        problems = validate_plan(config, plan)
    except (OSError, UnicodeError, ValueError) as error:
        print(f"ERROR: failed to build migration plan: {error}", file=sys.stderr)
        return 1
    if problems:
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    if not plan.changes:
        print("No resolvable legacy relation migrations found.")
        return 0

    for change in plan.changes:
        print(
            f"would-migrate\t{change.source_id}\t{change.relation}\t"
            f"{change.old_value}\t{change.new_value}\t{change.path.as_posix()}"
        )
    if apply:
        try:
            apply_migration_plan(config, plan)
        except (OSError, ValueError) as error:
            print(f"ERROR: failed to apply migration: {error}", file=sys.stderr)
            return 1
        print(
            f"Applied {len(plan.changes)} legacy relation migration(s) across "
            f"{len(plan.updated_contents)} file(s)."
        )
    else:
        print(
            f"Preview only; {len(plan.changes)} legacy relation migration(s) across "
            f"{len(plan.updated_contents)} file(s). Re-run with --apply to write."
        )
    return 0


def _issue_json(issue: ValidationIssue) -> dict[str, object]:
    return {
        "path": issue.path.as_posix(),
        "message": issue.message,
        "severity": issue.severity,
        "target_id": issue.target_id,
    }


def readiness(
    project_root: Path,
    *,
    json_output: bool = False,
    selection: _Selection | None = None,
) -> int:
    """Report, read-only, whether an existing project is adoption-ready.

    Stable summary data (counts, projection state, the next safe command)
    goes to stdout; ERROR/WARNING diagnostics go to stderr, matching
    `validate`, `doctor` and `migrate`. `--json` prints one deterministic
    object carrying the same data in full instead of counts, so a consumer
    never has to parse the stderr diagnostics.

    In selected-source mode the project is addressed by its reusable
    `--source NAME` selector everywhere the project root would otherwise be
    printed, and the payload gains a `source` key. Without a source the
    output is unchanged.
    """

    selection = selection or _Selection(project_root)
    try:
        config = load_config(project_root)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    catalog_value = build_catalog(config)
    report = evaluate_readiness(config, catalog_value)
    next_command = report.next_command(selection.selector)

    if json_output:
        # One payload shape for every project state: a missing documentation
        # root reports empty categories rather than a shorter object, so a
        # consumer never has to branch on which keys exist.
        payload: dict[str, object] = {
            "documentation_root_exists": report.documentation_root_exists,
            "ready": report.ready,
            "blocking": [_issue_json(issue) for issue in report.blocking],
            "resolvable_migrations": [
                _migration_json(item) for item in report.resolvable_migrations
            ],
            "boundaries": [_boundary_json(item) for item in report.boundaries],
            "stale_pins": [_issue_json(issue) for issue in report.stale_pins],
            "projection": {
                "state": report.projection_state,
                "reason": report.projection_reason,
            },
            "next_command": next_command,
        }
        if selection.source is not None:
            payload["source"] = selection.source
        _print_json(payload)
        return 0 if report.ready else 1

    if not report.documentation_root_exists:
        # The documentation root is named relatively under a selected source,
        # so no diagnostic stream carries the private absolute path either.
        documentation_root = (
            config.documentation_root.relative_to(config.project_root).as_posix()
            if selection.source is not None
            else config.documentation_root
        )
        print(f"# Adoption readiness: {selection.selector}")
        print()
        print(
            f"ERROR: documentation root does not exist: {documentation_root}",
            file=sys.stderr,
        )
        print(f"- Next safe command: {next_command}")
        return 1

    print(f"# Adoption readiness: {selection.selector}")
    print()
    print(f"- Blocking structural/configuration errors: {len(report.blocking)}")
    for issue in report.blocking:
        level = "WARNING" if issue.severity == "warning" else "ERROR"
        print(f"  {level}: {issue.path.as_posix()}: {issue.message}", file=sys.stderr)
    print(f"- Resolvable legacy relation migrations: {len(report.resolvable_migrations)}")
    print(f"- Explicit unresolved/resource boundaries: {len(report.boundaries)}")
    print(f"- Stale freshness pins: {len(report.stale_pins)}")
    for issue in report.stale_pins:
        print(f"  WARNING: {issue.path.as_posix()}: {issue.message}", file=sys.stderr)
    print(f"- Projection: {report.projection_state} ({report.projection_reason})")
    print()
    print(
        "Run `docsystem migration-report` or `docsystem validate --verbose-adoption` "
        "for row-level migration and boundary detail."
    )
    print(f"- Next safe command: {next_command}")
    return 0 if report.ready else 1


def index_projection(project_root: Path, *, write: bool = False) -> int:
    try:
        config = load_config(project_root)
        catalog_value = build_catalog(config)
        errors = [
            issue
            for issue in validate_catalog(catalog_value, config)
            if issue.severity != "warning"
        ]
        if errors:
            for issue in errors:
                print(
                    f"ERROR: {issue.path.as_posix()}: {issue.message}",
                    file=sys.stderr,
                )
            return 1
        current = build_projection(catalog_value, config)
        valid, reason = projection_status(config, current)
        if write:
            generation = write_projection(config, current)
            print(f"Projection generation written: {generation}")
            return 0
        if not valid:
            print(f"ERROR: {reason}", file=sys.stderr)
            return 1
        print("Projection is current.")
        return 0
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


def changes(project_root: Path, *, json_output: bool = False) -> int:
    try:
        config = load_config(project_root)
        catalog_value = build_catalog(config)
        errors = [
            issue
            for issue in validate_catalog(catalog_value, config)
            if issue.severity != "warning"
        ]
        if errors:
            for issue in errors:
                print(
                    f"ERROR: {issue.path.as_posix()}: {issue.message}",
                    file=sys.stderr,
                )
            return 1
        current = build_projection(catalog_value, config)
        if json_output:
            report = evaluate_changes(config, current)
            _print_json(
                {
                    "status": report.status,
                    "changes": [
                        {
                            "document_id": change.document_id,
                            "kind": change.kind,
                            "sections": list(change.sections),
                        }
                        for change in report.changes
                    ],
                }
            )
            return 0
        for line in projection_changes(config, current):
            print(line)
        return 0
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


def _validation_summary(issues: tuple[ValidationIssue, ...]) -> dict[str, int]:
    summary = {
        "errors": 0,
        "warnings": 0,
        "adoption_resolved": 0,
        "adoption_boundaries": 0,
        "stale_pins": 0,
    }
    for issue in issues:
        if issue.severity == "warning":
            summary["warnings"] += 1
        else:
            summary["errors"] += 1
        if issue.category == "adoption-resolved":
            summary["adoption_resolved"] += 1
        elif issue.category == "adoption-boundary":
            summary["adoption_boundaries"] += 1
        elif issue.target_id is not None:
            summary["stale_pins"] += 1
    return summary


def _sanitize_local_error(error: Exception, selection: _Selection) -> str:
    """Strip the local project root out of an error message.

    Without a source the message keeps naming the project root exactly as the
    caller typed it. With a source the root is private workspace wiring, so
    every spelling of it is replaced by the reusable selector.
    """

    project_root = selection.project_root
    message = str(error)
    try:
        resolved = project_root.resolve().as_posix()
    except OSError:
        return message
    if selection.source is None:
        return message.replace(resolved, project_root.as_posix())
    for spelling in (resolved, project_root.as_posix(), str(project_root)):
        message = message.replace(spelling, selection.selector)
    return message


def _readiness_diagnostics(
    config,
    catalog_value: MarkdownCatalog,
    selection: _Selection,
) -> list[str]:
    report = evaluate_readiness(config, catalog_value)
    diagnostics = [
        f"documentation_root_exists={report.documentation_root_exists}",
        f"ready={report.ready}",
        f"blocking_errors={len(report.blocking)}",
        f"resolvable_migrations={len(report.resolvable_migrations)}",
        f"boundaries={len(report.boundaries)}",
        f"stale_pins={len(report.stale_pins)}",
        f"projection={report.projection_state} ({report.projection_reason})",
        f"next_command={report.next_command(selection.selector)}",
    ]
    return diagnostics


def _report_body(
    *,
    selection: _Selection,
    project_name: str,
    report_type: str,
    source: str,
    component: str | None,
    command_override: str | None = None,
    extra_section: str = "",
) -> str:
    project_root = selection.project_root
    labels = [
        report_type,
        "triage",
        f"project:{_label_slug(project_name)}",
        f"source:{source}",
    ]
    if component:
        labels.append(f"component:{component}")

    diagnostics: list[str] = []
    config_excerpt = "not available"
    affected: list[str] = []
    runtime_changes = "none; report draft is read-only"
    try:
        config = load_config(project_root)
        catalog_value = build_catalog(config)
        issues = validate_catalog(catalog_value, config)
        summary = _validation_summary(issues)
        diagnostics.extend(_readiness_diagnostics(config, catalog_value, selection))
        diagnostics.append(
            "validation="
            f"{summary['errors']} error(s), {summary['warnings']} warning(s)"
        )
        diagnostics.append(
            "adoption="
            f"{summary['adoption_resolved']} resolved mapping(s), "
            f"{summary['adoption_boundaries']} boundary row(s)"
        )
        diagnostics.append(f"stale_pins={summary['stale_pins']}")
        views, _ = _views_from_catalog(catalog_value)
        ordered_ids = sorted(views)
        freshness = _freshness_rows(config, views, ordered_ids)
        historical_count = sum(
            1
            for item in freshness
            if item["classification"] == "historical snapshot"
        )
        current_stale_count = sum(
            1 for item in freshness if item["classification"] == "stale"
        )
        diagnostics.append(
            "freshness_classification="
            f"{current_stale_count} stale, {historical_count} historical snapshot"
        )
        current = build_projection(catalog_value, config)
        changes_report = evaluate_changes(config, current)
        diagnostics.append(
            f"changes={changes_report.status}, {len(changes_report.changes)} change(s)"
        )
        documentation_root = config.documentation_root.relative_to(
            config.project_root
        ).as_posix()
        config_excerpt = "\n".join(
            (
                f'documentation.root = "{documentation_root}"',
                f'documentation.language = "{config.language}"',
                "areas = "
                + json.dumps(
                    {role: path.as_posix() for role, path in config.areas.items()},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "identifiers = "
                + json.dumps(config.identifiers, ensure_ascii=False, sort_keys=True),
                "catalog.exclude = "
                + json.dumps(
                    list(config.catalog_exclusions),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "navigation.extend_through = "
                + json.dumps(
                    list(config.navigation_extend_through),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                f'relations.legacy_paths = "{config.legacy_relation_mode}"',
                "relations.snapshot_types = "
                + json.dumps(
                    list(config.snapshot_document_types),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "relations.snapshot_rules = "
                + json.dumps(
                    [
                        {
                            "source_type": rule.source_type,
                            "source_status": rule.source_status,
                        }
                        for rule in config.snapshot_rules
                    ],
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                f'projection.format = "{config.projection_format}"',
                "context.views = "
                + json.dumps(
                    [
                        {
                            "name": view.name,
                            "tier": view.tier,
                            "delivery": view.delivery,
                            "direction": view.direction,
                            "depth": view.depth,
                            "relations": list(view.relations),
                            "layers": list(view.layers),
                        }
                        for view in config.context_views
                    ],
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            )
        )
        affected.extend(
            sorted(
                {
                    issue.path.as_posix()
                    for issue in issues
                    if issue.severity != "warning" or issue.target_id is not None
                }
            )
        )
    except (OSError, ValueError) as error:
        diagnostics.append(
            f"configuration_error={_sanitize_local_error(error, selection)}"
        )

    title = REPORT_TYPES[report_type]
    component_text = component or "not selected"
    affected_text = "\n".join(f"- `{item}`" for item in affected) or "- none captured"
    diagnostics_text = "\n".join(f"- {item}" for item in diagnostics) or "- none captured"
    labels_text = ", ".join(f"`{label}`" for label in labels)
    command = command_override or (
        f"docsystem report draft {selection.report_selector} --project-name "
        f"{project_name!r} --type {report_type} --source {source}"
        + (f" --component {component}" if component else "")
    )
    return f"""# {title}: {project_name}

## Labels

{labels_text}

## Project

- Project/adopter name: {project_name}
- Source host or agent: {source}
- DocumentationEngine version: {__version__}
- DocumentationEngine commit: <!-- fill if different from installed version -->
- Component: {component_text}

## Commands run and exit codes

```bash
{command}
exit: 0
```

## Compact diagnostics

{diagnostics_text}

## Sanitized config/profile excerpt

```text
{config_excerpt}
```

## Affected IDs, anchors, or paths

{affected_text}

{extra_section}

## Expected behavior

<!-- Fill in the expected behavior. -->

## Actual behavior

<!-- Fill in the observed behavior or compatibility gap. -->

## Runtime or local-state changes made

{runtime_changes}

## Requested DocumentationEngine action

<!-- Fill in the requested fix, clarification, migration support, or pattern. -->

## Privacy and sanitization checklist

- [ ] Private document bodies are omitted or sanitized.
- [ ] Private scratch, review, roadmap, or planning content is omitted.
- [ ] Config/profile excerpts are sanitized and contain no secrets.
- [ ] Local artifact paths, if any, are pointers only.
- [ ] A minimal public/synthetic fixture is included when this is a core bug.
"""


def _context_gap_address_evidence(
    selection: _Selection,
    raw_addresses: tuple[str, ...],
    *,
    option: str,
) -> tuple[str, ...]:
    """Validate graph addresses and return body-free revision/range evidence."""

    config = load_config(selection.project_root)
    catalog_value = build_catalog(config)
    seen: set[str] = set()
    evidence: list[str] = []
    for raw in raw_addresses:
        address = parse_address(raw)
        if address.text in seen:
            raise ValueError(f"duplicate {option} address: {address.text}")
        seen.add(address.text)
        document = find_document(catalog_value, address.document_id)
        assert document.metadata is not None
        detail = f"revision {document.metadata.revision}"
        if address.anchor is not None:
            section = next(
                (
                    item
                    for item in document.sections
                    if item.anchor == address.anchor
                ),
                None,
            )
            if section is None:
                raise ValueError(
                    f"section anchor not found: {address.document_id}#{address.anchor}"
                )
            detail += f", lines {section.start_line}-{section.end_line}"
        evidence.append(f"{address.text} ({detail})")
    return tuple(evidence)


def _context_gap_section(evidence: _ContextGapEvidence) -> str:
    initial = "\n".join(f"- `{item}`" for item in evidence.initial)
    expanded = "\n".join(f"- `{item}`" for item in evidence.expanded)
    impacts = ", ".join(f"`{item}`" for item in evidence.impacts)
    return f"""## Context expansion evidence

- Classification: material unexpected context gap
- Reason code: `{evidence.reason}`
- Materially affected: {impacts}
- Projection generation: `{evidence.projection}`
- Document bodies included in this evidence: no

### Initial addresses

{initial}

### Additional reads

{expanded}

### Initial packet coverage

<!-- Add a sanitized task category, packet mode/depth, compact included/omitted
counts and the completeness claim. Do not paste the original prompt. -->

### Material effect and missing information category

<!-- Explain what changed after expansion and name the missing relation, section,
profile rule, authority or engine behavior. Do not paste private source text. -->
"""


def context_gap_draft(
    project_root: Path,
    *,
    project_name: str,
    report_type: str,
    source: str,
    reason: str,
    initial: tuple[str, ...],
    expanded: tuple[str, ...],
    impacts: tuple[str, ...],
    output: Path | None = None,
    selection: _Selection | None = None,
) -> int:
    """Draft a report only for an agent-declared material context gap.

    Normal progressive reads remain local behavior. The command validates
    stable addresses and emits revisions/ranges, never document bodies.
    """

    if reason not in CONTEXT_GAP_REASONS:
        print(f"ERROR: unsupported context gap reason: {reason}", file=sys.stderr)
        return 1
    if reason in CONTEXT_GAP_LOCAL_REASONS:
        print(
            f"ERROR: context expansion reason {reason!r} is normally local "
            "evidence, not a reportable product gap",
            file=sys.stderr,
        )
        return 1
    if not initial or not expanded or not impacts:
        print(
            "ERROR: context gap requires initial, expanded and impact evidence",
            file=sys.stderr,
        )
        return 1
    invalid_impacts = sorted(set(impacts) - set(CONTEXT_GAP_IMPACTS))
    if invalid_impacts:
        print(
            "ERROR: unsupported context gap impact: " + ", ".join(invalid_impacts),
            file=sys.stderr,
        )
        return 1
    if len(set(impacts)) != len(impacts):
        print("ERROR: duplicate context gap impact", file=sys.stderr)
        return 1
    selected = selection or _Selection(project_root)
    try:
        initial_evidence = _context_gap_address_evidence(
            selected, initial, option="initial"
        )
        expanded_evidence = _context_gap_address_evidence(
            selected, expanded, option="expanded"
        )
        overlap = {parse_address(item).text for item in initial} & {
            parse_address(item).text for item in expanded
        }
        if overlap:
            raise ValueError(
                "addresses cannot be both initial and expanded: "
                + ", ".join(sorted(overlap))
            )
        config = load_config(selected.project_root)
        loaded, projection_reason = load_verified_projection(config)
    except (OSError, ValueError) as error:
        print(f"ERROR: {_sanitize_local_error(error, selected)}", file=sys.stderr)
        return 1

    ordered_impacts = tuple(
        item for item in CONTEXT_GAP_IMPACTS if item in set(impacts)
    )
    projection = (
        loaded.generation
        if loaded is not None
        else _sanitize_local_error(ValueError(projection_reason), selected)
    )
    evidence = _ContextGapEvidence(
        reason=reason,
        impacts=ordered_impacts,
        initial=initial_evidence,
        expanded=expanded_evidence,
        projection=projection,
    )
    repeated = " ".join(f"--initial {item}" for item in initial)
    repeated += " " + " ".join(f"--expanded {item}" for item in expanded)
    repeated += " " + " ".join(f"--impact {item}" for item in impacts)
    command = (
        f"docsystem report context-gap {selected.report_selector} --project-name "
        f"{project_name!r} --type {report_type} --source {source} "
        f"--reason {reason} {repeated.strip()}"
    )
    text = _report_body(
        selection=selected,
        project_name=project_name,
        report_type=report_type,
        source=source,
        component="context",
        command_override=command,
        extra_section=_context_gap_section(evidence),
    )
    return _write_text_or_stdout(text, output)


def report_draft(
    project_root: Path,
    *,
    project_name: str,
    report_type: str,
    source: str,
    component: str | None = None,
    output: Path | None = None,
    selection: _Selection | None = None,
) -> int:
    if component is not None and component not in REPORT_COMPONENTS:
        print(f"ERROR: unsupported report component: {component}", file=sys.stderr)
        return 1
    text = _report_body(
        selection=selection or _Selection(project_root),
        project_name=project_name,
        report_type=report_type,
        source=source,
        component=component,
    )
    return _write_text_or_stdout(text, output)


def _criterion_json(criterion: WorkstreamCriterion) -> dict[str, object]:
    return {
        "id": criterion.criterion_id,
        "revision": criterion.revision,
        "reference": criterion.reference,
        "required_sections": list(criterion.required_sections),
        "required_evidence": list(criterion.required_evidence),
        "max_attempts": criterion.max_attempts,
        "safe_fallback": criterion.safe_fallback,
    }


def criteria_registry(project_root: Path, *, json_output: bool = False) -> int:
    """Inspect project-authored workstream, intake and admission criteria."""

    try:
        config = load_config(project_root)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if json_output:
        _print_json(
            {
                "criteria": [
                    _criterion_json(criterion)
                    for criterion in config.workstream_criteria
                ],
                "intake": [
                    _intake_criterion_json(criterion)
                    for criterion in config.intake_criteria
                ],
                "admission": [
                    _admission_criterion_json(criterion)
                    for criterion in config.admission_criteria
                ],
            }
        )
        return 0
    for criterion in config.workstream_criteria:
        print(
            f"{criterion.reference}\t{criterion.max_attempts}\t"
            f"{criterion.safe_fallback}\t"
            f"{','.join(criterion.required_sections) or '-'}\t"
            f"{','.join(criterion.required_evidence)}"
        )
    for criterion in config.intake_criteria:
        print(
            f"intake\t{criterion.reference}\t{criterion.max_candidates}\t"
            f"{criterion.safe_fallback}\t"
            f"{','.join(criterion.allowed_decisions)}"
        )
    for criterion in config.admission_criteria:
        print(
            f"admission\t{criterion.reference}\t{criterion.max_autonomy}\t"
            f"{criterion.max_risk}\t{','.join(criterion.allowed_actions)}"
        )
    return 0


def _criterion_by_reference(
    config: ProjectConfig, reference: str
) -> WorkstreamCriterion:
    matches = [
        criterion
        for criterion in config.workstream_criteria
        if criterion.reference == reference
    ]
    if not matches:
        raise WorkstreamError(f"workstream criterion not found: {reference}")
    return matches[0]


def _validate_evidence_address(
    catalog_value: MarkdownCatalog,
    raw: str,
    *,
    field: str,
) -> None:
    try:
        address = parse_address(raw)
        document = find_document(catalog_value, address.document_id)
    except ValueError as error:
        raise WorkstreamError(
            f"{field} has invalid address {raw!r}: {error}"
        ) from error
    if address.anchor is not None and all(
        section.anchor != address.anchor for section in document.sections
    ):
        raise WorkstreamError(
            f"{field} has unknown section address: {address.text}"
        )


def _evaluate_workstream_file(
    config: ProjectConfig,
    document_id: str,
    record_path: Path,
) -> tuple[WorkstreamRecord, WorkstreamEvaluation]:
    catalog_value = build_catalog(config)
    blocking = [
        issue
        for issue in validate_catalog(catalog_value, config)
        if issue.severity != "warning"
    ]
    if blocking:
        first = blocking[0]
        raise WorkstreamError(
            f"catalog validation blocks workstream evidence: "
            f"{first.path.as_posix()}: {first.message}"
        )
    document = find_document(catalog_value, document_id)
    if document.metadata is None or document.metadata.document_type != "workstream":
        raise WorkstreamError(
            f"document {document_id} must declare metadata.type 'workstream'"
        )
    record = load_record(record_path)
    if record.workstream_id != document_id:
        raise WorkstreamError(
            f"record workstream_id {record.workstream_id!r} does not match "
            f"{document_id!r}"
        )
    criterion = _criterion_by_reference(config, record.criterion)
    evaluation = evaluate_record(
        record,
        criterion,
        section_anchors=frozenset(section.anchor for section in document.sections),
    )
    for index, address in enumerate(record.evidence.changes):
        _validate_evidence_address(
            catalog_value, address, field=f"evidence.changes[{index}]"
        )
    for index, address in enumerate(record.evidence.returns):
        _validate_evidence_address(
            catalog_value, address, field=f"evidence.returns[{index}]"
        )
    return record, evaluation


def _workstream_evaluation_json(
    record: WorkstreamRecord,
    evaluation: WorkstreamEvaluation,
) -> dict[str, object]:
    return {
        "workstream_id": evaluation.workstream_id,
        "criterion": evaluation.criterion,
        "final_state": evaluation.final_state,
        "attempts": evaluation.attempts,
        "max_attempts": evaluation.max_attempts,
        "findings": evaluation.findings,
        "resolved_findings": evaluation.resolved_findings,
        "ready_to_finish": evaluation.ready_to_finish,
        "evidence": {
            "changes": list(record.evidence.changes),
            "checks": [
                {
                    "name": check.name,
                    "status": check.status,
                    "evidence": check.evidence,
                }
                for check in record.evidence.checks
            ],
            "review": (
                {
                    "status": record.evidence.review.status,
                    "independent": record.evidence.review.independent,
                    "reviewer": record.evidence.review.reviewer,
                    "evidence": record.evidence.review.evidence,
                }
                if record.evidence.review is not None
                else None
            ),
            "omissions": list(record.evidence.omissions),
            "risks": list(record.evidence.risks),
            "returns": list(record.evidence.returns),
        },
    }


def workstream_status(
    project_root: Path,
    document_id: str,
    *,
    record_path: Path,
    json_output: bool = False,
) -> int:
    """Validate one bounded workstream record without changing source or state."""

    try:
        config = load_config(project_root)
        record, evaluation = _evaluate_workstream_file(
            config, document_id, record_path
        )
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if json_output:
        _print_json(_workstream_evaluation_json(record, evaluation))
        return 0
    print(f"# Workstream evidence: {evaluation.workstream_id}")
    print()
    print(f"- Criterion: {evaluation.criterion}")
    print(f"- Final state: {evaluation.final_state}")
    print(f"- Attempts: {evaluation.attempts}/{evaluation.max_attempts}")
    print(
        f"- Findings: {evaluation.findings}; "
        f"resolved: {evaluation.resolved_findings}"
    )
    print(f"- Ready to finish: {'yes' if evaluation.ready_to_finish else 'no'}")
    return 0


def _intake_criterion_json(criterion: IntakeCriterion) -> dict[str, object]:
    def placement(value: IntakePlacement) -> dict[str, object]:
        return {
            "area": value.area,
            "type": value.document_type,
            "identifier": value.identifier,
            "width": value.width,
        }

    return {
        "id": criterion.criterion_id,
        "revision": criterion.revision,
        "reference": criterion.reference,
        "allowed_decisions": list(criterion.allowed_decisions),
        "max_candidates": criterion.max_candidates,
        "safe_fallback": criterion.safe_fallback,
        "draft": placement(criterion.draft),
        "workstream": placement(criterion.workstream),
    }


def _intake_criterion_by_reference(
    config: ProjectConfig, reference: str
) -> IntakeCriterion:
    matches = [
        criterion
        for criterion in config.intake_criteria
        if criterion.reference == reference
    ]
    if not matches:
        raise IntakeError(f"intake criterion not found: {reference}")
    return matches[0]


def _catalog_allocation_guard(catalog_value: MarkdownCatalog) -> str:
    rows = [
        f"{document.metadata.document_id}@{document.metadata.revision}:"
        f"{document.path.as_posix()}"
        for document in catalog_value.documents
        if document.metadata is not None
    ]
    return hashlib.sha256("\n".join(sorted(rows)).encode()).hexdigest()


def _intake_new_target(
    config: ProjectConfig,
    catalog_value: MarkdownCatalog,
    placement: IntakePlacement,
) -> dict[str, object]:
    prefix = config.identifiers[placement.identifier]
    used_numbers = [
        int(document.metadata.document_id.rsplit("-", 1)[1])
        for document in catalog_value.documents
        if document.metadata is not None
        and document.metadata.document_id.startswith(f"{prefix}-")
    ]
    used_paths = {membership.path for membership in catalog_value.memberships}
    number = max(used_numbers, default=0) + 1
    while True:
        document_id = f"{prefix}-{number:0{placement.width}d}"
        path = config.areas[placement.area] / f"{document_id.lower()}.md"
        if path not in used_paths:
            return {
                "id": document_id,
                "area": placement.area,
                "type": placement.document_type,
                "identifier": placement.identifier,
                "width": placement.width,
                "path": path.as_posix(),
            }
        number += 1


def _intake_payload(
    config: ProjectConfig,
    catalog_value: MarkdownCatalog,
    request: IntakeRequest,
    criterion: IntakeCriterion,
    evaluation: IntakeEvaluation,
) -> dict[str, object]:
    target: dict[str, object] | None = None
    if evaluation.decision == "update-existing":
        owner = next(
            candidate
            for candidate in request.candidates
            if candidate.authority == "owner"
        )
        target = {"address": owner.address}
    elif evaluation.decision == "create-draft":
        target = _intake_new_target(config, catalog_value, criterion.draft)
    elif evaluation.decision == "create-workstream":
        target = _intake_new_target(config, catalog_value, criterion.workstream)
    return {
        "idea_id": request.idea_id,
        "criterion": criterion.reference,
        "request_sha256": request.request_sha256,
        "outcome_sha256": hashlib.sha256(request.outcome.encode()).hexdigest(),
        "source": request.source,
        "decision": evaluation.decision,
        "blocked": evaluation.blocked,
        "reasons": list(evaluation.reasons),
        "requested_decision": evaluation.requested_decision,
        "candidates": [
            {"address": item.address, "authority": item.authority}
            for item in request.candidates
        ],
        "assumptions": list(request.assumptions),
        "target": target,
        "allocation_guard": _catalog_allocation_guard(catalog_value),
    }


def idea_intake(
    project_root: Path,
    *,
    request_path: Path,
    json_output: bool = False,
) -> int:
    """Evaluate one bounded semantic request without writing a document."""

    try:
        config = load_config(project_root)
        catalog_value = build_catalog(config)
        blocking = [
            issue
            for issue in validate_catalog(catalog_value, config)
            if issue.severity != "warning"
        ]
        if blocking:
            first = blocking[0]
            raise IntakeError(
                f"catalog validation blocks idea intake: "
                f"{first.path.as_posix()}: {first.message}"
            )
        request = load_intake_request(request_path)
        criterion = _intake_criterion_by_reference(config, request.criterion)
        evaluation = evaluate_intake_request(request, criterion)
        for index, candidate in enumerate(request.candidates):
            _validate_evidence_address(
                catalog_value,
                candidate.address,
                field=f"candidates[{index}].address",
            )
        payload = _intake_payload(
            config, catalog_value, request, criterion, evaluation
        )
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if json_output:
        _print_json(payload)
        return 0
    print(f"idea\t{request.idea_id}")
    print(f"criterion\t{criterion.reference}")
    print(f"decision\t{evaluation.decision}")
    print(f"blocked\t{str(evaluation.blocked).lower()}")
    for reason in evaluation.reasons:
        print(f"reason\t{reason}")
    for candidate in request.candidates:
        print(f"candidate\t{candidate.authority}\t{candidate.address}")
    target = payload["target"]
    if isinstance(target, dict):
        for name, value in sorted(target.items()):
            print(f"target\t{name}\t{value}")
    for assumption in request.assumptions:
        print(f"assumption\t{assumption}")
    print(f"allocation_guard\t{payload['allocation_guard']}")
    return 0


def _admission_criterion_json(
    criterion: AdmissionCriterion,
) -> dict[str, object]:
    return {
        "id": criterion.criterion_id,
        "revision": criterion.revision,
        "reference": criterion.reference,
        "max_autonomy": criterion.max_autonomy,
        "allowed_actions": list(criterion.allowed_actions),
        "required_authorizations": list(criterion.required_authorizations),
        "allowed_verification": list(criterion.allowed_verification),
        "max_risk": criterion.max_risk,
        "max_targets": criterion.max_targets,
        "required_sections": list(criterion.required_sections),
        "require_source_scope_for": list(criterion.require_source_scope_for),
        "safe_fallback": criterion.safe_fallback,
    }


def _admission_criterion_by_reference(
    config: ProjectConfig, reference: str
) -> AdmissionCriterion:
    matches = [
        criterion
        for criterion in config.admission_criteria
        if criterion.reference == reference
    ]
    if not matches:
        raise AdmissionError(f"admission criterion not found: {reference}")
    return matches[0]


def _admission_payload(
    catalog_value: MarkdownCatalog,
    request: AdmissionRequest,
    criterion: AdmissionCriterion,
    evaluation: AdmissionEvaluation,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "workstream_id": request.workstream_id,
        "criterion": criterion.reference,
        "intake_request_sha256": request.intake_request_sha256,
        "request_sha256": request.request_sha256,
        "outcome_sha256": hashlib.sha256(request.outcome.encode()).hexdigest(),
        "decision": evaluation.decision,
        "blocked": evaluation.blocked,
        "reasons": list(evaluation.reasons),
        "required_autonomy": evaluation.required_autonomy,
        "actions": list(request.actions),
        "targets": list(request.targets),
        "risk": request.risk,
        "verification": request.verification,
        "boundaries": {
            "authored_deletion": request.boundaries.authored_deletion,
            "privacy_boundary": request.boundaries.privacy_boundary,
            "permission_expansion": request.boundaries.permission_expansion,
            "external_commitment": request.boundaries.external_commitment,
        },
        "authorizations": [
            {
                "action": item.action,
                "authority": item.authority,
                "evidence": item.evidence,
            }
            for item in request.authorizations
        ],
        "missing_authorizations": list(evaluation.missing_authorizations),
        "assumptions": list(request.assumptions),
        "catalog_guard": _catalog_allocation_guard(catalog_value),
    }
    if request.source_scope:
        payload["source_scope"] = [
            {"path": item.path, "sha256": item.sha256}
            for item in request.source_scope
        ]
    return payload


def _validate_admission_source_scope(
    project_root: Path, request: AdmissionRequest
) -> None:
    root = project_root.resolve()
    for item in request.source_scope:
        candidate = (root / item.path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise AdmissionError(
                f"source_scope path escapes the project root: {item.path}"
            ) from error
        if item.sha256 is None:
            if candidate.exists():
                raise AdmissionError(
                    f"source_scope expected an absent path: {item.path}"
                )
            continue
        if not candidate.is_file():
            raise AdmissionError(f"source_scope file does not exist: {item.path}")
        actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if actual != item.sha256:
            raise AdmissionError(
                f"source_scope hash does not match current file: {item.path}"
            )


def _evaluate_execution_admission(
    project_root: Path,
    document_id: str,
    request_path: Path,
) -> tuple[
    ProjectConfig,
    MarkdownCatalog,
    AdmissionRequest,
    AdmissionCriterion,
    AdmissionEvaluation,
]:
    config = load_config(project_root)
    catalog_value = build_catalog(config)
    blocking = [
        issue
        for issue in validate_catalog(catalog_value, config)
        if issue.severity != "warning"
    ]
    if blocking:
        first = blocking[0]
        raise AdmissionError(
            f"catalog validation blocks execution admission: "
            f"{first.path.as_posix()}: {first.message}"
        )
    request = load_admission_request(request_path)
    if request.workstream_id != document_id:
        raise AdmissionError(
            f"request workstream_id {request.workstream_id!r} does not match "
            f"{document_id!r}"
        )
    criterion = _admission_criterion_by_reference(config, request.criterion)
    evaluation = evaluate_admission_request(request, criterion)
    _validate_admission_source_scope(project_root, request)
    document = find_document(catalog_value, document_id)
    if (
        document.metadata is None
        or document.metadata.document_type != "workstream"
    ):
        raise AdmissionError(
            f"document {document_id} must declare metadata.type 'workstream'"
        )
    if document.metadata.status in {"completed", "cancelled", "failed"}:
        raise AdmissionError(
            f"workstream {document_id} has terminal status "
            f"{document.metadata.status!r}"
        )
    anchors = {section.anchor for section in document.sections}
    missing_sections = sorted(set(criterion.required_sections) - anchors)
    if missing_sections:
        raise AdmissionError(
            f"workstream {document_id} is missing required section(s): "
            + ", ".join(missing_sections)
        )
    for index, target in enumerate(request.targets):
        _validate_evidence_address(
            catalog_value, target, field=f"targets[{index}]"
        )
    return config, catalog_value, request, criterion, evaluation


def execution_admission(
    project_root: Path,
    document_id: str,
    *,
    request_path: Path,
    json_output: bool = False,
) -> int:
    """Evaluate one bounded A0-A2 intent without executing or writing it."""

    try:
        _, catalog_value, request, criterion, evaluation = (
            _evaluate_execution_admission(
                project_root, document_id, request_path
            )
        )
        payload = _admission_payload(
            catalog_value, request, criterion, evaluation
        )
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if json_output:
        _print_json(payload)
        return 0
    print(f"workstream\t{request.workstream_id}")
    print(f"criterion\t{criterion.reference}")
    print(f"decision\t{evaluation.decision}")
    print(f"blocked\t{str(evaluation.blocked).lower()}")
    print(f"required_autonomy\t{evaluation.required_autonomy}")
    for reason in evaluation.reasons:
        print(f"reason\t{reason}")
    for action in request.actions:
        print(f"action\t{action}")
    for target in request.targets:
        print(f"target\t{target}")
    print(f"catalog_guard\t{payload['catalog_guard']}")
    return 0


def _execution_address_snapshot(
    catalog_value: MarkdownCatalog, raw_address: str
) -> dict[str, object]:
    address = parse_address(raw_address)
    document = find_document(catalog_value, address.document_id)
    assert document.metadata is not None
    snapshot: dict[str, object] = {
        "address": address.text,
        "document_id": address.document_id,
        "revision": document.metadata.revision,
        "path": document.path.as_posix(),
        "document_sha256": hashlib.sha256(document.content.encode()).hexdigest(),
    }
    if address.anchor is None:
        snapshot["section"] = None
        return snapshot
    section = next(item for item in document.sections if item.anchor == address.anchor)
    lines = document.content.splitlines()
    section_text = "\n".join(lines[section.start_line - 1 : section.end_line])
    snapshot["section"] = {
        "anchor": section.anchor,
        "title": section.title,
        "level": section.level,
        "start_line": section.start_line,
        "end_line": section.end_line,
        "sha256": hashlib.sha256(section_text.encode()).hexdigest(),
    }
    return snapshot


def _execution_change_plan(
    reference_graph: SectionReferenceGraph,
    raw_address: str,
) -> ChangePlan:
    address = parse_address(raw_address)

    def _forward(current: Address) -> tuple[GraphEdge, ...]:
        return reference_graph.forward(current)

    def _reverse(current: Address) -> tuple[GraphEdge, ...]:
        return reference_graph.reverse_edges(current)

    forward_reasons = graph_traverse_reasons(
        address,
        forward=_forward,
        reverse_edges=_reverse,
        reverse=False,
        transitive=True,
    )
    reverse_reasons = graph_traverse_reasons(
        address,
        forward=_forward,
        reverse_edges=_reverse,
        reverse=True,
        transitive=True,
    )
    touched = (address, *dict.fromkeys(item.address for item in forward_reasons))
    boundaries = tuple(
        boundary
        for source in touched
        for boundary in reference_graph.boundaries_from(source)
    )
    return build_change_plan(
        address,
        reverse=True,
        transitive=True,
        forward_reasons=forward_reasons,
        reverse_reasons=reverse_reasons,
        boundaries=boundaries,
    )


def _execution_plan_json(plan: ChangePlan) -> dict[str, object]:
    return {
        "address": plan.address.text,
        "reverse": plan.reverse,
        "transitive": plan.transitive,
        "items": [_change_plan_item_json(item) for item in plan.items],
        "boundaries": [
            _references_boundary_json(boundary) for boundary in plan.boundaries
        ],
        "completeness": {
            "authored": plan.completeness.authored,
            "observed": plan.completeness.observed,
            "generated": plan.completeness.generated,
        },
    }


def _build_execution_handoff(
    project_root: Path,
    document_id: str,
    admission_path: Path,
) -> dict[str, object]:
    config, catalog_value, request, criterion, evaluation = (
        _evaluate_execution_admission(project_root, document_id, admission_path)
    )
    if evaluation.blocked:
        raise ExecutionPacketError(
            "execution admission is blocked: " + ", ".join(evaluation.reasons)
        )
    mandate = find_document(catalog_value, document_id)
    assert mandate.metadata is not None
    required_sections = [
        _execution_address_snapshot(catalog_value, f"{document_id}#{anchor}")
        for anchor in criterion.required_sections
    ]
    reference_graph = build_reference_graph(catalog_value, config)
    plan_groups: list[list[ChangePlan]] = []
    for target in request.targets:
        target_address = parse_address(target)
        plan_addresses = [target]
        if target_address.anchor is not None:
            plan_addresses.append(target_address.document_id)
        plan_groups.append(
            [
                _execution_change_plan(reference_graph, address)
                for address in plan_addresses
            ]
        )
    target_rows = [
        {
            "snapshot": _execution_address_snapshot(catalog_value, target),
            "change_plans": [_execution_plan_json(plan) for plan in plans],
        }
        for target, plans in zip(request.targets, plan_groups, strict=True)
    ]
    dispositions: dict[str, set[str]] = {
        "read": {
            f"{document_id}#{anchor}" for anchor in criterion.required_sections
        },
        "review": set(),
    }
    for plans in plan_groups:
        for plan in plans:
            for item in plan.items:
                dispositions[item.disposition].add(item.address.text)
    dispositions["review"].difference_update(dispositions["read"])
    admission_payload = _admission_payload(
        catalog_value, request, criterion, evaluation
    )
    packet: dict[str, object] = {
        "kind": "execution-handoff",
        "workstream_id": document_id,
        "admission": admission_payload,
        "mandate": {
            "id": document_id,
            "revision": mandate.metadata.revision,
            "status": mandate.metadata.status,
            "path": mandate.path.as_posix(),
            "document_sha256": hashlib.sha256(mandate.content.encode()).hexdigest(),
            "required_sections": required_sections,
        },
        "targets": target_rows,
        "context_manifest": {
            "read": sorted(dispositions["read"]),
            "review": sorted(dispositions["review"]),
            "content_embedded": False,
            "expansion": "on-demand-by-stable-address",
        },
    }
    if request.source_scope:
        packet["source_scope"] = [
            {
                "path": item.path,
                "sha256": item.sha256,
                "bytes": (
                    None
                    if item.sha256 is None
                    else (project_root.resolve() / item.path).stat().st_size
                ),
            }
            for item in request.source_scope
        ]
    return seal_packet(packet)


def execution_handoff(
    project_root: Path,
    document_id: str,
    *,
    admission_path: Path,
    verify_path: Path | None = None,
    json_output: bool = False,
) -> int:
    """Build or verify an immutable handoff without executing the workstream."""

    try:
        current = _build_execution_handoff(
            project_root, document_id, admission_path
        )
        if verify_path is not None:
            supplied = load_packet(verify_path)
            if supplied != current:
                raise ExecutionPacketError(
                    "execution packet is stale or does not match the admitted intent"
                )
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if json_output:
        if verify_path is None:
            print(json.dumps(current, ensure_ascii=False, sort_keys=True, indent=2))
        else:
            _print_json(
                {
                    "workstream_id": document_id,
                    "packet_sha256": current["packet_sha256"],
                    "current": True,
                }
            )
        return 0
    print(f"workstream\t{document_id}")
    print(f"packet_sha256\t{current['packet_sha256']}")
    print(f"targets\t{len(current['targets'])}")
    print(f"verified\t{str(verify_path is not None).lower()}")
    return 0


def execution_result(
    project_root: Path,
    document_id: str,
    *,
    packet_path: Path,
    result_path: Path,
    json_output: bool = False,
) -> int:
    """Validate structured changed-file evidence against an admitted packet."""

    try:
        packet = load_packet(packet_path)
        result = load_execution_result(result_path)
        if packet.get("workstream_id") != document_id:
            raise ExecutionPacketError(
                "execution packet workstream_id does not match the requested ID"
            )
        if result.workstream_id != document_id:
            raise ExecutionPacketError(
                "execution result workstream_id does not match the requested ID"
            )
        if result.packet_sha256 != packet["packet_sha256"]:
            raise ExecutionPacketError(
                "execution result does not reference the supplied packet"
            )
        raw_scope = packet.get("source_scope")
        if not isinstance(raw_scope, list):
            raise ExecutionPacketError("execution packet has no valid source_scope")
        baseline: dict[str, str | None] = {}
        for index, raw in enumerate(raw_scope):
            if not isinstance(raw, dict):
                raise ExecutionPacketError(
                    f"execution packet source_scope[{index}] is invalid"
                )
            path = raw.get("path")
            digest = raw.get("sha256")
            if (
                not isinstance(path, str)
                or path in baseline
                or (
                    digest is not None
                    and (
                        not isinstance(digest, str)
                        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                    )
                )
            ):
                raise ExecutionPacketError(
                    f"execution packet source_scope[{index}] is invalid"
                )
            try:
                normalized = normalize_source_path(path, "execution packet source path")
            except AdmissionError as error:
                raise ExecutionPacketError(str(error)) from error
            if normalized != path:
                raise ExecutionPacketError(
                    f"execution packet source_scope[{index}] is invalid"
                )
            baseline[path] = digest
        declared = {item.path: item.sha256 for item in result.changed_files}
        outside = sorted(set(declared) - set(baseline))
        if outside:
            raise ExecutionPacketError(
                "execution result contains out-of-scope path(s): "
                + ", ".join(outside)
            )
        root = project_root.resolve()
        current: dict[str, str | None] = {}
        for path in baseline:
            candidate = (root / path).resolve()
            try:
                candidate.relative_to(root)
            except ValueError as error:
                raise ExecutionPacketError(
                    f"execution packet source path escapes project root: {path}"
                ) from error
            if candidate.exists() and not candidate.is_file():
                raise ExecutionPacketError(
                    f"execution source path is not a file: {path}"
                )
            current[path] = (
                hashlib.sha256(candidate.read_bytes()).hexdigest()
                if candidate.is_file()
                else None
            )
        actual = {path for path in baseline if current[path] != baseline[path]}
        missing = sorted(actual - set(declared))
        unchanged = sorted(set(declared) - actual)
        if missing:
            raise ExecutionPacketError(
                "execution result omits changed scoped path(s): "
                + ", ".join(missing)
            )
        if unchanged:
            raise ExecutionPacketError(
                "execution result declares unchanged path(s): "
                + ", ".join(unchanged)
            )
        mismatched = sorted(
            path for path, digest in declared.items() if current[path] != digest
        )
        if mismatched:
            raise ExecutionPacketError(
                "execution result hash does not match current path(s): "
                + ", ".join(mismatched)
            )
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    payload = {
        "workstream_id": document_id,
        "packet_sha256": packet["packet_sha256"],
        "changed_files": [
            {"path": item.path, "sha256": item.sha256}
            for item in result.changed_files
        ],
        "declared_changes_within_scope": True,
        "inventory_authority": "caller-declared",
    }
    if json_output:
        _print_json(payload)
        return 0
    print(f"workstream\t{document_id}")
    print(f"packet_sha256\t{packet['packet_sha256']}")
    print(f"changed_files\t{len(result.changed_files)}")
    print("declared_changes_within_scope\ttrue")
    print("inventory_authority\tcaller-declared")
    return 0


def finish(
    project_root: Path,
    document_id: str,
    *,
    depth: int = 1,
    include_related: bool = False,
    json_output: bool = False,
    context_expansion: str = "not-observed",
    context_gap_report: str = "not-needed",
    workstream_record: Path | None = None,
) -> int:
    if context_expansion not in CONTEXT_EXPANSION_STATES:
        print(
            f"ERROR: unsupported context expansion state: {context_expansion}",
            file=sys.stderr,
        )
        return 1
    if context_gap_report not in CONTEXT_GAP_REPORT_STATES:
        print(
            f"ERROR: unsupported context gap report state: {context_gap_report}",
            file=sys.stderr,
        )
        return 1
    valid_report_state = (
        context_gap_report in {"drafted", "filed"}
        if context_expansion == "material-gap"
        else context_gap_report == "not-needed"
    )
    if not valid_report_state:
        print(
            "ERROR: material-gap expansion requires a drafted or filed report; "
            "other expansion states require not-needed",
            file=sys.stderr,
        )
        return 1
    workstream_result: tuple[WorkstreamRecord, WorkstreamEvaluation] | None = None
    try:
        config = load_config(project_root)
        if workstream_record is not None:
            workstream_result = _evaluate_workstream_file(
                config, document_id, workstream_record
            )
            if not workstream_result[1].ready_to_finish:
                raise WorkstreamError(
                    "workstream record is valid but final state is not completed"
                )
        views, _, catalog_value = _load_views(config)
        if catalog_value is not None:
            find_document(catalog_value, document_id)
        elif document_id not in views:
            raise ValueError(f"document ID not found: {document_id}")
        included, _ = _context_selection(
            views,
            document_id,
            depth=depth,
            include_related=include_related,
        )
        ordered = _ordered_selection(included, document_id)
        target = views[document_id]
        omitted_by_document = {
            selected_id: [
                section.anchor
                for section in views[selected_id].sections
                if section.level == 2
                and section.anchor not in config.navigation_extend_through
            ]
            for selected_id in ordered
        }
        freshness = _freshness_rows(config, views, ordered)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    migrations = [
        {
            "source_id": selected_id,
            "relation": relation,
            "value": value,
            "target_id": target_id,
        }
        for selected_id in ordered
        for relation, value, target_id in views[selected_id].migrations
    ]
    boundaries = [
        {
            "source_id": selected_id,
            "relation": relation,
            "value": value,
            "reason": reason,
        }
        for selected_id in ordered
        for relation, value, reason in views[selected_id].boundaries
    ]
    if json_output:
        payload: dict[str, object] = {
            "target": document_id,
            "path": target.path.as_posix(),
            "depth": depth,
            "include_related": include_related,
            "included_documents": [
                {
                    "id": selected_id,
                    "path": views[selected_id].path.as_posix(),
                    "relations": sorted(included[selected_id]),
                    "omitted_h2": omitted_by_document[selected_id],
                }
                for selected_id in ordered
            ],
            "freshness": freshness,
            "migrations": migrations,
            "boundaries": boundaries,
        }
        if context_expansion != "not-observed":
            payload["context_expansion"] = {
                "classification": context_expansion,
                "report_state": context_gap_report,
            }
        if workstream_result is not None:
            payload["workstream"] = _workstream_evaluation_json(*workstream_result)
        _print_json(payload)
        return 0

    print(f"# Finish handoff: {document_id}")
    print()
    print(f"- Path: `{target.path.as_posix()}`")
    print(f"- Type/status: {target.document_type} / {target.status}")
    print(f"- Revision: {target.revision}")
    print(f"- Dependency depth summarized: {depth}")
    print(f"- Related traversal: {'included' if include_related else 'omitted'}")
    print()
    print("## Included context")
    print()
    for selected_id in ordered:
        relation_text = ", ".join(sorted(included[selected_id]))
        omitted = omitted_by_document[selected_id]
        print(
            f"- `{selected_id}` — `{views[selected_id].path.as_posix()}` "
            f"({relation_text}); omitted H2: "
            f"{', '.join(omitted) if omitted else 'none'}"
        )
    print()
    print("## Freshness and snapshot pins")
    print()
    if freshness:
        for row in freshness:
            print(
                f"- `{row['source_id']}` pins `{row['target_id']}@"
                f"{row['pinned_revision']}`; current revision is "
                f"{row['current_revision']} — {row['classification']}"
            )
    else:
        print("- No stale or historical snapshot pins among included documents.")
    print()
    print("## Migration and boundary notes")
    print()
    if migrations:
        for item in migrations:
            print(
                f"- `{item['source_id']}` {item['relation']} "
                f"`{item['value']}` -> `{item['target_id']}`"
            )
    else:
        print("- No resolved legacy relation mappings among included documents.")
    if boundaries:
        for item in boundaries:
            print(
                f"- `{item['source_id']}` unresolved/resource "
                f"{item['relation']} `{item['value']}` ({item['reason']})"
            )
    else:
        print("- No unresolved/resource boundaries among included documents.")
    if context_expansion != "not-observed":
        print()
        print("## Context expansion")
        print()
        print(f"- Classification: {context_expansion}")
        print(f"- Report state: {context_gap_report}")
    if workstream_result is not None:
        record, evaluation = workstream_result
        print()
        print("## Verified workstream evidence")
        print()
        print(f"- Criterion: {evaluation.criterion}")
        print(f"- Lifecycle: {evaluation.final_state}")
        print(f"- Attempts: {evaluation.attempts}/{evaluation.max_attempts}")
        print(
            f"- Findings: {evaluation.findings}; "
            f"resolved: {evaluation.resolved_findings}"
        )
        print(
            "- Changes: "
            + (", ".join(record.evidence.changes) or "none")
        )
        print(
            "- Checks: "
            + (
                ", ".join(
                    f"{check.name}={check.status}"
                    for check in record.evidence.checks
                )
                or "none"
            )
        )
        if record.evidence.review is not None:
            print(
                f"- Review: {record.evidence.review.status}; "
                f"independent={str(record.evidence.review.independent).lower()}; "
                f"reviewer={record.evidence.review.reviewer}"
            )
        print(
            "- Omissions: "
            + (", ".join(record.evidence.omissions) or "none declared")
        )
        print("- Risks: " + (", ".join(record.evidence.risks) or "none declared"))
        print(
            "- Returns to: "
            + (", ".join(record.evidence.returns) or "none")
        )
    print()
    print("## Return protocol")
    print()
    print("- Re-run `docsystem validate PROJECT` before handing work back.")
    print("- Re-run `docsystem changes PROJECT` after writing a projection.")
    print("- File adopter reports with `docsystem report draft` when reusable gaps remain.")
    return 0


def dependencies(project_root: Path, document_id: str, *, reverse: bool = False) -> int:
    try:
        config = load_config(project_root)
        markdown_catalog = build_catalog(config)
        document = find_document(markdown_catalog, document_id)
        metadata_issues = validate_metadata(markdown_catalog, config)
        relevant_issues = (
            (*validate_membership(markdown_catalog), *metadata_issues)
            if reverse
            else tuple(
                issue for issue in metadata_issues if issue.path == document.path
            )
        )
        graph_errors = [
            issue
            for issue in relevant_issues
            if issue.affects_graph and issue.severity != "warning"
        ]
        for issue in relevant_issues:
            related_warning = (
                issue.severity == "warning"
                and (not reverse or issue.target_id == document_id)
            )
            if related_warning or issue in graph_errors:
                level = "WARNING" if issue.severity == "warning" else "ERROR"
                print(
                    f"{level}: {issue.path.as_posix()}: {issue.message}",
                    file=sys.stderr,
                )
        if graph_errors:
            return 1
        graph = build_dependency_graph(markdown_catalog)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    edges = graph.incoming(document_id) if reverse else graph.outgoing(document_id)
    for edge in sorted(
        edges,
        key=lambda item: (
            item.relation,
            item.source_id if reverse else item.target_id,
        ),
    ):
        peer = edge.source_id if reverse else edge.target_id
        expected_revision = (
            str(edge.expected_revision)
            if edge.expected_revision is not None
            else "-"
        )
        print(f"{edge.relation}\t{peer}\t{expected_revision}")
    return 0


# Text column order for `references`: kind, relation, authority, origin,
# distance, class (direct/transitive), address, path, reason. `kind` is
# "edge" for a traversal result or "boundary" for a visible unresolved
# target; unused columns for a boundary row are "-".
_REFERENCES_COLUMNS = (
    "kind",
    "relation",
    "authority",
    "origin",
    "distance",
    "class",
    "address",
    "path",
    "reason",
)


def _references_result_row(result: TraversalResult) -> str:
    path = " -> ".join(address.text for address in result.path.addresses)
    return "\t".join(
        (
            "edge",
            result.relation,
            result.authority,
            result.origin,
            str(result.distance),
            "direct" if result.direct else "transitive",
            result.address.text,
            path,
            result.reason or "-",
        )
    )


def _references_boundary_row(boundary: Boundary) -> str:
    return "\t".join(
        (
            "boundary",
            "-",
            "-",
            "-",
            "-",
            "-",
            boundary.source.text,
            boundary.raw_target,
            f"{boundary.category}: {boundary.reason}",
        )
    )


def _references_result_json(result: TraversalResult) -> dict[str, object]:
    return {
        "address": result.address.text,
        "relation": result.relation,
        "authority": result.authority,
        "origin": result.origin,
        "distance": result.distance,
        "direct": result.direct,
        "path": [address.text for address in result.path.addresses],
        "reason": result.reason,
    }


def _references_boundary_json(boundary: Boundary) -> dict[str, object]:
    return {
        "source": boundary.source.text,
        "raw_target": boundary.raw_target,
        "category": boundary.category,
        "reason": boundary.reason,
    }


def _references_direct(
    config: ProjectConfig, address: Address, *, reverse: bool, transitive: bool
) -> _ReferencesOutput | None:
    """Resolve entirely from Markdown: the fallback and no-projection path."""

    markdown_catalog = build_catalog(config)
    try:
        document = find_document(markdown_catalog, address.document_id)
    except ValueError:
        return None
    if address.anchor is not None and address.anchor not in {
        section.anchor for section in document.sections
    }:
        return None
    all_graph_issues = (
        *validate_membership(markdown_catalog),
        *validate_metadata(markdown_catalog, config),
        *validate_adoption(markdown_catalog, config),
    )
    relevant_issues = (
        all_graph_issues
        if reverse or transitive
        else tuple(
            issue
            for issue in all_graph_issues
            if issue.path == document.path
        )
    )
    blockers = tuple(
        issue
        for issue in relevant_issues
        if issue.affects_graph and issue.severity != "warning"
    )
    if blockers:
        raise _ReferenceGraphInvalid(blockers)
    reference_graph = build_reference_graph(markdown_catalog, config)
    results = reference_graph.traverse(address, reverse=reverse, transitive=transitive)
    boundary_sources = (
        ()
        if reverse
        else (address, *(result.address for result in results if transitive))
    )
    boundaries = tuple(
        sorted(
            (
                boundary
                for source in boundary_sources
                for boundary in reference_graph.boundaries_from(source)
            ),
            key=lambda item: (item.source.text, item.category, item.raw_target),
        )
    )
    observed = "unknown" if reverse else ("bounded" if boundaries else "complete")
    return _ReferencesOutput(results, boundaries, observed)


def _references_projected(
    config: ProjectConfig, address: Address, *, reverse: bool, transitive: bool
) -> _ReferencesOutput | None:
    """Resolve using only the shards the query touches, or raise/return None.

    Returns `None` for a genuinely unknown ID/anchor (proven by manifest
    membership, which `open_targeted_projection` already bound to current
    source freshness). Raises `ProjectionUnavailable` when a *touched* shard
    fails verification, so the caller can fall back to `_references_direct`
    for the whole query instead of reporting a partial result.
    """

    accessor, reason = open_targeted_projection(config)
    if accessor is None:
        raise ProjectionUnavailable(reason)
    if address.document_id not in accessor.manifest.get("documents", {}):
        return None
    if address.anchor is not None:
        document_shard = accessor.document(address.document_id)
        if document_shard is None:
            raise ProjectionUnavailable(f"document shard unavailable for {address.document_id}")
        if address.anchor not in document_shard.get("sections", {}):
            return None
    observed_boundaries: dict[tuple[str, str, str], Boundary] = {}

    def _forward(current: Address) -> tuple:
        edges = targeted_forward_edges(accessor, current)
        if edges is None:
            raise ProjectionUnavailable(f"references shard unavailable for {current.text}")
        for item in edges[1]:
            boundary = Boundary(
                current, item["raw_target"], item["category"], item["reason"]
            )
            observed_boundaries[
                (boundary.source.text, boundary.category, boundary.raw_target)
            ] = boundary
        return edges[0]

    def _reverse_edges(current: Address) -> tuple:
        edges = targeted_reverse_edges(accessor, current)
        if edges is None:
            raise ProjectionUnavailable(f"reverse shard unavailable for {current.text}")
        return edges

    results = graph_traverse(
        address, forward=_forward, reverse_edges=_reverse_edges, reverse=reverse,
        transitive=transitive,
    )
    boundaries = tuple(
        sorted(
            observed_boundaries.values(),
            key=lambda item: (item.source.text, item.category, item.raw_target),
        )
    )
    observed = "unknown" if reverse else ("bounded" if boundaries else "complete")
    return _ReferencesOutput(results, boundaries, observed)


def references(
    project_root: Path,
    raw_address: str,
    *,
    reverse: bool = False,
    transitive: bool = False,
    json_output: bool = False,
) -> int:
    try:
        config = load_config(project_root)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    try:
        address = parse_address(raw_address)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    diagnostic: str | None = None
    output = None
    try:
        output = _references_projected(
            config, address, reverse=reverse, transitive=transitive
        )
    except ProjectionUnavailable as error:
        diagnostic = f"{error}; using direct Markdown"
        try:
            output = _references_direct(
                config, address, reverse=reverse, transitive=transitive
            )
        except _ReferenceGraphInvalid as invalid:
            for issue in invalid.issues:
                print(
                    f"ERROR: {issue.path.as_posix()}: {issue.message}",
                    file=sys.stderr,
                )
            return 1

    if output is None:
        print(f"ERROR: unknown graph address: {address.text}", file=sys.stderr)
        return 1

    if diagnostic is not None:
        print(f"NOTE: {diagnostic}", file=sys.stderr)

    results, boundaries = output.results, output.boundaries
    if json_output:
        _print_json(
            {
                "address": address.text,
                "reverse": reverse,
                "transitive": transitive,
                "results": [_references_result_json(result) for result in results],
                "boundaries": [_references_boundary_json(boundary) for boundary in boundaries],
                "completeness": {
                    "authored": "complete",
                    "observed": output.observed_completeness,
                },
            }
        )
        return 0
    for result in results:
        print(_references_result_row(result))
    for boundary in boundaries:
        print(_references_boundary_row(boundary))
    return 0


# Text column order for `change-plan`: kind, address, disposition, scope,
# relation, authority, origin, distance, class, path, reason. `kind` is
# "item" for one plan-item reason, "boundary" for a visible unresolved
# target, or "completeness" for one graph-layer state; unused columns for a
# "boundary" or "completeness" row are "-". A "completeness" row reuses
# `address` for the layer name (`authored`/`observed`/`generated`) and
# `reason` for its `complete`/`bounded`/`unknown`/`not-enumerated` state.
_CHANGE_PLAN_COLUMNS = (
    "kind",
    "address",
    "disposition",
    "scope",
    "relation",
    "authority",
    "origin",
    "distance",
    "class",
    "path",
    "reason",
)


def _change_plan_reason_class(reason: InclusionReason) -> str:
    if reason.scope == "target":
        return "target"
    return "direct" if reason.direct else "transitive"


def _change_plan_item_rows(item: PlanItem) -> tuple[str, ...]:
    rows = []
    for reason in item.reasons:
        path = " -> ".join(step.text for step in reason.path)
        rows.append(
            "\t".join(
                (
                    "item",
                    item.address.text,
                    item.disposition,
                    reason.scope,
                    reason.relation,
                    reason.authority,
                    reason.origin,
                    str(reason.distance),
                    _change_plan_reason_class(reason),
                    path,
                    reason.detail or "-",
                )
            )
        )
    return tuple(rows)


def _change_plan_boundary_row(boundary: Boundary) -> str:
    return "\t".join(
        (
            "boundary",
            boundary.source.text,
            "-",
            "-",
            "-",
            "-",
            "-",
            "-",
            "-",
            boundary.raw_target,
            f"{boundary.category}: {boundary.reason}",
        )
    )


def _change_plan_completeness_rows(completeness: Completeness) -> tuple[str, ...]:
    return tuple(
        "\t".join(("completeness", layer, "-", "-", "-", "-", "-", "-", "-", "-", state))
        for layer, state in (
            ("authored", completeness.authored),
            ("observed", completeness.observed),
            ("generated", completeness.generated),
        )
    )


def _change_plan_reason_json(reason: InclusionReason) -> dict[str, object]:
    return {
        "scope": reason.scope,
        "relation": reason.relation,
        "authority": reason.authority,
        "origin": reason.origin,
        "distance": reason.distance,
        "direct": reason.direct,
        "path": [step.text for step in reason.path],
        "detail": reason.detail,
    }


def _change_plan_item_json(item: PlanItem) -> dict[str, object]:
    return {
        "address": item.address.text,
        "disposition": item.disposition,
        "reasons": [_change_plan_reason_json(reason) for reason in item.reasons],
    }


def _change_plan_direct(
    config: ProjectConfig, address: Address, *, reverse: bool, transitive: bool
) -> ChangePlan | None:
    """Resolve entirely from Markdown: the fallback and no-projection path."""

    markdown_catalog = build_catalog(config)
    try:
        document = find_document(markdown_catalog, address.document_id)
    except ValueError:
        return None
    if address.anchor is not None and address.anchor not in {
        section.anchor for section in document.sections
    }:
        return None
    all_graph_issues = (
        *validate_membership(markdown_catalog),
        *validate_metadata(markdown_catalog, config),
        *validate_adoption(markdown_catalog, config),
    )
    blockers = tuple(
        issue
        for issue in all_graph_issues
        if issue.affects_graph and issue.severity != "warning"
    )
    if blockers:
        raise _ReferenceGraphInvalid(blockers)
    reference_graph = build_reference_graph(markdown_catalog, config)

    def _forward(current: Address) -> tuple[GraphEdge, ...]:
        return reference_graph.forward(current)

    def _reverse(current: Address) -> tuple[GraphEdge, ...]:
        return reference_graph.reverse_edges(current)

    forward_reasons = graph_traverse_reasons(
        address, forward=_forward, reverse_edges=_reverse, reverse=False, transitive=transitive
    )
    reverse_reasons = (
        graph_traverse_reasons(
            address, forward=_forward, reverse_edges=_reverse, reverse=True, transitive=transitive
        )
        if reverse
        else ()
    )
    touched = (
        (address,)
        if not transitive
        else (address, *dict.fromkeys(result.address for result in forward_reasons))
    )
    boundaries = tuple(
        boundary for source in touched for boundary in reference_graph.boundaries_from(source)
    )
    return build_change_plan(
        address,
        reverse=reverse,
        transitive=transitive,
        forward_reasons=forward_reasons,
        reverse_reasons=reverse_reasons,
        boundaries=boundaries,
    )


def _change_plan_projected(
    config: ProjectConfig, address: Address, *, reverse: bool, transitive: bool
) -> ChangePlan | None:
    """Resolve using only the shards the query touches, or raise/return None."""

    accessor, reason = open_targeted_projection(config)
    if accessor is None:
        raise ProjectionUnavailable(reason)
    if address.document_id not in accessor.manifest.get("documents", {}):
        return None
    if address.anchor is not None:
        document_shard = accessor.document(address.document_id)
        if document_shard is None:
            raise ProjectionUnavailable(f"document shard unavailable for {address.document_id}")
        if address.anchor not in document_shard.get("sections", {}):
            return None
    observed_boundaries: dict[tuple[str, str, str], Boundary] = {}

    def _forward(current: Address) -> tuple[GraphEdge, ...]:
        edges = targeted_forward_edges(accessor, current)
        if edges is None:
            raise ProjectionUnavailable(f"references shard unavailable for {current.text}")
        for item in edges[1]:
            boundary = Boundary(current, item["raw_target"], item["category"], item["reason"])
            observed_boundaries[
                (boundary.source.text, boundary.category, boundary.raw_target)
            ] = boundary
        return edges[0]

    def _reverse(current: Address) -> tuple[GraphEdge, ...]:
        edges = targeted_reverse_edges(accessor, current)
        if edges is None:
            raise ProjectionUnavailable(f"reverse shard unavailable for {current.text}")
        return edges

    forward_reasons = graph_traverse_reasons(
        address, forward=_forward, reverse_edges=_reverse, reverse=False, transitive=transitive
    )
    reverse_reasons = (
        graph_traverse_reasons(
            address, forward=_forward, reverse_edges=_reverse, reverse=True, transitive=transitive
        )
        if reverse
        else ()
    )
    boundaries = tuple(observed_boundaries.values())
    return build_change_plan(
        address,
        reverse=reverse,
        transitive=transitive,
        forward_reasons=forward_reasons,
        reverse_reasons=reverse_reasons,
        boundaries=boundaries,
    )


def change_plan(
    project_root: Path,
    raw_address: str,
    *,
    reverse: bool = False,
    transitive: bool = False,
    json_output: bool = False,
) -> int:
    try:
        config = load_config(project_root)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    try:
        address = parse_address(raw_address)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    diagnostic: str | None = None
    plan: ChangePlan | None = None
    try:
        plan = _change_plan_projected(config, address, reverse=reverse, transitive=transitive)
    except ProjectionUnavailable as error:
        diagnostic = f"{error}; using direct Markdown"
        try:
            plan = _change_plan_direct(config, address, reverse=reverse, transitive=transitive)
        except _ReferenceGraphInvalid as invalid:
            for issue in invalid.issues:
                print(
                    f"ERROR: {issue.path.as_posix()}: {issue.message}",
                    file=sys.stderr,
                )
            return 1

    if plan is None:
        print(f"ERROR: unknown graph address: {address.text}", file=sys.stderr)
        return 1

    if diagnostic is not None:
        print(f"NOTE: {diagnostic}", file=sys.stderr)

    if json_output:
        _print_json(
            {
                "address": address.text,
                "reverse": reverse,
                "transitive": transitive,
                "items": [_change_plan_item_json(item) for item in plan.items],
                "boundaries": [
                    _references_boundary_json(boundary) for boundary in plan.boundaries
                ],
                "completeness": {
                    "authored": plan.completeness.authored,
                    "observed": plan.completeness.observed,
                    "generated": plan.completeness.generated,
                },
            }
        )
        return 0
    for item in plan.items:
        for row in _change_plan_item_rows(item):
            print(row)
    for boundary in plan.boundaries:
        print(_change_plan_boundary_row(boundary))
    for row in _change_plan_completeness_rows(plan.completeness):
        print(row)
    return 0


@dataclass(frozen=True)
class _MaintenanceOccurrenceResult:
    """One resolved occurrence: excluded evidence, or an eligible current block."""

    document_id: str
    anchor: str
    role: str
    eligible: bool
    disposition: str  # "clean" | "drifted" | "excluded"
    reason: str | None
    section_range: tuple[int, int]
    marker_range: tuple[int, int] | None
    content_range: tuple[int, int] | None
    document_hash: str
    section_hash: str
    block_hash: str | None
    diff: str | None


def _maintenance_find_target(config: ProjectConfig, name: str):
    for target in config.maintenance_targets:
        if target.name == name:
            return target
    return None


def _maintenance_occurrence_row(result: _MaintenanceOccurrenceResult) -> str:
    return "\t".join(
        (
            "occurrence",
            f"{result.document_id}#{result.anchor}",
            result.role,
            result.disposition,
            result.reason or "-",
            f"section={result.section_range[0]}-{result.section_range[1]}",
            (
                f"content={result.content_range[0]}-{result.content_range[1]}"
                if result.content_range is not None
                else "content=-"
            ),
            f"document_hash={result.document_hash}",
            f"section_hash={result.section_hash}",
            f"block_hash={result.block_hash or '-'}",
        )
    )


def _maintenance_occurrence_json(result: _MaintenanceOccurrenceResult) -> dict[str, object]:
    return {
        "address": f"{result.document_id}#{result.anchor}",
        "role": result.role,
        "eligible": result.eligible,
        "disposition": result.disposition,
        "reason": result.reason,
        "section_range": {
            "start_line": result.section_range[0],
            "end_line": result.section_range[1],
        },
        "marker_range": (
            {
                "start_line": result.marker_range[0],
                "end_line": result.marker_range[1],
            }
            if result.marker_range is not None
            else None
        ),
        "content_range": (
            {
                "start_line": result.content_range[0],
                "end_line": result.content_range[1],
            }
            if result.content_range is not None
            else None
        ),
        "document_hash": result.document_hash,
        "section_hash": result.section_hash,
        "block_hash": result.block_hash,
        "diff": result.diff,
    }


def _utc_second() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _maintenance_journal_root(config: ProjectConfig) -> Path:
    local = config.project_root / ".docsystem" / "journal"
    documentation_root = config.documentation_root.resolve()
    if not local.resolve(strict=False).is_relative_to(documentation_root):
        return local
    state_home = Path(
        os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")
    )
    identity = hashlib.sha256(str(config.project_root.resolve()).encode()).hexdigest()[:16]
    return state_home / "documentation-engine" / "journals" / identity


def _marker_newline(content: str, start_line: int) -> str:
    line = content.splitlines(keepends=True)[start_line - 1]
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\r"):
        return "\r"
    return "\n"


def _maintenance_write_plan(
    config: ProjectConfig,
    target,
    views: _Views,
    source_block: str,
    occurrence_results: list[_MaintenanceOccurrenceResult],
) -> tuple[tuple[FileEdit, ...], tuple[FileGuard, ...]]:
    """Build exact raw-file edits and a canonical-source read guard."""

    source_view = views[target.source_document_id]
    source_relative = source_view.path.as_posix()
    source_path = config.documentation_root / source_view.path
    source_bytes = source_path.read_bytes()
    try:
        source_raw = source_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise JournalError(f"{source_relative}: source is not valid UTF-8") from error
    source_scan = scan_markers(source_raw, target.name)
    source_span, source_issues = resolve_marker(source_scan, SOURCE)
    if source_span is None:
        raise JournalError(f"{source_relative}: {'; '.join(source_issues)}")
    current_source_block = block_text(source_raw, source_span)
    normalized_source_block = current_source_block.replace("\r\n", "\n").replace(
        "\r", "\n"
    )
    if normalized_source_block != source_block:
        raise JournalError("canonical source block changed after preview")

    by_document: dict[str, list[tuple[_MaintenanceOccurrenceResult, object]]] = {}
    for occurrence, result in zip(
        target.occurrences, occurrence_results, strict=True
    ):
        if result.eligible and result.disposition == DRIFTED:
            if occurrence.document_id == target.source_document_id:
                raise JournalError(
                    "canonical source and a drifted occurrence share one document; "
                    "this write shape is not supported"
                )
            by_document.setdefault(occurrence.document_id, []).append(
                (result, occurrence)
            )

    edits: list[FileEdit] = []
    for document_id in sorted(by_document):
        view = views[document_id]
        relative = view.path.as_posix()
        path = config.documentation_root / view.path
        before_bytes = path.read_bytes()
        try:
            before = before_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            raise JournalError(f"{relative}: source is not valid UTF-8") from error
        lines = before.splitlines(keepends=True)
        replacements: list[tuple[int, int, list[str]]] = []
        ranges: list[LineRange] = []
        for result, _occurrence in by_document[document_id]:
            scan = scan_markers(before, target.name)
            if scan.issues:
                raise JournalError(f"{relative}: {'; '.join(scan.issues)}")
            span = next(
                (
                    item
                    for item in scan.spans
                    if item.kind == MANAGED
                    and result.marker_range == (item.start_line, item.end_line)
                ),
                None,
            )
            if span is None:
                raise JournalError(f"{relative}: managed marker range changed after preview")
            current_block = block_text(before, span).replace("\r\n", "\n").replace(
                "\r", "\n"
            )
            if sha256_text(current_block) != result.block_hash:
                raise JournalError(
                    f"{relative}: managed block changed after preview"
                )
            content_start = span.start_line + 1
            content_end = span.end_line - 1
            if content_end < content_start:
                raise JournalError(
                    f"{relative}: empty managed block cannot be expanded safely"
                )
            newline = _marker_newline(before, span.start_line)
            rendered = source_block.replace("\n", newline)
            replacements.append(
                (
                    span.start_line,
                    span.end_line - 1,
                    rendered.splitlines(keepends=True),
                )
            )
            ranges.append(LineRange(content_start, content_end))

        mechanical_lines = list(lines)
        for start, end, replacement in sorted(replacements, reverse=True):
            mechanical_lines[start:end] = replacement
        edits.append(
            FileEdit(
                path=relative,
                operation="bounded-edit",
                before_sha256=hashlib.sha256(before_bytes).hexdigest(),
                semantic_content=before,
                mechanical_content="".join(mechanical_lines),
                allowed_ranges=tuple(sorted(ranges, key=lambda item: item.start)),
            )
        )

    return (
        tuple(edits),
        (
            FileGuard(
                path=source_relative,
                sha256=hashlib.sha256(source_bytes).hexdigest(),
            ),
        ),
    )


def _maintenance_validation(
    config: ProjectConfig, diagnostics: list[str] | None = None
) -> bool:
    catalog_value = build_catalog(config)
    issues = _with_graph_issues(
        validate_catalog(catalog_value, config), catalog_value, config
    )
    blockers = [issue for issue in issues if issue.severity != "warning"]
    if blockers:
        if diagnostics is not None:
            diagnostics.extend(
                f"{issue.path.as_posix()}: {issue.message}" for issue in blockers
            )
        return False
    build_projection(catalog_value, config)
    return True


def _maintenance_refresh_projection(config: ProjectConfig) -> tuple[bool, str | None]:
    try:
        catalog_value = build_catalog(config)
        write_projection(config, build_projection(catalog_value, config))
    except (OSError, ValueError) as error:
        return False, str(error)
    return True, None


def maintenance(
    project_root: Path,
    target_name: str,
    *,
    check: bool,
    preview: bool,
    write: bool = False,
    json_output: bool = False,
    expected_source_hash: str | None = None,
    workstream_id: str | None = None,
    created_at: str | None = None,
) -> int:
    """Check, preview or journal one declared managed-block target.

    `--check` reports the same deterministic result as `--preview` but exits
    `2` on drift so it composes as a CI gate; `--preview` always exits `0` for
    a valid target. `--write` applies only drifted current blocks through an
    immutable journal and requires source-hash plus workstream evidence.
    Invalid config, an unknown target, unknown or
    ambiguous document/section/marker addresses, and graph-blocking errors
    fail closed with exit `1`, diagnostics on stderr only and no stdout.
    """

    try:
        config = load_config(project_root)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    if sum((check, preview, write)) != 1:
        print("ERROR: choose exactly one of --check, --preview or --write", file=sys.stderr)
        return 1
    if write and expected_source_hash is None:
        print("ERROR: --write requires --expect-source-hash", file=sys.stderr)
        return 1
    if write and workstream_id is None:
        print("ERROR: --write requires --workstream-id", file=sys.stderr)
        return 1
    if write and workstream_id is not None:
        try:
            validate_workstream_id(workstream_id)
        except JournalError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1
    if not write and workstream_id is not None:
        print("ERROR: --workstream-id is only valid with --write", file=sys.stderr)
        return 1

    target = _maintenance_find_target(config, target_name)
    if target is None:
        print(f"ERROR: unknown maintenance target: {target_name}", file=sys.stderr)
        return 1

    involved_ids = {
        target.source_document_id,
        *(occurrence.document_id for occurrence in target.occurrences),
    }
    try:
        views, _, catalog_value = _load_views(config)
        if catalog_value is not None:
            by_id = {
                document.metadata.document_id: document
                for document in catalog_value.documents
                if document.metadata is not None
            }
            for document_id in involved_ids:
                if document_id not in by_id:
                    raise ValueError(f"document ID not found: {document_id}")
            blockers = [
                issue
                for issue in (
                    *validate_membership(catalog_value),
                    *validate_metadata(catalog_value, config),
                    *validate_adoption(catalog_value, config),
                )
                if issue.affects_graph and issue.severity != "warning"
            ]
            for document_id in involved_ids:
                document = by_id[document_id]
                blockers.extend(
                    ValidationIssue(document.path, message)
                    for message in document_section_issues(document, config)
                )
            if blockers:
                for issue in blockers:
                    print(
                        f"ERROR: {issue.path.as_posix()}: {issue.message}",
                        file=sys.stderr,
                    )
                return 1
        for document_id in involved_ids:
            if document_id not in views:
                raise ValueError(f"document ID not found: {document_id}")

        source_view = views[target.source_document_id]
        source_section = next(
            (
                section
                for section in source_view.sections
                if section.anchor == target.source_anchor
            ),
            None,
        )
        if source_section is None:
            raise ValueError(
                f"anchor not found in {target.source_document_id}: "
                f"{target.source_anchor}"
            )
        occurrence_sections: dict[int, MarkdownSection] = {}
        for index, occurrence in enumerate(target.occurrences):
            occurrence_view = views[occurrence.document_id]
            section = next(
                (
                    item
                    for item in occurrence_view.sections
                    if item.anchor == occurrence.anchor
                ),
                None,
            )
            if section is None:
                raise ValueError(
                    f"anchor not found in {occurrence.document_id}: "
                    f"{occurrence.anchor}"
                )
            occurrence_sections[index] = section
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    marker_scans = {
        document_id: scan_markers(view.content, target.name)
        for document_id, view in views.items()
    }
    marker_errors = False
    for document_id, scan in marker_scans.items():
        for issue in scan.issues:
            marker_errors = True
            print(f"ERROR: {document_id}: {issue}", file=sys.stderr)
    if marker_errors:
        return 1
    source_locations = [
        (document_id, span)
        for document_id, scan in marker_scans.items()
        for span in scan.spans
        if span.kind == SOURCE
    ]
    if len(source_locations) != 1:
        message = (
            "no source marker pair found"
            if not source_locations
            else f"duplicate source marker pair ({len(source_locations)} found)"
        )
        print(f"ERROR: {target.source_document_id}: {message}", file=sys.stderr)
        return 1
    if source_locations[0][0] != target.source_document_id:
        print(
            "ERROR: canonical source marker is not in declared source document "
            f"{target.source_document_id}",
            file=sys.stderr,
        )
        return 1

    declared_by_document: dict[str, list[MarkdownSection]] = {}
    for index, occurrence in enumerate(target.occurrences):
        declared_by_document.setdefault(occurrence.document_id, []).append(
            occurrence_sections[index]
        )
    for document_id, scan in marker_scans.items():
        for span in (item for item in scan.spans if item.kind == MANAGED):
            owners = [
                section
                for section in declared_by_document.get(document_id, ())
                if span_within_section(span, section)
            ]
            if len(owners) != 1:
                print(
                    f"ERROR: {document_id}: managed marker lines "
                    f"{span.start_line}-{span.end_line} have {len(owners)} "
                    "declared occurrence owners",
                    file=sys.stderr,
                )
                return 1

    source_scan = marker_scans[target.source_document_id]
    source_span, source_issues = resolve_marker(source_scan, SOURCE)
    if source_span is None:
        for issue in source_issues:
            print(f"ERROR: {target.source_document_id}: {issue}", file=sys.stderr)
        return 1
    if not span_within_section(source_span, source_section):
        print(
            f"ERROR: {target.source_document_id}: source marker for target "
            f"{target.name!r} is outside declared section #{target.source_anchor}",
            file=sys.stderr,
        )
        return 1

    source_block = block_text(source_view.content, source_span)
    source_document_hash = sha256_text(source_view.content)
    source_section_hash = sha256_text(extract_section(source_view.content, source_section))
    source_block_hash = sha256_text(source_block)
    if expected_source_hash is not None:
        if not re.fullmatch(r"[0-9a-f]{64}", expected_source_hash):
            print(
                "ERROR: --expect-source-hash must be a lowercase SHA-256 value",
                file=sys.stderr,
            )
            return 1
        if expected_source_hash != source_block_hash:
            print(
                "ERROR: source block hash changed: expected "
                f"{expected_source_hash}, current {source_block_hash}",
                file=sys.stderr,
            )
            return 1
    source_marker_range = (source_span.start_line, source_span.end_line)
    source_content_range = (source_span.start_line + 1, source_span.end_line - 1)

    occurrence_results: list[_MaintenanceOccurrenceResult] = []
    any_drift = False
    for index, occurrence in enumerate(target.occurrences):
        occurrence_view = views[occurrence.document_id]
        occurrence_section = occurrence_sections[index]
        occurrence_document_hash = sha256_text(occurrence_view.content)
        occurrence_section_hash = sha256_text(
            extract_section(occurrence_view.content, occurrence_section)
        )
        if occurrence.role != CURRENT:
            occurrence_results.append(
                _MaintenanceOccurrenceResult(
                    document_id=occurrence.document_id,
                    anchor=occurrence.anchor,
                    role=occurrence.role,
                    eligible=False,
                    disposition=EXCLUDED,
                    reason=f"role is {occurrence.role!r}, not eligible for preview",
                    section_range=(
                        occurrence_section.start_line,
                        occurrence_section.end_line,
                    ),
                    marker_range=None,
                    content_range=None,
                    document_hash=occurrence_document_hash,
                    section_hash=occurrence_section_hash,
                    block_hash=None,
                    diff=None,
                )
            )
            continue
        occurrence_scan = marker_scans[occurrence.document_id]
        occurrence_span, occurrence_issues = resolve_marker_in_section(
            occurrence_scan, MANAGED, occurrence_section
        )
        if occurrence_span is None:
            for issue in occurrence_issues:
                print(f"ERROR: {occurrence.document_id}: {issue}", file=sys.stderr)
            return 1
        if not span_within_section(occurrence_span, occurrence_section):
            print(
                f"ERROR: {occurrence.document_id}: managed marker for target "
                f"{target.name!r} is outside declared section #{occurrence.anchor}",
                file=sys.stderr,
            )
            return 1
        occurrence_block = block_text(occurrence_view.content, occurrence_span)
        occurrence_block_hash = sha256_text(occurrence_block)
        drifted = occurrence_block != source_block
        diff_text = None
        if drifted:
            any_drift = True
            diff_text = unified_block_diff(
                before=block_lines(occurrence_view.content, occurrence_span),
                after=block_lines(source_view.content, source_span),
                from_label=f"{occurrence.document_id}#{occurrence.anchor}",
                to_label=f"{target.source_document_id}#{target.source_anchor}",
            )
        occurrence_results.append(
            _MaintenanceOccurrenceResult(
                document_id=occurrence.document_id,
                anchor=occurrence.anchor,
                role=occurrence.role,
                eligible=True,
                disposition=DRIFTED if drifted else CLEAN,
                reason=None,
                section_range=(
                    occurrence_section.start_line,
                    occurrence_section.end_line,
                ),
                marker_range=(occurrence_span.start_line, occurrence_span.end_line),
                content_range=(
                    occurrence_span.start_line + 1,
                    occurrence_span.end_line - 1,
                ),
                document_hash=occurrence_document_hash,
                section_hash=occurrence_section_hash,
                block_hash=occurrence_block_hash,
                diff=diff_text,
            )
        )

    status = DRIFTED if any_drift else CLEAN

    source_address = Address(target.source_document_id, target.source_anchor)
    plan_diagnostic: str | None = None
    plan: ChangePlan | None = None
    try:
        if catalog_value is not None:
            plan = _change_plan_direct(
                config, source_address, reverse=True, transitive=False
            )
        else:
            plan = _change_plan_projected(
                config, source_address, reverse=True, transitive=False
            )
    except ProjectionUnavailable as error:
        plan_diagnostic = f"{error}; using direct Markdown"
        try:
            plan = _change_plan_direct(config, source_address, reverse=True, transitive=False)
        except _ReferenceGraphInvalid as invalid:
            for issue in invalid.issues:
                print(
                    f"ERROR: {issue.path.as_posix()}: {issue.message}",
                    file=sys.stderr,
                )
            return 1
    if plan is None:
        print(f"ERROR: unknown graph address: {source_address.text}", file=sys.stderr)
        return 1
    if plan_diagnostic is not None:
        print(f"NOTE: {plan_diagnostic}", file=sys.stderr)

    apply_result = None
    projection_updated = False
    write_validation_issues: list[str] = []
    if write and any_drift:
        try:
            edits, guards = _maintenance_write_plan(
                config, target, views, source_block, occurrence_results
            )
            apply_result = run_bounded_transaction(
                source_root=config.documentation_root,
                journal_root=_maintenance_journal_root(config),
                workstream_id=workstream_id or "",
                created_at=created_at or _utc_second(),
                edits=edits,
                validate=lambda _root: _maintenance_validation(
                    config, write_validation_issues
                ),
                guards=guards,
            )
        except (JournalError, OSError) as error:
            print(f"ERROR: maintenance write failed: {error}", file=sys.stderr)
            return 1
        if apply_result.status != "applied":
            print(
                "ERROR: maintenance write rolled back: "
                f"{apply_result.reason or 'validation failed'}; "
                f"generation={apply_result.generation_id}",
                file=sys.stderr,
            )
            for issue in write_validation_issues:
                print(f"ERROR: post-write validation: {issue}", file=sys.stderr)
            return 1
        status = "applied"
        projection_updated, projection_error = _maintenance_refresh_projection(config)
        if not projection_updated:
            print(
                "WARNING: maintenance source was applied but projection refresh "
                f"failed: {projection_error}; direct Markdown remains authoritative",
                file=sys.stderr,
            )

    mode = "write" if write else ("check" if check else "preview")
    if json_output:
        payload: dict[str, object] = {
            "target": target.name,
            "mode": mode,
            "status": status,
            "source": {
                "address": source_address.text,
                "document_hash": source_document_hash,
                "section_hash": source_section_hash,
                "block_hash": source_block_hash,
                "section_range": {
                    "start_line": source_section.start_line,
                    "end_line": source_section.end_line,
                },
                "marker_range": {
                    "start_line": source_marker_range[0],
                    "end_line": source_marker_range[1],
                },
                "content_range": {
                    "start_line": source_content_range[0],
                    "end_line": source_content_range[1],
                },
            },
            "occurrences": [
                _maintenance_occurrence_json(result) for result in occurrence_results
            ],
            "change_plan": {
                "address": plan.address.text,
                "items": [_change_plan_item_json(item) for item in plan.items],
                "boundaries": [
                    _references_boundary_json(boundary)
                    for boundary in plan.boundaries
                ],
                "completeness": {
                    "authored": plan.completeness.authored,
                    "observed": plan.completeness.observed,
                    "generated": plan.completeness.generated,
                },
            },
        }
        if write:
            payload["write"] = (
                {
                    "generation": apply_result.generation_id,
                    "status": apply_result.status,
                    "changed_paths": list(apply_result.changed_paths),
                    "manifest_hash": apply_result.manifest_sha256,
                    "projection_updated": projection_updated,
                }
                if apply_result is not None
                else {
                    "generation": None,
                    "status": "not-needed",
                    "changed_paths": [],
                    "manifest_hash": None,
                    "projection_updated": False,
                }
            )
        _print_json(
            payload
        )
    else:
        print(f"target\t{target.name}\tstatus\t{status}")
        print(
            "source\t"
            f"{source_address.text}\t"
            f"document_hash={source_document_hash},"
            f"section_hash={source_section_hash},"
            f"block_hash={source_block_hash},"
            f"section={source_section.start_line}-{source_section.end_line},"
            f"content={source_content_range[0]}-{source_content_range[1]}"
        )
        for result in occurrence_results:
            print(_maintenance_occurrence_row(result))
        for item in plan.items:
            for row in _change_plan_item_rows(item):
                print(f"changeplan\t{row}")
        for boundary in plan.boundaries:
            print(f"changeplan\t{_change_plan_boundary_row(boundary)}")
        for row in _change_plan_completeness_rows(plan.completeness):
            print(f"changeplan\t{row}")
        if write:
            if apply_result is None:
                print("write\tnot-needed\tchanged_paths=-")
            else:
                print(
                    f"write\t{apply_result.status}\t"
                    f"generation={apply_result.generation_id}\t"
                    f"changed_paths={','.join(apply_result.changed_paths)}\t"
                    f"manifest_hash={apply_result.manifest_sha256}\t"
                    f"projection_updated={str(projection_updated).lower()}"
                )
        for result in occurrence_results:
            if result.diff:
                print()
                print(f"## diff {result.document_id}#{result.anchor}")
                print()
                sys.stdout.write(result.diff)

    if check:
        return 2 if status == DRIFTED else 0
    return 0


def maintenance_recover(
    project_root: Path,
    generation_id: str,
    *,
    json_output: bool = False,
    recovered_at: str | None = None,
) -> int:
    """Restore one verified maintenance generation without overwriting newer work."""

    try:
        config = load_config(project_root)
        result = recover_generation(
            source_root=config.documentation_root,
            journal_root=_maintenance_journal_root(config),
            generation_id=generation_id,
            recovered_at=recovered_at or _utc_second(),
        )
    except (ValueError, JournalError, OSError) as error:
        print(f"ERROR: maintenance recovery failed: {error}", file=sys.stderr)
        return 1
    if result.status == "refused":
        print(
            f"ERROR: maintenance recovery refused: {result.reason}",
            file=sys.stderr,
        )
        return 1
    recovery_validation_issues: list[str] = []
    if not _maintenance_validation(config, recovery_validation_issues):
        print(
            "ERROR: maintenance recovery restored source but project validation failed",
            file=sys.stderr,
        )
        for issue in recovery_validation_issues:
            print(f"ERROR: post-recovery validation: {issue}", file=sys.stderr)
        return 1
    projection_updated, projection_error = _maintenance_refresh_projection(config)
    if not projection_updated:
        print(
            "WARNING: recovery succeeded but projection refresh failed: "
            f"{projection_error}; direct Markdown remains authoritative",
            file=sys.stderr,
        )
    if json_output:
        _print_json(
            {
                "generation": result.generation_id,
                "status": result.status,
                "restored_paths": list(result.restored_paths),
                "recovery_record": result.recovery_record,
                "projection_updated": projection_updated,
            }
        )
    else:
        print(
            f"recovery\t{result.status}\tgeneration={result.generation_id}\t"
            f"restored_paths={','.join(result.restored_paths) or '-'}\t"
            f"record={result.recovery_record or '-'}"
            f"\tprojection_updated={str(projection_updated).lower()}"
        )
    return 0


def show_config(project_root: Path) -> int:
    try:
        config = load_config(project_root)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"documentation_root={config.documentation_root}")
    print(f"language={config.language}")
    for role, path in sorted(config.areas.items()):
        print(f"area.{role}={path}")
    for role, prefix in sorted(config.identifiers.items()):
        print(f"identifier.{role}={prefix}")
    for pattern in config.catalog_exclusions:
        print(f"catalog.exclude={pattern}")
    for anchor in config.navigation_extend_through:
        print(f"navigation.extend_through={anchor}")
    print(f"relations.legacy_paths={config.legacy_relation_mode}")
    for document_type in config.snapshot_document_types:
        print(f"relations.snapshot_type={document_type}")
    for rule in config.snapshot_rules:
        print(
            "relations.snapshot_rule="
            f"source_type:{rule.source_type or '-'},"
            f"source_status:{rule.source_status or '-'}"
        )
    if config.delivery_policy is not None:
        policy = config.delivery_policy
        print(
            "traceability="
            f"field:{policy.metadata_field},"
            f"types:{','.join(policy.document_types)},"
            f"evidence_role:{policy.evidence_role},"
            f"terminal_statuses:{','.join(policy.terminal_statuses)}"
        )
    graph_health_values = (
        ("hub_in_degree", config.graph_health_policy.hub_in_degree),
        ("hub_out_degree", config.graph_health_policy.hub_out_degree),
        ("boundary_count", config.graph_health_policy.boundary_count),
        ("stale_pin_count", config.graph_health_policy.stale_pin_count),
        ("max_weak_components", config.graph_health_policy.max_weak_components),
    )
    for name, value in graph_health_values:
        if value is not None:
            print(f"graph_health.{name}={value}")
    print(
        "graph_health.required_metadata="
        + (",".join(config.graph_health_policy.required_metadata) or "-")
    )
    print(
        "graph_health.report_orphans="
        + str(config.graph_health_policy.report_orphans).lower()
    )
    for view in config.context_views:
        print(
            f"context.view.{view.name}=tier:{view.tier},delivery:{view.delivery},"
            f"direction:{view.direction},depth:{view.depth},"
            f"relations:{','.join(view.relations) or '-'},layers:{','.join(view.layers)}"
        )
    for criterion in config.workstream_criteria:
        print(
            f"workstreams.criterion.{criterion.reference}="
            f"sections:{','.join(criterion.required_sections) or '-'},"
            f"evidence:{','.join(criterion.required_evidence)},"
            f"max_attempts:{criterion.max_attempts},"
            f"fallback:{criterion.safe_fallback}"
        )
    for criterion in config.intake_criteria:
        print(
            f"intake.criterion.{criterion.reference}="
            f"decisions:{','.join(criterion.allowed_decisions)},"
            f"max_candidates:{criterion.max_candidates},"
            f"fallback:{criterion.safe_fallback},"
            f"draft:{criterion.draft.area}/{criterion.draft.document_type}/"
            f"{criterion.draft.identifier}/width:{criterion.draft.width},"
            f"workstream:{criterion.workstream.area}/"
            f"{criterion.workstream.document_type}/"
            f"{criterion.workstream.identifier}/width:"
            f"{criterion.workstream.width}"
        )
    for criterion in config.admission_criteria:
        print(
            f"admission.criterion.{criterion.reference}="
            f"autonomy:{criterion.max_autonomy},"
            f"actions:{','.join(criterion.allowed_actions)},"
            f"authorizations:{','.join(criterion.required_authorizations) or '-'},"
            f"verification:{','.join(criterion.allowed_verification)},"
            f"risk:{criterion.max_risk},targets:{criterion.max_targets},"
            f"sections:{','.join(criterion.required_sections)},"
            f"fallback:{criterion.safe_fallback}"
        )
    print(f"projection.format={config.projection_format}")
    print(f"projection.keep_generations={config.keep_generations}")
    return 0


def _agent_instructions_text(selection: _Selection, config: ProjectConfig) -> str:
    doc_root = config.documentation_root.relative_to(config.project_root).as_posix()
    out: list[str] = []
    out.append("## Documentation with Documentation Engine")
    out.append("")
    out.append(
        "This project uses `docsystem` for structured Markdown documentation "
        f"rooted at `{doc_root}` (language: {config.language})."
    )
    out.append("")
    out.append("Configured areas and identifier namespaces:")
    out.append("")
    for role, path in sorted(config.areas.items()):
        out.append(f"- {role} -> {path.as_posix()}")
    for role, prefix in sorted(config.identifiers.items()):
        out.append(f"- {prefix} ({role})")
    if config.context_views:
        out.append("")
        out.append("Configured purpose context views:")
        out.append("")
        for view in config.context_views:
            out.append(
                f"- {view.name}: tier {view.tier}, {view.delivery}, "
                f"{view.direction}, depth {view.depth}, authored relations "
                f"{','.join(view.relations) or 'none'}"
            )
    if config.workstream_criteria:
        out.append("")
        out.append("Configured workstream completion criteria:")
        out.append("")
        for criterion in config.workstream_criteria:
            out.append(
                f"- {criterion.reference}: max {criterion.max_attempts} attempt(s), "
                f"fallback {criterion.safe_fallback}, evidence "
                f"{','.join(criterion.required_evidence)}"
            )
    if config.intake_criteria:
        out.append("")
        out.append("Configured semantic intake criteria:")
        out.append("")
        for criterion in config.intake_criteria:
            out.append(
                f"- {criterion.reference}: "
                f"{','.join(criterion.allowed_decisions)}, max "
                f"{criterion.max_candidates} candidate(s), fallback "
                f"{criterion.safe_fallback}"
            )
    if config.admission_criteria:
        out.append("")
        out.append("Configured execution admission criteria:")
        out.append("")
        for criterion in config.admission_criteria:
            out.append(
                f"- {criterion.reference}: max {criterion.max_autonomy}, "
                f"risk {criterion.max_risk}, actions "
                f"{','.join(criterion.allowed_actions)}, fallback "
                f"{criterion.safe_fallback}"
            )
    if config.delivery_policy is not None:
        policy = config.delivery_policy
        out.append("")
        out.append("Configured delivery traceability:")
        out.append("")
        out.append(
            f"- field {policy.metadata_field}; document types "
            f"{','.join(policy.document_types)}; evidence role "
            f"{policy.evidence_role}; terminal statuses "
            f"{','.join(policy.terminal_statuses)}"
        )
    out.append("")
    out.append("Agent rules:")
    out.append("")
    out.append(
        "- Always pass the project root explicitly; do not rely on the "
        "current working directory matching the intended project."
    )
    out.append(
        "- Start read-only with `docsystem readiness "
        f"{selection.selector} --json` and follow its `next_command` field."
    )
    out.append(
        "- Prefer `--json` on commands that support it instead of parsing "
        "human-readable text output."
    )
    out.append(
        "- Without a configured view, expand context with `--depth`, `--include` "
        "or `--include-related` instead of assuming an omitted document or "
        "section is irrelevant."
    )
    out.append(
        "- Prefer `docsystem context ID PROJECT --compact --json` when fetching "
        "content: it emits each overlapping source range once while preserving "
        "every stable address, inclusion reason and omission in the manifest."
    )
    out.append(
        "- Use `docsystem graph-health PROJECT --json` for broad planning or "
        "graph diagnosis, not as mandatory overhead for every edit; metrics are "
        "facts and configured signals remain advisory."
    )
    if config.intake_criteria:
        out.append(
            "- Convert a new human idea into a bounded request, run `docsystem "
            "intake PROJECT --request REQUEST --json`, and follow only its "
            "explicit decision; a blocked result requires owner input."
        )
    if config.admission_criteria:
        out.append(
            "- Before implementation, validate the bounded workstream intent "
            "with `docsystem admission ID PROJECT --request REQUEST --json`; "
            "do not execute a blocked intent or treat authorization assertions "
            "as authenticated identity."
        )
        out.append(
            "- Immediately before an external executor acts, build the "
            "immutable packet with `docsystem execution-handoff ID PROJECT "
            "--admission REQUEST --json`, save that output as `PACKET`, then "
            "give the executor the exact "
            "`docsystem execution-handoff ID PROJECT --admission REQUEST "
            "--verify PACKET --json` re-check to run first; a failed or "
            "non-zero verification stops the executor before any edit, and "
            "neither the admission nor the packet is itself a permission grant."
        )
        out.append(
            "- When the packet carries `source_scope`, require a machine-readable "
            "`RESULT` from the runtime after execution and run `docsystem "
            "execution-result ID PROJECT --packet PACKET --result RESULT --json`; "
            "treat it as caller-declared inventory, stop on omitted scoped or "
            "out-of-scope paths, and do not replace an authoritative host diff "
            "with worker prose."
        )
    if config.workstream_criteria:
        out.append(
            "- For governed workstreams, inspect `docsystem criteria PROJECT "
            "--json`, validate `RECORD` with `docsystem workstream ID PROJECT "
            "--record RECORD --json`, require `ready_to_finish` to be true, "
            "then run `docsystem finish ID PROJECT --workstream-record RECORD "
            "--json`; never claim completion from an in-progress record."
        )
    if config.context_views:
        out.append(
            "- Prefer the lowest configured `docsystem context --view NAME` tier "
            "that fits the task, inspect every `view_omissions` row, and expand "
            "on demand rather than treating a view as an access boundary."
        )
    if config.delivery_policy is not None:
        out.append(
            "- Before changing an exact source contract, inspect bounded delivery "
            f"ownership with `docsystem delivery-map {selection.selector} "
            "--contract ID#anchor --json`; `unowned_contracts` means no configured "
            "owner was found, not permission to infer or create one."
        )
    out.append(
        "- If an additional read materially changes the plan, scope, decision, "
        "verification or result, finish the task and draft a sanitized "
        "`docsystem report context-gap`, then preserve its classification and "
        "report state in `docsystem finish`; ordinary precautionary expansion "
        "is not a product issue."
    )
    out.append(
        "- Never run `docsystem init`, `docsystem migrate --apply`, "
        "`docsystem index --write`, `docsystem maintenance --write` or "
        "`docsystem maintenance-recover` without explicit approval."
    )
    out.append(
        "- Before mutating ignored/local-only documentation state, follow "
        "this project's local backup policy if one exists."
    )
    out.append("")
    out.append(
        "See `docs/agent-contract.md` in the Documentation Engine repository "
        "for the full agent contract."
    )
    return "\n".join(out) + "\n"


def agent_instructions(
    project_root: Path,
    *,
    json_output: bool = False,
    selection: _Selection | None = None,
) -> int:
    """Print a deterministic agent-rules snippet for AGENTS.md/CLAUDE.md.

    Read-only: the snippet is derived from the project's `.docsystem.toml`
    plus the engine's stable agent contract, never from parsing
    `docs/setup-guide.md`, so a pasted snippet cannot silently drift from the
    project's actually configured areas and identifiers. Works even when the
    documentation root itself is missing, since only configuration is read.

    Under a selected source the snippet addresses the project as
    `--source NAME`, so a snippet pasted into a committed AGENTS.md never
    carries the private workspace path it was generated from.
    """
    try:
        config = load_config(project_root)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    text = _agent_instructions_text(selection or _Selection(project_root), config)
    if json_output:
        _print_json({"text": text})
        return 0
    sys.stdout.write(text)
    return 0


def _add_source_options(
    command_parser: argparse.ArgumentParser, *, short_flag: bool = True
) -> None:
    """Add the workspace selection flags shared by every project command.

    `--workspace-source` is the spelling every project command accepts.
    `--source` is the short alias for the same option, added everywhere it is
    free; `report draft` already owns `--source` for the reporting host, whose
    existing meaning is preserved rather than overloaded.
    """

    names = ["--source", "--workspace-source"] if short_flag else ["--workspace-source"]
    command_parser.add_argument(
        *names,
        dest="workspace_source",
        metavar="NAME",
        help=(
            "Run against the named source from the local workspace registry "
            "instead of the positional project root."
        ),
    )
    command_parser.add_argument(
        "--workspace",
        metavar="PATH",
        type=Path,
        help=(
            "Workspace root holding workspace.toml. Overrides "
            "DOCSYSTEM_WORKSPACE and .docsystem.local.toml."
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command, help_text in (
        ("init", "Create configuration and the documentation root."),
        ("doctor", "Validate project configuration and filesystem state."),
        ("show-config", "Print the normalized project configuration."),
        ("catalog", "List Markdown files and their logical area roles."),
        ("validate", "Validate hierarchical Markdown navigation."),
    ):
        command_parser = subparsers.add_parser(command, help=help_text)
        command_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())
        if command != "init":
            _add_source_options(command_parser)
        if command == "catalog":
            command_parser.add_argument(
                "--explain",
                action="store_true",
                help="Classify every Markdown source under the documentation root.",
            )
            command_parser.add_argument(
                "--json",
                action="store_true",
                dest="json_output",
                help="Print a deterministic JSON object instead of tab-separated text.",
            )
        if command in {"doctor", "validate"}:
            command_parser.add_argument(
                "--verbose-adoption",
                action="store_true",
                help="Print every legacy adoption warning instead of summaries.",
            )

    read_parser = subparsers.add_parser(
        "read", help="Read a Markdown document or section by stable ID."
    )
    read_parser.add_argument("document_id")
    read_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())
    selection = read_parser.add_mutually_exclusive_group()
    selection.add_argument("--anchor")
    selection.add_argument("--navigation", action="store_true")
    selection.add_argument("--list", action="store_true", dest="list_sections")

    dependencies_parser = subparsers.add_parser(
        "dependencies", help="List forward or reverse semantic dependencies."
    )
    dependencies_parser.add_argument("document_id")
    dependencies_parser.add_argument(
        "project", nargs="?", type=Path, default=Path.cwd()
    )
    dependencies_parser.add_argument("--reverse", action="store_true")

    references_parser = subparsers.add_parser(
        "references",
        help="Read-only forward/reverse section-and-reference graph inspection.",
    )
    references_parser.add_argument("address", metavar="ID[#anchor]")
    references_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())
    references_parser.add_argument("--reverse", action="store_true")
    references_parser.add_argument("--transitive", action="store_true")
    references_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print a deterministic JSON object instead of tab-separated text.",
    )

    change_plan_parser = subparsers.add_parser(
        "change-plan",
        help=(
            "Read-only explainable change plan: read/review items, "
            "aggregated reasons and boundaries for a document or section."
        ),
    )
    change_plan_parser.add_argument("address", metavar="ID[#anchor]")
    change_plan_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())
    change_plan_parser.add_argument("--reverse", action="store_true")
    change_plan_parser.add_argument("--transitive", action="store_true")
    change_plan_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print a deterministic JSON object instead of tab-separated text.",
    )

    maintenance_parser = subparsers.add_parser(
        "maintenance",
        help="Read-only managed maintenance drift check and preview diff.",
    )
    maintenance_parser.add_argument("target", metavar="TARGET")
    maintenance_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())
    maintenance_mode = maintenance_parser.add_mutually_exclusive_group(required=True)
    maintenance_mode.add_argument("--check", action="store_true")
    maintenance_mode.add_argument("--preview", action="store_true")
    maintenance_mode.add_argument("--write", action="store_true")
    maintenance_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print a deterministic JSON object instead of tab-separated text.",
    )
    maintenance_parser.add_argument(
        "--expect-source-hash",
        metavar="SHA256",
        help="Fail closed unless the canonical source block still has this hash.",
    )
    maintenance_parser.add_argument(
        "--workstream-id",
        metavar="ID",
        help="Required audit identity for --write (for example WS-001).",
    )

    maintenance_recover_parser = subparsers.add_parser(
        "maintenance-recover",
        help="Restore one verified maintenance journal generation.",
    )
    maintenance_recover_parser.add_argument("generation")
    maintenance_recover_parser.add_argument(
        "project", nargs="?", type=Path, default=Path.cwd()
    )
    maintenance_recover_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print a deterministic JSON object instead of tab-separated text.",
    )

    context_parser = subparsers.add_parser("context", help="Build an inspectable context packet.")
    context_parser.add_argument("document_id")
    context_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())
    context_parser.add_argument("--anchor")
    context_parser.add_argument("--depth", type=int, choices=range(0, 6))
    context_parser.add_argument("--include-related", action="store_true", default=None)
    context_parser.add_argument("--view", dest="view_name")
    context_parser.add_argument("--include", action="append", default=[])
    context_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print a deterministic JSON object instead of the Markdown packet.",
    )
    context_parser.add_argument(
        "--outline",
        action="store_true",
        default=None,
        help=(
            "Print section size maps instead of content; combine with --json "
            "for the structured form. Cannot combine with --anchor or --include."
        ),
    )
    context_parser.add_argument(
        "--compact",
        action="store_true",
        help=(
            "Emit merged non-overlapping source fragments plus an address/reason "
            "manifest. Cannot combine with outline delivery."
        ),
    )
    context_parser.add_argument(
        "--assume-known",
        action="append",
        default=[],
        dest="assume_known",
        metavar="ID@REV",
        help=(
            "Declare a document already held at revision REV (repeatable). When "
            "it enters the packet at that exact revision its content is omitted; "
            "a revision mismatch includes content with a diagnostics note."
        ),
    )
    context_parser.add_argument(
        "--since",
        metavar="GENERATION",
        help=(
            "Emit a delta packet against a retained projection generation "
            "(full hash or unique >=12-char prefix): unchanged documents are "
            "omitted, changed documents add their changed sections."
        ),
    )

    impact_parser = subparsers.add_parser("impact", help="Show reverse metadata impact.")
    impact_parser.add_argument("document_id")
    impact_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())

    graph_health_parser = subparsers.add_parser(
        "graph-health",
        help="Report deterministic graph metrics and configured advisory signals.",
    )
    graph_health_parser.add_argument(
        "project", nargs="?", type=Path, default=Path.cwd()
    )
    graph_health_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print a deterministic JSON object instead of Markdown.",
    )

    metadata_inventory_parser = subparsers.add_parser(
        "metadata-inventory",
        help="Inspect metadata coverage and body-free document graph facts.",
    )
    metadata_inventory_parser.add_argument(
        "project", nargs="?", type=Path, default=Path.cwd()
    )
    metadata_inventory_parser.add_argument(
        "--field",
        dest="field_name",
        metavar="NAME",
        help="Restrict the field summary to one observed metadata field.",
    )
    metadata_inventory_parser.add_argument(
        "--values",
        action="store_true",
        help="Include values for the explicitly selected --field.",
    )
    metadata_inventory_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print a deterministic JSON object instead of tab-separated text.",
    )

    profile_check_parser = subparsers.add_parser(
        "profile-check",
        help="Validate documents against project-authored profile policy.",
    )
    profile_check_parser.add_argument(
        "project", nargs="?", type=Path, default=Path.cwd()
    )
    profile_check_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print a deterministic JSON object instead of tab-separated text.",
    )

    delivery_map_parser = subparsers.add_parser(
        "delivery-map",
        help="Map exact source contracts to authored delivery evidence.",
    )
    delivery_map_parser.add_argument(
        "project", nargs="?", type=Path, default=Path.cwd()
    )
    delivery_map_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print a deterministic JSON object instead of tab-separated text.",
    )
    delivery_map_parser.add_argument(
        "--contract",
        action="append",
        dest="contracts",
        default=[],
        metavar="ID#ANCHOR",
        help="Return mappings only for this exact source contract; repeatable.",
    )

    criteria_parser = subparsers.add_parser(
        "criteria",
        help="List versioned workstream, intake and admission criteria.",
    )
    criteria_parser.add_argument(
        "project", nargs="?", type=Path, default=Path.cwd()
    )
    criteria_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print a deterministic JSON object instead of tab-separated text.",
    )

    workstream_parser = subparsers.add_parser(
        "workstream",
        help="Validate a bounded workstream lifecycle and evidence record.",
    )
    workstream_parser.add_argument("document_id")
    workstream_parser.add_argument(
        "project", nargs="?", type=Path, default=Path.cwd()
    )
    workstream_parser.add_argument(
        "--record",
        required=True,
        type=Path,
        dest="record_path",
        help="Read-only path to the bounded JSON lifecycle/evidence record.",
    )
    workstream_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print a deterministic JSON object instead of Markdown.",
    )

    intake_parser = subparsers.add_parser(
        "intake",
        help="Evaluate a bounded semantic idea request without writing source.",
    )
    intake_parser.add_argument(
        "project", nargs="?", type=Path, default=Path.cwd()
    )
    intake_parser.add_argument(
        "--request",
        required=True,
        type=Path,
        dest="request_path",
        help="Read-only path to the bounded JSON idea-intake request.",
    )
    intake_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print a deterministic JSON decision instead of tab-separated text.",
    )

    admission_parser = subparsers.add_parser(
        "admission",
        help="Evaluate a bounded A0-A2 workstream intent without executing it.",
    )
    admission_parser.add_argument("document_id")
    admission_parser.add_argument(
        "project", nargs="?", type=Path, default=Path.cwd()
    )
    admission_parser.add_argument(
        "--request",
        required=True,
        type=Path,
        dest="request_path",
        help="Read-only path to the bounded JSON execution intent.",
    )
    admission_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print a deterministic JSON decision instead of tab-separated text.",
    )

    execution_handoff_parser = subparsers.add_parser(
        "execution-handoff",
        help="Build or verify an immutable provider-neutral execution packet.",
    )
    execution_handoff_parser.add_argument("document_id")
    execution_handoff_parser.add_argument(
        "project", nargs="?", type=Path, default=Path.cwd()
    )
    execution_handoff_parser.add_argument(
        "--admission",
        required=True,
        type=Path,
        dest="admission_path",
        help="Read-only path to the bounded admitted intent.",
    )
    execution_handoff_parser.add_argument(
        "--verify",
        type=Path,
        dest="verify_path",
        help="Verify a previously captured packet against current Markdown.",
    )
    execution_handoff_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print the packet or verification result as deterministic JSON.",
    )

    execution_result_parser = subparsers.add_parser(
        "execution-result",
        help="Validate structured changed-file evidence against a handoff packet.",
    )
    execution_result_parser.add_argument("document_id")
    execution_result_parser.add_argument(
        "project", nargs="?", type=Path, default=Path.cwd()
    )
    execution_result_parser.add_argument("--packet", required=True, type=Path)
    execution_result_parser.add_argument("--result", required=True, type=Path)
    execution_result_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print deterministic JSON evidence instead of tab-separated text.",
    )

    migration_report_parser = subparsers.add_parser(
        "migration-report", help="Report legacy relation adoption mappings."
    )
    migration_report_parser.add_argument(
        "project", nargs="?", type=Path, default=Path.cwd()
    )
    migration_report_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print a deterministic JSON object instead of tab-separated text.",
    )

    migrate_parser = subparsers.add_parser(
        "migrate",
        help="Preview (default) or apply resolvable legacy relation migrations.",
    )
    migrate_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())
    migrate_parser.add_argument(
        "--apply",
        action="store_true",
        help="Write resolved values into Markdown source. Default is a "
        "non-mutating preview.",
    )

    readiness_parser = subparsers.add_parser(
        "readiness", help="Report adoption readiness without writing source."
    )
    readiness_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())
    readiness_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print a deterministic JSON object instead of a text summary.",
    )

    index_parser = subparsers.add_parser("index", help="Check or write the projection.")
    index_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())
    index_parser.add_argument("--write", action="store_true")

    changes_parser = subparsers.add_parser("changes", help="Show changes since projection.")
    changes_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())
    changes_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print a deterministic JSON object instead of tab-separated text.",
    )

    finish_parser = subparsers.add_parser(
        "finish",
        help="Build a compact handoff packet for returning work to a parent context.",
    )
    finish_parser.add_argument("document_id")
    finish_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())
    finish_parser.add_argument("--depth", type=int, choices=range(0, 6), default=1)
    finish_parser.add_argument("--include-related", action="store_true")
    finish_parser.add_argument(
        "--context-expansion",
        choices=CONTEXT_EXPANSION_STATES,
        default="not-observed",
    )
    finish_parser.add_argument(
        "--context-gap-report",
        choices=CONTEXT_GAP_REPORT_STATES,
        default="not-needed",
    )
    finish_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print a deterministic JSON object instead of the Markdown packet.",
    )
    finish_parser.add_argument(
        "--workstream-record",
        type=Path,
        help=(
            "Require a completed, criteria-verified workstream record and include "
            "its bounded evidence in the handoff."
        ),
    )

    report_parser = subparsers.add_parser(
        "report",
        help="Create privacy-safe adopter report drafts.",
    )
    report_subparsers = report_parser.add_subparsers(
        dest="report_command", required=True
    )
    draft_parser = report_subparsers.add_parser(
        "draft",
        help="Draft a GitHub issue body from compact local diagnostics.",
    )
    draft_parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())
    draft_parser.add_argument("--project-name", required=True)
    draft_parser.add_argument(
        "--type",
        required=True,
        choices=tuple(REPORT_TYPES),
        dest="report_type",
    )
    draft_parser.add_argument(
        "--source",
        required=True,
        choices=REPORT_SOURCES,
    )
    draft_parser.add_argument("--component")
    draft_parser.add_argument("--output", type=Path)

    context_gap_parser = report_subparsers.add_parser(
        "context-gap",
        help="Draft a body-free report for a material unexpected context gap.",
    )
    context_gap_parser.add_argument(
        "project", nargs="?", type=Path, default=Path.cwd()
    )
    context_gap_parser.add_argument("--project-name", required=True)
    context_gap_parser.add_argument(
        "--type",
        required=True,
        choices=tuple(REPORT_TYPES),
        dest="report_type",
    )
    context_gap_parser.add_argument(
        "--source", required=True, choices=REPORT_SOURCES
    )
    context_gap_parser.add_argument(
        "--reason", required=True, choices=CONTEXT_GAP_REASONS
    )
    context_gap_parser.add_argument(
        "--initial", required=True, action="append", metavar="ID[#ANCHOR]"
    )
    context_gap_parser.add_argument(
        "--expanded", required=True, action="append", metavar="ID[#ANCHOR]"
    )
    context_gap_parser.add_argument(
        "--impact",
        required=True,
        action="append",
        choices=CONTEXT_GAP_IMPACTS,
    )
    context_gap_parser.add_argument("--output", type=Path)

    agent_instructions_parser = subparsers.add_parser(
        "agent-instructions",
        help="Print a deterministic agent-rules snippet for AGENTS.md/CLAUDE.md.",
    )
    agent_instructions_parser.add_argument(
        "project", nargs="?", type=Path, default=Path.cwd()
    )
    agent_instructions_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print a deterministic JSON object instead of the Markdown snippet.",
    )

    workspace_parser = subparsers.add_parser(
        "workspace",
        help="Inspect the local workspace registry of selectable sources.",
    )
    workspace_subparsers = workspace_parser.add_subparsers(
        dest="workspace_command", required=True
    )
    for workspace_command, workspace_help in (
        ("list", "List registered sources, their visibility and availability."),
        ("doctor", "Report whether every registered source is selectable."),
    ):
        command_parser = workspace_subparsers.add_parser(
            workspace_command, help=workspace_help
        )
        command_parser.add_argument(
            "project", nargs="?", type=Path, default=Path.cwd()
        )
        command_parser.add_argument(
            "--workspace",
            metavar="PATH",
            type=Path,
            help=(
                "Workspace root holding workspace.toml. Overrides "
                "DOCSYSTEM_WORKSPACE and .docsystem.local.toml."
            ),
        )
        command_parser.add_argument(
            "--json",
            action="store_true",
            dest="json_output",
            help="Print a deterministic JSON object instead of tab-separated text.",
        )

    for command_parser in (
        read_parser,
        dependencies_parser,
        references_parser,
        change_plan_parser,
        maintenance_parser,
        maintenance_recover_parser,
        context_parser,
        impact_parser,
        graph_health_parser,
        metadata_inventory_parser,
        profile_check_parser,
        delivery_map_parser,
        criteria_parser,
        workstream_parser,
        intake_parser,
        admission_parser,
        execution_handoff_parser,
        execution_result_parser,
        migration_report_parser,
        migrate_parser,
        readiness_parser,
        index_parser,
        changes_parser,
        finish_parser,
        agent_instructions_parser,
    ):
        _add_source_options(command_parser)
    _add_source_options(draft_parser, short_flag=False)
    _add_source_options(context_gap_parser, short_flag=False)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "workspace":
        if args.workspace_command == "list":
            return workspace_list(
                args.project,
                workspace_option=args.workspace,
                json_output=args.json_output,
            )
        if args.workspace_command == "doctor":
            return workspace_doctor(
                args.project,
                workspace_option=args.workspace,
                json_output=args.json_output,
            )
        raise AssertionError(f"unknown workspace command: {args.workspace_command}")

    selection = _resolve_selection(args)
    if selection is None:
        return 1
    project = selection.project_root

    if args.command == "read":
        return read_document(
            project,
            args.document_id,
            anchor=args.anchor,
            navigation=args.navigation,
            list_sections=args.list_sections,
        )
    if args.command == "dependencies":
        return dependencies(project, args.document_id, reverse=args.reverse)
    if args.command == "references":
        return references(
            project,
            args.address,
            reverse=args.reverse,
            transitive=args.transitive,
            json_output=args.json_output,
        )
    if args.command == "change-plan":
        return change_plan(
            project,
            args.address,
            reverse=args.reverse,
            transitive=args.transitive,
            json_output=args.json_output,
        )
    if args.command == "maintenance":
        return maintenance(
            project,
            args.target,
            check=args.check,
            preview=args.preview,
            write=args.write,
            json_output=args.json_output,
            expected_source_hash=args.expect_source_hash,
            workstream_id=args.workstream_id,
        )
    if args.command == "maintenance-recover":
        return maintenance_recover(
            project,
            args.generation,
            json_output=args.json_output,
        )
    if args.command == "catalog":
        return catalog(project, explain=args.explain, json_output=args.json_output)
    if args.command == "context":
        return context(
            project,
            args.document_id,
            anchor=args.anchor,
            depth=args.depth,
            include_related=args.include_related,
            includes=args.include,
            json_output=args.json_output,
            outline=args.outline,
            assume_known=args.assume_known,
            since=args.since,
            view_name=args.view_name,
            compact=args.compact,
        )
    if args.command == "impact":
        return impact(project, args.document_id)
    if args.command == "graph-health":
        return graph_health(project, json_output=args.json_output)
    if args.command == "metadata-inventory":
        return metadata_inventory(
            project,
            field_name=args.field_name,
            show_values=args.values,
            json_output=args.json_output,
        )
    if args.command == "profile-check":
        return profile_check(project, json_output=args.json_output)
    if args.command == "delivery-map":
        return delivery_map(
            project, contracts=tuple(args.contracts), json_output=args.json_output
        )
    if args.command == "criteria":
        return criteria_registry(project, json_output=args.json_output)
    if args.command == "workstream":
        return workstream_status(
            project,
            args.document_id,
            record_path=args.record_path,
            json_output=args.json_output,
        )
    if args.command == "intake":
        return idea_intake(
            project,
            request_path=args.request_path,
            json_output=args.json_output,
        )
    if args.command == "admission":
        return execution_admission(
            project,
            args.document_id,
            request_path=args.request_path,
            json_output=args.json_output,
        )
    if args.command == "execution-handoff":
        return execution_handoff(
            project,
            args.document_id,
            admission_path=args.admission_path,
            verify_path=args.verify_path,
            json_output=args.json_output,
        )
    if args.command == "execution-result":
        return execution_result(
            project,
            args.document_id,
            packet_path=args.packet,
            result_path=args.result,
            json_output=args.json_output,
        )
    if args.command == "migration-report":
        return migration_report(project, json_output=args.json_output)
    if args.command == "migrate":
        return migrate(project, apply=args.apply)
    if args.command == "readiness":
        return readiness(
            project, json_output=args.json_output, selection=selection
        )
    if args.command == "index":
        return index_projection(project, write=args.write)
    if args.command == "changes":
        return changes(project, json_output=args.json_output)
    if args.command == "finish":
        return finish(
            project,
            args.document_id,
            depth=args.depth,
            include_related=args.include_related,
            json_output=args.json_output,
            context_expansion=args.context_expansion,
            context_gap_report=args.context_gap_report,
            workstream_record=args.workstream_record,
        )
    if args.command == "agent-instructions":
        return agent_instructions(
            project, json_output=args.json_output, selection=selection
        )
    if args.command == "report":
        if args.report_command == "draft":
            return report_draft(
                project,
                project_name=args.project_name,
                report_type=args.report_type,
                source=args.source,
                component=args.component,
                output=args.output,
                selection=selection,
            )
        if args.report_command == "context-gap":
            return context_gap_draft(
                project,
                project_name=args.project_name,
                report_type=args.report_type,
                source=args.source,
                reason=args.reason,
                initial=tuple(args.initial),
                expanded=tuple(args.expanded),
                impacts=tuple(args.impact),
                output=args.output,
                selection=selection,
            )
        raise AssertionError(f"unknown report command: {args.report_command}")
    if args.command == "doctor":
        return doctor(
            project, verbose_adoption=args.verbose_adoption
        )
    if args.command == "validate":
        return validate(
            project, verbose_adoption=args.verbose_adoption
        )
    handlers = {
        "init": initialize,
        "show-config": show_config,
    }
    return handlers[args.command](project)
