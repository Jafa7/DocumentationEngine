import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from docsystem.catalog import build_catalog
from docsystem.cli import (
    build_parser,
    context,
    impact,
    index_projection,
    migrate,
    migration_report,
    readiness,
    validate,
)
from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG, load_config
from docsystem.migration import (
    _rewrite_yaml_values,
    apply_migration_plan,
    build_migration_plan,
    validate_plan,
)


def existing_project(tmp_path: Path) -> Path:
    """A synthetic existing project with strict mode (the untouched default)."""

    config = DEFAULT_CONFIG.replace("[areas]\n", '[areas]\nworkspace = "."\n')
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    root = tmp_path / "plan"
    architecture = root / "architecture"
    architecture.mkdir(parents=True)
    (root / "README.md").write_text(
        """\
---
id: DOC-001
revision: 1
---
# Index
[Architecture](architecture/README.md)
""",
        encoding="utf-8",
    )
    (architecture / "README.md").write_text(
        """\
---
id: DOC-002
revision: 1
depends_on: [../README.md]   # legacy relative link, comment preserved
related: [https://example.com/spec, diagram.png]
unknown_field: keep-me
---
# Architecture

Body text.
""",
        encoding="utf-8",
    )
    return root


def crlf_project(tmp_path: Path) -> Path:
    """A synthetic existing project whose Markdown source uses CRLF line endings."""

    config = DEFAULT_CONFIG.replace("[areas]\n", '[areas]\nworkspace = "."\n')
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    root = tmp_path / "plan"
    architecture = root / "architecture"
    architecture.mkdir(parents=True)
    (root / "README.md").write_bytes(
        b"---\r\n"
        b"id: DOC-001\r\n"
        b"revision: 1\r\n"
        b"---\r\n"
        b"# Index\r\n"
        b"[Architecture](architecture/README.md)\r\n"
    )
    (architecture / "README.md").write_bytes(
        b"---\r\n"
        b"id: DOC-002\r\n"
        b"revision: 1\r\n"
        b"depends_on: [../README.md]   # legacy relative link, comment preserved\r\n"
        b"related: [https://example.com/spec, diagram.png]\r\n"
        b"unknown_field: keep-me\r\n"
        b"---\r\n"
        b"# Architecture\r\n"
        b"\r\n"
        b"Body text.\r\n"
    )
    return root


def two_file_crlf_project(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Two CRLF Markdown files, each with one resolvable legacy relation."""

    config = DEFAULT_CONFIG.replace("[areas]\n", '[areas]\nworkspace = "."\n')
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    root = tmp_path / "plan"
    a_dir = root / "a"
    b_dir = root / "b"
    a_dir.mkdir(parents=True)
    b_dir.mkdir(parents=True)
    (root / "README.md").write_bytes(
        b"---\r\nid: DOC-001\r\nrevision: 1\r\n---\r\n"
        b"# Index\r\n[A](a/README.md)\r\n[B](b/README.md)\r\n"
    )
    (a_dir / "README.md").write_bytes(
        b"---\r\nid: DOC-002\r\nrevision: 1\r\n"
        b"derived_from: [../README.md]\r\n---\r\n# A\r\n"
    )
    (b_dir / "README.md").write_bytes(
        b"---\r\nid: DOC-003\r\nrevision: 1\r\n"
        b"derived_from: [../README.md]\r\n---\r\n# B\r\n"
    )
    return root, a_dir / "README.md", b_dir / "README.md"


def test_migrate_apply_preserves_crlf_line_endings_byte_for_byte(
    tmp_path: Path, capsys
) -> None:
    root = crlf_project(tmp_path)
    target = root / "architecture" / "README.md"
    before = target.read_bytes()
    assert b"\r\n" in before

    assert migrate(tmp_path, apply=True) == 0
    capsys.readouterr()

    after = target.read_bytes()
    assert after == before.replace(b"[../README.md]", b"[DOC-001]")
    assert after.count(b"\r\n") == before.count(b"\r\n")
    assert b"# legacy relative link, comment preserved\r\n" in after
    assert b"related: [https://example.com/spec, diagram.png]\r\n" in after
    assert b"unknown_field: keep-me\r\n" in after

    # Re-apply is idempotent and still byte-for-byte CRLF.
    assert migrate(tmp_path, apply=True) == 0
    capsys.readouterr()
    assert target.read_bytes() == after


def test_apply_rolls_back_committed_files_byte_for_byte_when_a_later_replace_fails(
    tmp_path: Path, monkeypatch
) -> None:
    root, a_path, b_path = two_file_crlf_project(tmp_path)
    a_before = a_path.read_bytes()
    b_before = b_path.read_bytes()

    config = load_config(tmp_path)
    catalog = build_catalog(config)
    plan = build_migration_plan(config, catalog)
    assert len(plan.changes) == 2
    assert [path.as_posix() for path, _ in plan.updated_contents] == [
        "a/README.md",
        "b/README.md",
    ]

    calls = {"count": 0}
    original_replace = Path.replace

    def flaky_replace(self: Path, target: Path) -> Path:
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("simulated rename failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)

    with pytest.raises(OSError):
        apply_migration_plan(config, plan)

    # The already-renamed first file is restored to its exact original bytes
    # (including CRLF), and the never-renamed second file is untouched.
    assert a_path.read_bytes() == a_before
    assert b_path.read_bytes() == b_before
    assert not list(root.rglob("*.tmp"))


def test_readiness_reports_resolvable_migrations_without_writing(
    tmp_path: Path, capsys
) -> None:
    root = existing_project(tmp_path)
    before = (root / "architecture" / "README.md").read_bytes()

    assert readiness(tmp_path) == 0
    output = capsys.readouterr().out
    assert "Blocking structural/configuration errors: 0" in output
    assert "Resolvable legacy relation migrations: 1" in output
    assert "Explicit unresolved/resource boundaries: 2" in output
    assert "Projection: absent" in output
    assert "Next safe command: docsystem migrate" in output

    assert (root / "architecture" / "README.md").read_bytes() == before


def test_migrate_preview_is_read_only(tmp_path: Path, capsys) -> None:
    root = existing_project(tmp_path)
    target = root / "architecture" / "README.md"
    before = target.read_bytes()

    assert migrate(tmp_path) == 0
    output = capsys.readouterr().out
    assert (
        "would-migrate\tDOC-002\tdepends_on\t../README.md\tDOC-001\t"
        "architecture/README.md" in output
    )
    assert "Preview only; 1 legacy relation migration(s)" in output
    assert target.read_bytes() == before


def test_migrate_supports_documentation_root_at_project_root(
    tmp_path: Path, capsys
) -> None:
    config = DEFAULT_CONFIG.replace('root = "plan"', 'root = "."').replace(
        "[areas]\n", '[areas]\nworkspace = "."\n'
    )
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    architecture = tmp_path / "architecture"
    architecture.mkdir()
    (tmp_path / "README.md").write_text(
        "---\nid: DOC-001\nrevision: 1\n---\n"
        "# Index\n[Architecture](architecture/README.md)\n",
        encoding="utf-8",
    )
    target = architecture / "README.md"
    target.write_text(
        "---\nid: DOC-002\nrevision: 1\n"
        "depends_on: [../README.md]\n---\n# Architecture\n",
        encoding="utf-8",
    )

    before = target.read_bytes()
    assert migrate(tmp_path) == 0
    assert "Preview only; 1 legacy relation migration" in capsys.readouterr().out
    assert target.read_bytes() == before

    assert migrate(tmp_path, apply=True) == 0
    assert "Applied 1 legacy relation migration" in capsys.readouterr().out
    assert b"depends_on: [DOC-001]" in target.read_bytes()


def test_migrate_apply_changes_only_resolved_scalars_and_is_idempotent(
    tmp_path: Path, capsys
) -> None:
    root = existing_project(tmp_path)
    target = root / "architecture" / "README.md"
    target.chmod(0o640)
    original_mode = stat.S_IMODE(target.stat().st_mode)
    before = target.read_text(encoding="utf-8")

    assert migrate(tmp_path, apply=True) == 0
    output = capsys.readouterr().out
    assert "Applied 1 legacy relation migration(s) across 1 file(s)." in output
    after = target.read_text(encoding="utf-8")

    assert after == before.replace("[../README.md]", "[DOC-001]")
    # Untouched boundaries, comment and unknown field survive byte-for-byte.
    assert "# legacy relative link, comment preserved" in after
    assert "related: [https://example.com/spec, diagram.png]" in after
    assert "unknown_field: keep-me" in after
    assert stat.S_IMODE(target.stat().st_mode) == original_mode

    # Re-apply is idempotent: no further resolvable migrations, no diff.
    assert migrate(tmp_path, apply=True) == 0
    assert "No resolvable legacy relation migrations found." in capsys.readouterr().out
    assert target.read_text(encoding="utf-8") == after

    assert migration_report(tmp_path) == 0
    report = capsys.readouterr().out
    assert "resolved" not in report
    assert "boundary\tDOC-002\trelated\thttps://example.com/spec\texternal URL" in report


def test_full_migration_lets_strict_mode_drop_resolve_with_warning(
    tmp_path: Path, capsys
) -> None:
    existing_project(tmp_path)
    assert migrate(tmp_path, apply=True) == 0
    capsys.readouterr()

    assert validate(tmp_path) == 0
    concise = capsys.readouterr().err
    assert "2 legacy relation values remain resource/outside boundaries" in concise

    assert context(tmp_path, "DOC-002", depth=1) == 0
    capsys.readouterr()
    assert impact(tmp_path, "DOC-001") == 0
    capsys.readouterr()
    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()


def test_migrate_reports_no_migrations_when_none_are_resolvable(
    tmp_path: Path, capsys
) -> None:
    config = DEFAULT_CONFIG.replace("[areas]\n", '[areas]\nworkspace = "."\n')
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    root = tmp_path / "plan"
    root.mkdir()
    (root / "README.md").write_text(
        "---\nid: DOC-001\nrevision: 1\n---\n# Index\n", encoding="utf-8"
    )

    assert migrate(tmp_path) == 0
    assert "No resolvable legacy relation migrations found." in capsys.readouterr().out
    assert migrate(tmp_path, apply=True) == 0
    assert "No resolvable legacy relation migrations found." in capsys.readouterr().out


def test_migrate_parser_accepts_apply_flag() -> None:
    args = build_parser().parse_args(["migrate", "/tmp/project", "--apply"])
    assert args.apply is True
    default_args = build_parser().parse_args(["migrate", "/tmp/project"])
    assert default_args.apply is False


def test_readiness_parser_and_project_default() -> None:
    args = build_parser().parse_args(["readiness"])
    assert args.project == Path.cwd()


def test_apply_rejects_a_plan_that_no_longer_matches_disk_content(tmp_path: Path) -> None:
    root = existing_project(tmp_path)
    config = load_config(tmp_path)
    catalog = build_catalog(config)
    plan = build_migration_plan(config, catalog)
    assert len(plan.changes) == 1

    # Edit the source file (an unrelated part) after the plan was computed;
    # apply must reject the now-stale plan rather than silently overwriting
    # whatever is on disk with content computed from an earlier read.
    target = root / "architecture" / "README.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace("Body text.", "Edited body text."),
        encoding="utf-8",
    )
    before = target.read_bytes()

    with pytest.raises(ValueError):
        apply_migration_plan(config, plan)

    assert target.read_bytes() == before


def test_validate_plan_is_empty_for_a_project_with_no_migrations(tmp_path: Path) -> None:
    config = DEFAULT_CONFIG.replace("[areas]\n", '[areas]\nworkspace = "."\n')
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    root = tmp_path / "plan"
    root.mkdir()
    (root / "README.md").write_text(
        "---\nid: DOC-001\nrevision: 1\n---\n# Index\n", encoding="utf-8"
    )
    project_config = load_config(tmp_path)
    plan = build_migration_plan(project_config, build_catalog(project_config))
    assert validate_plan(project_config, plan) == ()


def anchor_alias_project(tmp_path: Path) -> Path:
    """A document sharing one legacy value across two relations via a YAML anchor/alias."""

    config = DEFAULT_CONFIG.replace("[areas]\n", '[areas]\nworkspace = "."\n')
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    root = tmp_path / "plan"
    architecture = root / "architecture"
    architecture.mkdir(parents=True)
    (root / "README.md").write_text(
        "---\nid: DOC-001\nrevision: 1\n---\n# Index\n"
        "[Architecture](architecture/README.md)\n",
        encoding="utf-8",
    )
    (architecture / "README.md").write_text(
        "---\n"
        "id: DOC-002\n"
        "revision: 1\n"
        "derived_from: [&legacy ../README.md]\n"
        "depends_on: [*legacy]\n"
        "---\n"
        "# Architecture\n",
        encoding="utf-8",
    )
    return root


def test_migrate_dedupes_shared_anchor_alias_replacement_without_corruption(
    tmp_path: Path, capsys
) -> None:
    root = anchor_alias_project(tmp_path)
    target = root / "architecture" / "README.md"
    before = target.read_bytes()

    config = load_config(tmp_path)
    catalog = build_catalog(config)
    plan = build_migration_plan(config, catalog)
    # Both relations resolve to the same target through the shared anchor.
    assert {change.relation for change in plan.changes} == {"derived_from", "depends_on"}
    assert {change.new_value for change in plan.changes} == {"DOC-001"}

    assert migrate(tmp_path) == 0
    capsys.readouterr()
    assert target.read_bytes() == before  # preview never mutates

    assert migrate(tmp_path, apply=True) == 0
    capsys.readouterr()
    after = target.read_text(encoding="utf-8")
    assert "&legacy DOC-001" in after
    assert "*legacy" in after
    assert "DOC-002002" not in after
    assert "DOC-001" in after and after.count("DOC-001") == 1

    # Re-apply is idempotent: the alias already resolves for both relations.
    assert migrate(tmp_path, apply=True) == 0
    assert "No resolvable legacy relation migrations found." in capsys.readouterr().out
    assert target.read_text(encoding="utf-8") == after


def test_rewrite_yaml_values_fails_closed_on_conflicting_shared_anchor_alias() -> None:
    yaml_text = "derived_from: [&legacy ../README.md]\ndepends_on: [*legacy]\n"

    with pytest.raises(ValueError, match="unsupported YAML anchor/alias"):
        _rewrite_yaml_values(
            yaml_text,
            [
                ("derived_from", "../README.md", "DOC-001"),
                ("depends_on", "../README.md", "DOC-999"),
            ],
        )

    # The failure is raised before any text is sliced; the input is untouched.
    assert yaml_text == "derived_from: [&legacy ../README.md]\ndepends_on: [*legacy]\n"


def _run_module_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    repo_src = Path(__file__).resolve().parents[1] / "src"
    env = dict(os.environ, PYTHONPATH=str(repo_src))
    return subprocess.run(
        [sys.executable, "-m", "docsystem", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )


def test_cli_migrate_preview_matches_library_output_from_unrelated_cwd(
    tmp_path: Path, capsys
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    existing_project(project_root)

    assert migrate(project_root) == 0
    expected = capsys.readouterr().out

    unrelated_cwd = tmp_path / "unrelated-cwd"
    unrelated_cwd.mkdir()
    result = _run_module_cli(["migrate", str(project_root)], unrelated_cwd)

    assert result.returncode == 0, result.stderr
    assert result.stdout == expected
    assert list(unrelated_cwd.iterdir()) == []


def test_cli_migrate_apply_is_reachable_from_unrelated_cwd_and_writes_target_project(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    root = existing_project(project_root)
    target = root / "architecture" / "README.md"

    unrelated_cwd = tmp_path / "unrelated-cwd"
    unrelated_cwd.mkdir()
    result = _run_module_cli(["migrate", str(project_root), "--apply"], unrelated_cwd)

    assert result.returncode == 0, result.stderr
    assert "Applied 1 legacy relation migration(s)" in result.stdout
    assert "[DOC-001]" in target.read_text(encoding="utf-8")
    assert list(unrelated_cwd.iterdir()) == []


def test_cli_readiness_matches_library_output_from_unrelated_cwd(
    tmp_path: Path, capsys
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    existing_project(project_root)

    assert readiness(project_root) == 0
    expected = capsys.readouterr().out

    unrelated_cwd = tmp_path / "unrelated-cwd"
    unrelated_cwd.mkdir()
    result = _run_module_cli(["readiness", str(project_root)], unrelated_cwd)

    assert result.returncode == 0, result.stderr
    assert result.stdout == expected
    assert list(unrelated_cwd.iterdir()) == []
