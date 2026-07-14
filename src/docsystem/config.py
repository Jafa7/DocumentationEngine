"""Project configuration loading and validation."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from docsystem.metadata import DOCUMENT_ID_PATTERN, PINNED_RELATION, RELATION_FIELDS
from docsystem.sections import is_valid_anchor

CONFIG_FILENAME = ".docsystem.toml"
PREFIX_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{1,15}$")
MAINTENANCE_ROLES = frozenset(
    {"current", "historical", "example", "snapshot", "unmanaged"}
)
MAINTENANCE_TARGET_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
CONTEXT_VIEW_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
PROFILE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
PROFILE_HISTORY_MODES = frozenset({"living", "append-only", "immutable-after-state"})
WORKSTREAM_CRITERION_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
WORKSTREAM_EVIDENCE_FIELDS = frozenset(
    {"changes", "checks", "review", "omissions", "risks", "returns"}
)
INTAKE_DECISIONS = frozenset(
    {"update-existing", "create-draft", "create-workstream"}
)
ADMISSION_ACTION_LEVELS = {
    "inspect": 0,
    "plan": 1,
    "edit-local": 2,
    "run-checks": 2,
}
ADMISSION_AUTONOMY_LEVELS = {"A0": 0, "A1": 1, "A2": 2}
ADMISSION_RISK_LEVELS = {"low": 0, "medium": 1, "high": 2}
ADMISSION_VERIFICATION_LEVELS = frozenset({"structural", "focused", "full"})
CONTEXT_VIEW_RELATIONS = frozenset(
    {"derived_from", "depends_on", "validated_against", "related", "supersedes"}
)
DEFAULT_CONFIG = """\
version = 1

[documentation]
root = "plan"
language = "en"

[areas]
foundation = "foundation"
architecture = "architecture"
decisions = "decisions"
roadmap = "roadmap"
scratch = "scratch"
reviews = "reviews"
experiments = "experiments"
modules = "modules"

[identifiers]
document = "DOC"
decision = "DEC"
roadmap = "RM"

[catalog]
exclude = []

[navigation]
extend_through = []

[relations]
legacy_paths = "strict"
snapshot_types = []
snapshot_rules = []

[graph_health]
required_metadata = []
report_orphans = false

[profiles]

[traceability]

[workstreams]

[intake]

[admission]

[projection]
format = "sharded-json"
keep_generations = 2
"""


@dataclass(frozen=True)
class MaintenanceOccurrence:
    """One declared managed-block occurrence of a maintenance target."""

    document_id: str
    anchor: str
    role: str


@dataclass(frozen=True)
class MaintenanceTarget:
    """A project-defined managed maintenance target.

    `source_document_id`/`source_anchor` name the one canonical section that
    owns the block's authored bytes; `occurrences` are the bounded, declared
    document/section addresses where a bounded managed-block replica may
    exist. Only an `occurrences` entry with `role == "current"` is preview
    eligible; the remaining roles (`historical`, `example`, `snapshot`,
    `unmanaged`) are visible, excluded evidence and never receive a patch.
    """

    name: str
    source_document_id: str
    source_anchor: str
    occurrences: tuple[MaintenanceOccurrence, ...]


@dataclass(frozen=True)
class ContextView:
    """One authored, purpose-specific view over semantic dependency edges."""

    name: str
    tier: int
    delivery: str
    direction: str
    depth: int
    relations: tuple[str, ...]
    layers: tuple[str, ...]


@dataclass(frozen=True)
class SnapshotRule:
    """Project policy that classifies pins owned by matching documents."""

    source_type: str | None = None
    source_status: str | None = None

    def matches(self, document_type: str | None, status: str | None) -> bool:
        return (
            (self.source_type is None or self.source_type == document_type)
            and (self.source_status is None or self.source_status == status)
        )


@dataclass(frozen=True)
class GraphHealthPolicy:
    """Optional project thresholds for advisory graph-health signals."""

    hub_in_degree: int | None = None
    hub_out_degree: int | None = None
    boundary_count: int | None = None
    stale_pin_count: int | None = None
    max_weak_components: int | None = None
    required_metadata: tuple[str, ...] = ()
    report_orphans: bool = False


@dataclass(frozen=True)
class ProfileRole:
    """One semantic role and its project-authored canonical anchor aliases."""

    name: str
    anchors: tuple[str, ...]


@dataclass(frozen=True)
class DocumentProfile:
    """A project-owned validation contract for one or more document types."""

    name: str
    document_types: tuple[str, ...]
    history_mode: str
    required_metadata: tuple[str, ...]
    required_roles: tuple[str, ...]
    roles: tuple[ProfileRole, ...]
    allowed_relations: tuple[str, ...] | None
    allowed_statuses: tuple[str, ...] | None


@dataclass(frozen=True)
class DeliveryPolicy:
    """Project-authored metadata contract for delivery traceability."""

    metadata_field: str
    document_types: tuple[str, ...]
    evidence_role: str
    terminal_statuses: tuple[str, ...]


@dataclass(frozen=True)
class WorkstreamCriterion:
    """One versioned, project-authored completion evidence policy."""

    criterion_id: str
    revision: int
    required_sections: tuple[str, ...]
    required_evidence: tuple[str, ...]
    max_attempts: int
    safe_fallback: str

    @property
    def reference(self) -> str:
        return f"{self.criterion_id}@{self.revision}"


@dataclass(frozen=True)
class IntakePlacement:
    """Project-owned placement for one new-document intake outcome."""

    area: str
    document_type: str
    identifier: str
    width: int


@dataclass(frozen=True)
class IntakeCriterion:
    """One versioned policy for deterministic idea placement."""

    criterion_id: str
    revision: int
    allowed_decisions: tuple[str, ...]
    max_candidates: int
    safe_fallback: str
    draft: IntakePlacement
    workstream: IntakePlacement

    @property
    def reference(self) -> str:
        return f"{self.criterion_id}@{self.revision}"


@dataclass(frozen=True)
class AdmissionCriterion:
    """One versioned policy for bounded A0-A2 execution admission."""

    criterion_id: str
    revision: int
    max_autonomy: str
    allowed_actions: tuple[str, ...]
    required_authorizations: tuple[str, ...]
    allowed_verification: tuple[str, ...]
    max_risk: str
    max_targets: int
    required_sections: tuple[str, ...]
    require_source_scope_for: tuple[str, ...]
    safe_fallback: str

    @property
    def reference(self) -> str:
        return f"{self.criterion_id}@{self.revision}"


@dataclass(frozen=True)
class ProjectConfig:
    project_root: Path
    documentation_root: Path
    language: str
    areas: dict[str, PurePosixPath]
    identifiers: dict[str, str]
    projection_format: str
    keep_generations: int
    catalog_exclusions: tuple[str, ...] = ()
    navigation_extend_through: tuple[str, ...] = ()
    legacy_relation_mode: str = "strict"
    snapshot_document_types: tuple[str, ...] = ()
    snapshot_rules: tuple[SnapshotRule, ...] = ()
    graph_health_policy: GraphHealthPolicy = GraphHealthPolicy()
    document_profiles: tuple[DocumentProfile, ...] = ()
    delivery_policy: DeliveryPolicy | None = None
    maintenance_targets: tuple[MaintenanceTarget, ...] = ()
    context_views: tuple[ContextView, ...] = ()
    workstream_criteria: tuple[WorkstreamCriterion, ...] = ()
    intake_criteria: tuple[IntakeCriterion, ...] = ()
    admission_criteria: tuple[AdmissionCriterion, ...] = ()


def _relative_path(value: object, field: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{field} must be a project-relative path")
    return path


def _catalog_exclusions(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise ValueError("catalog must be a table")
    values = raw.get("exclude", [])
    if not isinstance(values, list):
        raise ValueError("catalog.exclude must be a list")

    patterns: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(values):
        field = f"catalog.exclude[{index}]"
        if not isinstance(value, str) or not value:
            raise ValueError(f"{field} must be a non-empty string")
        if "\\" in value:
            raise ValueError(f"{field} must use POSIX '/' separators")
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(
                f"{field} must be relative to the documentation root"
            )
        normalized = path.as_posix()
        if normalized in seen:
            raise ValueError(
                f"catalog.exclude contains duplicate normalized pattern "
                f"{normalized!r}"
            )
        seen.add(normalized)
        patterns.append(normalized)
    return tuple(patterns)


def _navigation_anchors(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise ValueError("navigation must be a table")
    values = raw.get("extend_through", [])
    if not isinstance(values, list):
        raise ValueError("navigation.extend_through must be a list")

    anchors: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(values):
        field = f"navigation.extend_through[{index}]"
        if not isinstance(value, str) or not value:
            raise ValueError(f"{field} must be a non-empty string")
        if not is_valid_anchor(value):
            raise ValueError(f"{field} has unsupported anchor syntax")
        if value in seen:
            raise ValueError(
                f"navigation.extend_through contains duplicate anchor {value!r}"
            )
        seen.add(value)
        anchors.append(value)
    return tuple(anchors)


def _snapshot_rules(raw: object) -> tuple[SnapshotRule, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("relations.snapshot_rules must be a list of tables")

    rules: list[SnapshotRule] = []
    seen: set[tuple[str | None, str | None]] = set()
    for index, entry in enumerate(raw):
        field = f"relations.snapshot_rules[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{field} must be a table")
        unknown = set(entry) - {"source_type", "source_status"}
        if unknown:
            raise ValueError(
                f"{field} has unknown key(s): {', '.join(sorted(unknown))}"
            )
        source_type = entry.get("source_type")
        source_status = entry.get("source_status")
        if source_type is None and source_status is None:
            raise ValueError(
                f"{field} must define source_type, source_status, or both"
            )
        for name, value in (
            ("source_type", source_type),
            ("source_status", source_status),
        ):
            if value is not None and (
                not isinstance(value, str) or not value.strip()
            ):
                raise ValueError(f"{field}.{name} must be a non-empty string")
        source_type = source_type.strip() if source_type is not None else None
        source_status = (
            source_status.strip() if source_status is not None else None
        )
        key = (source_type, source_status)
        if key in seen:
            raise ValueError(
                f"relations.snapshot_rules contains duplicate rule {key!r}"
            )
        seen.add(key)
        rules.append(SnapshotRule(source_type, source_status))
    return tuple(rules)


def _relations_policy(
    raw: object,
) -> tuple[str, tuple[str, ...], tuple[SnapshotRule, ...]]:
    if raw is None:
        return "strict", (), ()
    if not isinstance(raw, dict):
        raise ValueError("relations must be a table")
    mode = raw.get("legacy_paths", "strict")
    if mode not in {"strict", "resolve-with-warning"}:
        raise ValueError(
            "relations.legacy_paths must be 'strict' or 'resolve-with-warning'"
        )
    types = raw.get("snapshot_types", [])
    if not isinstance(types, list) or any(
        not isinstance(item, str) or not item.strip() for item in types
    ):
        raise ValueError("relations.snapshot_types must be a list of non-empty strings")
    if len(set(types)) != len(types):
        raise ValueError("relations.snapshot_types must be unique")
    return mode, tuple(types), _snapshot_rules(raw.get("snapshot_rules"))


def is_historical_snapshot(
    config: ProjectConfig,
    document_type: str | None,
    status: str | None,
) -> bool:
    """Return whether a pin owned by this document is historical policy."""

    return document_type in config.snapshot_document_types or any(
        rule.matches(document_type, status) for rule in config.snapshot_rules
    )


def _optional_positive_int(
    raw: dict[str, object], key: str, field: str
) -> int | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{field}.{key} must be a positive integer")
    return value


def _graph_health_policy(raw: object) -> GraphHealthPolicy:
    if raw is None:
        return GraphHealthPolicy()
    if not isinstance(raw, dict):
        raise ValueError("graph_health must be a table")
    allowed = {
        "hub_in_degree",
        "hub_out_degree",
        "boundary_count",
        "stale_pin_count",
        "max_weak_components",
        "required_metadata",
        "report_orphans",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(
            "graph_health has unknown key(s): " + ", ".join(sorted(unknown))
        )
    required = raw.get("required_metadata", [])
    if not isinstance(required, list) or any(
        not isinstance(item, str) or item not in {"type", "status"}
        for item in required
    ):
        raise ValueError(
            "graph_health.required_metadata may contain only 'type' and 'status'"
        )
    if len(set(required)) != len(required):
        raise ValueError("graph_health.required_metadata must be unique")
    report_orphans = raw.get("report_orphans", False)
    if not isinstance(report_orphans, bool):
        raise ValueError("graph_health.report_orphans must be a boolean")
    return GraphHealthPolicy(
        hub_in_degree=_optional_positive_int(raw, "hub_in_degree", "graph_health"),
        hub_out_degree=_optional_positive_int(
            raw, "hub_out_degree", "graph_health"
        ),
        boundary_count=_optional_positive_int(raw, "boundary_count", "graph_health"),
        stale_pin_count=_optional_positive_int(
            raw, "stale_pin_count", "graph_health"
        ),
        max_weak_components=_optional_positive_int(
            raw, "max_weak_components", "graph_health"
        ),
        required_metadata=tuple(required),
        report_orphans=report_orphans,
    )


def _profile_string_list(
    value: object,
    field: str,
    *,
    required: bool = False,
    pattern: re.Pattern[str] | None = None,
) -> tuple[str, ...]:
    if not isinstance(value, list) or (required and not value):
        suffix = "non-empty " if required else ""
        raise ValueError(f"{field} must be a {suffix}list")
    if any(
        not isinstance(item, str)
        or not item
        or (pattern is not None and pattern.fullmatch(item) is None)
        for item in value
    ):
        raise ValueError(f"{field} contains an invalid value")
    if len(set(value)) != len(value):
        raise ValueError(f"{field} must be unique")
    return tuple(value)


def _document_profiles(raw: object) -> tuple[DocumentProfile, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise ValueError("profiles must be a table")
    profiles: list[DocumentProfile] = []
    type_owners: dict[str, str] = {}
    relation_names = frozenset((*RELATION_FIELDS, PINNED_RELATION))
    allowed_keys = {
        "document_types",
        "history_mode",
        "required_metadata",
        "required_roles",
        "roles",
        "allowed_relations",
        "allowed_statuses",
    }
    for name, value in sorted(raw.items()):
        field = f"profiles.{name}"
        if not isinstance(name, str) or PROFILE_NAME_PATTERN.fullmatch(name) is None:
            raise ValueError(f"profile name is invalid: {name!r}")
        if not isinstance(value, dict):
            raise ValueError(f"{field} must be a table")
        unknown = set(value) - allowed_keys
        if unknown:
            raise ValueError(f"{field} has unknown key(s): " + ", ".join(sorted(unknown)))
        document_types = _profile_string_list(
            value.get("document_types"),
            f"{field}.document_types",
            required=True,
        )
        for document_type in document_types:
            owner = type_owners.get(document_type)
            if owner is not None:
                raise ValueError(
                    f"document type {document_type!r} belongs to both "
                    f"profiles.{owner} and {field}"
                )
            type_owners[document_type] = name
        history_mode = value.get("history_mode", "living")
        if history_mode not in PROFILE_HISTORY_MODES:
            raise ValueError(
                f"{field}.history_mode must be living, append-only or "
                "immutable-after-state"
            )
        required_metadata = _profile_string_list(
            value.get("required_metadata", []),
            f"{field}.required_metadata",
        )
        required_roles = _profile_string_list(
            value.get("required_roles", []),
            f"{field}.required_roles",
            pattern=PROFILE_NAME_PATTERN,
        )
        raw_roles = value.get("roles", {})
        if not isinstance(raw_roles, dict):
            raise ValueError(f"{field}.roles must be a table")
        roles: list[ProfileRole] = []
        for role_name, aliases in sorted(raw_roles.items()):
            role_field = f"{field}.roles.{role_name}"
            if (
                not isinstance(role_name, str)
                or PROFILE_NAME_PATTERN.fullmatch(role_name) is None
            ):
                raise ValueError(f"{role_field} has an invalid role name")
            anchors = _profile_string_list(aliases, role_field, required=True)
            if any(not is_valid_anchor(anchor) for anchor in anchors):
                raise ValueError(f"{role_field} contains an invalid canonical anchor")
            roles.append(ProfileRole(role_name, anchors))
        role_names = {role.name for role in roles}
        missing_roles = set(required_roles) - role_names
        if missing_roles:
            raise ValueError(
                f"{field}.roles is missing required role(s): "
                + ", ".join(sorted(missing_roles))
            )
        raw_relations = value.get("allowed_relations")
        allowed_relations = (
            None
            if raw_relations is None
            else _profile_string_list(
                raw_relations, f"{field}.allowed_relations"
            )
        )
        if allowed_relations is not None and any(
            relation not in relation_names for relation in allowed_relations
        ):
            raise ValueError(
                f"{field}.allowed_relations may contain only semantic relation names"
            )
        raw_statuses = value.get("allowed_statuses")
        allowed_statuses = (
            None
            if raw_statuses is None
            else _profile_string_list(
                raw_statuses,
                f"{field}.allowed_statuses",
            )
        )
        profiles.append(
            DocumentProfile(
                name=name,
                document_types=document_types,
                history_mode=str(history_mode),
                required_metadata=required_metadata,
                required_roles=required_roles,
                roles=tuple(roles),
                allowed_relations=allowed_relations,
                allowed_statuses=allowed_statuses,
            )
        )
    return tuple(profiles)


def _delivery_policy(
    raw: object, profiles: tuple[DocumentProfile, ...]
) -> DeliveryPolicy | None:
    if raw is None or raw == {}:
        return None
    if not isinstance(raw, dict):
        raise ValueError("traceability must be a table")
    allowed = {
        "metadata_field",
        "document_types",
        "evidence_role",
        "terminal_statuses",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(
            "traceability has unknown key(s): " + ", ".join(sorted(unknown))
        )
    metadata_field = raw.get("metadata_field")
    if not isinstance(metadata_field, str) or not metadata_field:
        raise ValueError("traceability.metadata_field must be a non-empty string")
    reserved = {"id", "revision", "type", "status", *RELATION_FIELDS, PINNED_RELATION}
    if metadata_field in reserved:
        raise ValueError("traceability.metadata_field must name an additional field")
    document_types = _profile_string_list(
        raw.get("document_types"),
        "traceability.document_types",
        required=True,
    )
    evidence_role = raw.get("evidence_role")
    if (
        not isinstance(evidence_role, str)
        or PROFILE_NAME_PATTERN.fullmatch(evidence_role) is None
    ):
        raise ValueError("traceability.evidence_role must be a semantic role name")
    terminal_statuses = _profile_string_list(
        raw.get("terminal_statuses"),
        "traceability.terminal_statuses",
        required=True,
    )
    profiles_by_type = {
        document_type: profile
        for profile in profiles
        for document_type in profile.document_types
    }
    for document_type in document_types:
        profile = profiles_by_type.get(document_type)
        if profile is None:
            raise ValueError(
                f"traceability document type {document_type!r} has no profile"
            )
        if evidence_role not in {role.name for role in profile.roles}:
            raise ValueError(
                f"profile {profile.name!r} has no traceability evidence role "
                f"{evidence_role!r}"
            )
    return DeliveryPolicy(
        metadata_field=metadata_field,
        document_types=document_types,
        evidence_role=evidence_role,
        terminal_statuses=terminal_statuses,
    )


def _context_views(raw: object) -> tuple[ContextView, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise ValueError("context must be a table")
    unknown_context = set(raw) - {"views"}
    if unknown_context:
        raise ValueError(
            "context has unknown key(s): " + ", ".join(sorted(unknown_context))
        )
    views = raw.get("views", {})
    if not isinstance(views, dict):
        raise ValueError("context.views must be a table")

    result: list[ContextView] = []
    seen_tiers: set[int] = set()
    for name, entry in views.items():
        field = f"context.views.{name}"
        if not isinstance(name, str) or not CONTEXT_VIEW_NAME_PATTERN.fullmatch(name):
            raise ValueError(f"{field} has an invalid view name")
        if not isinstance(entry, dict):
            raise ValueError(f"{field} must be a table")
        required = {"tier", "delivery", "direction", "depth", "relations", "layers"}
        missing = required - set(entry)
        unknown = set(entry) - required
        if missing:
            raise ValueError(
                f"{field} is missing required key(s): {', '.join(sorted(missing))}"
            )
        if unknown:
            raise ValueError(
                f"{field} has unknown key(s): {', '.join(sorted(unknown))}"
            )

        tier = entry["tier"]
        if not isinstance(tier, int) or isinstance(tier, bool) or not 1 <= tier <= 99:
            raise ValueError(f"{field}.tier must be an integer between 1 and 99")
        if tier in seen_tiers:
            raise ValueError(f"context view tier is duplicated: {tier}")
        seen_tiers.add(tier)

        delivery = entry["delivery"]
        if delivery not in {"outline", "navigation"}:
            raise ValueError(f"{field}.delivery must be 'outline' or 'navigation'")
        direction = entry["direction"]
        if direction not in {"forward", "reverse", "both"}:
            raise ValueError(
                f"{field}.direction must be 'forward', 'reverse' or 'both'"
            )
        depth = entry["depth"]
        if not isinstance(depth, int) or isinstance(depth, bool) or not 0 <= depth <= 5:
            raise ValueError(f"{field}.depth must be an integer between 0 and 5")

        relations = entry["relations"]
        if not isinstance(relations, list) or any(
            not isinstance(item, str) or item not in CONTEXT_VIEW_RELATIONS
            for item in relations
        ):
            raise ValueError(
                f"{field}.relations must contain only supported semantic relations"
            )
        if len(set(relations)) != len(relations):
            raise ValueError(f"{field}.relations must be unique")

        layers = entry["layers"]
        if layers != ["authored"]:
            raise ValueError(
                f"{field}.layers must currently be exactly ['authored']"
            )
        result.append(
            ContextView(
                name=name,
                tier=tier,
                delivery=delivery,
                direction=direction,
                depth=depth,
                relations=tuple(relations),
                layers=("authored",),
            )
        )
    return tuple(sorted(result, key=lambda item: (item.tier, item.name)))


def _workstream_criteria(raw: object) -> tuple[WorkstreamCriterion, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise ValueError("workstreams must be a table")
    unknown = set(raw) - {"criteria"}
    if unknown:
        raise ValueError(
            "workstreams has unknown key(s): " + ", ".join(sorted(unknown))
        )
    criteria = raw.get("criteria", [])
    if not isinstance(criteria, list):
        raise ValueError("workstreams.criteria must be a list of tables")

    result: list[WorkstreamCriterion] = []
    seen: set[tuple[str, int]] = set()
    allowed = {
        "id",
        "revision",
        "required_sections",
        "required_evidence",
        "max_attempts",
        "safe_fallback",
    }
    for index, entry in enumerate(criteria):
        field = f"workstreams.criteria[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{field} must be a table")
        missing = allowed - set(entry)
        unknown_entry = set(entry) - allowed
        if missing:
            raise ValueError(
                f"{field} is missing required key(s): {', '.join(sorted(missing))}"
            )
        if unknown_entry:
            raise ValueError(
                f"{field} has unknown key(s): {', '.join(sorted(unknown_entry))}"
            )
        criterion_id = entry["id"]
        if not isinstance(criterion_id, str) or not WORKSTREAM_CRITERION_ID_PATTERN.fullmatch(
            criterion_id
        ):
            raise ValueError(f"{field}.id has an invalid criterion ID")
        revision = entry["revision"]
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise ValueError(f"{field}.revision must be a positive integer")
        key = (criterion_id, revision)
        if key in seen:
            raise ValueError(f"workstream criterion is duplicated: {criterion_id}@{revision}")
        seen.add(key)

        sections = entry["required_sections"]
        if not isinstance(sections, list) or any(
            not isinstance(item, str) or not is_valid_anchor(item) for item in sections
        ):
            raise ValueError(
                f"{field}.required_sections must contain supported stable anchors"
            )
        if len(set(sections)) != len(sections):
            raise ValueError(f"{field}.required_sections must be unique")

        evidence = entry["required_evidence"]
        if not isinstance(evidence, list) or any(
            not isinstance(item, str) or item not in WORKSTREAM_EVIDENCE_FIELDS
            for item in evidence
        ):
            raise ValueError(
                f"{field}.required_evidence may contain only: "
                + ", ".join(sorted(WORKSTREAM_EVIDENCE_FIELDS))
            )
        if not evidence:
            raise ValueError(f"{field}.required_evidence must not be empty")
        if len(set(evidence)) != len(evidence):
            raise ValueError(f"{field}.required_evidence must be unique")

        max_attempts = entry["max_attempts"]
        if (
            not isinstance(max_attempts, int)
            or isinstance(max_attempts, bool)
            or not 1 <= max_attempts <= 20
        ):
            raise ValueError(f"{field}.max_attempts must be between 1 and 20")
        safe_fallback = entry["safe_fallback"]
        if safe_fallback != "blocked":
            raise ValueError(f"{field}.safe_fallback must be 'blocked'")
        result.append(
            WorkstreamCriterion(
                criterion_id,
                revision,
                tuple(sections),
                tuple(evidence),
                max_attempts,
                safe_fallback,
            )
        )
    return tuple(sorted(result, key=lambda item: (item.criterion_id, item.revision)))


def _intake_placement(
    raw: object,
    field: str,
    areas: dict[str, PurePosixPath],
    identifiers: dict[str, str],
) -> IntakePlacement:
    if not isinstance(raw, dict):
        raise ValueError(f"{field} must be a table")
    required = {"area", "type", "identifier", "width"}
    missing = required - set(raw)
    unknown = set(raw) - required
    if missing:
        raise ValueError(
            f"{field} is missing required key(s): {', '.join(sorted(missing))}"
        )
    if unknown:
        raise ValueError(
            f"{field} has unknown key(s): {', '.join(sorted(unknown))}"
        )
    area = raw["area"]
    if not isinstance(area, str) or area not in areas:
        raise ValueError(f"{field}.area must name a configured area")
    document_type = raw["type"]
    if not isinstance(document_type, str) or not document_type.strip():
        raise ValueError(f"{field}.type must be a non-empty string")
    identifier = raw["identifier"]
    if not isinstance(identifier, str) or identifier not in identifiers:
        raise ValueError(
            f"{field}.identifier must name a configured identifier role"
        )
    width = raw["width"]
    if (
        not isinstance(width, int)
        or isinstance(width, bool)
        or not 1 <= width <= 12
    ):
        raise ValueError(f"{field}.width must be between 1 and 12")
    return IntakePlacement(area, document_type.strip(), identifier, width)


def _intake_criteria(
    raw: object,
    areas: dict[str, PurePosixPath],
    identifiers: dict[str, str],
) -> tuple[IntakeCriterion, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise ValueError("intake must be a table")
    unknown = set(raw) - {"criteria"}
    if unknown:
        raise ValueError(
            "intake has unknown key(s): " + ", ".join(sorted(unknown))
        )
    criteria = raw.get("criteria", [])
    if not isinstance(criteria, list):
        raise ValueError("intake.criteria must be a list of tables")
    required = {
        "id",
        "revision",
        "allowed_decisions",
        "max_candidates",
        "safe_fallback",
        "draft",
        "workstream",
    }
    result: list[IntakeCriterion] = []
    seen: set[tuple[str, int]] = set()
    for index, entry in enumerate(criteria):
        field = f"intake.criteria[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{field} must be a table")
        missing = required - set(entry)
        unknown_entry = set(entry) - required
        if missing:
            raise ValueError(
                f"{field} is missing required key(s): {', '.join(sorted(missing))}"
            )
        if unknown_entry:
            raise ValueError(
                f"{field} has unknown key(s): {', '.join(sorted(unknown_entry))}"
            )
        criterion_id = entry["id"]
        if not isinstance(criterion_id, str) or not WORKSTREAM_CRITERION_ID_PATTERN.fullmatch(
            criterion_id
        ):
            raise ValueError(f"{field}.id has an invalid criterion ID")
        revision = entry["revision"]
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise ValueError(f"{field}.revision must be a positive integer")
        key = (criterion_id, revision)
        if key in seen:
            raise ValueError(f"intake criterion is duplicated: {criterion_id}@{revision}")
        seen.add(key)
        decisions = entry["allowed_decisions"]
        if not isinstance(decisions, list) or any(
            not isinstance(item, str) or item not in INTAKE_DECISIONS
            for item in decisions
        ):
            raise ValueError(
                f"{field}.allowed_decisions may contain only: "
                + ", ".join(sorted(INTAKE_DECISIONS))
            )
        if not decisions:
            raise ValueError(f"{field}.allowed_decisions must not be empty")
        if len(set(decisions)) != len(decisions):
            raise ValueError(f"{field}.allowed_decisions must be unique")
        max_candidates = entry["max_candidates"]
        if (
            not isinstance(max_candidates, int)
            or isinstance(max_candidates, bool)
            or not 1 <= max_candidates <= 50
        ):
            raise ValueError(f"{field}.max_candidates must be between 1 and 50")
        if entry["safe_fallback"] != "blocked":
            raise ValueError(f"{field}.safe_fallback must be 'blocked'")
        result.append(
            IntakeCriterion(
                criterion_id=criterion_id,
                revision=revision,
                allowed_decisions=tuple(decisions),
                max_candidates=max_candidates,
                safe_fallback="blocked",
                draft=_intake_placement(
                    entry["draft"], f"{field}.draft", areas, identifiers
                ),
                workstream=_intake_placement(
                    entry["workstream"],
                    f"{field}.workstream",
                    areas,
                    identifiers,
                ),
            )
        )
    return tuple(sorted(result, key=lambda item: (item.criterion_id, item.revision)))


def _admission_criteria(raw: object) -> tuple[AdmissionCriterion, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise ValueError("admission must be a table")
    unknown = set(raw) - {"criteria"}
    if unknown:
        raise ValueError(
            "admission has unknown key(s): " + ", ".join(sorted(unknown))
        )
    criteria = raw.get("criteria", [])
    if not isinstance(criteria, list):
        raise ValueError("admission.criteria must be a list of tables")
    required = {
        "id",
        "revision",
        "max_autonomy",
        "allowed_actions",
        "required_authorizations",
        "allowed_verification",
        "max_risk",
        "max_targets",
        "required_sections",
        "safe_fallback",
    }
    optional = {"require_source_scope_for"}
    result: list[AdmissionCriterion] = []
    seen: set[tuple[str, int]] = set()
    for index, entry in enumerate(criteria):
        field = f"admission.criteria[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{field} must be a table")
        missing = required - set(entry)
        unknown_entry = set(entry) - required - optional
        if missing:
            raise ValueError(
                f"{field} is missing required key(s): {', '.join(sorted(missing))}"
            )
        if unknown_entry:
            raise ValueError(
                f"{field} has unknown key(s): {', '.join(sorted(unknown_entry))}"
            )
        criterion_id = entry["id"]
        if (
            not isinstance(criterion_id, str)
            or not WORKSTREAM_CRITERION_ID_PATTERN.fullmatch(criterion_id)
        ):
            raise ValueError(f"{field}.id has an invalid criterion ID")
        revision = entry["revision"]
        if (
            not isinstance(revision, int)
            or isinstance(revision, bool)
            or revision < 1
        ):
            raise ValueError(f"{field}.revision must be a positive integer")
        key = (criterion_id, revision)
        if key in seen:
            raise ValueError(
                f"admission criterion is duplicated: {criterion_id}@{revision}"
            )
        seen.add(key)

        max_autonomy = entry["max_autonomy"]
        if max_autonomy not in ADMISSION_AUTONOMY_LEVELS:
            raise ValueError(f"{field}.max_autonomy must be A0, A1 or A2")
        actions = entry["allowed_actions"]
        if not isinstance(actions, list) or any(
            not isinstance(item, str) or item not in ADMISSION_ACTION_LEVELS
            for item in actions
        ):
            raise ValueError(
                f"{field}.allowed_actions may contain only: "
                + ", ".join(sorted(ADMISSION_ACTION_LEVELS))
            )
        if not actions:
            raise ValueError(f"{field}.allowed_actions must not be empty")
        if len(set(actions)) != len(actions):
            raise ValueError(f"{field}.allowed_actions must be unique")
        if any(
            ADMISSION_ACTION_LEVELS[action]
            > ADMISSION_AUTONOMY_LEVELS[max_autonomy]
            for action in actions
        ):
            raise ValueError(
                f"{field}.allowed_actions exceeds max_autonomy {max_autonomy}"
            )
        authorizations = entry["required_authorizations"]
        if not isinstance(authorizations, list) or any(
            not isinstance(item, str) or item not in ADMISSION_ACTION_LEVELS
            for item in authorizations
        ):
            raise ValueError(
                f"{field}.required_authorizations may contain only admission actions"
            )
        if len(set(authorizations)) != len(authorizations):
            raise ValueError(f"{field}.required_authorizations must be unique")
        if not set(authorizations).issubset(actions):
            raise ValueError(
                f"{field}.required_authorizations must be allowed actions"
            )
        source_scope_actions = entry.get("require_source_scope_for", [])
        if not isinstance(source_scope_actions, list) or any(
            not isinstance(item, str) or item not in ADMISSION_ACTION_LEVELS
            for item in source_scope_actions
        ):
            raise ValueError(
                f"{field}.require_source_scope_for may contain only admission actions"
            )
        if len(set(source_scope_actions)) != len(source_scope_actions):
            raise ValueError(f"{field}.require_source_scope_for must be unique")
        if not set(source_scope_actions).issubset(actions):
            raise ValueError(
                f"{field}.require_source_scope_for must be allowed actions"
            )
        verification = entry["allowed_verification"]
        if not isinstance(verification, list) or any(
            not isinstance(item, str)
            or item not in ADMISSION_VERIFICATION_LEVELS
            for item in verification
        ):
            raise ValueError(
                f"{field}.allowed_verification may contain only: "
                + ", ".join(sorted(ADMISSION_VERIFICATION_LEVELS))
            )
        if not verification:
            raise ValueError(f"{field}.allowed_verification must not be empty")
        if len(set(verification)) != len(verification):
            raise ValueError(f"{field}.allowed_verification must be unique")
        max_risk = entry["max_risk"]
        if max_risk not in ADMISSION_RISK_LEVELS:
            raise ValueError(f"{field}.max_risk must be low, medium or high")
        max_targets = entry["max_targets"]
        if (
            not isinstance(max_targets, int)
            or isinstance(max_targets, bool)
            or not 1 <= max_targets <= 100
        ):
            raise ValueError(f"{field}.max_targets must be between 1 and 100")
        sections = entry["required_sections"]
        if not isinstance(sections, list) or any(
            not isinstance(item, str) or not is_valid_anchor(item)
            for item in sections
        ):
            raise ValueError(
                f"{field}.required_sections must contain supported stable anchors"
            )
        if not sections:
            raise ValueError(f"{field}.required_sections must not be empty")
        if len(set(sections)) != len(sections):
            raise ValueError(f"{field}.required_sections must be unique")
        if entry["safe_fallback"] != "blocked":
            raise ValueError(f"{field}.safe_fallback must be 'blocked'")
        result.append(
            AdmissionCriterion(
                criterion_id=criterion_id,
                revision=revision,
                max_autonomy=max_autonomy,
                allowed_actions=tuple(actions),
                required_authorizations=tuple(authorizations),
                allowed_verification=tuple(verification),
                max_risk=max_risk,
                max_targets=max_targets,
                required_sections=tuple(sections),
                require_source_scope_for=tuple(source_scope_actions),
                safe_fallback="blocked",
            )
        )
    return tuple(sorted(result, key=lambda item: (item.criterion_id, item.revision)))


def _maintenance_id(value: object, field: str, prefixes: frozenset[str]) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a stable document ID")
    match = DOCUMENT_ID_PATTERN.fullmatch(value)
    if match is None or match.group(1) not in prefixes:
        raise ValueError(f"{field} must use a configured stable ID prefix")
    return value


def _maintenance_target_anchor(value: object, field: str) -> str:
    if not isinstance(value, str) or not is_valid_anchor(value):
        raise ValueError(f"{field} must use the supported stable anchor syntax")
    return value


def _maintenance_occurrence(
    raw: object, field: str, prefixes: frozenset[str]
) -> MaintenanceOccurrence:
    if not isinstance(raw, dict):
        raise ValueError(f"{field} must be a table")
    allowed = {"document", "anchor", "role"}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(
            f"{field} has unknown key(s): {', '.join(sorted(unknown))}"
        )
    document_id = _maintenance_id(raw.get("document"), f"{field}.document", prefixes)
    anchor = _maintenance_target_anchor(raw.get("anchor"), f"{field}.anchor")
    role = raw.get("role")
    if role not in MAINTENANCE_ROLES:
        raise ValueError(
            f"{field}.role must be one of: "
            f"{', '.join(sorted(MAINTENANCE_ROLES))}"
        )
    return MaintenanceOccurrence(document_id, anchor, role)


def _maintenance_targets(
    raw: object, prefixes: frozenset[str]
) -> tuple[MaintenanceTarget, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("maintenance must be a list of tables")

    targets: list[MaintenanceTarget] = []
    seen_names: set[str] = set()
    for index, entry in enumerate(raw):
        field = f"maintenance[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{field} must be a table")
        allowed = {"name", "source_document", "source_anchor", "occurrences"}
        unknown = set(entry) - allowed
        if unknown:
            raise ValueError(
                f"{field} has unknown key(s): {', '.join(sorted(unknown))}"
            )
        name = entry.get("name")
        if not isinstance(name, str) or not MAINTENANCE_TARGET_NAME_PATTERN.fullmatch(
            name
        ):
            raise ValueError(
                f"{field}.name must be a non-empty identifier-style string"
            )
        if name in seen_names:
            raise ValueError(f"maintenance target name is duplicated: {name!r}")
        seen_names.add(name)

        source_document_id = _maintenance_id(
            entry.get("source_document"), f"{field}.source_document", prefixes
        )
        source_anchor = _maintenance_target_anchor(
            entry.get("source_anchor"), f"{field}.source_anchor"
        )

        raw_occurrences = entry.get("occurrences")
        if not isinstance(raw_occurrences, list) or not raw_occurrences:
            raise ValueError(f"{field}.occurrences must be a non-empty list")

        occurrences: list[MaintenanceOccurrence] = []
        seen_occurrences: set[tuple[str, str]] = set()
        for occurrence_index, raw_occurrence in enumerate(raw_occurrences):
            occurrence_field = f"{field}.occurrences[{occurrence_index}]"
            occurrence = _maintenance_occurrence(
                raw_occurrence, occurrence_field, prefixes
            )
            key = (occurrence.document_id, occurrence.anchor)
            if key == (source_document_id, source_anchor):
                raise ValueError(
                    f"{occurrence_field} overlaps the declared source address "
                    f"{source_document_id}#{source_anchor}"
                )
            if key in seen_occurrences:
                raise ValueError(
                    f"{occurrence_field} duplicates another occurrence at "
                    f"{occurrence.document_id}#{occurrence.anchor}"
                )
            seen_occurrences.add(key)
            occurrences.append(occurrence)

        targets.append(
            MaintenanceTarget(
                name=name,
                source_document_id=source_document_id,
                source_anchor=source_anchor,
                occurrences=tuple(occurrences),
            )
        )
    return tuple(targets)


def load_config(project_root: Path) -> ProjectConfig:
    root = project_root.resolve()
    config_path = root / CONFIG_FILENAME
    if not config_path.is_file():
        raise ValueError(f"configuration not found: {config_path}")
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)

    if raw.get("version") != 1:
        raise ValueError("unsupported configuration version")
    documentation = raw.get("documentation")
    areas = raw.get("areas")
    identifiers = raw.get("identifiers")
    projection = raw.get("projection")
    if not all(isinstance(item, dict) for item in (documentation, areas, identifiers, projection)):
        raise ValueError("documentation, areas, identifiers and projection tables are required")

    documentation_path = _relative_path(documentation.get("root"), "documentation.root")
    language = documentation.get("language")
    if not isinstance(language, str) or not language:
        raise ValueError("documentation.language must be a non-empty string")

    normalized_areas = {
        str(role): _relative_path(path, f"areas.{role}") for role, path in areas.items()
    }
    if len(set(normalized_areas.values())) != len(normalized_areas):
        raise ValueError("area paths must be unique")

    normalized_identifiers: dict[str, str] = {}
    for role, prefix in identifiers.items():
        if not isinstance(prefix, str) or not PREFIX_PATTERN.fullmatch(prefix):
            raise ValueError(f"identifiers.{role} has an invalid prefix")
        normalized_identifiers[str(role)] = prefix
    if len(set(normalized_identifiers.values())) != len(normalized_identifiers):
        raise ValueError("identifier prefixes must be unique")

    catalog_exclusions = _catalog_exclusions(raw.get("catalog"))
    navigation_extend_through = _navigation_anchors(raw.get("navigation"))
    (
        legacy_relation_mode,
        snapshot_document_types,
        snapshot_rules,
    ) = _relations_policy(raw.get("relations"))
    maintenance_targets = _maintenance_targets(
        raw.get("maintenance"), frozenset(normalized_identifiers.values())
    )
    context_views = _context_views(raw.get("context"))
    graph_health_policy = _graph_health_policy(raw.get("graph_health"))
    document_profiles = _document_profiles(raw.get("profiles"))
    delivery_policy = _delivery_policy(raw.get("traceability"), document_profiles)
    workstream_criteria = _workstream_criteria(raw.get("workstreams"))
    intake_criteria = _intake_criteria(
        raw.get("intake"), normalized_areas, normalized_identifiers
    )
    admission_criteria = _admission_criteria(raw.get("admission"))

    projection_format = projection.get("format")
    if projection_format != "sharded-json":
        raise ValueError("only sharded-json projection is supported")
    keep_generations = projection.get("keep_generations")
    if not isinstance(keep_generations, int) or not 1 <= keep_generations <= 20:
        raise ValueError("projection.keep_generations must be between 1 and 20")

    return ProjectConfig(
        project_root=root,
        documentation_root=root / documentation_path,
        language=language,
        areas=normalized_areas,
        identifiers=normalized_identifiers,
        projection_format=projection_format,
        keep_generations=keep_generations,
        catalog_exclusions=catalog_exclusions,
        navigation_extend_through=navigation_extend_through,
        legacy_relation_mode=legacy_relation_mode,
        snapshot_document_types=snapshot_document_types,
        snapshot_rules=snapshot_rules,
        graph_health_policy=graph_health_policy,
        document_profiles=document_profiles,
        delivery_policy=delivery_policy,
        maintenance_targets=maintenance_targets,
        context_views=context_views,
        workstream_criteria=workstream_criteria,
        intake_criteria=intake_criteria,
        admission_criteria=admission_criteria,
    )
