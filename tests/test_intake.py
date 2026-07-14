import json
from copy import deepcopy
from pathlib import Path

import pytest

from docsystem.cli import build_parser, criteria_registry, idea_intake
from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG


def _project(tmp_path: Path, *, allowed: str | None = None) -> Path:
    config = DEFAULT_CONFIG.replace("[areas]\n", '[areas]\nworkspace = "."\n')
    decisions = allowed or (
        '"update-existing", "create-draft", "create-workstream"'
    )
    config += f"""
[[intake.criteria]]
id = "idea-placement"
revision = 1
allowed_decisions = [{decisions}]
max_candidates = 8
safe_fallback = "blocked"
draft = {{ area = "architecture", type = "architecture", identifier = "document", width = 3 }}
workstream = {{ area = "roadmap", type = "workstream", identifier = "roadmap", width = 3 }}
"""
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    plan = tmp_path / "plan"
    plan.mkdir()
    (plan / "README.md").write_text(
        """---
id: DOC-001
revision: 1
type: architecture
status: active
---

# Existing owner

## Ownership

This section owns the existing contract.
""",
        encoding="utf-8",
    )
    return tmp_path


def _request() -> dict[str, object]:
    return {
        "schema_version": 1,
        "idea_id": "IDEA-001",
        "criterion": "idea-placement@1",
        "outcome": "Add a bounded documentation capability.",
        "source": "human-idea:current-task",
        "candidates": [
            {"address": "DOC-001#ownership", "authority": "owner"}
        ],
        "signals": {
            "authority_conflict": False,
            "incompatible_outcomes": False,
            "independent_lifecycle": False,
            "existing_owner_sufficient": True,
        },
        "assumptions": ["The existing owner remains authoritative."],
    }


def _write_request(tmp_path: Path, request: dict[str, object]) -> Path:
    path = tmp_path / "idea-request.json"
    path.write_text(
        json.dumps(request, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    ("kind", "decision", "target"),
    [
        ("existing", "update-existing", {"address": "DOC-001#ownership"}),
        (
            "workstream",
            "create-workstream",
            {
                "area": "roadmap",
                "id": "RM-001",
                "identifier": "roadmap",
                "path": "roadmap/rm-001.md",
                "type": "workstream",
                "width": 3,
            },
        ),
        (
            "draft",
            "create-draft",
            {
                "area": "architecture",
                "id": "DOC-002",
                "identifier": "document",
                "path": "architecture/doc-002.md",
                "type": "architecture",
                "width": 3,
            },
        ),
    ],
)
def test_intake_selects_one_explainable_read_only_placement(
    tmp_path: Path, capsys, kind: str, decision: str, target: dict[str, object]
) -> None:
    _project(tmp_path)
    request = _request()
    signals = request["signals"]
    assert isinstance(signals, dict)
    if kind == "workstream":
        signals["existing_owner_sufficient"] = False
        signals["independent_lifecycle"] = True
    elif kind == "draft":
        signals["existing_owner_sufficient"] = False
    request_path = _write_request(tmp_path, request)
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    assert idea_intake(tmp_path, request_path=request_path, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == decision
    assert payload["blocked"] is False
    assert payload["target"] == target
    assert len(payload["allocation_guard"]) == 64
    assert len(payload["request_sha256"]) == 64
    assert len(payload["outcome_sha256"]) == 64
    assert "outcome" not in payload
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == before


def test_intake_normalizes_candidate_and_assumption_order(
    tmp_path: Path, capsys
) -> None:
    _project(tmp_path)
    first = _request()
    candidates = first["candidates"]
    assumptions = first["assumptions"]
    assert isinstance(candidates, list)
    assert isinstance(assumptions, list)
    candidates.append({"address": "DOC-001", "authority": "related"})
    assumptions.append("The new outcome remains bounded.")
    second = deepcopy(first)
    second["candidates"] = list(reversed(candidates))
    second["assumptions"] = list(reversed(assumptions))

    first_path = _write_request(tmp_path, first)
    assert idea_intake(tmp_path, request_path=first_path, json_output=True) == 0
    first_payload = json.loads(capsys.readouterr().out)
    second_path = _write_request(tmp_path, second)
    assert idea_intake(tmp_path, request_path=second_path, json_output=True) == 0
    second_payload = json.loads(capsys.readouterr().out)

    assert second_payload == first_payload


@pytest.mark.parametrize(
    ("mutation", "reason", "requested"),
    [
        ("authority", "authority-conflict", None),
        ("outcomes", "incompatible-outcomes", None),
        ("contradictory", "contradictory-owner-and-lifecycle", None),
        ("ambiguous-owner", "owner-evidence-ambiguous", "update-existing"),
    ],
)
def test_intake_returns_visible_blocked_decisions(
    tmp_path: Path, capsys, mutation: str, reason: str, requested: str | None
) -> None:
    _project(tmp_path)
    request = _request()
    signals = request["signals"]
    candidates = request["candidates"]
    assert isinstance(signals, dict)
    assert isinstance(candidates, list)
    if mutation == "authority":
        signals["authority_conflict"] = True
    elif mutation == "outcomes":
        signals["incompatible_outcomes"] = True
    elif mutation == "contradictory":
        signals["independent_lifecycle"] = True
    elif mutation == "ambiguous-owner":
        candidates[0]["authority"] = "related"
    path = _write_request(tmp_path, request)

    assert idea_intake(tmp_path, request_path=path, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "blocked"
    assert payload["blocked"] is True
    assert reason in payload["reasons"]
    assert payload["requested_decision"] == requested
    assert payload["target"] is None


def test_intake_blocks_a_valid_but_unauthorized_decision(
    tmp_path: Path, capsys
) -> None:
    _project(tmp_path, allowed='"update-existing"')
    request = _request()
    signals = request["signals"]
    assert isinstance(signals, dict)
    signals["existing_owner_sufficient"] = False
    path = _write_request(tmp_path, request)

    assert idea_intake(tmp_path, request_path=path, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "blocked"
    assert payload["reasons"] == ["decision-not-authorized"]
    assert payload["requested_decision"] == "create-draft"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("unknown-address", "unknown section address"),
        ("duplicate-candidate", "duplicate candidate address"),
        ("too-many", "criterion allows 8"),
        ("oversized", "exceeds the bounded size"),
        ("bad-signal", "must be a boolean"),
    ],
)
def test_invalid_intake_requests_fail_without_partial_stdout(
    tmp_path: Path, capsys, mutation: str, message: str
) -> None:
    _project(tmp_path)
    request = deepcopy(_request())
    candidates = request["candidates"]
    signals = request["signals"]
    assert isinstance(candidates, list)
    assert isinstance(signals, dict)
    if mutation == "unknown-address":
        candidates[0]["address"] = "DOC-001#missing"
    elif mutation == "duplicate-candidate":
        candidates.append(deepcopy(candidates[0]))
    elif mutation == "too-many":
        candidates.clear()
        candidates.extend(
            {"address": "DOC-001#ownership", "authority": "related"}
            for _ in range(9)
        )
        for index, candidate in enumerate(candidates):
            candidate["address"] = "DOC-001" if index == 0 else f"DOC-001#ownership-{index}"
    elif mutation == "oversized":
        request["outcome"] = "x" * (70 * 1024)
    elif mutation == "bad-signal":
        signals["authority_conflict"] = "no"
    path = _write_request(tmp_path, request)

    assert idea_intake(tmp_path, request_path=path, json_output=True) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert message in captured.err


def test_criteria_and_parser_expose_intake_contract(
    tmp_path: Path, capsys
) -> None:
    _project(tmp_path)
    assert criteria_registry(tmp_path, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["criteria"] == []
    assert payload["intake"][0]["reference"] == "idea-placement@1"
    assert payload["intake"][0]["draft"] == {
        "area": "architecture",
        "type": "architecture",
        "identifier": "document",
        "width": 3,
    }

    request_path = tmp_path / "idea-request.json"
    args = build_parser().parse_args(
        ["intake", str(tmp_path), "--request", str(request_path), "--json"]
    )
    assert args.command == "intake"
    assert args.request_path == request_path
