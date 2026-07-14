import hashlib
import json
import shutil
from pathlib import Path

import pytest

import docsystem.journal as journal_module
from docsystem.journal import (
    ApplyResult,
    FileEdit,
    FileGuard,
    JournalError,
    LineRange,
    copy_generation_to_cloud,
    evidence_packet,
    recover_generation,
    run_bounded_transaction,
)

BASE_CONTENT = "line1\nline2\nline3\nline4\nline5\n"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _source(tmp_path: Path, name: str = "source") -> Path:
    root = tmp_path / name
    _write(root / "docs" / "example.md", BASE_CONTENT)
    return root


def _journal(tmp_path: Path, name: str = "journal") -> Path:
    return tmp_path / name


def _accept(_root: Path) -> bool:
    return True


def _reject(_root: Path) -> bool:
    return False


def test_stale_read_guard_blocks_transaction_before_journal_creation(
    tmp_path: Path,
) -> None:
    source_root = _source(tmp_path)
    _write(source_root / "docs" / "authority.md", "canonical\n")
    journal_root = _journal(tmp_path)

    with pytest.raises(JournalError, match="stale read guard"):
        run_bounded_transaction(
            source_root=source_root,
            journal_root=journal_root,
            workstream_id="WS-GUARD-001",
            created_at="2026-07-14T10:00:00Z",
            edits=[
                FileEdit(
                    path="docs/example.md",
                    operation="bounded-edit",
                    before_sha256=_sha(BASE_CONTENT),
                    semantic_content=BASE_CONTENT,
                    mechanical_content=BASE_CONTENT.replace("line3", "changed"),
                    allowed_ranges=(LineRange(3, 3),),
                )
            ],
            guards=[FileGuard("docs/authority.md", "0" * 64)],
            validate=_accept,
        )
    assert not journal_root.exists() or not any(journal_root.iterdir())
    assert (source_root / "docs" / "example.md").read_text() == BASE_CONTENT


def test_read_guard_change_during_validation_rolls_back_edits(tmp_path: Path) -> None:
    source_root = _source(tmp_path)
    authority = source_root / "docs" / "authority.md"
    _write(authority, "canonical\n")
    journal_root = _journal(tmp_path)

    def mutate_guard(_root: Path) -> bool:
        authority.write_text("new canonical\n")
        return True

    result = run_bounded_transaction(
        source_root=source_root,
        journal_root=journal_root,
        workstream_id="WS-GUARD-002",
        created_at="2026-07-14T10:01:00Z",
        edits=[
            FileEdit(
                path="docs/example.md",
                operation="bounded-edit",
                before_sha256=_sha(BASE_CONTENT),
                semantic_content=BASE_CONTENT,
                mechanical_content=BASE_CONTENT.replace("line3", "changed"),
                allowed_ranges=(LineRange(3, 3),),
            )
        ],
        guards=[FileGuard("docs/authority.md", _sha("canonical\n"))],
        validate=mutate_guard,
    )

    assert result.status == "rolled-back"
    assert result.reason == "validation-error: JournalError"
    assert (source_root / "docs" / "example.md").read_text() == BASE_CONTENT
    assert authority.read_text() == "new canonical\n"


def test_valid_bounded_semantic_then_mechanical_edit(tmp_path: Path) -> None:
    source_root = _source(tmp_path)
    journal_root = _journal(tmp_path)
    semantic = "line1\nline2\nline3-semantic\nline4\nline5\n"
    mechanical = "line1\nline2\nline3-semantic\nline4-mechanical\nline5\n"

    result = run_bounded_transaction(
        source_root=source_root,
        journal_root=journal_root,
        workstream_id="WS-001",
        created_at="2026-07-13T18:00:00Z",
        edits=[
            FileEdit(
                path="docs/example.md",
                operation="bounded-edit",
                before_sha256=_sha(BASE_CONTENT),
                semantic_content=semantic,
                mechanical_content=mechanical,
                allowed_ranges=(LineRange(3, 4),),
            )
        ],
        validate=_accept,
    )

    assert isinstance(result, ApplyResult)
    assert result.status == "applied"
    assert result.validation_passed is True
    assert result.changed_paths == ("docs/example.md",)
    assert (source_root / "docs" / "example.md").read_text(encoding="utf-8") == mechanical

    generation_root = result.generation_root
    assert (generation_root / "manifest.json").is_file()
    assert (generation_root / "before" / "docs" / "example.md").read_text(
        encoding="utf-8"
    ) == BASE_CONTENT
    assert (generation_root / "after" / "docs" / "example.md").read_text(
        encoding="utf-8"
    ) == mechanical
    assert (generation_root / "semantic.patch.diff").is_file()
    assert (generation_root / "mechanical.patch.diff").is_file()
    assert (generation_root / "verification.json").is_file()
    assert not (generation_root / "recovery.json").exists()

    manifest = json.loads((generation_root / "manifest.json").read_text())
    assert manifest["status"] == "applied"
    assert manifest["schema_version"] == 1


def test_deterministic_sorted_manifest_and_separate_patches(tmp_path: Path) -> None:
    def edits() -> list[FileEdit]:
        return [
            FileEdit(
                path="docs/b.md",
                operation="bounded-edit",
                before_sha256=_sha(BASE_CONTENT),
                semantic_content=BASE_CONTENT.replace("line2", "line2-b"),
                mechanical_content=BASE_CONTENT.replace("line2", "line2-b"),
                allowed_ranges=(LineRange(2, 2),),
            ),
            FileEdit(
                path="docs/a.md",
                operation="bounded-edit",
                before_sha256=_sha(BASE_CONTENT),
                semantic_content=BASE_CONTENT.replace("line1", "line1-a"),
                mechanical_content=BASE_CONTENT.replace("line1", "line1-a").replace(
                    "line5", "line5-a"
                ),
                allowed_ranges=(LineRange(1, 1), LineRange(5, 5)),
            ),
        ]

    run_results = []
    for index in (1, 2):
        source_root = tmp_path / f"source{index}"
        _write(source_root / "docs" / "b.md", BASE_CONTENT)
        _write(source_root / "docs" / "a.md", BASE_CONTENT)
        result = run_bounded_transaction(
            source_root=source_root,
            journal_root=tmp_path / f"journal{index}",
            workstream_id="WS-002",
            created_at="2026-07-13T19:00:00Z",
            edits=edits(),
            validate=_accept,
        )
        run_results.append(result)

    manifest_1 = (run_results[0].generation_root / "manifest.json").read_bytes()
    manifest_2 = (run_results[1].generation_root / "manifest.json").read_bytes()
    assert manifest_1 == manifest_2

    manifest = json.loads(manifest_1)
    paths = [entry["path"] for entry in manifest["files"]]
    assert paths == sorted(paths) == ["docs/a.md", "docs/b.md"]

    semantic_patch = (run_results[0].generation_root / "semantic.patch.diff").read_text()
    mechanical_patch = (run_results[0].generation_root / "mechanical.patch.diff").read_text()
    assert semantic_patch != mechanical_patch
    assert "line5-a" in mechanical_patch
    assert "line5-a" not in semantic_patch


def test_stale_before_hash_fails_before_mutation(tmp_path: Path) -> None:
    source_root = _source(tmp_path)
    journal_root = _journal(tmp_path)

    with pytest.raises(JournalError, match="stale"):
        run_bounded_transaction(
            source_root=source_root,
            journal_root=journal_root,
            workstream_id="WS-003",
            created_at="2026-07-13T18:00:00Z",
            edits=[
                FileEdit(
                    path="docs/example.md",
                    operation="bounded-edit",
                    before_sha256=_sha("not the real content"),
                    semantic_content=BASE_CONTENT,
                    mechanical_content=BASE_CONTENT,
                    allowed_ranges=(LineRange(1, 5),),
                )
            ],
            validate=_accept,
        )

    assert (source_root / "docs" / "example.md").read_text() == BASE_CONTENT
    assert not journal_root.exists() or not any(journal_root.iterdir())


@pytest.mark.parametrize(
    "bad_path",
    ["/etc/passwd", "docs\\example.md", "../outside.md", "docs/../../outside.md", "./x.md"],
)
def test_unsafe_path_fails_before_mutation(tmp_path: Path, bad_path: str) -> None:
    source_root = _source(tmp_path)
    journal_root = _journal(tmp_path)

    with pytest.raises(JournalError):
        run_bounded_transaction(
            source_root=source_root,
            journal_root=journal_root,
            workstream_id="WS-004",
            created_at="2026-07-13T18:00:00Z",
            edits=[
                FileEdit(
                    path=bad_path,
                    operation="create",
                    before_sha256=None,
                    semantic_content="new\n",
                    mechanical_content="new\n",
                    allowed_ranges=(LineRange(1, 1),),
                )
            ],
            validate=_accept,
        )

    assert (source_root / "docs" / "example.md").read_text() == BASE_CONTENT
    assert not journal_root.exists() or not any(journal_root.iterdir())


def test_symlink_escape_fails_before_mutation(tmp_path: Path) -> None:
    source_root = _source(tmp_path)
    journal_root = _journal(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("outside content\n", encoding="utf-8")
    (source_root / "link.md").symlink_to(outside)

    with pytest.raises(JournalError, match="symlink"):
        run_bounded_transaction(
            source_root=source_root,
            journal_root=journal_root,
            workstream_id="WS-005",
            created_at="2026-07-13T18:00:00Z",
            edits=[
                FileEdit(
                    path="link.md",
                    operation="bounded-edit",
                    before_sha256=_sha("outside content\n"),
                    semantic_content="tampered\n",
                    mechanical_content="tampered\n",
                    allowed_ranges=(LineRange(1, 1),),
                )
            ],
            validate=_accept,
        )

    assert outside.read_text() == "outside content\n"
    assert not journal_root.exists() or not any(journal_root.iterdir())


def test_duplicate_normalized_target_fails_before_mutation(tmp_path: Path) -> None:
    source_root = _source(tmp_path)
    journal_root = _journal(tmp_path)

    with pytest.raises(JournalError, match="duplicate"):
        run_bounded_transaction(
            source_root=source_root,
            journal_root=journal_root,
            workstream_id="WS-006",
            created_at="2026-07-13T18:00:00Z",
            edits=[
                FileEdit(
                    path="docs/example.md",
                    operation="bounded-edit",
                    before_sha256=_sha(BASE_CONTENT),
                    semantic_content=BASE_CONTENT.replace("line1", "line1-x"),
                    mechanical_content=BASE_CONTENT.replace("line1", "line1-x"),
                    allowed_ranges=(LineRange(1, 1),),
                ),
                FileEdit(
                    path="docs/example.md",
                    operation="bounded-edit",
                    before_sha256=_sha(BASE_CONTENT),
                    semantic_content=BASE_CONTENT.replace("line5", "line5-y"),
                    mechanical_content=BASE_CONTENT.replace("line5", "line5-y"),
                    allowed_ranges=(LineRange(5, 5),),
                ),
            ],
            validate=_accept,
        )

    assert (source_root / "docs" / "example.md").read_text() == BASE_CONTENT
    assert not journal_root.exists() or not any(journal_root.iterdir())


def test_out_of_range_edit_fails_before_mutation(tmp_path: Path) -> None:
    source_root = _source(tmp_path)
    journal_root = _journal(tmp_path)

    with pytest.raises(JournalError, match="outside the declared allowed range"):
        run_bounded_transaction(
            source_root=source_root,
            journal_root=journal_root,
            workstream_id="WS-007",
            created_at="2026-07-13T18:00:00Z",
            edits=[
                FileEdit(
                    path="docs/example.md",
                    operation="bounded-edit",
                    before_sha256=_sha(BASE_CONTENT),
                    semantic_content=BASE_CONTENT.replace("line3", "line3-out"),
                    mechanical_content=BASE_CONTENT.replace("line3", "line3-out"),
                    allowed_ranges=(LineRange(1, 1),),
                )
            ],
            validate=_accept,
        )

    assert (source_root / "docs" / "example.md").read_text() == BASE_CONTENT
    assert not journal_root.exists() or not any(journal_root.iterdir())


def test_create_mechanical_stage_must_remain_within_full_declared_range(
    tmp_path: Path,
) -> None:
    source_root = _source(tmp_path)
    journal_root = _journal(tmp_path)
    with pytest.raises(JournalError, match="covering the entire new file"):
        run_bounded_transaction(
            source_root=source_root,
            journal_root=journal_root,
            workstream_id="WS-007-CREATE",
            created_at="2026-07-13T18:00:00Z",
            edits=[
                FileEdit(
                    path="docs/new.md",
                    operation="create",
                    before_sha256=None,
                    semantic_content="one\n",
                    mechanical_content="one\ntwo\n",
                    allowed_ranges=(LineRange(1, 1),),
                )
            ],
            validate=_accept,
        )
    assert not (source_root / "docs" / "new.md").exists()


def test_unsupported_delete_operation_is_rejected(tmp_path: Path) -> None:
    source_root = _source(tmp_path)
    journal_root = _journal(tmp_path)

    with pytest.raises(JournalError, match="unsupported operation"):
        run_bounded_transaction(
            source_root=source_root,
            journal_root=journal_root,
            workstream_id="WS-008",
            created_at="2026-07-13T18:00:00Z",
            edits=[
                FileEdit(
                    path="docs/example.md",
                    operation="delete",
                    before_sha256=_sha(BASE_CONTENT),
                    semantic_content="",
                    mechanical_content="",
                    allowed_ranges=(LineRange(1, 5),),
                )
            ],
            validate=_accept,
        )

    assert (source_root / "docs" / "example.md").read_text() == BASE_CONTENT


def test_validation_failure_restores_all_files_byte_for_byte(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    _write(source_root / "a.md", "a1\na2\na3\n")
    _write(source_root / "b.md", "b1\nb2\nb3\n")
    journal_root = _journal(tmp_path)

    result = run_bounded_transaction(
        source_root=source_root,
        journal_root=journal_root,
        workstream_id="WS-009",
        created_at="2026-07-13T18:00:00Z",
        edits=[
            FileEdit(
                path="a.md",
                operation="bounded-edit",
                before_sha256=_sha("a1\na2\na3\n"),
                semantic_content="a1\na2-changed\na3\n",
                mechanical_content="a1\na2-changed\na3\n",
                allowed_ranges=(LineRange(2, 2),),
            ),
            FileEdit(
                path="b.md",
                operation="bounded-edit",
                before_sha256=_sha("b1\nb2\nb3\n"),
                semantic_content="b1\nb2-changed\nb3\n",
                mechanical_content="b1\nb2-changed\nb3\n",
                allowed_ranges=(LineRange(2, 2),),
            ),
        ],
        validate=_reject,
    )

    assert result.status == "rolled-back"
    assert result.validation_passed is False
    assert (source_root / "a.md").read_text() == "a1\na2\na3\n"
    assert (source_root / "b.md").read_text() == "b1\nb2\nb3\n"
    assert (result.generation_root / "verification.json").is_file()
    assert (result.generation_root / "recovery.json").is_file()
    verification = json.loads((result.generation_root / "verification.json").read_text())
    assert verification["status"] == "rolled-back"
    assert verification["reason"] == "validation-failure"


def test_validation_exception_restores_source_and_records_failure(tmp_path: Path) -> None:
    source_root = _source(tmp_path)

    def explode(_root: Path) -> bool:
        raise RuntimeError("validator crashed")

    result = run_bounded_transaction(
        source_root=source_root,
        journal_root=_journal(tmp_path),
        workstream_id="WS-009-EXCEPTION",
        created_at="2026-07-13T18:00:00Z",
        edits=[
            FileEdit(
                path="docs/example.md",
                operation="bounded-edit",
                before_sha256=_sha(BASE_CONTENT),
                semantic_content=BASE_CONTENT.replace("line2", "line2-changed"),
                mechanical_content=BASE_CONTENT.replace("line2", "line2-changed"),
                allowed_ranges=(LineRange(2, 2),),
            )
        ],
        validate=explode,
    )

    assert result.status == "rolled-back"
    assert result.reason == "validation-error: RuntimeError"
    assert (source_root / "docs" / "example.md").read_bytes() == BASE_CONTENT.encode()


def test_evidence_finalization_failure_restores_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = _source(tmp_path)
    original_write_json = journal_module._write_json
    failed = {"done": False}

    def fail_applied_verification(path: Path, data: dict[str, object]) -> bytes:
        if (
            path.name == "verification.json"
            and data.get("status") == "applied"
            and not failed["done"]
        ):
            failed["done"] = True
            raise OSError("verification storage failed")
        return original_write_json(path, data)

    monkeypatch.setattr(journal_module, "_write_json", fail_applied_verification)
    result = run_bounded_transaction(
        source_root=source_root,
        journal_root=_journal(tmp_path),
        workstream_id="WS-009-EVIDENCE",
        created_at="2026-07-13T18:00:00Z",
        edits=[
            FileEdit(
                path="docs/example.md",
                operation="bounded-edit",
                before_sha256=_sha(BASE_CONTENT),
                semantic_content=BASE_CONTENT.replace("line2", "line2-changed"),
                mechanical_content=BASE_CONTENT.replace("line2", "line2-changed"),
                allowed_ranges=(LineRange(2, 2),),
            )
        ],
        validate=_accept,
    )

    assert result.status == "rolled-back"
    assert result.reason is not None
    assert result.reason.startswith("evidence-finalization-failure")
    assert (source_root / "docs" / "example.md").read_bytes() == BASE_CONTENT.encode()


def test_multi_file_partial_apply_failure_restores_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "source"
    _write(source_root / "a.md", "a1\na2\na3\n")
    _write(source_root / "b.md", "b1\nb2\nb3\n")
    journal_root = _journal(tmp_path)

    original_replace = Path.replace
    calls = {"count": 0}

    def flaky_replace(self: Path, target: Path) -> Path:
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("simulated partial-apply failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)

    result = run_bounded_transaction(
        source_root=source_root,
        journal_root=journal_root,
        workstream_id="WS-010",
        created_at="2026-07-13T18:00:00Z",
        edits=[
            FileEdit(
                path="a.md",
                operation="bounded-edit",
                before_sha256=_sha("a1\na2\na3\n"),
                semantic_content="a1\na2-changed\na3\n",
                mechanical_content="a1\na2-changed\na3\n",
                allowed_ranges=(LineRange(2, 2),),
            ),
            FileEdit(
                path="b.md",
                operation="bounded-edit",
                before_sha256=_sha("b1\nb2\nb3\n"),
                semantic_content="b1\nb2-changed\nb3\n",
                mechanical_content="b1\nb2-changed\nb3\n",
                allowed_ranges=(LineRange(2, 2),),
            ),
        ],
        validate=_accept,
    )

    assert result.status == "rolled-back"
    assert calls["count"] == 3
    assert (source_root / "a.md").read_text() == "a1\na2\na3\n"
    assert (source_root / "b.md").read_text() == "b1\nb2\nb3\n"


def test_concurrent_source_change_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "source"
    _write(source_root / "a.md", "a-before\n")
    _write(source_root / "b.md", "b-before\n")
    original_replace = Path.replace
    calls = {"count": 0}

    def change_second_source(self: Path, target: Path) -> Path:
        calls["count"] += 1
        replaced = original_replace(self, target)
        if calls["count"] == 1:
            (source_root / "b.md").write_text("b-concurrent\n", encoding="utf-8")
        return replaced

    monkeypatch.setattr(Path, "replace", change_second_source)
    result = run_bounded_transaction(
        source_root=source_root,
        journal_root=_journal(tmp_path),
        workstream_id="WS-010-CONCURRENT",
        created_at="2026-07-13T18:00:00Z",
        edits=[
            FileEdit(
                path="a.md",
                operation="bounded-edit",
                before_sha256=_sha("a-before\n"),
                semantic_content="a-after\n",
                mechanical_content="a-after\n",
                allowed_ranges=(LineRange(1, 1),),
            ),
            FileEdit(
                path="b.md",
                operation="bounded-edit",
                before_sha256=_sha("b-before\n"),
                semantic_content="b-after\n",
                mechanical_content="b-after\n",
                allowed_ranges=(LineRange(1, 1),),
            ),
        ],
        validate=_accept,
    )

    assert result.status == "rolled-back"
    assert result.reason is not None
    assert "source changed immediately before apply" in result.reason
    assert (source_root / "a.md").read_text() == "a-before\n"
    assert (source_root / "b.md").read_text() == "b-concurrent\n"


def test_explicit_recovery_succeeds_only_when_current_equals_after(
    tmp_path: Path,
) -> None:
    source_root = _source(tmp_path)
    journal_root = _journal(tmp_path)
    mechanical = BASE_CONTENT.replace("line3", "line3-changed")

    result = run_bounded_transaction(
        source_root=source_root,
        journal_root=journal_root,
        workstream_id="WS-011",
        created_at="2026-07-13T18:00:00Z",
        edits=[
            FileEdit(
                path="docs/example.md",
                operation="bounded-edit",
                before_sha256=_sha(BASE_CONTENT),
                semantic_content=mechanical,
                mechanical_content=mechanical,
                allowed_ranges=(LineRange(3, 3),),
            )
        ],
        validate=_accept,
    )
    assert result.status == "applied"
    generation_snapshot = {
        path.relative_to(result.generation_root).as_posix(): path.read_bytes()
        for path in result.generation_root.rglob("*")
        if path.is_file()
    }

    recovery = recover_generation(
        source_root=source_root,
        journal_root=journal_root,
        generation_id=result.generation_id,
        recovered_at="2026-07-13T20:00:00Z",
    )

    assert recovery.status == "recovered"
    assert recovery.restored_paths == ("docs/example.md",)
    assert (source_root / "docs" / "example.md").read_text() == BASE_CONTENT
    assert recovery.recovery_record is not None
    recovery_root = journal_root / recovery.recovery_record
    assert (recovery_root / "manifest.json").is_file()
    assert (recovery_root / "verification.json").is_file()
    assert {
        path.relative_to(result.generation_root).as_posix(): path.read_bytes()
        for path in result.generation_root.rglob("*")
        if path.is_file()
    } == generation_snapshot

    repeat = recover_generation(
        source_root=source_root,
        journal_root=journal_root,
        generation_id=result.generation_id,
        recovered_at="2026-07-13T21:00:00Z",
    )
    assert repeat.status == "already-recovered"
    assert repeat.recovery_record is None
    recovery_manifest = json.loads((recovery_root / "manifest.json").read_text())
    assert recovery_manifest["recovered_at"] == "2026-07-13T20:00:00Z"


def test_recovery_refuses_newer_unknown_content(tmp_path: Path) -> None:
    source_root = _source(tmp_path)
    journal_root = _journal(tmp_path)
    mechanical = BASE_CONTENT.replace("line3", "line3-changed")

    result = run_bounded_transaction(
        source_root=source_root,
        journal_root=journal_root,
        workstream_id="WS-012",
        created_at="2026-07-13T18:00:00Z",
        edits=[
            FileEdit(
                path="docs/example.md",
                operation="bounded-edit",
                before_sha256=_sha(BASE_CONTENT),
                semantic_content=mechanical,
                mechanical_content=mechanical,
                allowed_ranges=(LineRange(3, 3),),
            )
        ],
        validate=_accept,
    )

    newer_content = mechanical.replace("line5", "line5-newer-work")
    (source_root / "docs" / "example.md").write_text(newer_content, encoding="utf-8")

    recovery = recover_generation(
        source_root=source_root,
        journal_root=journal_root,
        generation_id=result.generation_id,
        recovered_at="2026-07-13T20:00:00Z",
    )

    assert recovery.status == "refused"
    assert (source_root / "docs" / "example.md").read_text() == newer_content
    assert not (journal_root / "recoveries").exists()


def test_rename_simulation_preserves_original_authored_file(tmp_path: Path) -> None:
    source_root = _source(tmp_path)
    journal_root = _journal(tmp_path)

    result = run_bounded_transaction(
        source_root=source_root,
        journal_root=journal_root,
        workstream_id="WS-013",
        created_at="2026-07-13T18:00:00Z",
        edits=[
            FileEdit(
                path="docs/renamed.md",
                operation="create",
                before_sha256=None,
                semantic_content=BASE_CONTENT,
                mechanical_content=BASE_CONTENT,
                allowed_ranges=(LineRange(1, 5),),
            )
        ],
        validate=_accept,
    )

    assert result.status == "applied"
    assert (source_root / "docs" / "example.md").read_text() == BASE_CONTENT
    assert (source_root / "docs" / "renamed.md").read_text() == BASE_CONTENT


def test_generation_reuse_is_rejected(tmp_path: Path) -> None:
    source_root = _source(tmp_path)
    journal_root = _journal(tmp_path)
    edit = FileEdit(
        path="docs/example.md",
        operation="bounded-edit",
        before_sha256=_sha(BASE_CONTENT),
        semantic_content=BASE_CONTENT.replace("line1", "line1-x"),
        mechanical_content=BASE_CONTENT.replace("line1", "line1-x"),
        allowed_ranges=(LineRange(1, 1),),
    )

    first = run_bounded_transaction(
        source_root=source_root,
        journal_root=journal_root,
        workstream_id="WS-014",
        created_at="2026-07-13T18:00:00Z",
        edits=[edit],
        validate=_accept,
    )
    assert first.status == "applied"

    second_edit = FileEdit(
        path="docs/example.md",
        operation="bounded-edit",
        before_sha256=_sha(BASE_CONTENT.replace("line1", "line1-x")),
        semantic_content=BASE_CONTENT.replace("line1", "line1-y"),
        mechanical_content=BASE_CONTENT.replace("line1", "line1-y"),
        allowed_ranges=(LineRange(1, 1),),
    )
    with pytest.raises(JournalError, match="already exists"):
        run_bounded_transaction(
            source_root=source_root,
            journal_root=journal_root,
            workstream_id="WS-014",
            created_at="2026-07-13T18:00:00Z",
            edits=[second_edit],
            validate=_accept,
        )


def test_new_identity_creates_a_separate_immutable_attempt(tmp_path: Path) -> None:
    source_root = _source(tmp_path)
    journal_root = _journal(tmp_path)
    edit = FileEdit(
        path="docs/example.md",
        operation="bounded-edit",
        before_sha256=_sha(BASE_CONTENT),
        semantic_content=BASE_CONTENT.replace("line1", "line1-x"),
        mechanical_content=BASE_CONTENT.replace("line1", "line1-x"),
        allowed_ranges=(LineRange(1, 1),),
    )

    first = run_bounded_transaction(
        source_root=source_root,
        journal_root=journal_root,
        workstream_id="WS-015",
        created_at="2026-07-13T18:00:00Z",
        edits=[edit],
        validate=_accept,
    )
    first_manifest_before = (first.generation_root / "manifest.json").read_bytes()

    second_edit = FileEdit(
        path="docs/example.md",
        operation="bounded-edit",
        before_sha256=_sha(BASE_CONTENT.replace("line1", "line1-x")),
        semantic_content=BASE_CONTENT.replace("line1", "line1-x").replace("line2", "line2-x"),
        mechanical_content=BASE_CONTENT.replace("line1", "line1-x").replace("line2", "line2-x"),
        allowed_ranges=(LineRange(2, 2),),
    )
    second = run_bounded_transaction(
        source_root=source_root,
        journal_root=journal_root,
        workstream_id="WS-015",
        created_at="2026-07-13T19:00:00Z",
        edits=[second_edit],
        validate=_accept,
    )

    assert first.generation_id != second.generation_id
    assert (first.generation_root / "manifest.json").read_bytes() == first_manifest_before


def test_cloud_copy_publishes_and_verifies_readback(tmp_path: Path) -> None:
    source_root = _source(tmp_path)
    journal_root = _journal(tmp_path)
    backup_root = tmp_path / "cloud-backup"

    result = run_bounded_transaction(
        source_root=source_root,
        journal_root=journal_root,
        workstream_id="WS-016",
        created_at="2026-07-13T18:00:00Z",
        edits=[
            FileEdit(
                path="docs/example.md",
                operation="bounded-edit",
                before_sha256=_sha(BASE_CONTENT),
                semantic_content=BASE_CONTENT.replace("line1", "line1-x"),
                mechanical_content=BASE_CONTENT.replace("line1", "line1-x"),
                allowed_ranges=(LineRange(1, 1),),
            )
        ],
        validate=_accept,
    )

    copy_result = copy_generation_to_cloud(
        journal_root=journal_root,
        generation_id=result.generation_id,
        backup_root=backup_root,
        copied_at="2026-07-13T20:00:00Z",
    )

    assert copy_result.verified is True
    destination = backup_root / result.generation_id
    assert destination.is_dir()
    assert (destination / "manifest.json").read_bytes() == (
        result.generation_root / "manifest.json"
    ).read_bytes()
    assert (destination / "after" / "docs" / "example.md").read_text() == (
        result.generation_root / "after" / "docs" / "example.md"
    ).read_text()
    assert not list(backup_root.glob(".incoming-*"))

    with pytest.raises(JournalError, match="already exists"):
        copy_generation_to_cloud(
            journal_root=journal_root,
            generation_id=result.generation_id,
            backup_root=backup_root,
            copied_at="2026-07-13T21:00:00Z",
        )


def test_recovery_uses_verified_cloud_copy_without_original_workspace(
    tmp_path: Path,
) -> None:
    source_root = _source(tmp_path)
    journal_root = _journal(tmp_path)
    backup_root = tmp_path / "cloud-backup"
    changed = BASE_CONTENT.replace("line3", "line3-after")
    result = run_bounded_transaction(
        source_root=source_root,
        journal_root=journal_root,
        workstream_id="WS-016-RECOVERY",
        created_at="2026-07-13T18:00:00Z",
        edits=[
            FileEdit(
                path="docs/example.md",
                operation="bounded-edit",
                before_sha256=_sha(BASE_CONTENT),
                semantic_content=changed,
                mechanical_content=changed,
                allowed_ranges=(LineRange(3, 3),),
            )
        ],
        validate=_accept,
    )
    copy_generation_to_cloud(
        journal_root=journal_root,
        generation_id=result.generation_id,
        backup_root=backup_root,
        copied_at="2026-07-13T20:00:00Z",
    )

    restore_root = tmp_path / "restore-target"
    _write(restore_root / "docs" / "example.md", changed)
    shutil.rmtree(source_root)
    shutil.rmtree(journal_root)

    recovery = recover_generation(
        source_root=restore_root,
        journal_root=backup_root,
        generation_id=result.generation_id,
        recovered_at="2026-07-13T21:00:00Z",
    )

    assert recovery.status == "recovered"
    assert (restore_root / "docs" / "example.md").read_bytes() == BASE_CONTENT.encode()
    assert not source_root.exists()
    assert not journal_root.exists()


def test_cloud_copy_rejects_backup_path_through_symlink(tmp_path: Path) -> None:
    source_root = _source(tmp_path)
    journal_root = _journal(tmp_path)
    result = run_bounded_transaction(
        source_root=source_root,
        journal_root=journal_root,
        workstream_id="WS-016-SYMLINK",
        created_at="2026-07-13T18:00:00Z",
        edits=[
            FileEdit(
                path="docs/example.md",
                operation="bounded-edit",
                before_sha256=_sha(BASE_CONTENT),
                semantic_content=BASE_CONTENT.replace("line1", "line1-x"),
                mechanical_content=BASE_CONTENT.replace("line1", "line1-x"),
                allowed_ranges=(LineRange(1, 1),),
            )
        ],
        validate=_accept,
    )
    outside = tmp_path / "outside-backup"
    outside.mkdir()
    link = tmp_path / "backup-link"
    link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(JournalError, match="must not contain a symlink"):
        copy_generation_to_cloud(
            journal_root=journal_root,
            generation_id=result.generation_id,
            backup_root=link / "nested",
            copied_at="2026-07-13T20:00:00Z",
        )
    assert not any(outside.iterdir())


def test_cloud_copy_refuses_generation_changed_during_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = _source(tmp_path)
    journal_root = _journal(tmp_path)
    backup_root = tmp_path / "cloud-backup"
    result = run_bounded_transaction(
        source_root=source_root,
        journal_root=journal_root,
        workstream_id="WS-016-RACE",
        created_at="2026-07-13T18:00:00Z",
        edits=[
            FileEdit(
                path="docs/example.md",
                operation="bounded-edit",
                before_sha256=_sha(BASE_CONTENT),
                semantic_content=BASE_CONTENT.replace("line1", "line1-x"),
                mechanical_content=BASE_CONTENT.replace("line1", "line1-x"),
                allowed_ranges=(LineRange(1, 1),),
            )
        ],
        validate=_accept,
    )
    original_copytree = journal_module.shutil.copytree

    def copy_then_tamper(source: Path, destination: Path, *args: object, **kwargs: object) -> Path:
        copied = original_copytree(source, destination, *args, **kwargs)
        source_path = Path(source)
        if (source_path / "manifest.json").is_file():
            (source_path / "semantic.patch.diff").write_text("changed during copy\n")
        return copied

    monkeypatch.setattr(journal_module.shutil, "copytree", copy_then_tamper)
    with pytest.raises(JournalError, match="verification failed"):
        copy_generation_to_cloud(
            journal_root=journal_root,
            generation_id=result.generation_id,
            backup_root=backup_root,
            copied_at="2026-07-13T20:00:00Z",
        )
    assert not (backup_root / result.generation_id).exists()
    assert not list(backup_root.glob(".incoming-*"))


def test_tampered_manifest_fails_closed_for_recovery_and_cloud_copy(
    tmp_path: Path,
) -> None:
    source_root = _source(tmp_path)
    journal_root = _journal(tmp_path)
    backup_root = tmp_path / "cloud-backup"

    result = run_bounded_transaction(
        source_root=source_root,
        journal_root=journal_root,
        workstream_id="WS-017",
        created_at="2026-07-13T18:00:00Z",
        edits=[
            FileEdit(
                path="docs/example.md",
                operation="bounded-edit",
                before_sha256=_sha(BASE_CONTENT),
                semantic_content=BASE_CONTENT.replace("line1", "line1-x"),
                mechanical_content=BASE_CONTENT.replace("line1", "line1-x"),
                allowed_ranges=(LineRange(1, 1),),
            )
        ],
        validate=_accept,
    )

    after_file = result.generation_root / "after" / "docs" / "example.md"
    after_file.write_text("tampered content\n", encoding="utf-8")

    with pytest.raises(JournalError, match="corrupted"):
        recover_generation(
            source_root=source_root,
            journal_root=journal_root,
            generation_id=result.generation_id,
            recovered_at="2026-07-13T20:00:00Z",
        )

    with pytest.raises(JournalError):
        copy_generation_to_cloud(
            journal_root=journal_root,
            generation_id=result.generation_id,
            backup_root=backup_root,
            copied_at="2026-07-13T20:00:00Z",
        )


def test_tampered_manifest_bytes_fail_closed(tmp_path: Path) -> None:
    source_root = _source(tmp_path)
    journal_root = _journal(tmp_path)

    result = run_bounded_transaction(
        source_root=source_root,
        journal_root=journal_root,
        workstream_id="WS-018",
        created_at="2026-07-13T18:00:00Z",
        edits=[
            FileEdit(
                path="docs/example.md",
                operation="bounded-edit",
                before_sha256=_sha(BASE_CONTENT),
                semantic_content=BASE_CONTENT.replace("line1", "line1-x"),
                mechanical_content=BASE_CONTENT.replace("line1", "line1-x"),
                allowed_ranges=(LineRange(1, 1),),
            )
        ],
        validate=_accept,
    )

    manifest_path = result.generation_root / "manifest.json"
    manifest_path.write_bytes(manifest_path.read_bytes() + b" ")

    with pytest.raises(JournalError, match="integrity"):
        recover_generation(
            source_root=source_root,
            journal_root=journal_root,
            generation_id=result.generation_id,
            recovered_at="2026-07-13T20:00:00Z",
        )


def test_tampered_verification_fails_closed(tmp_path: Path) -> None:
    source_root = _source(tmp_path)
    journal_root = _journal(tmp_path)
    result = run_bounded_transaction(
        source_root=source_root,
        journal_root=journal_root,
        workstream_id="WS-018-VERIFY",
        created_at="2026-07-13T18:00:00Z",
        edits=[
            FileEdit(
                path="docs/example.md",
                operation="bounded-edit",
                before_sha256=_sha(BASE_CONTENT),
                semantic_content=BASE_CONTENT.replace("line1", "line1-x"),
                mechanical_content=BASE_CONTENT.replace("line1", "line1-x"),
                allowed_ranges=(LineRange(1, 1),),
            )
        ],
        validate=_accept,
    )
    verification_path = result.generation_root / "verification.json"
    verification = json.loads(verification_path.read_text())
    verification["changed_paths"] = ["unrelated.md"]
    verification_path.write_text(json.dumps(verification), encoding="utf-8")

    with pytest.raises(JournalError, match="verification integrity"):
        recover_generation(
            source_root=source_root,
            journal_root=journal_root,
            generation_id=result.generation_id,
            recovered_at="2026-07-13T20:00:00Z",
        )


def test_evidence_packet_has_no_absolute_root_or_document_bodies(
    tmp_path: Path,
) -> None:
    source_root = _source(tmp_path)
    journal_root = _journal(tmp_path)
    secret = "SECRET-DOCUMENT-BODY-MARKER"
    mechanical = BASE_CONTENT.replace("line3", secret)

    result = run_bounded_transaction(
        source_root=source_root,
        journal_root=journal_root,
        workstream_id="WS-019",
        created_at="2026-07-13T18:00:00Z",
        edits=[
            FileEdit(
                path="docs/example.md",
                operation="bounded-edit",
                before_sha256=_sha(BASE_CONTENT),
                semantic_content=mechanical,
                mechanical_content=mechanical,
                allowed_ranges=(LineRange(3, 3),),
            )
        ],
        validate=_accept,
    )

    packet = evidence_packet(result)
    serialized = json.dumps(packet)

    assert secret not in serialized
    assert str(tmp_path) not in serialized
    assert str(source_root) not in serialized
    assert packet["generation_id"] == result.generation_id
    assert packet["status"] == "applied"
    assert packet["unresolved_failure"] is None


def test_generation_id_traversal_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(JournalError, match="invalid generation id"):
        recover_generation(
            source_root=_source(tmp_path),
            journal_root=_journal(tmp_path),
            generation_id="../outside",
            recovered_at="2026-07-13T20:00:00Z",
        )


def test_tampered_patch_fails_closed(tmp_path: Path) -> None:
    source_root = _source(tmp_path)
    journal_root = _journal(tmp_path)
    result = run_bounded_transaction(
        source_root=source_root,
        journal_root=journal_root,
        workstream_id="WS-020",
        created_at="2026-07-13T18:00:00Z",
        edits=[
            FileEdit(
                path="docs/example.md",
                operation="bounded-edit",
                before_sha256=_sha(BASE_CONTENT),
                semantic_content=BASE_CONTENT.replace("line2", "line2-x"),
                mechanical_content=BASE_CONTENT.replace("line2", "line2-x"),
                allowed_ranges=(LineRange(2, 2),),
            )
        ],
        validate=_accept,
    )
    (result.generation_root / "semantic.patch.diff").write_text("tampered\n")

    with pytest.raises(JournalError, match="patch evidence is corrupted"):
        recover_generation(
            source_root=source_root,
            journal_root=journal_root,
            generation_id=result.generation_id,
            recovered_at="2026-07-13T20:00:00Z",
        )


def test_recovery_rejects_symlink_introduced_after_apply(tmp_path: Path) -> None:
    source_root = _source(tmp_path)
    journal_root = _journal(tmp_path)
    changed = BASE_CONTENT.replace("line2", "line2-x")
    result = run_bounded_transaction(
        source_root=source_root,
        journal_root=journal_root,
        workstream_id="WS-021",
        created_at="2026-07-13T18:00:00Z",
        edits=[
            FileEdit(
                path="docs/example.md",
                operation="bounded-edit",
                before_sha256=_sha(BASE_CONTENT),
                semantic_content=changed,
                mechanical_content=changed,
                allowed_ranges=(LineRange(2, 2),),
            )
        ],
        validate=_accept,
    )
    target = source_root / "docs" / "example.md"
    target.unlink()
    outside = tmp_path / "outside.md"
    outside.write_text(changed, encoding="utf-8")
    target.symlink_to(outside)

    with pytest.raises(JournalError, match="symlink"):
        recover_generation(
            source_root=source_root,
            journal_root=journal_root,
            generation_id=result.generation_id,
            recovered_at="2026-07-13T20:00:00Z",
        )
    assert outside.read_text() == changed


def test_source_and_journal_roots_must_not_overlap(tmp_path: Path) -> None:
    source_root = _source(tmp_path)
    with pytest.raises(JournalError, match="must not overlap"):
        run_bounded_transaction(
            source_root=source_root,
            journal_root=source_root / ".journal",
            workstream_id="WS-022",
            created_at="2026-07-13T18:00:00Z",
            edits=[
                FileEdit(
                    path="docs/example.md",
                    operation="bounded-edit",
                    before_sha256=_sha(BASE_CONTENT),
                    semantic_content=BASE_CONTENT,
                    mechanical_content=BASE_CONTENT,
                    allowed_ranges=(LineRange(1, 5),),
                )
            ],
            validate=_accept,
        )
    assert not (source_root / ".journal").exists()


def test_partial_explicit_recovery_failure_restores_after_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "source"
    _write(source_root / "a.md", "a-before\n")
    _write(source_root / "b.md", "b-before\n")
    journal_root = _journal(tmp_path)
    result = run_bounded_transaction(
        source_root=source_root,
        journal_root=journal_root,
        workstream_id="WS-023",
        created_at="2026-07-13T18:00:00Z",
        edits=[
            FileEdit(
                path="a.md",
                operation="bounded-edit",
                before_sha256=_sha("a-before\n"),
                semantic_content="a-after\n",
                mechanical_content="a-after\n",
                allowed_ranges=(LineRange(1, 1),),
            ),
            FileEdit(
                path="b.md",
                operation="bounded-edit",
                before_sha256=_sha("b-before\n"),
                semantic_content="b-after\n",
                mechanical_content="b-after\n",
                allowed_ranges=(LineRange(1, 1),),
            ),
        ],
        validate=_accept,
    )

    original_replace = Path.replace
    calls = {"count": 0}

    def fail_second_replace(self: Path, target: Path) -> Path:
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("recovery replacement failed")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_second_replace)
    with pytest.raises(JournalError, match="recovery failed and was rolled back"):
        recover_generation(
            source_root=source_root,
            journal_root=journal_root,
            generation_id=result.generation_id,
            recovered_at="2026-07-13T20:00:00Z",
        )

    assert (source_root / "a.md").read_text() == "a-after\n"
    assert (source_root / "b.md").read_text() == "b-after\n"
