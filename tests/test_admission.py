import json
from copy import deepcopy
from pathlib import Path

import pytest

from docsystem.admission import load_request
from docsystem.cli import build_parser, criteria_registry, execution_admission
from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG


def _project(
    tmp_path: Path,
    *,
    max_autonomy: str = "A2",
    allowed_actions: str = '"inspect", "plan", "edit-local", "run-checks"',
    required_authorizations: str = '"edit-local"',
    required_sections: str = '"mandate", "boundaries", "review-gate"',
    require_source_scope_for: str = "",
) -> Path:
    config = DEFAULT_CONFIG.replace("[areas]\n", '[areas]\nworkspace = "."\n')
    config = config.replace(
        "[identifiers]\n", '[identifiers]\nworkstream = "WS"\n'
    )
    config += f"""
[[admission.criteria]]
id = "bounded-local"
revision = 1
max_autonomy = "{max_autonomy}"
allowed_actions = [{allowed_actions}]
required_authorizations = [{required_authorizations}]
allowed_verification = ["focused", "full"]
max_risk = "medium"
max_targets = 4
required_sections = [{required_sections}]
require_source_scope_for = [{require_source_scope_for}]
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

Make one bounded local change.

<a id="boundaries"></a>
## Boundaries

No destructive or external operations.

<a id="review-gate"></a>
## Review gate

Run focused checks and independent host review.
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

<a id="contract"></a>
## Contract

The bounded target.
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
        "outcome": "Make one bounded local change.",
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
        "assumptions": ["The workstream mandate remains authoritative."],
    }


def _write_request(
    tmp_path: Path, request: dict[str, object], name: str = "admission.json"
) -> Path:
    path = tmp_path / name
    path.write_text(
        json.dumps(request, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    return path


def test_admission_accepts_bounded_a2_intent_without_writes(
    tmp_path: Path, capsys
) -> None:
    _project(tmp_path)
    path = _write_request(tmp_path, _request())
    before = {
        item.relative_to(tmp_path): item.read_bytes()
        for item in tmp_path.rglob("*")
        if item.is_file()
    }

    assert execution_admission(
        tmp_path, "WS-001", request_path=path, json_output=True
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["decision"] == "admitted"
    assert payload["blocked"] is False
    assert payload["reasons"] == ["policy-satisfied"]
    assert payload["required_autonomy"] == "A2"
    assert payload["targets"] == ["DOC-002#contract"]
    assert payload["missing_authorizations"] == []
    assert len(payload["request_sha256"]) == 64
    assert len(payload["outcome_sha256"]) == 64
    assert len(payload["catalog_guard"]) == 64
    assert "outcome" not in payload
    after = {
        item.relative_to(tmp_path): item.read_bytes()
        for item in tmp_path.rglob("*")
        if item.is_file()
    }
    assert after == before


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("deletion", "boundary:authored-deletion"),
        ("privacy", "boundary:privacy-boundary"),
        ("permission", "boundary:permission-expansion"),
        ("external", "boundary:external-commitment"),
        ("risk", "risk-exceeds-policy"),
        ("verification", "verification-not-allowed"),
        ("authorization", "authorization-missing:edit-local"),
    ],
)
def test_admission_returns_explainable_blocked_results(
    tmp_path: Path, capsys, mutation: str, reason: str
) -> None:
    _project(tmp_path)
    request = _request()
    boundaries = request["boundaries"]
    assert isinstance(boundaries, dict)
    if mutation == "deletion":
        boundaries["authored_deletion"] = True
    elif mutation == "privacy":
        boundaries["privacy_boundary"] = True
    elif mutation == "permission":
        boundaries["permission_expansion"] = True
    elif mutation == "external":
        boundaries["external_commitment"] = True
    elif mutation == "risk":
        request["risk"] = "high"
    elif mutation == "verification":
        request["verification"] = "structural"
    elif mutation == "authorization":
        request["authorizations"] = []
    path = _write_request(tmp_path, request)

    assert execution_admission(
        tmp_path, "WS-001", request_path=path, json_output=True
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "blocked"
    assert payload["blocked"] is True
    assert reason in payload["reasons"]


def test_admission_policy_can_require_source_scope_for_local_edits(
    tmp_path: Path, capsys
) -> None:
    _project(tmp_path, require_source_scope_for='"edit-local"')
    path = _write_request(tmp_path, _request())

    assert execution_admission(
        tmp_path, "WS-001", request_path=path, json_output=True
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "blocked"
    assert payload["reasons"] == ["source-scope-required:edit-local"]


def test_disallowed_action_is_blocked_with_required_autonomy(
    tmp_path: Path, capsys
) -> None:
    _project(
        tmp_path,
        max_autonomy="A1",
        allowed_actions='"inspect", "plan"',
        required_authorizations="",
    )
    request = _request()
    request["actions"] = ["inspect", "edit-local"]
    request["authorizations"] = []
    path = _write_request(tmp_path, request)

    assert execution_admission(
        tmp_path, "WS-001", request_path=path, json_output=True
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "blocked"
    assert "action-not-allowed:edit-local" in payload["reasons"]
    assert "autonomy-exceeds-policy" in payload["reasons"]
    assert payload["required_autonomy"] == "A2"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("unknown-target", "unknown section address"),
        ("workstream-mismatch", "does not match"),
        ("bad-hash", "must be null or a lowercase SHA-256"),
        ("unrequested-authorization", "unrequested action"),
        ("too-many-targets", "criterion allows 4"),
    ],
)
def test_invalid_admission_requests_fail_without_partial_stdout(
    tmp_path: Path, capsys, mutation: str, message: str
) -> None:
    _project(tmp_path)
    request = deepcopy(_request())
    if mutation == "unknown-target":
        request["targets"] = ["DOC-002#missing"]
    elif mutation == "workstream-mismatch":
        request["workstream_id"] = "WS-999"
    elif mutation == "bad-hash":
        request["intake_request_sha256"] = "not-a-hash"
    elif mutation == "unrequested-authorization":
        request["actions"] = ["inspect"]
    elif mutation == "too-many-targets":
        request["targets"] = [
            "DOC-002",
            "DOC-002#contract",
            "WS-001",
            "WS-001#mandate",
            "WS-001#boundaries",
        ]
    path = _write_request(tmp_path, request)

    assert execution_admission(
        tmp_path, "WS-001", request_path=path, json_output=True
    ) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert message in captured.err


def test_missing_required_mandate_section_fails_closed(
    tmp_path: Path, capsys
) -> None:
    _project(tmp_path, required_sections='"mandate", "return-protocol"')
    path = _write_request(tmp_path, _request())

    assert execution_admission(
        tmp_path, "WS-001", request_path=path, json_output=True
    ) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "missing required section(s): return-protocol" in captured.err


def test_terminal_workstream_cannot_be_admitted(
    tmp_path: Path, capsys
) -> None:
    _project(tmp_path)
    mandate = tmp_path / "plan" / "workstream.md"
    mandate.write_text(
        mandate.read_text(encoding="utf-8").replace(
            "status: active", "status: completed"
        ),
        encoding="utf-8",
    )
    path = _write_request(tmp_path, _request())

    assert execution_admission(
        tmp_path, "WS-001", request_path=path, json_output=True
    ) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "has terminal status 'completed'" in captured.err


def test_legacy_workstream_may_declare_absent_intake_provenance(
    tmp_path: Path, capsys
) -> None:
    _project(tmp_path)
    request = _request()
    request["intake_request_sha256"] = None
    path = _write_request(tmp_path, request)

    assert execution_admission(
        tmp_path, "WS-001", request_path=path, json_output=True
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "admitted"
    assert payload["intake_request_sha256"] is None


def test_omitted_source_scope_preserves_legacy_request_hash(tmp_path: Path) -> None:
    request = _request()
    omitted = _write_request(tmp_path, request, "omitted.json")
    request["source_scope"] = []
    explicit_empty = _write_request(tmp_path, request, "empty.json")

    expected = "43efda6c89fc7d09b9fefcc06da4b8054141802f9d95b726ab68e03f04854400"
    assert load_request(omitted).request_sha256 == expected
    assert load_request(explicit_empty).request_sha256 == expected


def test_admission_normalizes_semantically_equivalent_request_order(
    tmp_path: Path, capsys
) -> None:
    _project(tmp_path)
    first = _request()
    first["targets"] = ["DOC-002#contract", "WS-001#mandate"]
    first["assumptions"] = ["Second assumption.", "First assumption."]
    second = deepcopy(first)
    second["targets"] = list(reversed(first["targets"]))
    second["actions"] = list(reversed(first["actions"]))
    second["assumptions"] = list(reversed(first["assumptions"]))

    first_path = _write_request(tmp_path, first, "first.json")
    assert execution_admission(
        tmp_path, "WS-001", request_path=first_path, json_output=True
    ) == 0
    first_payload = json.loads(capsys.readouterr().out)
    second_path = _write_request(tmp_path, second, "second.json")
    assert execution_admission(
        tmp_path, "WS-001", request_path=second_path, json_output=True
    ) == 0
    second_payload = json.loads(capsys.readouterr().out)

    assert second_payload == first_payload


def test_criteria_and_parser_expose_admission_contract(
    tmp_path: Path, capsys
) -> None:
    _project(tmp_path)
    assert criteria_registry(tmp_path, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["admission"][0]["reference"] == "bounded-local@1"
    assert payload["admission"][0]["max_autonomy"] == "A2"

    request_path = tmp_path / "admission.json"
    args = build_parser().parse_args(
        [
            "admission",
            "WS-001",
            str(tmp_path),
            "--request",
            str(request_path),
            "--json",
        ]
    )
    assert args.command == "admission"
    assert args.document_id == "WS-001"
    assert args.request_path == request_path
