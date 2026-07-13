import json
import os
from pathlib import Path

import pytest

from docsystem.cli import (
    agent_instructions,
    build_parser,
    main,
    readiness,
    report_draft,
    workspace_doctor,
    workspace_list,
)
from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG
from docsystem.workspace import (
    LOCAL_POINTER_FILENAME,
    WORKSPACE_ENV_VAR,
    WORKSPACE_FILENAME,
    WorkspaceError,
    discover_workspace_root,
    load_workspace,
    resolve_source_root,
    source_statuses,
)

MANIFEST = """\
version = 1

[[sources]]
name = "alpha"
root = "projects/alpha"
visibility = "private"

[[sources]]
name = "beta"
root = "projects/beta"
visibility = "public"
"""


PROJECT_CONFIG = DEFAULT_CONFIG.replace("[areas]\n", '[areas]\ndocumentation = "."\n')


def write_project(root: Path, document_id: str = "DOC-001") -> Path:
    """Create a minimal valid synthetic project profile."""

    root.mkdir(parents=True, exist_ok=True)
    (root / CONFIG_FILENAME).write_text(PROJECT_CONFIG, encoding="utf-8")
    plan = root / "plan"
    plan.mkdir(parents=True, exist_ok=True)
    (plan / "README.md").write_text(
        f"---\nid: {document_id}\nrevision: 1\n---\n# Index\n\n## Purpose\n\nBody.\n",
        encoding="utf-8",
    )
    return root


def build_workspace(tmp_path: Path, manifest: str = MANIFEST) -> Path:
    """Create a workspace with two available synthetic sources."""

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / WORKSPACE_FILENAME).write_text(manifest, encoding="utf-8")
    write_project(workspace / "projects" / "alpha", "DOC-001")
    write_project(workspace / "projects" / "beta", "DOC-002")
    return workspace


def run(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr("sys.argv", ["docsystem", *argv])
    return main()


# --- manifest validation ---------------------------------------------------


def test_manifest_loads_sources_sorted_by_name(tmp_path: Path) -> None:
    workspace = build_workspace(tmp_path)
    loaded = load_workspace(workspace)

    assert [source.name for source in loaded.sources] == ["alpha", "beta"]
    assert [source.visibility for source in loaded.sources] == ["private", "public"]
    assert loaded.find("alpha").project_root == (
        workspace / "projects" / "alpha"
    ).resolve()


def test_manifest_listing_is_sorted_regardless_of_declaration_order(
    tmp_path: Path,
) -> None:
    manifest = """\
version = 1

[[sources]]
name = "zulu"
root = "projects/zulu"
visibility = "public"

[[sources]]
name = "alpha"
root = "projects/alpha"
visibility = "private"
"""
    workspace = build_workspace(tmp_path, manifest)

    assert [source.name for source in load_workspace(workspace).sources] == [
        "alpha",
        "zulu",
    ]


@pytest.mark.parametrize(
    "manifest",
    [
        pytest.param('version = 2\n', id="wrong-version"),
        pytest.param('version = true\n', id="boolean-version"),
        pytest.param('version = 1\nextra = 1\n', id="unknown-manifest-key"),
        pytest.param(
            'version = 1\n\n[[sources]]\nname = "a"\nroot = "p"\n'
            'visibility = "private"\nextra = 1\n',
            id="unknown-source-key",
        ),
        pytest.param(
            'version = 1\n\n[[sources]]\nname = "Alpha"\nroot = "p"\n'
            'visibility = "private"\n',
            id="uppercase-name",
        ),
        pytest.param(
            'version = 1\n\n[[sources]]\nname = "1alpha"\nroot = "p"\n'
            'visibility = "private"\n',
            id="leading-digit-name",
        ),
        pytest.param(
            'version = 1\n\n[[sources]]\nname = "a"\nroot = ""\n'
            'visibility = "private"\n',
            id="empty-root",
        ),
        pytest.param(
            'version = 1\n\n[[sources]]\nname = "a"\nroot = "/abs"\n'
            'visibility = "private"\n',
            id="absolute-root",
        ),
        pytest.param(
            'version = 1\n\n[[sources]]\nname = "a"\nroot = "../escape"\n'
            'visibility = "private"\n',
            id="parent-traversal-root",
        ),
        pytest.param(
            'version = 1\n\n[[sources]]\nname = "a"\nroot = "a\\\\b"\n'
            'visibility = "private"\n',
            id="backslash-root",
        ),
        pytest.param(
            'version = 1\n\n[[sources]]\nname = "a"\nroot = "p"\n',
            id="missing-visibility",
        ),
        pytest.param(
            'version = 1\n\n[[sources]]\nname = "a"\nroot = "p"\n'
            'visibility = "secret"\n',
            id="unknown-visibility",
        ),
        pytest.param(
            'version = 1\n\n[[sources]]\nname = "a"\nroot = "p"\n'
            'visibility = "private"\n\n[[sources]]\nname = "a"\nroot = "q"\n'
            'visibility = "public"\n',
            id="duplicate-name",
        ),
        pytest.param(
            'version = 1\n\n[[sources]]\nname = "a"\nroot = "p"\n'
            'visibility = "private"\n\n[[sources]]\nname = "b"\nroot = "./p"\n'
            'visibility = "public"\n',
            id="duplicate-resolved-root",
        ),
        pytest.param(
            'version = 1\n\n[[sources]]\nname = "a"\nroot = "projects"\n'
            'visibility = "private"\n\n[[sources]]\nname = "b"\n'
            'root = "projects/b"\nvisibility = "public"\n',
            id="overlapping-resolved-root",
        ),
    ],
)
def test_invalid_manifest_is_rejected(tmp_path: Path, manifest: str) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / WORKSPACE_FILENAME).write_text(manifest, encoding="utf-8")

    with pytest.raises(WorkspaceError):
        load_workspace(workspace)


def test_missing_manifest_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(
        WorkspaceError, match=r"^workspace manifest not found$"
    ) as error:
        load_workspace(tmp_path)

    assert str(tmp_path) not in str(error.value)


def test_symlinked_source_root_escaping_the_workspace_is_rejected(
    tmp_path: Path,
) -> None:
    outside = write_project(tmp_path / "outside")
    workspace = tmp_path / "workspace"
    (workspace / "projects").mkdir(parents=True)
    (workspace / WORKSPACE_FILENAME).write_text(
        'version = 1\n\n[[sources]]\nname = "escape"\n'
        'root = "projects/escape"\nvisibility = "private"\n',
        encoding="utf-8",
    )
    (workspace / "projects" / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorkspaceError, match="escapes the workspace root"):
        load_workspace(workspace)


def test_symlink_loop_in_source_root_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / WORKSPACE_FILENAME).write_text(
        'version = 1\n\n[[sources]]\nname = "loop"\n'
        'root = "loop"\nvisibility = "private"\n',
        encoding="utf-8",
    )
    (workspace / "loop").symlink_to("loop")

    with pytest.raises(WorkspaceError, match="cannot be resolved safely"):
        load_workspace(workspace)


def test_symlink_loop_in_workspace_root_fails_closed(tmp_path: Path) -> None:
    loop = tmp_path / "loop"
    loop.symlink_to("loop")

    with pytest.raises(
        WorkspaceError, match=r"^workspace root cannot be resolved safely$"
    ):
        load_workspace(loop)


# --- availability ----------------------------------------------------------


def test_availability_reports_sanitized_reasons_without_paths(tmp_path: Path) -> None:
    manifest = """\
version = 1

[[sources]]
name = "gone"
root = "projects/gone"
visibility = "private"

[[sources]]
name = "unconfigured"
root = "projects/unconfigured"
visibility = "private"

[[sources]]
name = "broken"
root = "projects/broken"
visibility = "private"

[[sources]]
name = "ready"
root = "projects/ready"
visibility = "public"
"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / WORKSPACE_FILENAME).write_text(manifest, encoding="utf-8")
    (workspace / "projects" / "unconfigured").mkdir(parents=True)
    broken = workspace / "projects" / "broken"
    broken.mkdir(parents=True)
    (broken / CONFIG_FILENAME).write_text("version = 99\n", encoding="utf-8")
    write_project(workspace / "projects" / "ready")

    statuses = source_statuses(load_workspace(workspace))

    assert [(item.name, item.available, item.reason) for item in statuses] == [
        ("broken", False, "invalid-configuration"),
        ("gone", False, "missing-root"),
        ("ready", True, None),
        ("unconfigured", False, "missing-configuration"),
    ]
    assert not any(str(tmp_path) in (item.reason or "") for item in statuses)


@pytest.mark.parametrize("escape_kind", ["documentation-root", "projection-cache"])
def test_source_with_writable_symlink_escape_is_unavailable(
    tmp_path: Path, escape_kind: str
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / WORKSPACE_FILENAME).write_text(
        'version = 1\n\n[[sources]]\nname = "unsafe"\n'
        'root = "unsafe"\nvisibility = "private"\n',
        encoding="utf-8",
    )
    source = workspace / "unsafe"
    outside = tmp_path / "outside"
    outside.mkdir()
    if escape_kind == "documentation-root":
        source.mkdir()
        (source / CONFIG_FILENAME).write_text(PROJECT_CONFIG, encoding="utf-8")
        (source / "plan").symlink_to(outside, target_is_directory=True)
    else:
        write_project(source)
        state = source / ".docsystem"
        state.mkdir()
        (state / "cache").symlink_to(outside, target_is_directory=True)

    status = source_statuses(load_workspace(workspace))[0]

    assert status.available is False
    assert status.reason == "unsafe-local-path"


# --- discovery precedence --------------------------------------------------


def test_explicit_option_wins_over_environment_and_pointer(tmp_path: Path) -> None:
    project = tmp_path / "anchor"
    project.mkdir()
    (project / LOCAL_POINTER_FILENAME).write_text(
        f'workspace = "{(tmp_path / "pointer").as_posix()}"\n', encoding="utf-8"
    )

    resolved = discover_workspace_root(
        workspace_option=tmp_path / "explicit",
        project_root=project,
        environ={WORKSPACE_ENV_VAR: str(tmp_path / "environment")},
    )

    assert resolved == tmp_path / "explicit"


def test_environment_wins_over_pointer(tmp_path: Path) -> None:
    project = tmp_path / "anchor"
    project.mkdir()
    (project / LOCAL_POINTER_FILENAME).write_text(
        f'workspace = "{(tmp_path / "pointer").as_posix()}"\n', encoding="utf-8"
    )

    resolved = discover_workspace_root(
        workspace_option=None,
        project_root=project,
        environ={WORKSPACE_ENV_VAR: str(tmp_path / "environment")},
    )

    assert resolved == tmp_path / "environment"


def test_pointer_is_used_when_no_option_or_environment(tmp_path: Path) -> None:
    project = tmp_path / "anchor"
    project.mkdir()
    (project / LOCAL_POINTER_FILENAME).write_text(
        f'workspace = "{(tmp_path / "pointer").as_posix()}"\n', encoding="utf-8"
    )

    resolved = discover_workspace_root(
        workspace_option=None, project_root=project, environ={}
    )

    assert resolved == tmp_path / "pointer"


def test_no_workspace_is_discovered_without_wiring(tmp_path: Path) -> None:
    assert (
        discover_workspace_root(
            workspace_option=None, project_root=tmp_path, environ={}
        )
        is None
    )


@pytest.mark.parametrize(
    "pointer",
    [
        pytest.param('workspace = "relative/path"\n', id="relative-path"),
        pytest.param('workspace = ""\n', id="empty-path"),
        pytest.param("workspace = 1\n", id="non-string"),
        pytest.param('workspace = "/tmp/ws"\nextra = 1\n', id="unknown-key"),
        pytest.param("not toml\n", id="malformed-toml"),
    ],
)
def test_invalid_pointer_is_rejected(tmp_path: Path, pointer: str) -> None:
    (tmp_path / LOCAL_POINTER_FILENAME).write_text(pointer, encoding="utf-8")

    with pytest.raises(WorkspaceError):
        discover_workspace_root(
            workspace_option=None, project_root=tmp_path, environ={}
        )


def test_empty_environment_value_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError, match="must not be empty"):
        discover_workspace_root(
            workspace_option=None,
            project_root=tmp_path,
            environ={WORKSPACE_ENV_VAR: ""},
        )


# --- selection -------------------------------------------------------------


def test_selection_resolves_the_registered_project_root(tmp_path: Path) -> None:
    workspace = build_workspace(tmp_path)

    resolved = resolve_source_root(
        "beta", workspace_option=workspace, project_root=tmp_path, environ={}
    )

    assert resolved == (workspace / "projects" / "beta").resolve()


def test_unknown_source_fails_closed(tmp_path: Path) -> None:
    workspace = build_workspace(tmp_path)

    with pytest.raises(WorkspaceError, match="unknown workspace source: gamma"):
        resolve_source_root(
            "gamma", workspace_option=workspace, project_root=tmp_path, environ={}
        )


def test_unavailable_source_fails_closed(tmp_path: Path) -> None:
    workspace = build_workspace(tmp_path)
    (workspace / "projects" / "beta" / CONFIG_FILENAME).unlink()

    with pytest.raises(WorkspaceError, match="unavailable: beta"):
        resolve_source_root(
            "beta", workspace_option=workspace, project_root=tmp_path, environ={}
        )


# --- workspace listing commands --------------------------------------------


def test_workspace_list_reports_sorted_availability(tmp_path: Path, capsys) -> None:
    workspace = build_workspace(tmp_path)
    (workspace / "projects" / "beta" / CONFIG_FILENAME).unlink()

    assert workspace_list(tmp_path, workspace_option=workspace) == 0

    captured = capsys.readouterr()
    assert captured.out == (
        "alpha\tprivate\tavailable\t-\n"
        "beta\tpublic\tunavailable\tmissing-configuration\n"
    )
    assert captured.err == ""


def test_workspace_list_json_carries_no_local_path(tmp_path: Path, capsys) -> None:
    workspace = build_workspace(tmp_path)

    assert workspace_list(tmp_path, workspace_option=workspace, json_output=True) == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["sources"] == [
        {"name": "alpha", "visibility": "private", "available": True, "reason": None},
        {"name": "beta", "visibility": "public", "available": True, "reason": None},
    ]
    assert str(tmp_path) not in captured.out


def test_workspace_doctor_fails_when_a_source_is_unavailable(
    tmp_path: Path, capsys
) -> None:
    workspace = build_workspace(tmp_path)
    (workspace / "projects" / "beta" / CONFIG_FILENAME).unlink()

    assert workspace_doctor(tmp_path, workspace_option=workspace) == 1

    captured = capsys.readouterr()
    assert "- Unavailable sources: 1" in captured.out
    assert (
        "ERROR: workspace source is unavailable: beta (missing-configuration)"
        in captured.err
    )


def test_workspace_doctor_passes_for_a_healthy_workspace(
    tmp_path: Path, capsys
) -> None:
    workspace = build_workspace(tmp_path)

    assert workspace_doctor(tmp_path, workspace_option=workspace) == 0
    assert capsys.readouterr().err == ""


def test_workspace_commands_fail_closed_without_a_workspace(
    tmp_path: Path, capsys
) -> None:
    assert workspace_list(tmp_path) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines() == [
        "ERROR: no workspace configured; pass --workspace PATH, set "
        "DOCSYSTEM_WORKSPACE, or add .docsystem.local.toml"
    ]


def test_missing_explicit_workspace_error_does_not_leak_its_path(
    tmp_path: Path, capsys
) -> None:
    missing = tmp_path / "private-workspace-name"

    assert workspace_list(tmp_path, workspace_option=missing) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "ERROR: workspace manifest not found\n"
    assert str(missing) not in captured.err


# --- CLI source selection --------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["catalog"], id="catalog"),
        pytest.param(["catalog", "--json"], id="catalog-json"),
        pytest.param(["validate"], id="validate"),
        pytest.param(["doctor"], id="doctor"),
        pytest.param(["read", "DOC-002"], id="read"),
        pytest.param(["context", "DOC-002"], id="context"),
        pytest.param(["impact", "DOC-002"], id="impact"),
        pytest.param(["migration-report", "--json"], id="migration-report"),
        pytest.param(["migrate"], id="migrate-preview"),
        pytest.param(["index"], id="index"),
        pytest.param(["changes"], id="changes"),
        pytest.param(["finish", "DOC-002"], id="finish"),
    ],
)
def test_selected_source_matches_the_positional_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, argv: list[str]
) -> None:
    workspace = build_workspace(tmp_path)
    beta = workspace / "projects" / "beta"
    monkeypatch.delenv(WORKSPACE_ENV_VAR, raising=False)
    # A written projection makes `index`/`changes` report a current state and
    # serves the read commands from the projection, as in a real project.
    assert run(["index", str(beta), "--write"], monkeypatch) == 0
    capsys.readouterr()

    # `read`/`context`/`impact`/`finish` take the document ID before the root.
    positional = [item for item in argv if not item.startswith("--")]
    options = [item for item in argv if item.startswith("--")]

    direct_code = run([*positional, str(beta), *options], monkeypatch)
    direct = capsys.readouterr()
    selected_code = run(
        [
            *positional,
            str(tmp_path),
            *options,
            "--source",
            "beta",
            "--workspace",
            str(workspace),
        ],
        monkeypatch,
    )
    selected = capsys.readouterr()

    assert selected_code == direct_code == 0
    assert selected.out == direct.out
    assert selected.err == direct.err


def test_no_source_argv_never_loads_workspace_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    project = write_project(tmp_path / "project")
    # A broken pointer in the discovery root must not affect a flagless run.
    (project / LOCAL_POINTER_FILENAME).write_text("not toml\n", encoding="utf-8")
    monkeypatch.setenv(WORKSPACE_ENV_VAR, "")

    assert run(["catalog", str(project)], monkeypatch) == 0

    captured = capsys.readouterr()
    assert captured.out == "documentation\tREADME.md\n"
    assert captured.err == ""


def test_unknown_source_exits_one_with_empty_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    workspace = build_workspace(tmp_path)
    monkeypatch.delenv(WORKSPACE_ENV_VAR, raising=False)

    code = run(
        ["catalog", str(tmp_path), "--source", "gamma", "--workspace", str(workspace)],
        monkeypatch,
    )

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert captured.err.splitlines() == ["ERROR: unknown workspace source: gamma"]


def test_unavailable_source_exits_one_with_empty_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    workspace = build_workspace(tmp_path)
    (workspace / "projects" / "beta" / CONFIG_FILENAME).unlink()
    monkeypatch.delenv(WORKSPACE_ENV_VAR, raising=False)

    code = run(
        ["catalog", str(tmp_path), "--source", "beta", "--workspace", str(workspace)],
        monkeypatch,
    )

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert captured.err.splitlines() == [
        "ERROR: workspace source is unavailable: beta (missing-configuration)"
    ]


def test_missing_workspace_never_falls_back_to_the_positional_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    project = write_project(tmp_path / "project")
    monkeypatch.delenv(WORKSPACE_ENV_VAR, raising=False)

    code = run(["catalog", str(project), "--source", "alpha"], monkeypatch)

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert "no workspace configured" in captured.err


def test_environment_variable_selects_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    workspace = build_workspace(tmp_path)
    monkeypatch.setenv(WORKSPACE_ENV_VAR, str(workspace))

    assert run(["catalog", str(tmp_path), "--source", "alpha"], monkeypatch) == 0
    assert capsys.readouterr().out == "documentation\tREADME.md\n"


# --- privacy ---------------------------------------------------------------


def test_readiness_renders_a_selector_instead_of_the_source_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    workspace = build_workspace(tmp_path)
    monkeypatch.delenv(WORKSPACE_ENV_VAR, raising=False)

    code = run(
        [
            "readiness",
            str(tmp_path),
            "--json",
            "--source",
            "alpha",
            "--workspace",
            str(workspace),
        ],
        monkeypatch,
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 0
    assert payload["source"] == "alpha"
    assert payload["next_command"] == (
        f"docsystem index {tmp_path} --source alpha --write"
    )
    assert str(workspace) not in captured.out
    assert str(workspace) not in captured.err


def test_readiness_without_a_source_keeps_its_payload_byte_compatible(
    tmp_path: Path, capsys
) -> None:
    project = write_project(tmp_path / "project")

    assert readiness(project, json_output=True) == 0

    payload = json.loads(capsys.readouterr().out)
    assert "source" not in payload
    assert payload["next_command"] == f"docsystem index {project} --write"


def test_agent_instructions_snippet_carries_no_source_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    workspace = build_workspace(tmp_path)
    monkeypatch.delenv(WORKSPACE_ENV_VAR, raising=False)

    code = run(
        [
            "agent-instructions",
            str(tmp_path),
            "--source",
            "alpha",
            "--workspace",
            str(workspace),
        ],
        monkeypatch,
    )

    captured = capsys.readouterr()
    assert code == 0
    assert f"docsystem readiness {tmp_path} --source alpha --json" in captured.out
    assert str(workspace) not in captured.out
    assert "Body." not in captured.out


def test_report_draft_carries_no_source_path_or_document_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    workspace = build_workspace(tmp_path)
    monkeypatch.delenv(WORKSPACE_ENV_VAR, raising=False)

    code = run(
        [
            "report",
            "draft",
            str(tmp_path),
            "--project-name",
            "Example",
            "--type",
            "core-bug",
            "--source",
            "claude",
            "--workspace-source",
            "alpha",
            "--workspace",
            str(workspace),
        ],
        monkeypatch,
    )

    captured = capsys.readouterr()
    assert code == 0
    assert (
        f"docsystem report draft {tmp_path} --workspace-source alpha --project-name"
        in captured.out
    )
    assert captured.out.count("--source claude") == 1
    assert "`source:claude`" in captured.out
    assert str(workspace) not in captured.out
    assert "Body." not in captured.out


def test_report_draft_without_a_source_keeps_the_project_root(
    tmp_path: Path, capsys
) -> None:
    project = write_project(tmp_path / "project")

    assert (
        report_draft(
            project,
            project_name="Example",
            report_type="core-bug",
            source="claude",
        )
        == 0
    )

    assert f"docsystem report draft {project} --project-name" in capsys.readouterr().out


def test_agent_instructions_without_a_source_keeps_the_project_root(
    tmp_path: Path, capsys
) -> None:
    project = write_project(tmp_path / "project")

    assert agent_instructions(project) == 0
    assert f"docsystem readiness {project} --json" in capsys.readouterr().out


# --- mutation containment --------------------------------------------------


def test_mutating_command_only_touches_the_selected_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = build_workspace(tmp_path)
    anchor = write_project(tmp_path / "anchor", "DOC-900")
    alpha = workspace / "projects" / "alpha"
    beta = workspace / "projects" / "beta"
    monkeypatch.delenv(WORKSPACE_ENV_VAR, raising=False)

    code = run(
        [
            "index",
            str(anchor),
            "--write",
            "--source",
            "alpha",
            "--workspace",
            str(workspace),
        ],
        monkeypatch,
    )

    assert code == 0
    assert (alpha / ".docsystem").is_dir()
    assert not (beta / ".docsystem").exists()
    assert not (anchor / ".docsystem").exists()


def test_selection_never_mutates_the_workspace_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = build_workspace(tmp_path)
    manifest = workspace / WORKSPACE_FILENAME
    before = manifest.read_bytes()
    monkeypatch.delenv(WORKSPACE_ENV_VAR, raising=False)

    assert (
        run(
            [
                "validate",
                str(tmp_path),
                "--source",
                "alpha",
                "--workspace",
                str(workspace),
            ],
            monkeypatch,
        )
        == 0
    )
    assert manifest.read_bytes() == before


def subcommands(parser) -> dict:
    return parser._subparsers._group_actions[0].choices


def option_strings(parser) -> set[str]:
    return {
        option for action in parser._actions for option in action.option_strings
    }


def test_every_project_command_accepts_the_selection_flags() -> None:
    commands = subcommands(build_parser())
    project_commands = [
        name
        for name, command_parser in commands.items()
        if name not in {"workspace", "report", "init"}
        and any(action.dest == "project" for action in command_parser._actions)
    ]

    assert len(project_commands) == 15
    for name in project_commands:
        options = option_strings(commands[name])
        assert {"--source", "--workspace-source", "--workspace"} <= options, name

    # A registered source is an existing valid profile. `init` remains a
    # direct-path bootstrap command rather than a workspace mutation surface.
    assert not {
        "--source",
        "--workspace-source",
        "--workspace",
    } & option_strings(commands["init"])

    # `report draft` keeps `--source` for the reporting host rather than
    # overloading it, so it takes the long selection spelling only.
    draft = subcommands(commands["report"])["draft"]
    source_action = next(
        action for action in draft._actions if "--source" in action.option_strings
    )
    assert source_action.dest == "source"
    assert source_action.choices == ("codex", "claude", "vscode", "other")
    assert {"--workspace-source", "--workspace"} <= option_strings(draft)
    assert "--source" not in {
        option
        for action in draft._actions
        if action.dest == "workspace_source"
        for option in action.option_strings
    }


def test_environment_variable_is_ignored_without_a_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    project = write_project(tmp_path / "project")
    monkeypatch.setenv(WORKSPACE_ENV_VAR, str(tmp_path / "does-not-exist"))

    assert run(["catalog", str(project)], monkeypatch) == 0
    assert capsys.readouterr().out == "documentation\tREADME.md\n"


def test_workspace_option_without_source_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    project = write_project(tmp_path / "project")

    assert run(["catalog", str(project), "--workspace", str(tmp_path)], monkeypatch) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "ERROR: --workspace requires --source/--workspace-source\n"


def test_os_environ_is_consulted_when_no_mapping_is_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = build_workspace(tmp_path)
    monkeypatch.setitem(os.environ, WORKSPACE_ENV_VAR, str(workspace))

    resolved = resolve_source_root(
        "alpha", workspace_option=None, project_root=tmp_path
    )

    assert resolved == (workspace / "projects" / "alpha").resolve()
