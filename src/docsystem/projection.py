"""Deterministic sharded projection derived exclusively from Markdown."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from docsystem.catalog import (
    MarkdownCatalog,
    build_dependency_graph,
    included_source_paths,
)
from docsystem.config import ProjectConfig
from docsystem.graph import (
    AUTHORED,
    GENERATED,
    Address,
    GraphEdge,
    build_reference_graph,
)

# Version 3 binds every document, reverse, reference, and reverse-reference
# shard hash into the immutable generation identity.  Version 2 generations
# therefore fail closed as incompatible and are rebuilt by `index --write`.
SCHEMA_VERSION = 3

# The observed-reference graph shard payload has its own version, while its
# hashes and presence remain part of the generation identity.
REFERENCE_SCHEMA_VERSION = 1


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


def config_fingerprint(config: ProjectConfig) -> str:
    """Return a deterministic fingerprint of projection-relevant config.

    A projection generation is only valid while the configuration that shaped
    it is unchanged, so this fingerprint is folded into the generation hash and
    recorded in the manifest. It covers every normalized field that affects
    catalog membership, metadata parsing/validation, section and navigation
    policy, dependency-graph semantics, or projection layout: the documentation
    root identity relative to the project, area and identifier maps, catalog
    exclusions, `navigation.extend_through`, `relations.legacy_paths`,
    `relations.snapshot_types`, `relations.snapshot_rules`, authored context
    views, the projection format, and the schema version.
    When any of these change the generation identity changes too, so
    `load_verified_projection` reports the generation stale and reads fall back
    to direct Markdown and normal validation instead of serving output that no
    longer matches the active configuration.
    """

    try:
        documentation_root = config.documentation_root.relative_to(
            config.project_root
        ).as_posix()
    except ValueError:
        documentation_root = config.documentation_root.as_posix()
    normalized = {
        "schema_version": SCHEMA_VERSION,
        "projection_format": config.projection_format,
        "documentation_root": documentation_root,
        "areas": {role: path.as_posix() for role, path in config.areas.items()},
        "identifiers": dict(config.identifiers),
        "catalog_exclusions": list(config.catalog_exclusions),
        "navigation_extend_through": list(config.navigation_extend_through),
        "legacy_relation_mode": config.legacy_relation_mode,
        "snapshot_document_types": list(config.snapshot_document_types),
        "snapshot_rules": [
            {
                "source_type": rule.source_type,
                "source_status": rule.source_status,
            }
            for rule in config.snapshot_rules
        ],
        "context_views": [
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
    }
    return _sha(_json(normalized))


def _shard(kind: str, document_id: str) -> Path:
    namespace, number = document_id.split("-", 1)
    bucket = f"{(int(number) // 100) * 100:06d}"
    return Path(kind) / namespace / bucket / f"{document_id}.json"


def build_projection(
    catalog: MarkdownCatalog, config: ProjectConfig
) -> dict[str, Any]:
    graph = build_dependency_graph(catalog)
    migrations_by_source: dict[str, list[dict[str, str]]] = {}
    for item in catalog.relation_migrations:
        migrations_by_source.setdefault(item.source_id, []).append(
            {
                "relation": item.relation,
                "value": item.value,
                "target": item.target_id,
            }
        )
    documents: dict[str, Any] = {}
    for document in catalog.documents:
        if document.metadata is None:
            continue
        lines = document.content.splitlines()
        related_values = [
            value
            for relation, value in document.metadata.legacy_references
            if relation == "related"
        ]
        related_values.extend(
            reference.target_id
            for reference in document.metadata.references
            if reference.relation == "related"
        )
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
            "migrations": migrations_by_source.get(
                document.metadata.document_id, []
            ),
            "related_values": related_values,
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
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "config_fingerprint": config_fingerprint(config),
        "documents": dict(sorted(documents.items())),
        "reverse": {key: value for key, value in sorted(reverse.items()) if value},
    }
    payload["references"], payload["reverse_references"] = _build_reference_shards(
        catalog, config, documents
    )
    payload["generation"] = _manifest_generation(_projection_manifest(payload))
    return payload


def _build_reference_shards(
    catalog: MarkdownCatalog, config: ProjectConfig, documents: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    graph = build_reference_graph(catalog, config)
    forward_by_doc: dict[str, list[dict[str, Any]]] = {}
    boundaries_by_doc: dict[str, list[dict[str, Any]]] = {}
    reverse_by_doc: dict[str, list[dict[str, Any]]] = {}
    for edge in graph.edges:
        if edge.authority != "observed":
            # Authored dependencies are already carried by the existing
            # `documents`/`reverse` shards; generated containment is derivable
            # from a document shard's `sections` map. Only the new observed
            # Markdown-reference layer needs dedicated storage.
            continue
        forward_by_doc.setdefault(edge.source.document_id, []).append(
            {
                "source_anchor": edge.source.anchor,
                "relation": edge.relation,
                "authority": edge.authority,
                "origin": edge.origin,
                "target": edge.target.document_id,
                "target_anchor": edge.target.anchor,
                "reason": edge.reason,
            }
        )
        reverse_by_doc.setdefault(edge.target.document_id, []).append(
            {
                "source": edge.source.document_id,
                "source_anchor": edge.source.anchor,
                "target_anchor": edge.target.anchor,
                "relation": edge.relation,
                "authority": edge.authority,
                "origin": edge.origin,
                "reason": edge.reason,
            }
        )
    for boundary in graph.boundaries:
        boundaries_by_doc.setdefault(boundary.source.document_id, []).append(
            {
                "source_anchor": boundary.source.anchor,
                "raw_target": boundary.raw_target,
                "category": boundary.category,
                "reason": boundary.reason,
            }
        )

    def _sort_forward(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            items,
            key=lambda item: (
                item["source_anchor"] or "",
                item["target"],
                item["target_anchor"] or "",
                item["reason"] or "",
            ),
        )

    def _sort_boundaries(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            items,
            key=lambda item: (
                item["source_anchor"] or "",
                item["category"],
                item["raw_target"],
            ),
        )

    def _sort_incoming(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            items,
            key=lambda item: (
                item["target_anchor"] or "",
                item["source"],
                item["source_anchor"] or "",
                item["reason"] or "",
            ),
        )

    references = {
        document_id: {
            "path": record["path"],
            "source_sha256": record["source_sha256"],
            "forward": _sort_forward(forward_by_doc.get(document_id, [])),
            "boundaries": _sort_boundaries(boundaries_by_doc.get(document_id, [])),
        }
        for document_id, record in documents.items()
    }
    reverse_references = {
        document_id: {"incoming": _sort_incoming(incoming)}
        for document_id, incoming in reverse_by_doc.items()
        if incoming
    }
    return references, reverse_references


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
        if not isinstance(generation, str) or manifest.get("generation") != generation:
            return False, "projection pointer mismatch"
        if not _manifest_is_bound(manifest, generation):
            return False, "projection corrupt"
        if generation != current.get("generation"):
            return False, "projection stale"
        generation_dir = cache_root(config) / "generations" / str(generation)
        for document_id in current["documents"]:
            shard = _read(generation_dir / _shard("documents", document_id))
            record = manifest.get("documents", {}).get(document_id, {})
            if (
                shard.get("schema_version") != SCHEMA_VERSION
                or shard.get("id") != document_id
                or not _verify_shard_hash(shard, record.get("shard_sha256"))
            ):
                return False, f"projection document shard invalid: {document_id}"
        for document_id in current["reverse"]:
            shard = _read(generation_dir / _shard("reverse", document_id))
            record = manifest.get("reverse", {}).get(document_id, {})
            if (
                shard.get("schema_version") != SCHEMA_VERSION
                or shard.get("id") != document_id
                or not _verify_shard_hash(shard, record.get("shard_sha256"))
            ):
                return False, f"projection reverse shard invalid: {document_id}"
        for kind, manifest_key, schema_version in (
            ("references", "references", REFERENCE_SCHEMA_VERSION),
            ("reverse-references", "reverse_references", REFERENCE_SCHEMA_VERSION),
        ):
            records = manifest.get(manifest_key)
            if not isinstance(records, dict):
                return False, f"projection {kind} manifest invalid"
            for document_id, record in records.items():
                shard = _read(generation_dir / _shard(kind, document_id))
                if (
                    shard.get("schema_version") != schema_version
                    or shard.get("id") != document_id
                    or not _verify_shard_hash(shard, record.get("shard_sha256"))
                ):
                    return False, f"projection {kind} shard invalid: {document_id}"
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return False, f"projection unreadable: {error}"
    return True, "projection current"


@dataclass(frozen=True)
class LoadedProjection:
    """A hash-verified projection generation ready to serve read commands."""

    generation: str
    documents: dict[str, dict[str, Any]]
    reverse: dict[str, tuple[dict[str, Any], ...]]
    contents: dict[str, str]


def load_verified_projection(
    config: ProjectConfig,
) -> tuple[LoadedProjection | None, str]:
    """Verify the selected generation against current sources and load it.

    Verification re-reads every included source file and compares its sha256
    with the generation manifest, so a served read can never disagree with
    the Markdown truth; what the fast path removes is Markdown, metadata and
    link parsing plus graph reconstruction, not source I/O. It also rejects
    the generation when the active configuration fingerprint no longer
    matches the one recorded at build time, so a read-time policy change
    (for example `relations.legacy_paths` or `navigation.extend_through`)
    forces a rebuild instead of serving stale, differently-shaped output.
    Every consumed document and reverse shard is checked against a hash in the
    manifest.  The manifest itself is the generation's content-addressed root,
    so changing either a shard hash or any manifest record invalidates the
    selected generation before output is produced. On any mismatch the caller
    receives `(None, reason)` and falls back to direct Markdown with a
    diagnostic.
    """

    pointer = cache_root(config) / "current.json"
    if not pointer.is_file():
        return None, "projection absent"
    try:
        selected = _read(pointer)
        if selected.get("schema_version") != SCHEMA_VERSION:
            return None, "projection schema incompatible"
        generation = str(selected.get("generation"))
        generation_dir = cache_root(config) / "generations" / generation
        manifest = _read(generation_dir / "manifest.json")
        if manifest.get("generation") != generation:
            return None, "projection pointer mismatch"
        if not _manifest_is_bound(manifest, generation):
            return None, "projection corrupt"
        if manifest.get("config_fingerprint") != config_fingerprint(config):
            return None, "projection stale: configuration changed"
        manifest_documents = manifest.get("documents")
        if not isinstance(manifest_documents, dict):
            return None, "projection unreadable: manifest documents missing"
        manifest_paths = {
            str(record.get("path")): document_id
            for document_id, record in manifest_documents.items()
        }
        included = included_source_paths(config)
        if {path.as_posix() for path in included} != set(manifest_paths):
            return None, "projection stale"
        contents: dict[str, str] = {}
        for relative in included:
            text = (config.documentation_root / relative).read_text(encoding="utf-8")
            document_id = manifest_paths[relative.as_posix()]
            if _sha(text) != manifest_documents[document_id].get("source_sha256"):
                return None, "projection stale"
            contents[relative.as_posix()] = text
        documents: dict[str, dict[str, Any]] = {}
        for document_id in manifest_documents:
            shard = _read(generation_dir / _shard("documents", document_id))
            record = manifest_documents[document_id]
            if (
                shard.get("schema_version") != SCHEMA_VERSION
                or shard.get("id") != document_id
                or not _verify_shard_hash(
                    shard, record.get("shard_sha256")
                )
            ):
                return None, f"projection document shard invalid: {document_id}"
            if (
                shard.get("path") != record.get("path")
                or shard.get("source_sha256") != record.get("source_sha256")
            ):
                # The freshness decision above trusts the manifest's per-source
                # path and hash; binding them to the generation-verified shard
                # keeps a manifest-only edit from masking stale shard data.
                return None, "projection manifest mismatch"
            documents[document_id] = shard
        reverse: dict[str, tuple[dict[str, Any], ...]] = {}
        targets = {
            dependency["target"]
            for shard in documents.values()
            for dependency in shard.get("dependencies", ())
        }
        for document_id in sorted(targets):
            shard = _read(generation_dir / _shard("reverse", document_id))
            if (
                shard.get("schema_version") != SCHEMA_VERSION
                or shard.get("id") != document_id
                or not _verify_shard_hash(
                    shard,
                    manifest.get("reverse", {})
                    .get(document_id, {})
                    .get("shard_sha256"),
                )
            ):
                return None, f"projection reverse shard invalid: {document_id}"
            reverse[document_id] = tuple(shard.get("incoming", ()))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        return None, f"projection unreadable: {error}"
    return LoadedProjection(generation, documents, reverse, contents), "projection current"


def _body_hash(body: dict[str, Any]) -> str:
    content = {
        key: value for key, value in body.items() if key not in ("schema_version", "id")
    }
    return _sha(_json(content))


def _projection_manifest(projection: dict[str, Any]) -> dict[str, Any]:
    """Build the complete hash manifest whose digest is the generation ID."""

    documents = {
        document_id: {
            "path": record["path"],
            "source_sha256": record["source_sha256"],
            "sections": record["sections"],
            "shard_sha256": _body_hash(record),
        }
        for document_id, record in sorted(projection["documents"].items())
    }
    reverse = {
        document_id: {"shard_sha256": _body_hash({"incoming": incoming})}
        for document_id, incoming in sorted(projection["reverse"].items())
    }
    references = {
        document_id: {
            "path": record["path"],
            "source_sha256": record["source_sha256"],
            "shard_sha256": _body_hash(record),
        }
        for document_id, record in sorted(projection.get("references", {}).items())
    }
    reverse_references = {
        document_id: {"shard_sha256": _body_hash(record)}
        for document_id, record in sorted(
            projection.get("reverse_references", {}).items()
        )
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "config_fingerprint": projection["config_fingerprint"],
        "documents": documents,
        "reverse": reverse,
        "references": references,
        "reverse_references": reverse_references,
    }


def _manifest_generation(manifest: dict[str, Any]) -> str:
    identity = {key: value for key, value in manifest.items() if key != "generation"}
    return _sha(_json(identity))


def _manifest_is_bound(manifest: dict[str, Any], generation: str) -> bool:
    return (
        manifest.get("schema_version") == SCHEMA_VERSION
        and manifest.get("generation") == generation
        and _manifest_generation(manifest) == generation
    )


def _write_shard(staging: Path, kind: str, document_id: str, body: dict[str, Any]) -> str:
    """Write one shard and return the sha256 of its content (envelope stripped)."""

    path = staging / _shard(kind, document_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return _body_hash(body)


def write_projection(config: ProjectConfig, projection: dict[str, Any]) -> str:
    root = cache_root(config)
    generation = str(projection["generation"])
    generation_dir = root / "generations" / generation
    generations = root / "generations"
    generations.mkdir(parents=True, exist_ok=True)
    if not generation_dir.exists():
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=generations))
        try:
            manifest = _projection_manifest(projection)
            manifest["generation"] = generation
            if _manifest_generation(manifest) != generation:
                raise ValueError("projection generation does not match shard manifest")
            for document_id, record in projection["documents"].items():
                _write_shard(
                    staging,
                    "documents",
                    document_id,
                    {"schema_version": SCHEMA_VERSION, "id": document_id, **record},
                )

            for document_id, incoming in projection["reverse"].items():
                _write_shard(
                    staging,
                    "reverse",
                    document_id,
                    {
                        "schema_version": SCHEMA_VERSION,
                        "id": document_id,
                        "incoming": incoming,
                    },
                )

            for document_id, record in projection.get("references", {}).items():
                _write_shard(
                    staging,
                    "references",
                    document_id,
                    {
                        "schema_version": REFERENCE_SCHEMA_VERSION,
                        "id": document_id,
                        **record,
                    },
                )

            for document_id, record in projection.get("reverse_references", {}).items():
                _write_shard(
                    staging,
                    "reverse-references",
                    document_id,
                    {
                        "schema_version": REFERENCE_SCHEMA_VERSION,
                        "id": document_id,
                        **record,
                    },
                )

            (staging / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
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
            if path.is_dir()
            and path != generation_dir
            and not path.name.startswith(".staging-")
        ),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    for obsolete in others[max(0, config.keep_generations - 1) :]:
        shutil.rmtree(obsolete)
    return generation


def resolve_generation_manifest(
    config: ProjectConfig, selector: str
) -> tuple[str, dict[str, Any]] | None:
    """Resolve and verify a `--since` generation.

    A selector is accepted when it is a full retained generation hash or an
    unambiguous prefix of at least 12 characters that matches exactly one
    retained generation. The manifest is then bound to every document and
    reverse shard, the active configuration fingerprint and the reconstructed
    generation hash before any recorded source hash can authorize an omission.
    The returned `documents` mapping contains the verified full document
    shards rather than the manifest summaries, so delta callers can compare
    semantic metadata as well as section hashes.

    Anything shorter, ambiguous, unknown, incompatible, corrupt or unreadable
    returns `None`, so the caller fails closed with a single deterministic
    error. Retention staging directories are ignored, matching projection
    write semantics.
    """

    if not isinstance(selector, str) or len(selector) < 12:
        return None
    generations_dir = cache_root(config) / "generations"
    try:
        names = [
            path.name
            for path in generations_dir.iterdir()
            if path.is_dir() and not path.name.startswith(".staging-")
        ]
    except OSError:
        return None
    matches = sorted({name for name in names if name.startswith(selector)})
    if len(matches) != 1:
        return None
    generation = matches[0]
    try:
        generation_dir = generations_dir / generation
        manifest = _read(generation_dir / "manifest.json")
        if manifest.get("schema_version") != SCHEMA_VERSION:
            return None
        if not _manifest_is_bound(manifest, generation):
            return None
        if manifest.get("config_fingerprint") != config_fingerprint(config):
            return None
        manifest_documents = manifest.get("documents")
        if not isinstance(manifest_documents, dict):
            return None

        documents: dict[str, dict[str, Any]] = {}
        for document_id, record in manifest_documents.items():
            if not isinstance(document_id, str) or not isinstance(record, dict):
                return None
            shard = _read(generation_dir / _shard("documents", document_id))
            if (
                shard.get("schema_version") != SCHEMA_VERSION
                or shard.get("id") != document_id
                or shard.get("path") != record.get("path")
                or shard.get("source_sha256") != record.get("source_sha256")
                or shard.get("sections") != record.get("sections")
                or not _verify_shard_hash(shard, record.get("shard_sha256"))
            ):
                return None
            documents[document_id] = shard

        reverse: dict[str, tuple[dict[str, Any], ...]] = {}
        targets = {
            dependency["target"]
            for shard in documents.values()
            for dependency in shard.get("dependencies", ())
        }
        for document_id in sorted(targets):
            shard = _read(generation_dir / _shard("reverse", document_id))
            if (
                shard.get("schema_version") != SCHEMA_VERSION
                or shard.get("id") != document_id
                or not isinstance(shard.get("incoming"), list)
                or not _verify_shard_hash(
                    shard,
                    manifest.get("reverse", {})
                    .get(document_id, {})
                    .get("shard_sha256"),
                )
            ):
                return None
            reverse[document_id] = tuple(shard["incoming"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return generation, {**manifest, "documents": documents}


def evaluate_changes(config: ProjectConfig, current: dict[str, Any]) -> ChangesReport:
    """Compare `current` against the selected projection generation, if any."""

    pointer = cache_root(config) / "current.json"
    if not pointer.is_file():
        return ChangesReport(status="absent")
    try:
        selected = _read(pointer)
        if selected.get("schema_version") != SCHEMA_VERSION:
            return ChangesReport(status="unavailable")
        generation = selected.get("generation")
        if not isinstance(generation, str):
            return ChangesReport(status="unavailable")
        manifest = _read(
            cache_root(config)
            / "generations"
            / generation
            / "manifest.json"
        )
        if not _manifest_is_bound(manifest, generation):
            return ChangesReport(status="unavailable")
        if manifest.get("config_fingerprint") != config_fingerprint(config):
            return ChangesReport(status="unavailable")
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        return ChangesReport(status="unavailable")
    previous = manifest.get("documents", {})
    if not isinstance(previous, dict):
        return ChangesReport(status="unavailable")
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


def _verify_shard_hash(shard: dict[str, Any], expected: object) -> bool:
    if not isinstance(expected, str):
        return False
    body = {key: value for key, value in shard.items() if key not in ("schema_version", "id")}
    return _sha(_json(body)) == expected


@dataclass
class TargetedProjection:
    """A verified generation opened for narrow, per-document shard access.

    Unlike `load_verified_projection`, opening this does not read every
    document/reverse/reference shard: `document`, `incoming`, `references`
    and `reverse_references` each read and verify exactly one shard the first
    time it is requested, recording it in `read_shards` as evidence that
    unrelated shards were never touched.
    """

    generation_dir: Path
    manifest: dict[str, Any]
    read_shards: set[tuple[str, str]] = field(default_factory=set)

    def document(self, document_id: str) -> dict[str, Any] | None:
        record = self.manifest.get("documents", {}).get(document_id)
        if record is None:
            return None
        try:
            shard = _read(self.generation_dir / _shard("documents", document_id))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if (
            shard.get("schema_version") != SCHEMA_VERSION
            or shard.get("id") != document_id
            or shard.get("path") != record.get("path")
            or shard.get("source_sha256") != record.get("source_sha256")
            or not _verify_shard_hash(shard, record.get("shard_sha256"))
        ):
            return None
        self.read_shards.add(("documents", document_id))
        return shard

    def incoming(self, document_id: str) -> tuple[dict[str, Any], ...] | None:
        record = self.manifest.get("reverse", {}).get(document_id)
        if record is None:
            return ()
        try:
            shard = _read(self.generation_dir / _shard("reverse", document_id))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if (
            shard.get("schema_version") != SCHEMA_VERSION
            or shard.get("id") != document_id
            or not _verify_shard_hash(shard, record.get("shard_sha256"))
        ):
            return None
        self.read_shards.add(("reverse", document_id))
        incoming = shard.get("incoming")
        if not isinstance(incoming, list):
            return None
        return tuple(incoming)

    def references(
        self, document_id: str
    ) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]] | None:
        record = self.manifest.get("references", {}).get(document_id)
        if record is None:
            return (), ()
        try:
            shard = _read(self.generation_dir / _shard("references", document_id))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if (
            shard.get("schema_version") != REFERENCE_SCHEMA_VERSION
            or shard.get("id") != document_id
            or shard.get("path") != record.get("path")
            or shard.get("source_sha256") != record.get("source_sha256")
            or not _verify_shard_hash(shard, record.get("shard_sha256"))
        ):
            return None
        forward = shard.get("forward")
        boundaries = shard.get("boundaries")
        if not isinstance(forward, list) or not isinstance(boundaries, list):
            return None
        self.read_shards.add(("references", document_id))
        return tuple(forward), tuple(boundaries)

    def reverse_references(self, document_id: str) -> tuple[dict[str, Any], ...] | None:
        record = self.manifest.get("reverse_references", {}).get(document_id)
        if record is None:
            return ()
        try:
            shard = _read(self.generation_dir / _shard("reverse-references", document_id))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if (
            shard.get("schema_version") != REFERENCE_SCHEMA_VERSION
            or shard.get("id") != document_id
            or not _verify_shard_hash(shard, record.get("shard_sha256"))
        ):
            return None
        incoming = shard.get("incoming")
        if not isinstance(incoming, list):
            return None
        self.read_shards.add(("reverse-references", document_id))
        return tuple(incoming)


def open_targeted_projection(config: ProjectConfig) -> tuple[TargetedProjection | None, str]:
    """Verify pointer/schema/config/complete-source-freshness for targeted reads.

    This proves the same freshness guarantee as `load_verified_projection`
    (every included source's sha256 matches the manifest) without reading any
    document, reverse or reference *shard*; callers then fetch only the
    shards their query actually needs through the returned accessor.
    """

    pointer = cache_root(config) / "current.json"
    if not pointer.is_file():
        return None, "projection absent"
    try:
        selected = _read(pointer)
        if selected.get("schema_version") != SCHEMA_VERSION:
            return None, "projection schema incompatible"
        generation = str(selected.get("generation"))
        generation_dir = cache_root(config) / "generations" / generation
        manifest = _read(generation_dir / "manifest.json")
        if manifest.get("generation") != generation:
            return None, "projection pointer mismatch"
        if not _manifest_is_bound(manifest, generation):
            return None, "projection corrupt"
        if manifest.get("config_fingerprint") != config_fingerprint(config):
            return None, "projection stale: configuration changed"
        manifest_documents = manifest.get("documents")
        if not isinstance(manifest_documents, dict):
            return None, "projection unreadable: manifest documents missing"
        manifest_paths = {
            str(record.get("path")): document_id
            for document_id, record in manifest_documents.items()
        }
        included = included_source_paths(config)
        if {path.as_posix() for path in included} != set(manifest_paths):
            return None, "projection stale"
        for relative in included:
            text = (config.documentation_root / relative).read_text(encoding="utf-8")
            document_id = manifest_paths[relative.as_posix()]
            if _sha(text) != manifest_documents[document_id].get("source_sha256"):
                return None, "projection stale"
        if not isinstance(manifest.get("references"), dict) or not isinstance(
            manifest.get("reverse_references"), dict
        ):
            return None, "projection incompatible: reference graph shards missing"
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        return None, f"projection unreadable: {error}"
    return TargetedProjection(generation_dir, manifest), "projection current"


def targeted_forward_edges(
    accessor: TargetedProjection, address: Address
) -> tuple[tuple[GraphEdge, ...], tuple[dict[str, Any], ...]] | None:
    """Outgoing contains/authored/observed edges for one address, targeted.

    Mirrors `build_reference_graph` exactly: `contains` and authored metadata
    edges only ever originate from the bare document address (metadata
    references and section containment are not anchor-specific), while
    observed edges originate from whichever section anchor contained the
    Markdown link -- including `None` for links above the first heading.
    """

    document_shard = accessor.document(address.document_id)
    if document_shard is None:
        return None
    graph_refs = accessor.references(address.document_id)
    if graph_refs is None:
        return None
    forward, boundaries = graph_refs
    edges: list[GraphEdge] = []
    if address.anchor is None:
        edges.extend(
            GraphEdge(
                address, Address(address.document_id, anchor), "contains", GENERATED,
                "section-parser",
            )
            for anchor in document_shard.get("sections", {})
        )
        edges.extend(
            GraphEdge(
                address,
                Address(dependency["target"], None),
                dependency["relation"],
                AUTHORED,
                "metadata",
                pin=dependency.get("expected_revision"),
            )
            for dependency in document_shard.get("dependencies", ())
        )
    edges.extend(
        GraphEdge(
            Address(address.document_id, reference.get("source_anchor")),
            Address(reference["target"], reference.get("target_anchor")),
            reference["relation"],
            reference["authority"],
            reference["origin"],
            reason=reference.get("reason"),
        )
        for reference in forward
        if reference.get("source_anchor") == address.anchor
    )
    boundaries_for_address = tuple(
        boundary for boundary in boundaries if boundary.get("source_anchor") == address.anchor
    )
    return tuple(edges), boundaries_for_address


def targeted_reverse_edges(
    accessor: TargetedProjection, address: Address
) -> tuple[GraphEdge, ...] | None:
    """Incoming authored/observed edges for one address, targeted.

    Authored dependency edges only ever target a bare document address;
    a section address's only structural incoming edge is its own document's
    `contains` edge, which is synthesized here rather than cached.
    """

    edges: list[GraphEdge] = []
    if address.anchor is None:
        incoming_dependencies = accessor.incoming(address.document_id)
        if incoming_dependencies is None:
            return None
        edges.extend(
            GraphEdge(
                Address(dependency["source"], None),
                address,
                dependency["relation"],
                AUTHORED,
                "metadata",
                pin=dependency.get("expected_revision"),
            )
            for dependency in incoming_dependencies
        )
    else:
        document_shard = accessor.document(address.document_id)
        if document_shard is None:
            return None
        if address.anchor in document_shard.get("sections", {}):
            edges.append(
                GraphEdge(
                    Address(address.document_id, None), address, "contains", GENERATED,
                    "section-parser",
                )
            )
    incoming_references = accessor.reverse_references(address.document_id)
    if incoming_references is None:
        return None
    edges.extend(
        GraphEdge(
            Address(reference["source"], reference.get("source_anchor")),
            address,
            reference["relation"],
            reference["authority"],
            reference["origin"],
            reason=reference.get("reason"),
        )
        for reference in incoming_references
        if reference.get("target_anchor") == address.anchor
    )
    return tuple(edges)
