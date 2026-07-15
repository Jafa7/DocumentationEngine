"""Workspace-owned, content-addressed projection of a federated catalog.

The projection is an integrity-checked acceleration structure. Markdown and
``workspace.toml`` remain the only authored truth, and no cache data is ever
written below a registered source root.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from docsystem.catalog import MarkdownDocument
from docsystem.config import load_config
from docsystem.federation import (
    FederatedCatalog,
    FederatedDocument,
    FederatedEdge,
    FederatedReferenceBoundary,
    FederatedReferenceEdge,
    FederationBoundary,
    FederationMigration,
    QualifiedAddress,
    build_federated_catalog,
)
from docsystem.metadata import (
    DocumentMetadata,
    FederatedMetadataReference,
    MetadataReference,
)
from docsystem.projection import config_fingerprint
from docsystem.sections import MarkdownSection
from docsystem.workspace import WORKSPACE_FILENAME, Workspace

SCHEMA_VERSION = 1
DEFAULT_KEEP_GENERATIONS = 2


@dataclass(frozen=True, order=True)
class FederatedSourceChange:
    """One source-level difference from the selected generation."""

    source: str
    kind: str


@dataclass(frozen=True)
class FederatedChangesReport:
    """Bounded source-level changes since the selected generation."""

    status: str
    changes: tuple[FederatedSourceChange, ...] = field(default_factory=tuple)


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_text(value: str) -> str:
    return _sha_bytes(value.encode())


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_object(value: object) -> str:
    return _sha_text(_json(value))


def federated_cache_root(workspace: Workspace) -> Path:
    """Return the cache root owned by the workspace, never by a source."""

    root = workspace.root.resolve()
    state_root = root / ".docsystem"
    cache_root = state_root / "federated-cache"
    for path in (state_root, cache_root):
        if path.is_symlink():
            raise ValueError("federated cache path must not contain symlinks")
    if not cache_root.resolve(strict=False).is_relative_to(root):
        raise ValueError("federated cache path escapes workspace root")
    return cache_root


def _assert_safe_cache_tree(workspace: Workspace, root: Path) -> None:
    """Reject writable/readable cache paths redirected through symlinks."""

    workspace_root = workspace.root.resolve()
    if root.is_symlink() or not root.resolve(strict=False).is_relative_to(workspace_root):
        raise ValueError("federated cache path escapes workspace root")
    if root.exists():
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ValueError("federated cache tree must not contain symlinks")


def _object_path(root: Path, digest: str) -> Path:
    return root / "objects" / digest[:2] / f"{digest}.json"


def _generation_path(root: Path, generation: str) -> Path:
    return root / "generations" / generation / "manifest.json"


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def _portable_value(value: object) -> object:
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, tuple):
        return {"$tuple": [_portable_value(item) for item in value]}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(f"unsupported metadata value: {type(value).__name__}")


def _restore_value(value: object) -> object:
    if isinstance(value, dict) and set(value) == {"$datetime"}:
        return datetime.fromisoformat(str(value["$datetime"]))
    if isinstance(value, dict) and set(value) == {"$date"}:
        return date.fromisoformat(str(value["$date"]))
    if isinstance(value, dict) and set(value) == {"$tuple"}:
        raw = value["$tuple"]
        if not isinstance(raw, list):
            raise ValueError("invalid tuple value")
        return tuple(_restore_value(item) for item in raw)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise ValueError("invalid portable metadata value")


def _address(value: QualifiedAddress) -> dict[str, object]:
    return {
        "source": value.source,
        "document_id": value.document_id,
        "anchor": value.anchor,
    }


def _load_address(value: object) -> QualifiedAddress:
    if not isinstance(value, dict):
        raise ValueError("invalid qualified address")
    source = value.get("source")
    document_id = value.get("document_id")
    anchor = value.get("anchor")
    if not isinstance(source, str) or not isinstance(document_id, str):
        raise ValueError("invalid qualified address")
    if anchor is not None and not isinstance(anchor, str):
        raise ValueError("invalid qualified anchor")
    return QualifiedAddress(source, document_id, anchor)


def _metadata(value: DocumentMetadata | None) -> object:
    if value is None:
        return None
    return {
        "document_id": value.document_id,
        "revision": value.revision,
        "document_type": value.document_type,
        "status": value.status,
        "references": [
            {
                "relation": item.relation,
                "target_id": item.target_id,
                "expected_revision": item.expected_revision,
            }
            for item in value.references
        ],
        "additional_fields": [
            [name, _portable_value(item)] for name, item in value.additional_fields
        ],
        "additional_field_types": [list(item) for item in value.additional_field_types],
        "legacy_references": [list(item) for item in value.legacy_references],
        "federated_references": [
            {
                "relation": item.relation,
                "target": item.target,
                "expected_revision": item.expected_revision,
            }
            for item in value.federated_references
        ],
    }


def _load_metadata(value: object) -> DocumentMetadata | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("invalid document metadata")
    return DocumentMetadata(
        document_id=str(value["document_id"]),
        revision=int(value["revision"]),
        document_type=value.get("document_type"),
        status=value.get("status"),
        references=tuple(
            MetadataReference(
                str(item["relation"]),
                str(item["target_id"]),
                item.get("expected_revision"),
            )
            for item in value.get("references", [])
        ),
        additional_fields=tuple(
            (str(item[0]), _restore_value(item[1]))
            for item in value.get("additional_fields", [])
        ),
        additional_field_types=tuple(
            (str(item[0]), str(item[1]))
            for item in value.get("additional_field_types", [])
        ),
        legacy_references=tuple(
            (str(item[0]), str(item[1]))
            for item in value.get("legacy_references", [])
        ),
        federated_references=tuple(
            FederatedMetadataReference(
                str(item["relation"]),
                str(item["target"]),
                item.get("expected_revision"),
            )
            for item in value.get("federated_references", [])
        ),
    )


def _markdown_document(value: MarkdownDocument) -> dict[str, object]:
    return {
        "role": value.role,
        "path": value.path.as_posix(),
        "links": [item.as_posix() for item in value.links],
        "is_index": value.is_index,
        "content": value.content,
        "metadata": _metadata(value.metadata),
        "sections": [
            {
                "title": item.title,
                "anchor": item.anchor,
                "level": item.level,
                "start_line": item.start_line,
                "end_line": item.end_line,
            }
            for item in value.sections
        ],
        "section_issues": list(value.section_issues),
        "metadata_issues": list(value.metadata_issues),
        "graph_issues": list(value.graph_issues),
    }


def _load_markdown_document(value: object) -> MarkdownDocument:
    if not isinstance(value, dict):
        raise ValueError("invalid Markdown document")
    return MarkdownDocument(
        role=str(value["role"]),
        path=PurePosixPath(str(value["path"])),
        links=tuple(PurePosixPath(str(item)) for item in value.get("links", [])),
        is_index=bool(value["is_index"]),
        content=str(value["content"]),
        metadata=_load_metadata(value.get("metadata")),
        sections=tuple(
            MarkdownSection(
                str(item["title"]),
                str(item["anchor"]),
                int(item["level"]),
                int(item["start_line"]),
                int(item["end_line"]),
            )
            for item in value.get("sections", [])
        ),
        section_issues=tuple(str(item) for item in value.get("section_issues", [])),
        metadata_issues=tuple(str(item) for item in value.get("metadata_issues", [])),
        graph_issues=tuple(str(item) for item in value.get("graph_issues", [])),
    )


def _document(value: FederatedDocument) -> dict[str, object]:
    return {
        "address": _address(value.address),
        "visibility": value.visibility,
        "role": value.role,
        "path": value.path,
        "revision": value.revision,
        "document_type": value.document_type,
        "status": value.status,
        "historical_snapshot": value.historical_snapshot,
        "content": value.content,
        "source_document": _markdown_document(value.source),
        "navigation_extend_through": list(value.navigation_extend_through),
    }


def _load_document(value: object) -> FederatedDocument:
    if not isinstance(value, dict):
        raise ValueError("invalid federated document")
    return FederatedDocument(
        address=_load_address(value["address"]),
        visibility=str(value["visibility"]),
        role=str(value["role"]),
        path=str(value["path"]),
        revision=int(value["revision"]),
        document_type=value.get("document_type"),
        status=value.get("status"),
        historical_snapshot=bool(value["historical_snapshot"]),
        content=str(value["content"]),
        source=_load_markdown_document(value["source_document"]),
        navigation_extend_through=tuple(
            str(item) for item in value.get("navigation_extend_through", [])
        ),
    )


def _inventory(documentation_root: Path) -> list[dict[str, str]]:
    if not documentation_root.is_dir():
        return []
    result = []
    for path in sorted(documentation_root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_file() and path.suffix.lower() == ".md":
            result.append(
                {
                    "path": path.relative_to(documentation_root).as_posix(),
                    "sha256": _sha_bytes(path.read_bytes()),
                }
            )
    return result


def _workspace_manifest_hash(workspace: Workspace) -> str:
    return _sha_bytes((workspace.root / WORKSPACE_FILENAME).read_bytes())


def _source_states(workspace: Workspace) -> dict[str, dict[str, object]]:
    states: dict[str, dict[str, object]] = {}
    for source in workspace.sources:
        config = load_config(source.project_root)
        inventory = _inventory(config.documentation_root)
        states[source.name] = {
            "root": source.root.as_posix(),
            "visibility": source.visibility,
            "config_fingerprint": config_fingerprint(config),
            "inventory": inventory,
            "inventory_hash": _hash_object(inventory),
        }
    return states


def _graph(catalog: FederatedCatalog) -> dict[str, object]:
    return {
        "edges": [
            {
                "relation": item.relation,
                "source": _address(item.source),
                "target": _address(item.target),
                "expected_revision": item.expected_revision,
            }
            for item in catalog.edges
        ],
        "boundaries": [
            {
                "source": _address(item.source),
                "relation": item.relation,
                "raw_target": item.raw_target,
                "reason": item.reason,
            }
            for item in catalog.boundaries
        ],
        "migrations": [
            {
                "source": _address(item.source),
                "relation": item.relation,
                "value": item.value,
                "target": _address(item.target),
            }
            for item in catalog.migrations
        ],
        "reference_edges": [
            {
                "relation": item.relation,
                "authority": item.authority,
                "source": _address(item.source),
                "target": _address(item.target),
                "origin": item.origin,
                "reason": item.reason,
                "pin": item.pin,
            }
            for item in catalog.reference_edges
        ],
        "reference_boundaries": [
            {
                "source": _address(item.source),
                "raw_target": item.raw_target,
                "category": item.category,
                "reason": item.reason,
            }
            for item in catalog.reference_boundaries
        ],
    }


def _load_graph(value: object, documents: tuple[FederatedDocument, ...]) -> FederatedCatalog:
    if not isinstance(value, dict):
        raise ValueError("invalid aggregate graph")
    return FederatedCatalog(
        documents=documents,
        edges=tuple(
            FederatedEdge(
                str(item["relation"]),
                _load_address(item["source"]),
                _load_address(item["target"]),
                item.get("expected_revision"),
            )
            for item in value.get("edges", [])
        ),
        boundaries=tuple(
            FederationBoundary(
                _load_address(item["source"]),
                str(item["relation"]),
                str(item["raw_target"]),
                str(item["reason"]),
            )
            for item in value.get("boundaries", [])
        ),
        migrations=tuple(
            FederationMigration(
                _load_address(item["source"]),
                str(item["relation"]),
                str(item["value"]),
                _load_address(item["target"]),
            )
            for item in value.get("migrations", [])
        ),
        reference_edges=tuple(
            FederatedReferenceEdge(
                str(item["relation"]),
                str(item["authority"]),
                _load_address(item["source"]),
                _load_address(item["target"]),
                str(item["origin"]),
                item.get("reason"),
                item.get("pin"),
            )
            for item in value.get("reference_edges", [])
        ),
        reference_boundaries=tuple(
            FederatedReferenceBoundary(
                _load_address(item["source"]),
                str(item["raw_target"]),
                str(item["category"]),
                str(item["reason"]),
            )
            for item in value.get("reference_boundaries", [])
        ),
    )


def build_federated_projection(
    workspace: Workspace,
    catalog: FederatedCatalog,
    *,
    source_states: dict[str, dict[str, object]] | None = None,
    workspace_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic portable projection without writing it."""

    states = source_states if source_states is not None else _source_states(workspace)
    source_objects: dict[str, dict[str, object]] = {}
    source_records: dict[str, dict[str, object]] = {}
    for source in workspace.sources:
        state = states[source.name]
        body = {
            "schema_version": SCHEMA_VERSION,
            "kind": "source",
            "source": source.name,
            **state,
            "documents": [
                _document(item)
                for item in catalog.documents
                if item.address.source == source.name
            ],
        }
        digest = _hash_object(body)
        source_objects[digest] = body
        source_records[source.name] = {
            "root": state["root"],
            "visibility": state["visibility"],
            "config_fingerprint": state["config_fingerprint"],
            "inventory_hash": state["inventory_hash"],
            "object": digest,
        }
    graph = {"schema_version": SCHEMA_VERSION, "kind": "graph", **_graph(catalog)}
    graph_digest = _hash_object(graph)
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "workspace_manifest_sha256": (
            workspace_manifest_sha256
            if workspace_manifest_sha256 is not None
            else _workspace_manifest_hash(workspace)
        ),
        "sources": dict(sorted(source_records.items())),
        "graph_object": graph_digest,
    }
    generation = _hash_object(manifest)
    manifest["generation"] = generation
    return {
        "schema_version": SCHEMA_VERSION,
        "generation": generation,
        "manifest": manifest,
        "objects": {**source_objects, graph_digest: graph},
    }


def build_current_federated_projection(
    workspace: Workspace,
) -> tuple[FederatedCatalog, dict[str, Any]]:
    """Build one projection bound to a stable authoritative source snapshot."""

    manifest_before = _workspace_manifest_hash(workspace)
    states_before = _source_states(workspace)
    catalog = build_federated_catalog(workspace)
    manifest_after = _workspace_manifest_hash(workspace)
    states_after = _source_states(workspace)
    if manifest_before != manifest_after or states_before != states_after:
        raise ValueError("federated sources changed during projection build")
    return catalog, build_federated_projection(
        workspace,
        catalog,
        source_states=states_after,
        workspace_manifest_sha256=manifest_after,
    )


def _manifest_bound(manifest: dict[str, Any], generation: str) -> bool:
    if manifest.get("schema_version") != SCHEMA_VERSION:
        return False
    identity = {key: value for key, value in manifest.items() if key != "generation"}
    return manifest.get("generation") == generation and _hash_object(identity) == generation


def _write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _write_object(root: Path, digest: str, value: dict[str, object]) -> None:
    if _hash_object(value) != digest:
        raise ValueError("federated projection object hash mismatch")
    target = _object_path(root, digest)
    if target.is_file():
        try:
            existing = _read_object(target)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            existing = None
        if isinstance(existing, dict) and _hash_object(existing) == digest:
            return
    _write_json_atomic(target, value)


def _garbage_collect(root: Path, keep_generations: int) -> None:
    generations = root / "generations"
    if not generations.is_dir():
        return
    ordered = sorted(
        (
            item
            for item in generations.iterdir()
            if item.is_dir() and not item.name.startswith(".staging-")
        ),
        key=lambda item: (item.stat().st_mtime_ns, item.name),
        reverse=True,
    )
    try:
        current_name = str(_read_object(root / "current.json")["generation"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return
    current = generations / current_name
    if not current.is_dir():
        return
    retained = [current]
    retained.extend(item for item in ordered if item != current)
    retained = retained[:keep_generations]
    candidates = {
        item
        for item in generations.iterdir()
        if not item.name.startswith(".staging-")
    }
    for obsolete in sorted(candidates - set(retained)):
        if obsolete.is_dir():
            shutil.rmtree(obsolete)
    referenced: set[str] = set()
    for generation_dir in retained:
        try:
            manifest = _read_object(generation_dir / "manifest.json")
            referenced.add(str(manifest["graph_object"]))
            referenced.update(
                str(item["object"])
                for item in manifest.get("sources", {}).values()
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            # A corrupt retained generation is not permission to delete objects
            # that might be its only recoverable data.
            return
    objects = root / "objects"
    if not objects.is_dir():
        return
    for path in objects.glob("*/*.json"):
        if path.stem not in referenced:
            path.unlink()
    for directory in objects.iterdir():
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()


def write_federated_projection(
    workspace: Workspace,
    projection: dict[str, Any],
    *,
    keep_generations: int = DEFAULT_KEEP_GENERATIONS,
) -> str:
    """Atomically publish one immutable generation in the workspace cache."""

    if not 1 <= keep_generations <= 20:
        raise ValueError("keep_generations must be between 1 and 20")
    generation = str(projection.get("generation"))
    manifest = projection.get("manifest")
    objects = projection.get("objects")
    if not isinstance(manifest, dict) or not isinstance(objects, dict):
        raise ValueError("invalid federated projection")
    if not _manifest_bound(manifest, generation):
        raise ValueError("federated projection generation mismatch")
    root = federated_cache_root(workspace)
    _assert_safe_cache_tree(workspace, root)
    for digest, value in sorted(objects.items()):
        if not isinstance(digest, str) or not isinstance(value, dict):
            raise ValueError("invalid federated projection object")
        _write_object(root, digest, value)
    target = _generation_path(root, generation)
    manifest_current = False
    if target.is_file():
        try:
            existing_manifest = _read_object(target)
            manifest_current = (
                existing_manifest == manifest
                and _manifest_bound(existing_manifest, generation)
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
    if not target.is_file():
        target.parent.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=target.parent.parent))
        try:
            (staging / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            staging.replace(target.parent)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    elif not manifest_current:
        _write_json_atomic(target, manifest)
    _write_json_atomic(
        root / "current.json",
        {"schema_version": SCHEMA_VERSION, "generation": generation},
    )
    _garbage_collect(root, keep_generations)
    return generation


def _current_manifest(workspace: Workspace) -> tuple[str, dict[str, Any]]:
    root = federated_cache_root(workspace)
    _assert_safe_cache_tree(workspace, root)
    pointer = _read_object(root / "current.json")
    if pointer.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("schema incompatible")
    generation = pointer.get("generation")
    if not isinstance(generation, str):
        raise ValueError("pointer mismatch")
    manifest = _read_object(_generation_path(root, generation))
    if not _manifest_bound(manifest, generation):
        raise ValueError("corrupt")
    return generation, manifest


def _freshness_reason(workspace: Workspace, manifest: dict[str, Any]) -> str | None:
    if manifest.get("workspace_manifest_sha256") != _workspace_manifest_hash(workspace):
        return "federated projection stale: workspace manifest changed"
    states = _source_states(workspace)
    records = manifest.get("sources")
    if not isinstance(records, dict) or set(records) != set(states):
        return "federated projection stale: source membership changed"
    for source, state in states.items():
        record = records.get(source)
        if not isinstance(record, dict):
            return "federated projection corrupt"
        for key in ("root", "visibility", "config_fingerprint", "inventory_hash"):
            if record.get(key) != state[key]:
                return f"federated projection stale: source {source} changed"
    return None


def load_verified_federated_projection(
    workspace: Workspace,
) -> tuple[FederatedCatalog | None, str]:
    """Load and rehydrate the current projection, or return a bounded reason."""

    root = federated_cache_root(workspace)
    _assert_safe_cache_tree(workspace, root)
    if not (root / "current.json").is_file():
        return None, "federated projection absent"
    try:
        _, manifest = _current_manifest(workspace)
        stale = _freshness_reason(workspace, manifest)
        if stale is not None:
            return None, stale
        documents: list[FederatedDocument] = []
        records = manifest["sources"]
        for source in sorted(records):
            digest = str(records[source]["object"])
            value = _read_object(_object_path(root, digest))
            if _hash_object(value) != digest or value.get("kind") != "source":
                return None, f"federated projection corrupt: source object {source}"
            documents.extend(_load_document(item) for item in value.get("documents", []))
        graph_digest = str(manifest["graph_object"])
        graph = _read_object(_object_path(root, graph_digest))
        if _hash_object(graph) != graph_digest or graph.get("kind") != "graph":
            return None, "federated projection corrupt: graph object"
        catalog = _load_graph(graph, tuple(documents))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None, "federated projection corrupt"
    return catalog, "federated projection current"


def federated_projection_status(
    workspace: Workspace, current_projection: dict[str, Any]
) -> tuple[bool, str]:
    """Compare the selected generation with a just-built projection."""

    root = federated_cache_root(workspace)
    _assert_safe_cache_tree(workspace, root)
    if not (root / "current.json").is_file():
        return False, "federated projection absent"
    try:
        generation, _ = _current_manifest(workspace)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False, "federated projection corrupt"
    if generation != current_projection.get("generation"):
        return False, "federated projection stale"
    catalog, reason = load_verified_federated_projection(workspace)
    return catalog is not None, reason


def evaluate_federated_changes(workspace: Workspace) -> FederatedChangesReport:
    """Compare lightweight current source state with the selected manifest."""

    root = federated_cache_root(workspace)
    _assert_safe_cache_tree(workspace, root)
    if not (root / "current.json").is_file():
        return FederatedChangesReport("absent")
    try:
        _, manifest = _current_manifest(workspace)
        current = _source_states(workspace)
        previous = manifest.get("sources")
        if not isinstance(previous, dict):
            raise ValueError("manifest sources missing")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return FederatedChangesReport("unavailable")
    changes: list[FederatedSourceChange] = []
    for source in sorted(set(previous) | set(current)):
        if source not in previous:
            changes.append(FederatedSourceChange(source, "added"))
        elif source not in current:
            changes.append(FederatedSourceChange(source, "removed"))
        else:
            record = previous[source]
            state = current[source]
            if not isinstance(record, dict) or any(
                record.get(key) != state[key]
                for key in ("root", "visibility", "config_fingerprint", "inventory_hash")
            ):
                changes.append(FederatedSourceChange(source, "modified"))
    return FederatedChangesReport("compared", tuple(changes))
