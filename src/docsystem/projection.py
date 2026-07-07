"""Deterministic sharded projection derived exclusively from Markdown."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from docsystem.catalog import MarkdownCatalog, build_dependency_graph
from docsystem.config import ProjectConfig

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class DocumentChange:
    """One document-level change between a projection and current Markdown."""

    document_id: str
    kind: str
    sections: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChangesReport:
    """A deterministic snapshot of changes since the selected projection.

    `status` is `"absent"` when no projection has ever been written,
    `"unavailable"` when the selected generation cannot be read, or
    `"compared"` when `changes` reflects a real comparison (possibly empty).
    """

    status: str
    changes: tuple[DocumentChange, ...] = field(default_factory=tuple)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def cache_root(config: ProjectConfig) -> Path:
    return config.project_root / ".docsystem" / "cache"


def _shard(kind: str, document_id: str) -> Path:
    namespace, number = document_id.split("-", 1)
    bucket = f"{(int(number) // 100) * 100:06d}"
    return Path(kind) / namespace / bucket / f"{document_id}.json"


def build_projection(catalog: MarkdownCatalog) -> dict[str, Any]:
    graph = build_dependency_graph(catalog)
    documents: dict[str, Any] = {}
    for document in catalog.documents:
        if document.metadata is None:
            continue
        lines = document.content.splitlines()
        documents[document.metadata.document_id] = {
            "path": document.path.as_posix(),
            "revision": document.metadata.revision,
            "type": document.metadata.document_type,
            "status": document.metadata.status,
            "source_sha256": _sha(document.content),
            "sections": {
                section.anchor: {
                    "title": section.title,
                    "level": section.level,
                    "start_line": section.start_line,
                    "end_line": section.end_line,
                    "sha256": _sha(
                        "\n".join(lines[section.start_line - 1 : section.end_line])
                    ),
                }
                for section in document.sections
            },
            "dependencies": [
                {
                    "relation": edge.relation,
                    "target": edge.target_id,
                    "expected_revision": edge.expected_revision,
                }
                for edge in graph.outgoing(document.metadata.document_id)
            ],
            "boundaries": [
                {
                    "relation": item.relation,
                    "value": item.value,
                    "reason": item.reason,
                }
                for item in catalog.relation_boundaries
                if item.source_id == document.metadata.document_id
            ],
        }
    reverse = {
        document_id: [
            {
                "source": edge.source_id,
                "relation": edge.relation,
                "expected_revision": edge.expected_revision,
            }
            for edge in graph.incoming(document_id)
        ]
        for document_id in documents
    }
    payload = {
        "schema_version": SCHEMA_VERSION,
        "documents": dict(sorted(documents.items())),
        "reverse": {key: value for key, value in sorted(reverse.items()) if value},
    }
    payload["generation"] = _sha(_json(payload))
    return payload


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"projection JSON is not an object: {path}")
    return value


def projection_status(
    config: ProjectConfig, current: dict[str, Any]
) -> tuple[bool, str]:
    pointer = cache_root(config) / "current.json"
    if not pointer.is_file():
        return False, "projection absent"
    try:
        selected = _read(pointer)
        if selected.get("schema_version") != SCHEMA_VERSION:
            return False, "projection schema incompatible"
        generation = selected.get("generation")
        manifest = _read(
            cache_root(config) / "generations" / str(generation) / "manifest.json"
        )
        if generation != manifest.get("generation"):
            return False, "projection pointer mismatch"
        if generation != current.get("generation"):
            return False, "projection stale"
        generation_dir = cache_root(config) / "generations" / str(generation)
        for document_id in current["documents"]:
            shard = _read(generation_dir / _shard("documents", document_id))
            if (
                shard.get("schema_version") != SCHEMA_VERSION
                or shard.get("id") != document_id
            ):
                return False, f"projection document shard invalid: {document_id}"
        for document_id in current["reverse"]:
            shard = _read(generation_dir / _shard("reverse", document_id))
            if (
                shard.get("schema_version") != SCHEMA_VERSION
                or shard.get("id") != document_id
            ):
                return False, f"projection reverse shard invalid: {document_id}"
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return False, f"projection unreadable: {error}"
    return True, "projection current"


def write_projection(config: ProjectConfig, projection: dict[str, Any]) -> str:
    root = cache_root(config)
    generation = str(projection["generation"])
    generation_dir = root / "generations" / generation
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generation": generation,
        "documents": {
            key: {
                "path": value["path"],
                "source_sha256": value["source_sha256"],
                "sections": value["sections"],
            }
            for key, value in projection["documents"].items()
        },
    }
    generations = root / "generations"
    generations.mkdir(parents=True, exist_ok=True)
    if not generation_dir.exists():
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=generations))
        try:
            (staging / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            for document_id, record in projection["documents"].items():
                path = staging / _shard("documents", document_id)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(
                        {
                            "schema_version": SCHEMA_VERSION,
                            "id": document_id,
                            **record,
                        },
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            for document_id, incoming in projection["reverse"].items():
                path = staging / _shard("reverse", document_id)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(
                        {
                            "schema_version": SCHEMA_VERSION,
                            "id": document_id,
                            "incoming": incoming,
                        },
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            staging.replace(generation_dir)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=root, delete=False
    ) as handle:
        json.dump(
            {"schema_version": SCHEMA_VERSION, "generation": generation},
            handle,
            indent=2,
            sort_keys=True,
        )
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(root / "current.json")
    others = sorted(
        (
            path
            for path in generations.iterdir()
            if path.is_dir() and path != generation_dir
        ),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    for obsolete in others[max(0, config.keep_generations - 1) :]:
        shutil.rmtree(obsolete)
    return generation


def evaluate_changes(config: ProjectConfig, current: dict[str, Any]) -> ChangesReport:
    """Compare `current` against the selected projection generation, if any."""

    pointer = cache_root(config) / "current.json"
    if not pointer.is_file():
        return ChangesReport(status="absent")
    try:
        selected = _read(pointer)
        manifest = _read(
            cache_root(config)
            / "generations"
            / str(selected["generation"])
            / "manifest.json"
        )
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        return ChangesReport(status="unavailable")
    previous = manifest.get("documents", {})
    current_documents = current["documents"]
    document_changes: list[DocumentChange] = []
    for document_id in sorted(set(previous) | set(current_documents)):
        if document_id not in previous:
            document_changes.append(DocumentChange(document_id, "added"))
        elif document_id not in current_documents:
            document_changes.append(DocumentChange(document_id, "removed"))
        elif (
            previous[document_id].get("source_sha256")
            != current_documents[document_id].get("source_sha256")
        ):
            old_sections = previous[document_id].get("sections", {})
            new_sections = current_documents[document_id].get("sections", {})
            changed_sections = tuple(
                sorted(
                    anchor
                    for anchor in set(old_sections) | set(new_sections)
                    if old_sections.get(anchor, {}).get("sha256")
                    != new_sections.get(anchor, {}).get("sha256")
                )
            )
            document_changes.append(DocumentChange(document_id, "changed", changed_sections))
    return ChangesReport(status="compared", changes=tuple(document_changes))


def changes(config: ProjectConfig, current: dict[str, Any]) -> tuple[str, ...]:
    report = evaluate_changes(config, current)
    if report.status == "absent":
        return ("projection absent; every document is new",)
    if report.status == "unavailable":
        return ("projection unavailable; changes cannot be compared",)
    lines: list[str] = []
    for change in report.changes:
        lines.append(f"{change.kind}\t{change.document_id}")
        for anchor in change.sections:
            lines.append(f"section\t{change.document_id}#{anchor}")
    return tuple(lines) or ("no changes",)
