import json
from pathlib import Path

import pytest

from docsystem.cli import (
    build_parser,
    doctor,
    roadmap_explain,
    roadmap_next,
    roadmap_status,
    validate,
)
from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    root = project / "plan"
    root.mkdir(parents=True)
    config = DEFAULT_CONFIG.replace("[areas]\n", '[areas]\nworkspace = "."\n')
    (project / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    (root / "README.md").write_text(
        "---\nid: DOC-000\nrevision: 1\ntype: index\nstatus: active\n---\n"
        "# Index\n\n[Contract](contract.md)\n[Program](program.md)\n"
        "[Foundation](foundation.md)\n",
        encoding="utf-8",
    )
    (root / "contract.md").write_text(
        "---\nid: DOC-001\nrevision: 1\ntype: architecture\nstatus: active\n---\n"
        '# Contract\n\n<a id="federation"></a>\n## Federation\n\nSource.\n',
        encoding="utf-8",
    )
    (root / "foundation.md").write_text(
        "---\nid: RM-010\nrevision: 1\ntype: roadmap\nstatus: completed\n---\n"
        '# Foundation\n\n<a id="completion-evidence"></a>\n## Evidence\n\nDone.\n',
        encoding="utf-8",
    )
    (root / "program.md").write_text(
        "---\n"
        "id: RM-001\n"
        "revision: 1\n"
        "type: roadmap\n"
        "status: proposed\n"
        "program_plan:\n"
        "  version: 1\n"
        "  milestones:\n"
        "    - id: foundation\n"
        "      title: Foundation\n"
        "      order: 10\n"
        "      priority: 10\n"
        "      roadmap: RM-010\n"
        "      source_contracts: [DOC-001#federation]\n"
        "    - id: projection\n"
        "      title: Federated projection\n"
        "      order: 20\n"
        "      priority: 10\n"
        "      state: planned\n"
        "      prerequisites: [foundation]\n"
        "      source_contracts: [DOC-001#federation]\n"
        "    - id: promotion\n"
        "      title: Knowledge promotion\n"
        "      order: 30\n"
        "      priority: 20\n"
        "      state: planned\n"
        "      prerequisites: [foundation]\n"
        "    - id: migration\n"
        "      title: Copy-only migration\n"
        "      order: 40\n"
        "      state: waiting\n"
        "      prerequisites: [projection]\n"
        "      waiting_for: owner-approved workspace location\n"
        "    - id: shared-service\n"
        "      title: Shared service\n"
        "      order: 50\n"
        "      state: deferred\n"
        "      reopen_when: local federation dogfood proves a remote need\n"
        "---\n"
        "# Program\n",
        encoding="utf-8",
    )
    return project


def test_program_status_next_and_explain_are_deterministic(
    tmp_path: Path, capsys
) -> None:
    project = _project(tmp_path)

    assert roadmap_status(project, json_output=True) == 0
    status = capsys.readouterr()
    assert status.err == ""
    payload = json.loads(status.out)
    assert payload["program"] == {
        "action": "start",
        "id": "RM-001",
        "path": "program.md",
        "recommended": ["projection"],
        "revision": 1,
    }
    assert [(item["id"], item["state"]) for item in payload["milestones"]] == [
        ("foundation", "delivered"),
        ("projection", "ready"),
        ("promotion", "ready"),
        ("migration", "waiting"),
        ("shared-service", "deferred"),
    ]
    assert payload["milestones"][0]["unlocks"] == ["projection", "promotion"]

    assert roadmap_next(project, json_output=True) == 0
    next_payload = json.loads(capsys.readouterr().out)
    assert [item["id"] for item in next_payload["recommended"]] == ["projection"]
    assert [item["id"] for item in next_payload["ready"]] == [
        "projection",
        "promotion",
    ]
    assert [item["id"] for item in next_payload["blocked"]] == ["migration"]

    assert roadmap_explain(project, "projection", json_output=True) == 0
    explanation = json.loads(capsys.readouterr().out)
    assert explanation["recommended"] is True
    assert explanation["milestone"]["prerequisites"] == ["foundation"]
    assert explanation["milestone"]["source_contracts"] == [
        "DOC-001#federation"
    ]

    assert roadmap_explain(project, "RM-010") == 0
    assert "milestone\tfoundation\ttitle=Foundation" in capsys.readouterr().out


def test_active_roadmap_is_continued_before_ready_work(tmp_path: Path, capsys) -> None:
    project = _project(tmp_path)
    program = project / "plan" / "program.md"
    text = program.read_text(encoding="utf-8").replace(
        "      state: planned\n      prerequisites: [foundation]\n"
        "      source_contracts: [DOC-001#federation]\n",
        "      roadmap: RM-020\n      prerequisites: [foundation]\n"
        "      source_contracts: [DOC-001#federation]\n",
        1,
    )
    program.write_text(text, encoding="utf-8")
    (project / "plan" / "active.md").write_text(
        "---\nid: RM-020\nrevision: 1\ntype: roadmap\nstatus: active\n---\n"
        "# Active\n",
        encoding="utf-8",
    )
    index = project / "plan" / "README.md"
    index.write_text(index.read_text(encoding="utf-8") + "[Active](active.md)\n")

    assert roadmap_next(project, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["program"]["action"] == "continue"
    assert [item["id"] for item in payload["recommended"]] == ["projection"]


@pytest.mark.parametrize(
    ("old", "new", "code"),
    [
        ("prerequisites: [foundation]", "prerequisites: [missing]", "unknown-prerequisite"),
        ("order: 30", "order: 20", "duplicate-order"),
        ("DOC-001#federation", "DOC-001#missing", "unknown-source-anchor"),
    ],
)
def test_invalid_program_plan_fails_closed_in_cli_validate_and_doctor(
    tmp_path: Path, capsys, old: str, new: str, code: str
) -> None:
    project = _project(tmp_path)
    path = project / "plan" / "program.md"
    path.write_text(path.read_text(encoding="utf-8").replace(old, new, 1))

    assert roadmap_status(project, json_output=True) == 1
    result = capsys.readouterr()
    assert result.out == ""
    assert code in result.err
    assert validate(project) == 1
    assert code in capsys.readouterr().err
    assert doctor(project) == 1
    assert code in capsys.readouterr().err


def test_program_prerequisite_cycle_fails_closed(tmp_path: Path, capsys) -> None:
    project = _project(tmp_path)
    path = project / "plan" / "program.md"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "id: projection\n      title: Federated projection\n      order: 20\n"
        "      priority: 10\n      state: planned\n"
        "      prerequisites: [foundation]",
        "id: projection\n      title: Federated projection\n      order: 20\n"
        "      priority: 10\n      state: planned\n"
        "      prerequisites: [promotion]",
    ).replace(
        "id: promotion\n      title: Knowledge promotion\n      order: 30\n"
        "      priority: 20\n      state: planned\n"
        "      prerequisites: [foundation]",
        "id: promotion\n      title: Knowledge promotion\n      order: 30\n"
        "      priority: 20\n      state: planned\n"
        "      prerequisites: [projection]",
    )
    path.write_text(text, encoding="utf-8")

    assert roadmap_status(project, json_output=True) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "prerequisite-cycle" in captured.err


def test_program_selection_and_parser_contract(tmp_path: Path, capsys) -> None:
    project = _project(tmp_path)
    second = project / "plan" / "second.md"
    second.write_text(
        "---\nid: RM-002\nrevision: 1\ntype: roadmap\nstatus: proposed\n"
        "program_plan:\n  version: 1\n  milestones:\n"
        "    - id: later\n      title: Later\n      order: 1\n"
        "      state: planned\n---\n# Second\n",
        encoding="utf-8",
    )
    index = project / "plan" / "README.md"
    index.write_text(index.read_text(encoding="utf-8") + "[Second](second.md)\n")

    assert roadmap_status(project, json_output=True) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "multiple program plans require --program" in captured.err
    assert roadmap_status(project, program_id="RM-002", json_output=True) == 0
    assert json.loads(capsys.readouterr().out)["program"]["id"] == "RM-002"

    args = build_parser().parse_args(
        ["roadmap", "explain", "projection", str(project), "--program", "RM-001", "--json"]
    )
    assert args.command == "roadmap"
    assert args.roadmap_command == "explain"
    assert args.milestone == "projection"
    assert args.program_id == "RM-001"
    assert args.json_output is True


def test_equal_priority_keeps_multiple_explicit_recommendations(
    tmp_path: Path, capsys
) -> None:
    project = _project(tmp_path)
    path = project / "plan" / "program.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("priority: 20", "priority: 10")
    )

    assert roadmap_next(project, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [item["id"] for item in payload["recommended"]] == [
        "projection",
        "promotion",
    ]


def test_program_cannot_own_itself_as_bounded_roadmap(
    tmp_path: Path, capsys
) -> None:
    project = _project(tmp_path)
    path = project / "plan" / "program.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "roadmap: RM-010", "roadmap: RM-001"
        )
    )

    assert roadmap_status(project, json_output=True) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "self-roadmap-owner" in captured.err


def test_unknown_milestone_explain_has_no_partial_stdout(
    tmp_path: Path, capsys
) -> None:
    project = _project(tmp_path)

    assert roadmap_explain(project, "unknown", json_output=True) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unknown program milestone" in captured.err


@pytest.mark.parametrize("version", ["true", "1.0"])
def test_program_version_requires_exact_non_boolean_integer(
    tmp_path: Path, capsys, version: str
) -> None:
    project = _project(tmp_path)
    path = project / "plan" / "program.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("version: 1", f"version: {version}")
    )

    assert roadmap_status(project, json_output=True) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unsupported-program-version" in captured.err


def test_bounded_roadmap_owner_requires_roadmap_type(
    tmp_path: Path, capsys
) -> None:
    project = _project(tmp_path)
    path = project / "plan" / "foundation.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("type: roadmap", "type: architecture")
    )

    assert roadmap_status(project, json_output=True) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "invalid-roadmap-type" in captured.err


def test_failed_roadmap_remains_visible_as_blocking_evidence(
    tmp_path: Path, capsys
) -> None:
    project = _project(tmp_path)
    path = project / "plan" / "foundation.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("status: completed", "status: failed")
    )

    assert roadmap_next(project, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["program"]["action"] == "blocked"
    assert payload["recommended"] == []
    assert payload["blocked"][0]["id"] == "foundation"
    assert payload["blocked"][0]["reason"] == "roadmap status is failed"


def test_bounded_roadmap_cannot_be_owned_by_multiple_programs(
    tmp_path: Path, capsys
) -> None:
    project = _project(tmp_path)
    second = project / "plan" / "second.md"
    second.write_text(
        "---\nid: RM-002\nrevision: 1\ntype: roadmap\nstatus: proposed\n"
        "program_plan:\n  version: 1\n  milestones:\n"
        "    - id: duplicate-owner\n      title: Duplicate owner\n"
        "      order: 1\n      roadmap: RM-010\n---\n# Second\n",
        encoding="utf-8",
    )
    index = project / "plan" / "README.md"
    index.write_text(index.read_text(encoding="utf-8") + "[Second](second.md)\n")

    assert roadmap_status(project, program_id="RM-001", json_output=True) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cross-program-roadmap-owner" in captured.err
    assert "RM-001, RM-002" in captured.err
    assert validate(project) == 1
    assert "cross-program-roadmap-owner" in capsys.readouterr().err


def test_program_plan_owner_requires_roadmap_type(tmp_path: Path, capsys) -> None:
    project = _project(tmp_path)
    path = project / "plan" / "program.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("type: roadmap", "type: architecture")
    )

    assert roadmap_status(project, json_output=True) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "invalid-program-type" in captured.err


@pytest.mark.parametrize(
    ("needle", "addition", "code"),
    [
        (
            "state: planned\n      prerequisites: [foundation]",
            "state: planned\n      waiting_for: not applicable\n"
            "      prerequisites: [foundation]",
            "unexpected-waiting-condition",
        ),
        (
            "state: planned\n      prerequisites: [foundation]",
            "state: planned\n      reopen_when: not applicable\n"
            "      prerequisites: [foundation]",
            "unexpected-reopen-condition",
        ),
    ],
)
def test_condition_fields_are_valid_only_for_their_effective_state(
    tmp_path: Path, capsys, needle: str, addition: str, code: str
) -> None:
    project = _project(tmp_path)
    path = project / "plan" / "program.md"
    path.write_text(path.read_text(encoding="utf-8").replace(needle, addition, 1))

    assert roadmap_status(project, json_output=True) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert code in captured.err


def test_roadmap_derived_waiting_requires_visible_condition(
    tmp_path: Path, capsys
) -> None:
    project = _project(tmp_path)
    path = project / "plan" / "foundation.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("status: completed", "status: waiting")
    )

    assert roadmap_status(project, json_output=True) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "missing-waiting-condition" in captured.err


def test_roadmap_derived_waiting_uses_authored_visible_condition(
    tmp_path: Path, capsys
) -> None:
    project = _project(tmp_path)
    roadmap = project / "plan" / "foundation.md"
    roadmap.write_text(
        roadmap.read_text(encoding="utf-8").replace(
            "status: completed", "status: waiting"
        )
    )
    program = project / "plan" / "program.md"
    program.write_text(
        program.read_text(encoding="utf-8").replace(
            "roadmap: RM-010\n      source_contracts:",
            "roadmap: RM-010\n      waiting_for: external evidence\n"
            "      source_contracts:",
        )
    )

    assert roadmap_next(project, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["program"]["action"] == "blocked"
    assert payload["blocked"][0]["id"] == "foundation"
    assert payload["blocked"][0]["reason"] == "external evidence"


def test_only_deferred_work_is_not_reported_as_complete(
    tmp_path: Path, capsys
) -> None:
    project = _project(tmp_path)
    path = project / "plan" / "program.md"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "state: planned\n      prerequisites: [foundation]",
        "state: deferred\n      reopen_when: evidence required\n"
        "      prerequisites: [foundation]",
    )
    text = text.replace(
        "state: waiting\n      prerequisites: [projection]\n"
        "      waiting_for: owner-approved workspace location",
        "state: deferred\n      prerequisites: [projection]\n"
        "      reopen_when: evidence required",
    )
    path.write_text(text, encoding="utf-8")

    assert roadmap_next(project, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["program"]["action"] == "deferred"
    assert payload["recommended"] == []
    assert {item["id"] for item in payload["deferred"]} == {
        "projection",
        "promotion",
        "migration",
        "shared-service",
    }
