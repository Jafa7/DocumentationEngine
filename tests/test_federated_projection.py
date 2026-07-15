import json
from pathlib import Path

from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG
from docsystem.federated_projection import (
    build_current_federated_projection,
    build_federated_projection,
    evaluate_federated_changes,
    federated_cache_root,
    federated_projection_status,
    load_verified_federated_projection,
    write_federated_projection,
)
from docsystem.federation import build_federated_catalog
from docsystem.workspace import WORKSPACE_FILENAME, load_workspace

PROJECT_CONFIG = DEFAULT_CONFIG.replace(
    "[areas]\n", '[areas]\ndocumentation = "."\n'
)


def _write_source(root: Path, document_id: str, body: str) -> None:
    plan = root / "plan"
    plan.mkdir(parents=True)
    (root / CONFIG_FILENAME).write_text(PROJECT_CONFIG, encoding="utf-8")
    (plan / "README.md").write_text(
        "---\n"
        f"id: {document_id}\n"
        "revision: 1\n"
        "---\n\n"
        f"# {document_id}\n\n{body}\n\n## Details\n\nDetail.\n",
        encoding="utf-8",
    )


def _workspace(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / WORKSPACE_FILENAME).write_text(
        "version = 1\n\n"
        "[[sources]]\n"
        'name = "alpha"\n'
        'root = "sources/alpha"\n'
        'visibility = "private"\n\n'
        "[[sources]]\n"
        'name = "beta"\n'
        'root = "sources/beta"\n'
        'visibility = "public"\n',
        encoding="utf-8",
    )
    _write_source(root / "sources" / "alpha", "DOC-001", "Alpha.")
    _write_source(root / "sources" / "beta", "DOC-002", "Beta.")
    return load_workspace(root)


def _projection(workspace):
    catalog = build_federated_catalog(workspace)
    return catalog, build_federated_projection(workspace, catalog)


def _artifact_text(root: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*.json"))
    )


def test_projection_is_deterministic_portable_and_rehydrates_semantics(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    direct, first = _projection(workspace)
    _, second = _projection(workspace)

    assert first == second
    generation = write_federated_projection(workspace, first)
    loaded, reason = load_verified_federated_projection(workspace)

    assert generation == first["generation"]
    assert reason == "federated projection current"
    assert loaded == direct
    artifacts = _artifact_text(federated_cache_root(workspace))
    assert str(tmp_path) not in artifacts
    assert not (workspace.sources[0].project_root / ".docsystem").exists()
    assert not (workspace.sources[1].project_root / ".docsystem").exists()


def test_unchanged_source_object_is_reused_when_one_source_changes(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    _, first = _projection(workspace)
    write_federated_projection(workspace, first)
    first_sources = first["manifest"]["sources"]
    alpha_object = first_sources["alpha"]["object"]
    alpha_path = (
        federated_cache_root(workspace)
        / "objects"
        / alpha_object[:2]
        / f"{alpha_object}.json"
    )
    alpha_stat = alpha_path.stat().st_mtime_ns

    beta = workspace.root / "sources" / "beta" / "plan" / "README.md"
    beta.write_text(beta.read_text(encoding="utf-8") + "\nChanged.\n", encoding="utf-8")
    direct, second = _projection(workspace)
    write_federated_projection(workspace, second)

    assert second["manifest"]["sources"]["alpha"]["object"] == alpha_object
    assert alpha_path.stat().st_mtime_ns == alpha_stat
    assert (
        second["manifest"]["sources"]["beta"]["object"]
        != first_sources["beta"]["object"]
    )
    loaded, _ = load_verified_federated_projection(workspace)
    assert loaded == direct


def test_source_change_is_stale_and_reported_without_reparsing(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    _, projection = _projection(workspace)
    write_federated_projection(workspace, projection)

    source = workspace.root / "sources" / "alpha" / "plan" / "README.md"
    source.write_text(source.read_text(encoding="utf-8") + "\nDrift.\n", encoding="utf-8")

    loaded, reason = load_verified_federated_projection(workspace)
    report = evaluate_federated_changes(workspace)
    assert loaded is None
    assert reason == "federated projection stale: source alpha changed"
    assert [(item.source, item.kind) for item in report.changes] == [
        ("alpha", "modified")
    ]


def test_workspace_manifest_config_and_membership_changes_are_stale(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    _, projection = _projection(workspace)
    write_federated_projection(workspace, projection)

    manifest = workspace.root / WORKSPACE_FILENAME
    manifest.write_text(manifest.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    changed_workspace = load_workspace(workspace.root)
    assert load_verified_federated_projection(changed_workspace)[1] == (
        "federated projection stale: workspace manifest changed"
    )

    # Re-publish after the formatting-only manifest change, then change config.
    _, current = _projection(changed_workspace)
    write_federated_projection(changed_workspace, current)
    config = workspace.root / "sources" / "alpha" / CONFIG_FILENAME
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "extend_through = []", 'extend_through = ["details"]'
        ),
        encoding="utf-8",
    )
    assert load_verified_federated_projection(changed_workspace)[1] == (
        "federated projection stale: source alpha changed"
    )

    # Membership/visibility is bound separately from source contents.
    config.write_text(PROJECT_CONFIG, encoding="utf-8")
    _, current = _projection(changed_workspace)
    write_federated_projection(changed_workspace, current)
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            'visibility = "public"', 'visibility = "private"'
        ),
        encoding="utf-8",
    )
    visibility_workspace = load_workspace(workspace.root)
    assert load_verified_federated_projection(visibility_workspace)[1] == (
        "federated projection stale: workspace manifest changed"
    )


def test_corrupt_object_fails_closed_and_status_checks_current(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    _, projection = _projection(workspace)
    write_federated_projection(workspace, projection)
    assert federated_projection_status(workspace, projection) == (
        True,
        "federated projection current",
    )

    digest = projection["manifest"]["graph_object"]
    graph_path = (
        federated_cache_root(workspace)
        / "objects"
        / digest[:2]
        / f"{digest}.json"
    )
    graph_path.write_text('{"kind": "graph"}\n', encoding="utf-8")

    loaded, reason = load_verified_federated_projection(workspace)
    assert loaded is None
    assert reason == "federated projection corrupt: graph object"
    assert evaluate_federated_changes(workspace).status == "compared"

    # Re-publishing the same content repairs a corrupt immutable object rather
    # than trusting its filename alone.
    write_federated_projection(workspace, projection)
    repaired, repaired_reason = load_verified_federated_projection(workspace)
    assert repaired is not None
    assert repaired_reason == "federated projection current"

    manifest_path = (
        federated_cache_root(workspace)
        / "generations"
        / projection["generation"]
        / "manifest.json"
    )
    manifest_path.write_text('{"generation": "broken"}\n', encoding="utf-8")
    assert load_verified_federated_projection(workspace)[1] == (
        "federated projection corrupt"
    )
    write_federated_projection(workspace, projection)
    assert load_verified_federated_projection(workspace)[1] == (
        "federated projection current"
    )


def test_current_builder_rejects_mixed_source_snapshot(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = _workspace(tmp_path)
    source = workspace.root / "sources" / "alpha" / "plan" / "README.md"
    original = build_federated_catalog

    def build_then_change(value):
        catalog = original(value)
        source.write_text(
            source.read_text(encoding="utf-8") + "\nChanged during build.\n",
            encoding="utf-8",
        )
        return catalog

    monkeypatch.setattr(
        "docsystem.federated_projection.build_federated_catalog",
        build_then_change,
    )
    try:
        build_current_federated_projection(workspace)
    except ValueError as error:
        assert str(error) == "federated sources changed during projection build"
    else:
        raise AssertionError("mixed source snapshot must not be published")


def test_workspace_cache_rejects_symlink_escape(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace.root / ".docsystem").symlink_to(outside, target_is_directory=True)
    _, projection = _projection(workspace)

    try:
        write_federated_projection(workspace, projection)
    except ValueError as error:
        assert "symlink" in str(error)
    else:
        raise AssertionError("symlinked workspace cache must be rejected")
    assert not (outside / "federated-cache").exists()


def test_retention_removes_old_generations_and_unreferenced_objects(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    generations = []
    projections = []
    source = workspace.root / "sources" / "beta" / "plan" / "README.md"
    for suffix in ("", "Second.", "Third."):
        if suffix:
            source.write_text(
                source.read_text(encoding="utf-8") + f"\n{suffix}\n",
                encoding="utf-8",
            )
        _, projection = _projection(workspace)
        projections.append(projection)
        generations.append(write_federated_projection(workspace, projection))

    root = federated_cache_root(workspace)
    staging = root / "generations" / ".staging-interrupted"
    staging.mkdir()
    (staging / "partial.json").write_text("{}\n", encoding="utf-8")
    write_federated_projection(workspace, projections[-1])
    retained = sorted(item.name for item in (root / "generations").iterdir())
    assert staging.is_dir()
    retained.remove(staging.name)
    assert generations[0] not in retained
    assert set(retained) == set(generations[1:])
    assert json.loads((root / "current.json").read_text())["generation"] == (
        generations[-1]
    )
    first_beta = projections[0]["manifest"]["sources"]["beta"]["object"]
    first_beta_path = root / "objects" / first_beta[:2] / f"{first_beta}.json"
    shared_alpha = projections[0]["manifest"]["sources"]["alpha"]["object"]
    shared_alpha_path = root / "objects" / shared_alpha[:2] / f"{shared_alpha}.json"
    assert not first_beta_path.exists()
    assert shared_alpha_path.is_file()
