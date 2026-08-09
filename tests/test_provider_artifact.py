import copy
import hashlib
import json
from pathlib import Path

import pytest

from docsystem.catalog import build_catalog, validate_catalog
from docsystem.cli import (
    provider_artifact_verify,
    provider_export_compare,
    provider_export_snapshot,
)
from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG, load_config
from docsystem.projection import (
    build_projection,
    load_pinned_projection,
    write_projection,
)
from docsystem.provider_artifact import (
    ProviderArtifactError,
    compare_artifact,
    encode_artifact,
    load_and_verify_artifact,
    snapshot_artifact,
    verify_artifact,
    write_artifact,
)


def _config() -> str:
    return (
        DEFAULT_CONFIG.replace("[areas]\n", '[areas]\nworkspace = "."\n')
        .replace("keep_generations = 2", "keep_generations = 10")
        .replace(
            "[provider]\n",
            '[provider]\nid = "artifact-example"\nvisibility = "private"\n',
        )
    )


def _document(*, changed: int | None = None) -> str:
    sections = []
    for index in range(510):
        body = "Changed" if changed == index else f"Value {index}"
        sections.append(
            f'<a id="section-{index}"></a>\n## Section {index}\n{body}\n'
        )
    return """\
---
id: DOC-002
revision: 1
---
# Large synthetic contract
""" + "".join(sections)


def _write_project(root: Path) -> Path:
    (root / CONFIG_FILENAME).write_text(_config(), encoding="utf-8")
    docs = root / "plan"
    docs.mkdir()
    (docs / "README.md").write_text(
        """\
---
id: DOC-001
revision: 1
---
# Index
[Contract](contract.md)
""",
        encoding="utf-8",
    )
    (docs / "contract.md").write_text(_document(), encoding="utf-8")
    return docs


def _generation(project: Path) -> str:
    config = load_config(project)
    catalog = build_catalog(config)
    assert [
        issue
        for issue in validate_catalog(catalog, config)
        if issue.severity != "warning"
    ] == []
    return write_projection(config, build_projection(catalog, config))


def _reseal(value: dict[str, object]) -> dict[str, object]:
    unsigned = {key: item for key, item in value.items() if key != "content_digest"}
    payload = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        **unsigned,
        "content_digest": {
            "algorithm": "sha256",
            "value": hashlib.sha256(payload).hexdigest(),
        },
    }


def test_complete_artifacts_assemble_pages_deterministically_and_verify(
    tmp_path: Path,
) -> None:
    docs = _write_project(tmp_path)
    before_generation = _generation(tmp_path)
    (docs / "contract.md").write_text(_document(changed=250), encoding="utf-8")
    after_generation = _generation(tmp_path)
    config = load_config(tmp_path)
    before = load_pinned_projection(config, before_generation)
    after = load_pinned_projection(config, after_generation)

    snapshot = snapshot_artifact(before)
    assert len(snapshot["observations"]) == 514
    assert snapshot["policy"]["assembly"] == "bounded-pages"
    assert snapshot["completeness"]["complete"] is True
    assert "Value 250" not in encode_artifact(snapshot)
    assert str(tmp_path) not in encode_artifact(snapshot)
    assert encode_artifact(snapshot_artifact(before)) == encode_artifact(snapshot)
    snapshot_result = verify_artifact(snapshot)
    assert snapshot_result["valid"] is True
    assert snapshot_result["observations"] == 514

    comparison = compare_artifact(before, after)
    assert comparison["completeness"]["summary"]["changed"] == 3
    assert comparison["completeness"]["changes"] == len(comparison["changes"])
    assert encode_artifact(compare_artifact(before, after)) == encode_artifact(
        comparison
    )
    compare_result = verify_artifact(comparison)
    assert compare_result["valid"] is True
    assert compare_result["generations"] == [before_generation, after_generation]


def test_artifact_writer_is_atomic_and_refuses_to_replace_evidence(
    tmp_path: Path,
) -> None:
    _write_project(tmp_path)
    generation = _generation(tmp_path)
    snapshot = load_pinned_projection(load_config(tmp_path), generation)
    artifact = snapshot_artifact(snapshot)
    output = tmp_path / "exports" / "snapshot.json"

    write_artifact(output, artifact)
    assert output.read_text(encoding="utf-8") == encode_artifact(artifact)
    assert load_and_verify_artifact(output)["valid"] is True
    with pytest.raises(ProviderArtifactError) as captured:
        write_artifact(output, artifact)
    assert captured.value.code == "artifact-output-exists"
    assert not list(output.parent.glob("*.tmp"))


def test_complete_empty_snapshot_remains_valid_evidence(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text(_config(), encoding="utf-8")
    (tmp_path / "plan").mkdir()
    generation = _generation(tmp_path)
    snapshot = load_pinned_projection(load_config(tmp_path), generation)

    artifact = snapshot_artifact(snapshot)
    assert artifact["observations"] == []
    assert artifact["completeness"]["coverage"] == {
        "documents": 0,
        "sections": 0,
        "excluded_markdown": 0,
        "unmapped_markdown": 0,
    }
    assert verify_artifact(artifact)["valid"] is True


def test_artifact_verification_rejects_corruption_partial_and_incompatibility(
    tmp_path: Path,
) -> None:
    docs = _write_project(tmp_path)
    before_generation = _generation(tmp_path)
    (docs / "contract.md").write_text(_document(changed=250), encoding="utf-8")
    after_generation = _generation(tmp_path)
    config = load_config(tmp_path)
    before = load_pinned_projection(config, before_generation)
    after = load_pinned_projection(config, after_generation)
    snapshot = snapshot_artifact(before)
    comparison = compare_artifact(before, after)

    tampered = copy.deepcopy(snapshot)
    tampered["observations"][0]["path"] = "tampered.md"
    with pytest.raises(ProviderArtifactError) as captured:
        verify_artifact(tampered)
    assert captured.value.code == "artifact-corrupt"

    partial = copy.deepcopy(snapshot)
    partial["observations"].pop()
    with pytest.raises(ProviderArtifactError) as captured:
        verify_artifact(_reseal(partial))
    assert captured.value.code == "artifact-incomplete"

    mixed = copy.deepcopy(comparison)
    mixed["query"]["after"]["provider_id"] = "another-provider"
    with pytest.raises(ProviderArtifactError) as captured:
        verify_artifact(_reseal(mixed))
    assert captured.value.code == "artifact-mixed-provider"

    mixed_query = copy.deepcopy(comparison)
    mixed_query["query"]["after"]["scope"]["catalog"] = "different"
    with pytest.raises(ProviderArtifactError) as captured:
        verify_artifact(_reseal(mixed_query))
    assert captured.value.code == "artifact-mixed-query"

    incompatible = copy.deepcopy(snapshot)
    incompatible["protocol"]["capabilities"] = []
    with pytest.raises(ProviderArtifactError) as captured:
        verify_artifact(_reseal(incompatible))
    assert captured.value.code == "artifact-incompatible"

    schema = copy.deepcopy(snapshot)
    schema["schema_version"] = 99
    with pytest.raises(ProviderArtifactError) as captured:
        verify_artifact(_reseal(schema))
    assert captured.value.code == "artifact-schema-unsupported"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("orphan", "section has no document observation"),
        ("path", "section conflicts with its document observation"),
        ("range", "section conflicts with its document observation"),
    ],
)
def test_artifact_verification_rejects_inconsistent_entity_relationships(
    tmp_path: Path, mutation: str, message: str
) -> None:
    _write_project(tmp_path)
    generation = _generation(tmp_path)
    snapshot = snapshot_artifact(
        load_pinned_projection(load_config(tmp_path), generation)
    )
    invalid = copy.deepcopy(snapshot)
    document = next(
        item for item in invalid["observations"] if item["document_id"] == "DOC-002"
        and item["kind"] == "document"
    )
    section = next(
        item for item in invalid["observations"] if item["document_id"] == "DOC-002"
        and item["kind"] == "section"
    )
    if mutation == "orphan":
        invalid["observations"].remove(document)
        invalid["completeness"]["observations"] -= 1
        invalid["completeness"]["coverage"]["documents"] -= 1
    elif mutation == "path":
        section["path"] = "another.md"
    else:
        section["lines"]["end"] = document["lines"]["end"] + 1

    with pytest.raises(ProviderArtifactError, match=message) as captured:
        verify_artifact(_reseal(invalid))
    assert captured.value.code == "artifact-incomplete"


def test_compare_verification_rejects_section_document_disagreement(
    tmp_path: Path,
) -> None:
    docs = _write_project(tmp_path)
    before_generation = _generation(tmp_path)
    (docs / "contract.md").write_text(_document(changed=250), encoding="utf-8")
    after_generation = _generation(tmp_path)
    config = load_config(tmp_path)
    comparison = compare_artifact(
        load_pinned_projection(config, before_generation),
        load_pinned_projection(config, after_generation),
    )
    invalid = copy.deepcopy(comparison)
    section_change = next(
        item
        for item in invalid["changes"]
        if item["entity"]["kind"] == "section"
    )
    section_change["before"]["path"] = "another.md"

    with pytest.raises(
        ProviderArtifactError,
        match="before comparison section conflicts with its document observation",
    ) as captured:
        verify_artifact(_reseal(invalid))
    assert captured.value.code == "artifact-incomplete"


def test_artifact_cli_exports_and_verifies_without_project_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    docs = _write_project(tmp_path)
    before = _generation(tmp_path)
    (docs / "contract.md").write_text(_document(changed=250), encoding="utf-8")
    after = _generation(tmp_path)
    snapshot_path = tmp_path / "snapshot.json"
    compare_path = tmp_path / "compare.json"

    assert provider_export_snapshot(tmp_path, before, output=snapshot_path) == 0
    assert provider_export_compare(
        tmp_path, before, after, output=compare_path
    ) == 0
    written = capsys.readouterr()
    assert written.err == ""
    assert "artifact written" in written.out

    isolated = tmp_path / "isolated"
    isolated.mkdir()
    assert provider_artifact_verify(snapshot_path) == 0
    snapshot_output = capsys.readouterr()
    assert snapshot_output.err == ""
    assert json.loads(snapshot_output.out)["valid"] is True
    assert provider_artifact_verify(compare_path) == 0
    compare_output = capsys.readouterr()
    assert compare_output.err == ""
    assert json.loads(compare_output.out)["valid"] is True

    broken = tmp_path / "broken.json"
    broken.write_text("{}\n", encoding="utf-8")
    assert provider_artifact_verify(broken) == 1
    failure = capsys.readouterr()
    assert failure.out == ""
    assert "[artifact-schema-unsupported]" in failure.err
