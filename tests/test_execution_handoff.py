import json
from copy import deepcopy
from pathlib import Path

import pytest

from docsystem.cli import build_parser, execution_handoff
from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG
from docsystem.execution import ExecutionPacketError, load_packet, seal_packet


def _project(tmp_path: Path) -> Path:
    config = DEFAULT_CONFIG.replace("[areas]\n", '[areas]\nworkspace = "."\n')
    config = config.replace(
        "[identifiers]\n", '[identifiers]\nworkstream = "WS"\n'
    )
    config += """
[[admission.criteria]]
id = "bounded-local"
revision = 1
max_autonomy = "A2"
allowed_actions = ["inspect", "plan", "edit-local", "run-checks"]
required_authorizations = ["edit-local"]
allowed_verification = ["focused", "full"]
max_risk = "medium"
max_targets = 4
required_sections = ["mandate", "boundaries", "review-gate"]
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

- [Workstream](workstream.md)
- [Target](target.md)
- [Dependency](dependency.md)
- [Consumer](consumer.md)
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

<a id="mandate"></a>
## Mandate

Implement one bounded target.

<a id="boundaries"></a>
## Boundaries

No external action.

<a id="review-gate"></a>
## Review gate

Run focused checks and review the impact manifest.
""",
        encoding="utf-8",
    )
    (plan / "target.md").write_text(
        """---
id: DOC-002
revision: 1
type: architecture
status: active
depends_on: [DOC-003]
---

# Target

<a id="contract"></a>
## Contract

The exact source bytes that an executor may inspect.
""",
        encoding="utf-8",
    )
    (plan / "dependency.md").write_text(
        """---
id: DOC-003
revision: 1
type: architecture
status: active
---

# Dependency

The target depends on this contract.
""",
        encoding="utf-8",
    )
    (plan / "consumer.md").write_text(
        """---
id: DOC-004
revision: 1
type: architecture
status: active
depends_on: [DOC-002]
---

# Consumer

This document consumes the target.
""",
        encoding="utf-8",
    )
    return tmp_path


def _request() -> dict[str, object]:
    return {
        "schema_version": 1,
        "workstream_id": "WS-001",
        "criterion": "bounded-local@1",
        "intake_request_sha256": "a" * 64,
        "outcome": "Change the private outcome body that must not enter the packet.",
        "targets": ["DOC-002#contract"],
        "actions": ["inspect", "plan", "edit-local", "run-checks"],
        "risk": "medium",
        "verification": "focused",
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
    }


def _write_request(tmp_path: Path, request: dict[str, object]) -> Path:
    path = tmp_path / "admission.json"
    path.write_text(json.dumps(request, sort_keys=True), encoding="utf-8")
    return path


def test_execution_handoff_is_body_free_deterministic_and_read_only(
    tmp_path: Path, capsys
) -> None:
    _project(tmp_path)
    admission = _write_request(tmp_path, _request())
    before = {
        item.relative_to(tmp_path): item.read_bytes()
        for item in tmp_path.rglob("*")
        if item.is_file()
    }

    assert execution_handoff(
        tmp_path,
        "WS-001",
        admission_path=admission,
        json_output=True,
    ) == 0
    first_text = capsys.readouterr().out
    packet = json.loads(first_text)
    assert packet["kind"] == "execution-handoff"
    assert packet["workstream_id"] == "WS-001"
    assert packet["admission"]["decision"] == "admitted"
    assert packet["context_manifest"]["content_embedded"] is False
    assert "DOC-002#contract" in packet["context_manifest"]["read"]
    assert "DOC-003" in packet["context_manifest"]["read"]
    assert "DOC-004" in packet["context_manifest"]["review"]
    assert "WS-001#mandate" in packet["context_manifest"]["read"]
    assert "WS-001#boundaries" in packet["context_manifest"]["read"]
    assert "WS-001#review-gate" in packet["context_manifest"]["read"]
    assert not (
        set(packet["context_manifest"]["read"])
        & set(packet["context_manifest"]["review"])
    )
    assert len(packet["targets"][0]["change_plans"]) == 2
    snapshot = packet["targets"][0]["snapshot"]
    assert snapshot["address"] == "DOC-002#contract"
    assert len(snapshot["document_sha256"]) == 64
    assert len(snapshot["section"]["sha256"]) == 64
    assert len(packet["packet_sha256"]) == 64
    assert "private outcome body" not in first_text
    assert "exact source bytes" not in first_text

    assert execution_handoff(
        tmp_path,
        "WS-001",
        admission_path=admission,
        json_output=True,
    ) == 0
    assert capsys.readouterr().out == first_text
    after = {
        item.relative_to(tmp_path): item.read_bytes()
        for item in tmp_path.rglob("*")
        if item.is_file()
    }
    assert after == before


def test_execution_handoff_verifies_current_and_rejects_stale_source(
    tmp_path: Path, capsys
) -> None:
    _project(tmp_path)
    admission = _write_request(tmp_path, _request())
    assert execution_handoff(
        tmp_path, "WS-001", admission_path=admission, json_output=True
    ) == 0
    packet_path = tmp_path / "packet.json"
    packet_path.write_text(capsys.readouterr().out, encoding="utf-8")

    assert execution_handoff(
        tmp_path,
        "WS-001",
        admission_path=admission,
        verify_path=packet_path,
        json_output=True,
    ) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["current"] is True

    target = tmp_path / "plan" / "target.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "The exact source bytes", "Changed source bytes"
        ),
        encoding="utf-8",
    )
    assert execution_handoff(
        tmp_path,
        "WS-001",
        admission_path=admission,
        verify_path=packet_path,
        json_output=True,
    ) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "packet is stale" in captured.err


def test_execution_handoff_rejects_blocked_admission_without_stdout(
    tmp_path: Path, capsys
) -> None:
    _project(tmp_path)
    request = _request()
    boundaries = request["boundaries"]
    assert isinstance(boundaries, dict)
    boundaries["permission_expansion"] = True
    admission = _write_request(tmp_path, request)

    assert execution_handoff(
        tmp_path, "WS-001", admission_path=admission, json_output=True
    ) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "execution admission is blocked" in captured.err


def test_packet_integrity_and_size_are_fail_closed(tmp_path: Path) -> None:
    packet = seal_packet({"kind": "execution-handoff", "value": "safe"})
    path = tmp_path / "packet.json"
    path.write_text(json.dumps(packet), encoding="utf-8")
    assert load_packet(path) == packet

    tampered = deepcopy(packet)
    tampered["value"] = "changed"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ExecutionPacketError, match="integrity hash does not match"):
        load_packet(path)

    with pytest.raises(ExecutionPacketError, match="exceeds the bounded size"):
        seal_packet({"kind": "execution-handoff", "value": "x" * (2 * 1024 * 1024)})


def test_execution_handoff_parser_contract(tmp_path: Path) -> None:
    admission = tmp_path / "admission.json"
    packet = tmp_path / "packet.json"
    args = build_parser().parse_args(
        [
            "execution-handoff",
            "WS-001",
            str(tmp_path),
            "--admission",
            str(admission),
            "--verify",
            str(packet),
            "--json",
        ]
    )
    assert args.command == "execution-handoff"
    assert args.document_id == "WS-001"
    assert args.admission_path == admission
    assert args.verify_path == packet
