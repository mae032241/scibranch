"""Adaptive solver frames are collected from actual files, not guessed names."""


import pytest

from tcad_artifact import remote_runner_py36
from tcad_artifact.project_packager import (
    ExecutionPackage, RuntimeOutputRecord, TCADRuntimeManifest,
    attest_runtime_contract, deck_project_diff,
)
from tests.operations.test_runtime_plugin_configuration import _execution_package


def test_collects_all_adaptive_frames_and_skips_unchanged_inputs(tmp_path):
    collector = remote_runner_py36._collect_expected
    source = tmp_path / "main.cmd"
    source.write_bytes(b"solver input")
    original = {"relative_path": "main.cmd", "size_bytes": source.stat().st_size}
    generations=remote_runner_py36._input_generations(str(tmp_path),[original])
    frames = tmp_path / "frames"
    frames.mkdir()
    for index in range(79):
        (frames / f"movie_{index:04d}.tdr").write_bytes(f"frame {index}".encode())
    records = collector(str(tmp_path), [],
                        {"max_output_bytes": 4096, "transfer_chunk_bytes": 1024}, archive_entries=[original],
                        input_generations=generations, collect_generated_outputs=True)
    assert len(records) == 79
    assert [item["relative_path"] for item in records] == [
        f"frames/movie_{index:04d}.tdr" for index in range(79)
    ]
    assert [item["name"] for item in records]==["generated_"+str(index) for index in range(79)]


def test_generated_capture_fails_closed_on_budget_and_symlink(tmp_path):
    collector = remote_runner_py36._collect_expected
    frame = tmp_path / "frame.tdr"
    frame.write_bytes(b"frame")
    root = str(tmp_path)
    with pytest.raises(RuntimeError, match="total output exceeds job limit"):
        collector(root, [], {"max_output_bytes": 4}, collect_generated_outputs=True)
    frame.unlink()
    frame.symlink_to(tmp_path / "other.tdr")
    (tmp_path / "other.tdr").write_bytes(b"other")
    with pytest.raises((OSError, RuntimeError, ValueError)):
        collector(root, [], {"max_output_bytes": 4096, "transfer_chunk_bytes": 1024}, collect_generated_outputs=True)


def test_generated_capture_rejects_empty_set(tmp_path):
    collector = remote_runner_py36._collect_expected
    root = str(tmp_path)
    with pytest.raises(RuntimeError, match="no generated output files"):
        collector(root, [], {"max_output_bytes": 4096, "transfer_chunk_bytes": 1024}, collect_generated_outputs=True)


def test_changed_staged_input_is_not_silently_excluded(tmp_path):
    collector = remote_runner_py36._collect_expected
    source = tmp_path / "main.cmd"
    source.write_bytes(b"before")
    original = {"relative_path": "main.cmd", "size_bytes": 6}
    generations=remote_runner_py36._input_generations(str(tmp_path),[original])
    source.write_bytes(b"after")
    root = str(tmp_path)
    records = collector(root, [], {"max_output_bytes": 4096, "transfer_chunk_bytes": 1024},
                        archive_entries=[original], input_generations=generations, collect_generated_outputs=True)
    assert [item["relative_path"] for item in records] == ["main.cmd"]


def test_generated_capture_rejects_symlinked_directory(tmp_path):
    collector = remote_runner_py36._collect_expected
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "frame.tdr").write_bytes(b"frame")
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
    root = str(tmp_path)
    with pytest.raises(RuntimeError, match="not a real directory"):
        collector(root, [], {"max_output_bytes": 4096, "transfer_chunk_bytes": 1024}, collect_generated_outputs=True)


def test_runtime_attestation_accepts_only_manifest_named_generated_files():
    package = ExecutionPackage.model_validate_json(_execution_package(), strict=True)
    project = package.project.model_copy(update={"collect_generated_outputs": True,
                                              "expected_outputs": ()})
    assert "collect_generated_outputs" in deck_project_diff(
        package.project, project)["frozen_field_changes"]
    package = package.model_copy(update={"project": project})
    raw = b"adaptive frame"
    path = "frames/movie_0079.tdr"
    name = "generated_0"
    record = RuntimeOutputRecord(name=name, relative_path=path,
                                 media_type="application/octet-stream",
                                 size_bytes=len(raw))
    manifest = TCADRuntimeManifest(started_at=None, completed_at="2026-09-24T00:00:00Z",
                                   terminal_state="succeeded", exit_code=0,
                                   error="", outputs=(record,))
    assert attest_runtime_contract(package, manifest,
                                   output_payloads={name: raw}).verdict == "pass"
    wrong = manifest.model_copy(update={"outputs": (record.model_copy(update={"name": "frame_79"}),)})
    assert attest_runtime_contract(package, wrong).verdict == "fail"
