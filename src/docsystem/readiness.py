"""Read-only adoption readiness evaluation for existing Markdown projects.

This module never writes to the source tree or the projection cache; it only
re-reads catalog and projection state already produced by `catalog.py` and
`projection.py` and groups it into the categories an adopting project needs to
tell apart before running any mutating command.
"""

from __future__ import annotations

from dataclasses import dataclass

from docsystem.catalog import (
    MarkdownCatalog,
    RelationBoundary,
    RelationMigration,
    ValidationIssue,
    validate_membership,
    validate_metadata,
    validate_reachability,
    validate_sections,
)
from docsystem.config import ProjectConfig
from docsystem.projection import build_projection, projection_status


@dataclass(frozen=True)
class ReadinessReport:
    """A deterministic snapshot of whether a project can adopt the engine."""

    documentation_root_exists: bool
    blocking: tuple[ValidationIssue, ...]
    resolvable_migrations: tuple[RelationMigration, ...]
    boundaries: tuple[RelationBoundary, ...]
    stale_pins: tuple[ValidationIssue, ...]
    projection_present: bool
    projection_current: bool
    projection_reason: str

    @property
    def ready(self) -> bool:
        """Whether the project is free of blocking structural errors."""

        return self.documentation_root_exists and not self.blocking

    @property
    def projection_state(self) -> str:
        if not self.projection_present:
            return "absent"
        if self.projection_current:
            return "current"
        return "stale"

    def next_command(self, project: str) -> str:
        """The single safe next command; never a source-mutating default."""

        if not self.documentation_root_exists:
            return f"docsystem init {project}"
        if self.blocking:
            return f"docsystem doctor {project}"
        if self.resolvable_migrations:
            return f"docsystem migrate {project}"
        if self.projection_state != "current":
            return f"docsystem index {project} --write"
        return f"docsystem context DOCUMENT_ID {project}"


def evaluate_readiness(
    config: ProjectConfig, catalog: MarkdownCatalog
) -> ReadinessReport:
    """Evaluate readiness without writing Markdown, cache or configuration."""

    if not config.documentation_root.is_dir():
        return ReadinessReport(
            documentation_root_exists=False,
            blocking=(),
            resolvable_migrations=(),
            boundaries=(),
            stale_pins=(),
            projection_present=False,
            projection_current=False,
            projection_reason="documentation root does not exist",
        )

    paths = {
        document.metadata.document_id: document.path
        for document in catalog.documents
        if document.metadata is not None
    }
    metadata_issues = validate_metadata(catalog)
    self_reference_errors = tuple(
        ValidationIssue(
            paths[item.source_id],
            f"legacy metadata.{item.relation} value {item.value!r}: {item.reason}",
            affects_graph=True,
        )
        for item in catalog.relation_boundaries
        if item.reason == "self reference"
    )
    blocking = tuple(
        sorted(
            (
                *validate_membership(catalog),
                *(issue for issue in metadata_issues if issue.severity != "warning"),
                *validate_sections(catalog, config),
                *validate_reachability(catalog, config),
                *self_reference_errors,
            ),
            key=lambda issue: (issue.path.as_posix(), issue.severity, issue.message),
        )
    )
    stale_pins = tuple(
        issue
        for issue in metadata_issues
        if issue.severity == "warning" and issue.target_id is not None
    )
    boundaries = tuple(
        item for item in catalog.relation_boundaries if item.reason != "self reference"
    )
    current_projection = build_projection(catalog)
    valid, reason = projection_status(config, current_projection)
    projection_present = reason != "projection absent"
    return ReadinessReport(
        documentation_root_exists=True,
        blocking=blocking,
        resolvable_migrations=catalog.relation_migrations,
        boundaries=boundaries,
        stale_pins=stale_pins,
        projection_present=projection_present,
        projection_current=valid,
        projection_reason=reason,
    )
