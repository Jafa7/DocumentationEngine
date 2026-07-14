import hashlib
import json
from pathlib import Path

import pytest

from docsystem.cli import build_parser, execution_handoff, execution_result, lifecycle
from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG
from docsystem.execution import seal_packet


def _project(tmp_path: Path, *, require_review: bool = True) -> Path:
    config = DEFAULT_CONFIG.replace("[areas]\n", '[areas]\nworkspace = "."\n')
    config = config.replace(
        "[identifiers]\n", '[identifiers]\nworkstream = "WS"\n'
    )
    required_evidence = (
        '["changes", "checks", "review", "returns"]'
        if require_review
        else '["changes", "checks", "returns"]'
    )
    config += f"""
[[workstreams.criteria]]
id = "verified-delivery"
revision = 1
required_sections = ["mandate", "boundaries", "review-gate"]
required_evidence = {required_evidence}
max_attempts = 2
safe_fallback = "blocked"

[[admission.criteria]]
id = "bounded-local"
revision = 1
max_autonomy = "A2"
allowed_actions = ["inspect", "plan", "edit-local", "run-checks"]
required_authorizations = ["edit-local"]
allowed_verification = ["full"]
max_risk = "medium"
max_targets = 4
required_sections = ["mandate", "boundaries", "review-gate"]
require_source_scope_for = ["edit-local"]
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

# Documentation

## Return gate

- [Workstream](workstream.md)
- [Target](target.md)
""",
        encoding="utf-8",
    )
    (plan / "workstream.md").write_text(
        """---
id: WS-001
revision: 1
type: workstream
status: active
---

# Workstream

## Mandate

Make one bounded change.

## Boundaries

Do not exceed the declared source scope.

## Review gate

Require checks and independent review.
""",
        encoding="utf-8",
    )
    (plan / "target.md").write_text(
        """---
id: DOC-002
revision: 1
type: architecture
status: active
---

# Target

## Contract

The admitted contract.
""",
        encoding="utf-8",
    )
    return tmp_path


def _admission(source_hash: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "workstream_id": "WS-001",
        "criterion": "bounded-local@1",
        "intake_request_sha256": "a" * 64,
        "outcome": "Make one bounded change.",
        "targets": ["DOC-002#contract"],
        "actions": ["inspect", "plan", "edit-local", "run-checks"],
        "risk": "medium",
        "verification": "full",
        "boundaries": {
            "authored_deletion": False,
            "privacy_boundary": False,
            "permission_expansion": False,
            "external_commitment": False,
        },
        "authorizations": [
            {
                "action": "edit-local",
                "authority": "project-owner",
                "evidence": "user-current-task",
            }
        ],
        "assumptions": ["The mandate remains authoritative."],
        "source_scope": [{"path": "source.txt", "sha256": source_hash}],
    }


def _record() -> dict[str, object]:
    return {
        "schema_version": 1,
        "workstream_id": "WS-001",
        "criterion": "verified-delivery@1",
        "history": [
            {"state": "mandated", "attempt": 1, "evidence": "WS-001#mandate"},
            {"state": "planned", "attempt": 1, "evidence": "plan-v1"},
            {"state": "implementing", "attempt": 1, "evidence": "packet-v1"},
            {"state": "validating", "attempt": 1, "evidence": "checks-v1"},
            {"state": "reviewing", "attempt": 1, "evidence": "review-v1"},
            {"state": "correcting", "attempt": 1, "evidence": "F-01"},
            {"state": "validating", "attempt": 2, "evidence": "checks-v2"},
            {"state": "reviewing", "attempt": 2, "evidence": "review-v2"},
            {"state": "accepted", "attempt": 2, "evidence": "accepted-v2"},
            {"state": "finishing", "attempt": 2, "evidence": "finish-v2"},
            {"state": "completed", "attempt": 2, "evidence": "complete-v2"},
        ],
        "findings": [
            {
                "id": "F-01",
                "attempt": 1,
                "severity": "medium",
                "target": "DOC-002#contract",
                "evidence": "review-v1",
                "correction": "Preserve the admitted contract.",
                "resolved_in_attempt": 2,
            }
        ],
        "evidence": {
            "changes": ["DOC-002#contract"],
            "checks": [
                {"name": "pytest", "status": "passed", "evidence": "passed"}
            ],
            "review": {
                "status": "accepted",
                "independent": True,
                "reviewer": "review-agent",
                "evidence": "review-v2",
            },
            "returns": ["DOC-001#return-gate"],
        },
    }


def _write_json(path: Path, payload: object) -> Path:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    return path


def _artifacts(
    tmp_path: Path, capsys, *, require_review: bool = True
) -> tuple[Path, Path, Path, Path]:
    _project(tmp_path, require_review=require_review)
    source = tmp_path / "source.txt"
    source.write_text("before\n", encoding="utf-8")
    admission_path = _write_json(
        tmp_path / "admission.json",
        _admission(hashlib.sha256(source.read_bytes()).hexdigest()),
    )
    assert execution_handoff(
        tmp_path, "WS-001", admission_path=admission_path, json_output=True
    ) == 0
    packet_path = tmp_path / "packet.json"
    packet_path.write_text(capsys.readouterr().out, encoding="utf-8")
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    source.write_text("after\n", encoding="utf-8")
    result_path = _write_json(
        tmp_path / "result.json",
        {
            "schema_version": 1,
            "workstream_id": "WS-001",
            "packet_sha256": packet["packet_sha256"],
            "changed_files": [
                {
                    "path": "source.txt",
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                }
            ],
        },
    )
    record_path = _write_json(tmp_path / "record.json", _record())
    return admission_path, packet_path, result_path, record_path


def test_lifecycle_validates_complete_cross_artifact_lineage(
    tmp_path: Path, capsys
) -> None:
    admission, packet, result, record = _artifacts(tmp_path, capsys)

    assert lifecycle(
        tmp_path,
        "WS-001",
        admission_path=admission,
        packet_path=packet,
        result_path=result,
        record_path=record,
        json_output=True,
    ) == 0
    first = capsys.readouterr().out
    payload = json.loads(first)
    assert payload["ready_to_finish"] is True
    assert payload["authority"] == "evidence-validation-only"
    assert payload["coverage"] == {
        "targets": ["DOC-002#contract"],
        "targets_covered": True,
    }
    assert payload["execution"]["changed_paths"] == ["source.txt"]
    assert payload["execution"]["source_scope_complete"] is True
    assert payload["workstream"]["attempts"] == 2
    assert payload["workstream"]["findings"] == 1
    assert payload["workstream"]["resolved_findings"] == 1
    assert payload["workstream"]["independent_review"] is True

    assert lifecycle(
        tmp_path,
        "WS-001",
        admission_path=admission,
        packet_path=packet,
        result_path=result,
        record_path=record,
        json_output=True,
    ) == 0
    assert capsys.readouterr().out == first


def test_lifecycle_text_output_is_deterministic(tmp_path: Path, capsys) -> None:
    admission, packet, result, record = _artifacts(tmp_path, capsys)

    assert lifecycle(
        tmp_path,
        "WS-001",
        admission_path=admission,
        packet_path=packet,
        result_path=result,
        record_path=record,
    ) == 0
    output = capsys.readouterr().out
    assert "target_coverage\tcomplete\n" in output
    assert "source_scope\tcomplete\n" in output
    assert "independent_review\taccepted\n" in output
    assert output.endswith("authority\tevidence-validation-only\n")


def test_lifecycle_requires_independent_review_even_when_criterion_does_not(
    tmp_path: Path, capsys
) -> None:
    admission, packet, result, record = _artifacts(
        tmp_path, capsys, require_review=False
    )
    payload = json.loads(record.read_text(encoding="utf-8"))
    payload["evidence"]["review"]["independent"] = False
    _write_json(record, payload)

    assert lifecycle(
        tmp_path,
        "WS-001",
        admission_path=admission,
        packet_path=packet,
        result_path=result,
        record_path=record,
        json_output=True,
    ) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "requires accepted independent review" in captured.err


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("admission", "admission.request_sha256 does not match"),
        ("packet-admission", "admission.boundaries does not match"),
        ("packet-target", "packet targets does not match"),
        ("packet-mandate", "mandate.required_sections does not match"),
        ("packet-kind", "packet kind must be 'execution-handoff'"),
        ("blocked", "execution admission is blocked"),
        ("result", "does not reference the supplied packet"),
        ("record", "omits admitted target"),
        ("source", "result hash does not match current path"),
    ],
)
def test_lifecycle_mismatches_fail_without_partial_stdout(
    tmp_path: Path, capsys, mutation: str, message: str
) -> None:
    admission, packet, result, record = _artifacts(tmp_path, capsys)
    if mutation == "admission":
        payload = json.loads(admission.read_text(encoding="utf-8"))
        payload["assumptions"] = ["A different assumption."]
        _write_json(admission, payload)
    elif mutation in {
        "packet-admission",
        "packet-target",
        "packet-mandate",
        "packet-kind",
    }:
        payload = json.loads(packet.read_text(encoding="utf-8"))
        if mutation == "packet-admission":
            payload["admission"]["boundaries"]["privacy_boundary"] = True
        elif mutation == "packet-target":
            payload["targets"][0]["snapshot"]["address"] = "DOC-002"
        elif mutation == "packet-mandate":
            payload["mandate"]["required_sections"] = payload["mandate"][
                "required_sections"
            ][:-1]
        else:
            payload["kind"] = "other"
        resealed = seal_packet(
            {
                key: value
                for key, value in payload.items()
                if key not in {"schema_version", "packet_sha256"}
            }
        )
        _write_json(packet, resealed)
        result_payload = json.loads(result.read_text(encoding="utf-8"))
        result_payload["packet_sha256"] = resealed["packet_sha256"]
        _write_json(result, result_payload)
    elif mutation == "blocked":
        payload = json.loads(admission.read_text(encoding="utf-8"))
        payload["boundaries"]["privacy_boundary"] = True
        _write_json(admission, payload)
    elif mutation == "result":
        payload = json.loads(result.read_text(encoding="utf-8"))
        payload["packet_sha256"] = "f" * 64
        _write_json(result, payload)
    elif mutation == "record":
        payload = json.loads(record.read_text(encoding="utf-8"))
        payload["evidence"]["changes"] = ["WS-001#mandate"]
        _write_json(record, payload)
    elif mutation == "source":
        (tmp_path / "source.txt").write_text("unexpected\n", encoding="utf-8")

    assert lifecycle(
        tmp_path,
        "WS-001",
        admission_path=admission,
        packet_path=packet,
        result_path=result,
        record_path=record,
        json_output=True,
    ) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert message in captured.err


def test_lifecycle_parser_contract(tmp_path: Path) -> None:
    paths = [tmp_path / name for name in ("admission", "packet", "result", "record")]
    args = build_parser().parse_args(
        [
            "lifecycle",
            "WS-001",
            str(tmp_path),
            "--admission",
            str(paths[0]),
            "--packet",
            str(paths[1]),
            "--result",
            str(paths[2]),
            "--record",
            str(paths[3]),
            "--json",
        ]
    )
    assert args.command == "lifecycle"
    assert args.document_id == "WS-001"
    assert args.admission_path == paths[0]
    assert args.packet == paths[1]
    assert args.result == paths[2]
    assert args.record == paths[3]


def test_execution_result_rejects_non_handoff_packet_kind(
    tmp_path: Path, capsys
) -> None:
    _, packet, result, _ = _artifacts(tmp_path, capsys)
    payload = json.loads(packet.read_text(encoding="utf-8"))
    payload["kind"] = "other"
    resealed = seal_packet(
        {
            key: value
            for key, value in payload.items()
            if key not in {"schema_version", "packet_sha256"}
        }
    )
    _write_json(packet, resealed)
    result_payload = json.loads(result.read_text(encoding="utf-8"))
    result_payload["packet_sha256"] = resealed["packet_sha256"]
    _write_json(result, result_payload)

    assert execution_result(
        tmp_path,
        "WS-001",
        packet_path=packet,
        result_path=result,
        json_output=True,
    ) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "execution packet kind must be 'execution-handoff'" in captured.err
