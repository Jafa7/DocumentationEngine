import json
import shutil
from pathlib import Path

import pytest

from docsystem.cli import main
from docsystem.config import CONFIG_FILENAME
from docsystem.workspace import WORKSPACE_FILENAME

MAINTENANCE = """
[[maintenance]]
name = "shared-value"
source_document = "DOC-001"
source_anchor = "source"

[[maintenance.occurrences]]
document = "DOC-002"
anchor = "current"
role = "current"
"""


def _run(monkeypatch: pytest.MonkeyPatch, *arguments: str) -> int:
    monkeypatch.setattr("sys.argv", ["docsystem", *arguments])
    return main()


def _profile(root: Path, *, drifted: bool = True) -> None:
    root.mkdir(parents=True)
    (root / CONFIG_FILENAME).write_text(
        """version = 1

[documentation]
root = "plan"
language = "en"

[areas]
documentation = "."

[identifiers]
document = "DOC"

[projection]
format = "sharded-json"
keep_generations = 2
"""
        + MAINTENANCE,
        encoding="utf-8",
    )
    plan = root / "plan"
    plan.mkdir()
    (plan / "README.md").write_text(
        "---\nid: DOC-003\nrevision: 1\n---\n# Index\n\n"
        "- [Source](source.md)\n- [Current](current.md)\n",
        encoding="utf-8",
    )
    (plan / "source.md").write_text(
        "---\nid: DOC-001\nrevision: 1\n---\n# Source document\n\n"
        '<a id="source"></a>\n## Source value\n\n'
        "<!-- docsystem:source target=shared-value -->\n"
        "current value\n"
        "<!-- /docsystem:source target=shared-value -->\n",
        encoding="utf-8",
    )
    value = "old value" if drifted else "current value"
    (plan / "current.md").write_text(
        "---\nid: DOC-002\nrevision: 1\n---\n# Current document\n\n"
        '<a id="current"></a>\n## Current value\n\n'
        "<!-- docsystem:managed target=shared-value -->\n"
        f"{value}\n"
        "<!-- /docsystem:managed target=shared-value -->\n",
        encoding="utf-8",
    )


def _workspace(
    tmp_path: Path,
    *,
    alpha_write: str = "managed-maintenance",
    beta_write: str | None = None,
) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / WORKSPACE_FILENAME).write_text(
        "version = 1\n\n"
        "[[sources]]\n"
        'name = "alpha"\n'
        'root = "projects/alpha"\n'
        'visibility = "private"\n'
        f'write = "{alpha_write}"\n\n'
        "[[sources]]\n"
        'name = "beta"\n'
        'root = "projects/beta"\n'
        'visibility = "private"\n'
        + (f'write = "{beta_write}"\n' if beta_write is not None else ""),
        encoding="utf-8",
    )
    _profile(root / "projects" / "alpha")
    _profile(root / "projects" / "beta")
    return root


def _preview(
    tmp_path: Path,
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    source: str = "alpha",
) -> dict:
    assert (
        _run(
            monkeypatch,
            "maintenance",
            "shared-value",
            str(tmp_path),
            "--source",
            source,
            "--workspace",
            str(workspace),
            "--preview",
            "--json",
        )
        == 0
    )
    captured = capsys.readouterr()
    return json.loads(captured.out)


def test_workspace_selected_write_is_preview_bound_and_source_local(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _workspace(tmp_path)
    beta = workspace / "projects" / "beta" / "plan" / "current.md"
    beta_before = beta.read_bytes()
    preview = _preview(tmp_path, workspace, monkeypatch, capsys)

    assert (
        _run(
            monkeypatch,
            "maintenance",
            "shared-value",
            str(tmp_path),
            "--source",
            "alpha",
            "--workspace",
            str(workspace),
            "--write",
            "--expect-source-hash",
            preview["source"]["block_hash"],
            "--expect-preview-hash",
            preview["preview_sha256"],
            "--workstream-id",
            "WS-001",
            "--json",
        )
        == 0
    )
    written = json.loads(capsys.readouterr().out)
    assert written["workspace_source"] == "alpha"
    assert written["write"]["preview_sha256"] == preview["preview_sha256"]
    assert beta.read_bytes() == beta_before
    generation = written["write"]["generation"]
    journal = workspace / "projects" / "alpha" / ".docsystem" / "journal"
    manifest = json.loads((journal / generation / "manifest.json").read_text())
    assert manifest["authority"] == {
        "preview_sha256": preview["preview_sha256"],
        "source": "alpha",
        "workspace_manifest_sha256": preview["workspace_manifest_sha256"],
        "project_config_sha256": preview["project_config_sha256"],
        "write_policy": "managed-maintenance",
    }
    assert str(tmp_path) not in json.dumps(manifest)
    assert not (workspace / "projects" / "beta" / ".docsystem" / "journal").exists()


def test_read_only_source_refuses_write_but_allows_preview(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _workspace(tmp_path, alpha_write="none")
    before = (workspace / "projects" / "alpha" / "plan" / "current.md").read_bytes()
    preview = _preview(tmp_path, workspace, monkeypatch, capsys)

    assert (
        _run(
            monkeypatch,
            "maintenance",
            "shared-value",
            str(tmp_path),
            "--source",
            "alpha",
            "--workspace",
            str(workspace),
            "--write",
            "--expect-source-hash",
            preview["source"]["block_hash"],
            "--expect-preview-hash",
            preview["preview_sha256"],
            "--workstream-id",
            "WS-001",
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "write policy does not allow" in captured.err
    assert (workspace / "projects" / "alpha" / "plan" / "current.md").read_bytes() == before
    assert not (workspace / "projects" / "alpha" / ".docsystem" / "journal").exists()


def test_occurrence_or_workspace_change_invalidates_preview_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _workspace(tmp_path)
    preview = _preview(tmp_path, workspace, monkeypatch, capsys)
    occurrence = workspace / "projects" / "alpha" / "plan" / "current.md"
    occurrence.write_text(
        occurrence.read_text(encoding="utf-8").replace("old value", "newer value"),
        encoding="utf-8",
    )

    assert (
        _run(
            monkeypatch,
            "maintenance",
            "shared-value",
            str(tmp_path),
            "--source",
            "alpha",
            "--workspace",
            str(workspace),
            "--write",
            "--expect-source-hash",
            preview["source"]["block_hash"],
            "--expect-preview-hash",
            preview["preview_sha256"],
            "--workstream-id",
            "WS-001",
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "preview hash changed" in captured.err
    assert not (workspace / "projects" / "alpha" / ".docsystem" / "journal").exists()


def test_workspace_manifest_change_invalidates_preview_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _workspace(tmp_path)
    preview = _preview(tmp_path, workspace, monkeypatch, capsys)
    manifest = workspace / WORKSPACE_FILENAME
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + "\n# operator note\n",
        encoding="utf-8",
    )

    assert (
        _run(
            monkeypatch,
            "maintenance",
            "shared-value",
            str(tmp_path),
            "--source",
            "alpha",
            "--workspace",
            str(workspace),
            "--write",
            "--expect-source-hash",
            preview["source"]["block_hash"],
            "--expect-preview-hash",
            preview["preview_sha256"],
            "--workstream-id",
            "WS-001",
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "preview hash changed" in captured.err


def test_project_config_change_invalidates_preview_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _workspace(tmp_path)
    preview = _preview(tmp_path, workspace, monkeypatch, capsys)
    config = workspace / "projects" / "alpha" / CONFIG_FILENAME
    config.write_text(
        config.read_text(encoding="utf-8") + "\n# reviewed policy changed\n",
        encoding="utf-8",
    )

    assert (
        _run(
            monkeypatch,
            "maintenance",
            "shared-value",
            str(tmp_path),
            "--source",
            "alpha",
            "--workspace",
            str(workspace),
            "--write",
            "--expect-source-hash",
            preview["source"]["block_hash"],
            "--expect-preview-hash",
            preview["preview_sha256"],
            "--workstream-id",
            "WS-001",
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "preview hash changed" in captured.err


def test_same_workstream_id_remains_source_qualified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _workspace(tmp_path, beta_write="managed-maintenance")
    generations: dict[str, str] = {}
    for source in ("alpha", "beta"):
        preview = _preview(tmp_path, workspace, monkeypatch, capsys, source)
        assert (
            _run(
                monkeypatch,
                "maintenance",
                "shared-value",
                str(tmp_path),
                "--source",
                source,
                "--workspace",
                str(workspace),
                "--write",
                "--expect-source-hash",
                preview["source"]["block_hash"],
                "--expect-preview-hash",
                preview["preview_sha256"],
                "--workstream-id",
                "WS-SHARED",
                "--json",
            )
            == 0
        )
        result = json.loads(capsys.readouterr().out)
        generations[source] = result["write"]["generation"]

    assert all(value.endswith("-WS-SHARED") for value in generations.values())
    for source, generation in generations.items():
        journal = workspace / "projects" / source / ".docsystem" / "journal"
        authority = json.loads(
            (journal / generation / "manifest.json").read_text(encoding="utf-8")
        )["authority"]
        assert authority["source"] == source
        assert "current value" in (
            workspace / "projects" / source / "plan" / "current.md"
        ).read_text(encoding="utf-8")


def test_selected_preview_is_byte_identical_direct_and_projected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _workspace(tmp_path)
    preview_args = (
        "maintenance",
        "shared-value",
        str(tmp_path),
        "--source",
        "alpha",
        "--workspace",
        str(workspace),
        "--preview",
        "--json",
    )
    assert _run(monkeypatch, *preview_args) == 0
    direct = capsys.readouterr()
    assert "using direct Markdown" in direct.err

    assert (
        _run(
            monkeypatch,
            "index",
            str(tmp_path),
            "--source",
            "alpha",
            "--workspace",
            str(workspace),
            "--write",
        )
        == 0
    )
    capsys.readouterr()
    assert _run(monkeypatch, *preview_args) == 0
    projected = capsys.readouterr()
    assert projected.err == ""
    assert projected.out == direct.out


def test_source_write_stales_federation_and_guarded_recovery_is_local(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _workspace(tmp_path)
    assert (
        _run(
            monkeypatch,
            "federation",
            "index",
            str(tmp_path),
            "--workspace",
            str(workspace),
            "--write",
        )
        == 0
    )
    capsys.readouterr()
    preview = _preview(tmp_path, workspace, monkeypatch, capsys)
    assert (
        _run(
            monkeypatch,
            "maintenance",
            "shared-value",
            str(tmp_path),
            "--source",
            "alpha",
            "--workspace",
            str(workspace),
            "--write",
            "--expect-source-hash",
            preview["source"]["block_hash"],
            "--expect-preview-hash",
            preview["preview_sha256"],
            "--workstream-id",
            "WS-002",
            "--json",
        )
        == 0
    )
    written = json.loads(capsys.readouterr().out)
    generation = written["write"]["generation"]
    manifest_hash = written["write"]["manifest_hash"]

    assert (
        _run(
            monkeypatch,
            "federation",
            "changes",
            str(tmp_path),
            "--workspace",
            str(workspace),
            "--json",
        )
        == 0
    )
    changes = json.loads(capsys.readouterr().out)
    assert changes["changes"] == [{"kind": "modified", "source": "alpha"}]

    context_args = (
        "federation",
        "context",
        "alpha::DOC-002",
        str(tmp_path),
        "--workspace",
        str(workspace),
        "--json",
    )
    assert _run(monkeypatch, *context_args) == 0
    direct = capsys.readouterr()
    assert "using direct Markdown" in direct.err
    assert (
        _run(
            monkeypatch,
            "federation",
            "index",
            str(tmp_path),
            "--workspace",
            str(workspace),
            "--write",
        )
        == 0
    )
    capsys.readouterr()
    assert _run(monkeypatch, *context_args) == 0
    projected = capsys.readouterr()
    assert projected.err == ""
    assert projected.out == direct.out

    assert (
        _run(
            monkeypatch,
            "maintenance-recover",
            generation,
            str(tmp_path),
            "--source",
            "alpha",
            "--workspace",
            str(workspace),
            "--expect-manifest-hash",
            "0" * 64,
        )
        == 1
    )
    failed = capsys.readouterr()
    assert failed.out == ""
    assert "manifest hash does not match" in failed.err

    assert (
        _run(
            monkeypatch,
            "maintenance-recover",
            generation,
            str(tmp_path),
            "--source",
            "alpha",
            "--workspace",
            str(workspace),
            "--expect-manifest-hash",
            manifest_hash,
            "--json",
        )
        == 0
    )
    recovered = json.loads(capsys.readouterr().out)
    assert recovered["workspace_source"] == "alpha"
    assert "old value" in (
        workspace / "projects" / "alpha" / "plan" / "current.md"
    ).read_text(encoding="utf-8")
    assert (
        _run(
            monkeypatch,
            "federation",
            "changes",
            str(tmp_path),
            "--workspace",
            str(workspace),
            "--json",
        )
        == 0
    )
    recovery_changes = json.loads(capsys.readouterr().out)
    assert recovery_changes["changes"] == [
        {"kind": "modified", "source": "alpha"}
    ]


def test_selected_recovery_rejects_wrong_source_and_changed_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _workspace(tmp_path, beta_write="managed-maintenance")
    preview = _preview(tmp_path, workspace, monkeypatch, capsys)
    assert (
        _run(
            monkeypatch,
            "maintenance",
            "shared-value",
            str(tmp_path),
            "--source",
            "alpha",
            "--workspace",
            str(workspace),
            "--write",
            "--expect-source-hash",
            preview["source"]["block_hash"],
            "--expect-preview-hash",
            preview["preview_sha256"],
            "--workstream-id",
            "WS-RECOVERY",
            "--json",
        )
        == 0
    )
    written = json.loads(capsys.readouterr().out)
    generation = written["write"]["generation"]
    manifest_hash = written["write"]["manifest_hash"]
    alpha_generation = (
        workspace / "projects" / "alpha" / ".docsystem" / "journal" / generation
    )
    beta_generation = (
        workspace / "projects" / "beta" / ".docsystem" / "journal" / generation
    )
    beta_generation.parent.mkdir(parents=True)
    shutil.copytree(alpha_generation, beta_generation)

    assert (
        _run(
            monkeypatch,
            "maintenance-recover",
            generation,
            str(tmp_path),
            "--source",
            "beta",
            "--workspace",
            str(workspace),
            "--expect-manifest-hash",
            manifest_hash,
        )
        == 1
    )
    wrong_source = capsys.readouterr()
    assert wrong_source.out == ""
    assert "does not match selected source" in wrong_source.err

    manifest = workspace / WORKSPACE_FILENAME
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + "\n# changed after write\n",
        encoding="utf-8",
    )
    assert (
        _run(
            monkeypatch,
            "maintenance-recover",
            generation,
            str(tmp_path),
            "--source",
            "alpha",
            "--workspace",
            str(workspace),
            "--expect-manifest-hash",
            manifest_hash,
        )
        == 1
    )
    changed_workspace = capsys.readouterr()
    assert changed_workspace.out == ""
    assert "does not match selected workspace_manifest_sha256" in (
        changed_workspace.err
    )
    assert "current value" in (
        workspace / "projects" / "alpha" / "plan" / "current.md"
    ).read_text(encoding="utf-8")
