import json
from pathlib import Path

import pytest

from docsystem.cli import main
from docsystem.config import CONFIG_FILENAME


def _run(monkeypatch: pytest.MonkeyPatch, *arguments: str) -> int:
    monkeypatch.setattr("sys.argv", ["docsystem", *arguments])
    return main()


def _project(tmp_path: Path, *, history_mode: str = "living") -> Path:
    project = tmp_path / "project"
    docs = project / "docs"
    docs.mkdir(parents=True)
    (project / CONFIG_FILENAME).write_text(
        f'''version = 1

[documentation]
root = "docs"
language = "en"

[areas]
docs = "."

[identifiers]
document = "DOC"

[projection]
format = "sharded-json"
keep_generations = 2

[profiles.authority]
document_types = ["canonical"]
history_mode = "{history_mode}"
''',
        encoding="utf-8",
    )
    (docs / "README.md").write_text(
        "---\nid: DOC-001\nrevision: 1\ntype: index\nstatus: active\n---\n"
        "# Index\n\n[Candidate](candidate.md)\n[Owner](owner.md)\n"
        "[Evidence](evidence.md)\n",
        encoding="utf-8",
    )
    (docs / "candidate.md").write_text(
        "---\nid: DOC-002\nrevision: 3\ntype: review\nstatus: active\n---\n"
        "# Candidate\n\n<a id=\"finding\"></a>\n## Finding\n\nPrivate finding body.\n",
        encoding="utf-8",
    )
    (docs / "owner.md").write_text(
        "---\nid: DOC-003\nrevision: 5\ntype: canonical\nstatus: active\n"
        "authority_for: [install-policy]\n---\n"
        "# Owner\n\n<a id=\"contract\"></a>\n## Contract\n\nCanonical body.\n",
        encoding="utf-8",
    )
    (docs / "evidence.md").write_text(
        "---\nid: DOC-004\nrevision: 2\ntype: experiment\nstatus: completed\n"
        "depends_on: [DOC-003]\n---\n"
        "# Evidence\n\n<a id=\"result\"></a>\n## Result\n\nEvidence body.\n",
        encoding="utf-8",
    )
    return project


def _request(path: Path, **overrides) -> Path:
    payload = {
        "schema_version": 1,
        "source": "DOC-002#finding",
        "destination": "DOC-003#contract",
        "authority_key": "install-policy",
        "knowledge_state": "fact",
        "disposition": "accepted",
        "evidence": ["DOC-004#result"],
        **overrides,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_promotion_plans_living_owner_update_without_reading_bodies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = _project(tmp_path)
    request = _request(tmp_path / "request.json")
    assert _run(monkeypatch, "promotion", str(project), "--request", str(request), "--json") == 0
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert output.err == ""
    assert payload["state"] == "ready"
    assert payload["ready_to_promote"] is True
    assert payload["action"] == "revise-owner"
    assert payload["destination"]["history_mode"] == "living"
    assert payload["provenance_pins"] == ["DOC-002@3", "DOC-004@2"]
    assert payload["impacted_documents"] == ["DOC-004"]
    assert payload["impact_scope"] == "destination-document-metadata-consumers"
    assert payload["conflicts"] == []
    assert "document-body-not-included" in payload["omissions"]
    assert "Private finding body" not in output.out
    assert str(tmp_path) not in output.out


@pytest.mark.parametrize(
    ("history_mode", "action"),
    [
        ("append-only", "append-record"),
        ("immutable-after-state", "create-superseding-document"),
    ],
)
def test_promotion_respects_authored_history_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    history_mode: str,
    action: str,
) -> None:
    project = _project(tmp_path, history_mode=history_mode)
    request = _request(tmp_path / "request.json")
    assert _run(monkeypatch, "promotion", str(project), "--request", str(request), "--json") == 0
    assert json.loads(capsys.readouterr().out)["action"] == action


def test_promotion_blocks_competing_authority_claims(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = _project(tmp_path)
    (project / "docs" / "other.md").write_text(
        "---\nid: DOC-005\nrevision: 1\ntype: canonical\nstatus: active\n"
        "authority_for: [install-policy]\n---\n# Other owner\n",
        encoding="utf-8",
    )
    index = project / "docs" / "README.md"
    index.write_text(index.read_text() + "[Other](other.md)\n", encoding="utf-8")
    request = _request(tmp_path / "request.json")
    assert _run(monkeypatch, "promotion", str(project), "--request", str(request), "--json") == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "blocked"
    assert payload["action"] == "resolve-authority-conflict"
    assert payload["conflicts"] == ["DOC-005"]


def test_promotion_retains_unaccepted_or_hypothetical_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = _project(tmp_path)
    request = _request(
        tmp_path / "request.json",
        knowledge_state="hypothesis",
        disposition="deferred",
        evidence=[],
    )
    assert _run(monkeypatch, "promotion", str(project), "--request", str(request), "--json") == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "retain-candidate"
    assert payload["ready_to_promote"] is False


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (
            '{"schema_version":1,"source":"DOC-002#finding",'
            '"source":"DOC-004#result"}',
            "duplicate JSON key: source",
        ),
        (
            json.dumps(
                {
                    "schema_version": 1,
                    "source": "DOC-002",
                    "destination": "DOC-003#contract",
                    "authority_key": "install-policy",
                    "knowledge_state": "fact",
                    "disposition": "accepted",
                    "evidence": ["DOC-004#result"],
                }
            ),
            "source must use an exact ID#anchor address",
        ),
    ],
)
def test_promotion_rejects_ambiguous_request_without_stdout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    content: str,
    message: str,
) -> None:
    project = _project(tmp_path)
    request = tmp_path / "request.json"
    request.write_text(content, encoding="utf-8")
    assert _run(monkeypatch, "promotion", str(project), "--request", str(request), "--json") == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert message in output.err


def test_promotion_requires_exact_evidence_for_promotable_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = _project(tmp_path)
    request = _request(tmp_path / "request.json", evidence=[])
    assert _run(monkeypatch, "promotion", str(project), "--request", str(request), "--json") == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "requires exact evidence" in output.err


@pytest.mark.parametrize(
    ("owner_change", "message"),
    [
        ("authority_for: [other-policy]", "does not declare authority_for"),
        ("type: unprofiled-owner", "has no authored profile/history authority"),
    ],
)
def test_promotion_requires_explicit_profiled_destination_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    owner_change: str,
    message: str,
) -> None:
    project = _project(tmp_path)
    owner = project / "docs" / "owner.md"
    if owner_change.startswith("authority_for"):
        owner.write_text(
            owner.read_text().replace(
                "authority_for: [install-policy]", owner_change
            ),
            encoding="utf-8",
        )
    else:
        owner.write_text(
            owner.read_text().replace("type: canonical", owner_change),
            encoding="utf-8",
        )
    request = _request(tmp_path / "request.json")
    assert (
        _run(
            monkeypatch,
            "promotion",
            str(project),
            "--request",
            str(request),
            "--json",
        )
        == 1
    )
    output = capsys.readouterr()
    assert output.out == ""
    assert message in output.err
