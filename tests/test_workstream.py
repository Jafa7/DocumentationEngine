import json
from copy import deepcopy
from pathlib import Path

import pytest

from docsystem.cli import (
    build_parser,
    criteria_registry,
    finish,
    workstream_status,
)
from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG


def _project(tmp_path: Path) -> Path:
    config = DEFAULT_CONFIG.replace("[areas]\n", '[areas]\nworkspace = "."\n')
    config += """
[[workstreams.criteria]]
id = "verified-delivery"
revision = 1
required_sections = ["why-this-branch-exists", "mandate", "review-gate"]
required_evidence = ["changes", "checks", "review", "omissions", "risks", "returns"]
max_attempts = 2
safe_fallback = "blocked"
"""
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    plan = tmp_path / "plan"
    plan.mkdir()
    (plan / "README.md").write_text(
        """---
id: DOC-001
revision: 1
type: index
status: active
---

# Parent

## Return gate

Parent acceptance lives here.

- [Workstream](workstream.md)
""",
        encoding="utf-8",
    )
    (plan / "workstream.md").write_text(
        """---
id: DOC-002
revision: 1
type: workstream
status: active
derived_from: [DOC-001]
---

# Workstream

## Why this branch exists

Implement one bounded outcome.

## Mandate

Change only the declared target.

## Review gate

Checks and independent review must pass.
""",
        encoding="utf-8",
    )
    return tmp_path


def _record(*, correction: bool = True) -> dict[str, object]:
    history = [
        {"state": "mandated", "attempt": 1, "evidence": "DOC-002#mandate"},
        {"state": "planned", "attempt": 1, "evidence": "plan-v1"},
        {"state": "implementing", "attempt": 1, "evidence": "patch-v1"},
        {"state": "validating", "attempt": 1, "evidence": "pytest-v1"},
        {"state": "reviewing", "attempt": 1, "evidence": "review-v1"},
    ]
    findings: list[dict[str, object]] = []
    if correction:
        history.extend(
            [
                {"state": "correcting", "attempt": 1, "evidence": "F-01"},
                {"state": "validating", "attempt": 2, "evidence": "pytest-v2"},
                {"state": "reviewing", "attempt": 2, "evidence": "review-v2"},
            ]
        )
        findings.append(
            {
                "id": "F-01",
                "attempt": 1,
                "severity": "medium",
                "target": "DOC-002#mandate",
                "evidence": "review-v1",
                "correction": "Preserve the bounded target.",
                "resolved_in_attempt": 2,
            }
        )
        final_attempt = 2
    else:
        final_attempt = 1
    history.extend(
        [
            {"state": "accepted", "attempt": final_attempt, "evidence": "review-ok"},
            {"state": "finishing", "attempt": final_attempt, "evidence": "handoff"},
            {"state": "completed", "attempt": final_attempt, "evidence": "finish-v1"},
        ]
    )
    return {
        "schema_version": 1,
        "workstream_id": "DOC-002",
        "criterion": "verified-delivery@1",
        "history": history,
        "findings": findings,
        "evidence": {
            "changes": ["DOC-002#mandate"],
            "checks": [
                {"name": "pytest", "status": "passed", "evidence": "464 passed"}
            ],
            "review": {
                "status": "accepted",
                "independent": True,
                "reviewer": "review-agent",
                "evidence": "review-v2",
            },
            "omissions": [],
            "risks": [],
            "returns": ["DOC-001#return-gate"],
        },
    }


def _write_record(tmp_path: Path, record: dict[str, object]) -> Path:
    path = tmp_path / "workstream-record.json"
    path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return path


def test_criteria_and_completed_workstream_outputs_are_deterministic(
    tmp_path: Path, capsys
) -> None:
    _project(tmp_path)
    record_path = _write_record(tmp_path, _record())

    assert criteria_registry(tmp_path, json_output=True) == 0
    criteria = json.loads(capsys.readouterr().out)
    assert criteria["criteria"] == [
        {
            "id": "verified-delivery",
            "revision": 1,
            "reference": "verified-delivery@1",
            "required_sections": [
                "why-this-branch-exists",
                "mandate",
                "review-gate",
            ],
            "required_evidence": [
                "changes",
                "checks",
                "review",
                "omissions",
                "risks",
                "returns",
            ],
            "max_attempts": 2,
            "safe_fallback": "blocked",
        }
    ]

    assert (
        workstream_status(
            tmp_path, "DOC-002", record_path=record_path, json_output=True
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["final_state"] == "completed"
    assert payload["attempts"] == 2
    assert payload["findings"] == 1
    assert payload["resolved_findings"] == 1
    assert payload["ready_to_finish"] is True
    assert payload["evidence"]["changes"] == ["DOC-002#mandate"]

    assert finish(
        tmp_path,
        "DOC-002",
        json_output=True,
        workstream_record=record_path,
    ) == 0
    finish_payload = json.loads(capsys.readouterr().out)
    assert finish_payload["workstream"] == {
        key: value for key, value in payload.items() if key != "schema_version"
    }

    criteria_args = build_parser().parse_args(["criteria", str(tmp_path), "--json"])
    assert criteria_args.command == "criteria"
    workstream_args = build_parser().parse_args(
        ["workstream", "DOC-002", str(tmp_path), "--record", str(record_path)]
    )
    assert workstream_args.command == "workstream"
    finish_args = build_parser().parse_args(
        [
            "finish",
            "DOC-002",
            str(tmp_path),
            "--workstream-record",
            str(record_path),
        ]
    )
    assert finish_args.workstream_record == record_path


def test_active_record_is_inspectable_but_strict_finish_fails_closed(
    tmp_path: Path, capsys
) -> None:
    _project(tmp_path)
    record = _record(correction=False)
    record["history"] = record["history"][:4]
    record["evidence"] = {}
    record_path = _write_record(tmp_path, record)

    assert workstream_status(tmp_path, "DOC-002", record_path=record_path) == 0
    output = capsys.readouterr().out
    assert "Final state: validating" in output
    assert "Ready to finish: no" in output

    assert finish(tmp_path, "DOC-002", workstream_record=record_path) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "final state is not completed" in captured.err


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("illegal-transition", "illegal workstream transition"),
        ("missing-evidence", "missing required evidence"),
        ("failed-check", "non-passing check"),
        ("rejected-review", "accepted independent review"),
        ("unresolved-finding", "unresolved finding"),
        ("missing-correction-finding", "no finding evidence"),
        ("too-many-attempts", "criterion allows 2"),
        ("unknown-address", "unknown section address"),
        ("oversized-record", "exceeds the bounded size"),
    ],
)
def test_invalid_completion_claims_fail_without_partial_stdout(
    tmp_path: Path, capsys, mutation: str, message: str
) -> None:
    _project(tmp_path)
    record = deepcopy(_record())
    history = record["history"]
    evidence = record["evidence"]
    findings = record["findings"]
    assert isinstance(history, list)
    assert isinstance(evidence, dict)
    assert isinstance(findings, list)
    if mutation == "illegal-transition":
        history[1]["state"] = "reviewing"
    elif mutation == "missing-evidence":
        del evidence["omissions"]
    elif mutation == "failed-check":
        evidence["checks"][0]["status"] = "failed"
    elif mutation == "rejected-review":
        evidence["review"]["status"] = "rejected"
    elif mutation == "unresolved-finding":
        findings[0]["resolved_in_attempt"] = None
    elif mutation == "missing-correction-finding":
        findings.clear()
    elif mutation == "too-many-attempts":
        history[8:8] = [
            {"state": "correcting", "attempt": 2, "evidence": "F-02"},
            {"state": "validating", "attempt": 3, "evidence": "pytest-v3"},
            {"state": "reviewing", "attempt": 3, "evidence": "review-v3"},
        ]
        for entry in history[11:]:
            entry["attempt"] = 3
        findings.append(
            {
                "id": "F-02",
                "attempt": 2,
                "severity": "low",
                "target": "DOC-002#review-gate",
                "evidence": "review-v2",
                "correction": "Clarify evidence.",
                "resolved_in_attempt": 3,
            }
        )
    elif mutation == "unknown-address":
        evidence["changes"] = ["DOC-002#missing"]
    elif mutation == "oversized-record":
        evidence["risks"] = ["x" * (300 * 1024)]
    record_path = _write_record(tmp_path, record)

    assert (
        workstream_status(
            tmp_path, "DOC-002", record_path=record_path, json_output=True
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert message in captured.err
