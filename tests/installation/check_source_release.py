"""Explicit source-release packaging check; not part of default test discovery.

Select this file directly in the installed lane when the release builder changes.
It never runs the packaged functional suite or creates an installation environment.
"""
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path


def _sha256_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def test_git_release_builder_emits_clean_manifested_source(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[2]
    output = tmp_path / "scidiscovery-agent"
    completed = subprocess.run(
        [
            sys.executable,
            str(project_root / "scripts/build_git_release.py"),
            "--source",
            str(project_root),
            "--output",
            str(output),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert (output / "README.md").is_file()
    assert (output / "README.zh-CN.md").is_file()
    assert (output / "docs/INSTALL.md").is_file()
    assert (output / "docs/INSTALL.zh-CN.md").is_file()
    assert (output / "docs/TCAD_QUALIFICATION_STATUS.md").is_file()
    assert not (output / "plugins/table_observation").exists()
    assert {path.name for path in (output / "plugins").iterdir() if path.is_dir()} == {
        "curve_score", "curve_figure_evidence", "tcad_artifact", "cov_execution",
    }
    assert not (output / "deploy/apply_ingaas_fig4_profile.sh").exists()
    assert not tuple(output.rglob("figure_geometry.json"))
    assert not (output / "src/scidiscovery/artifact_agent/service").exists()
    for name in ("runtime", "execution_bridge", "runtime_plugin_bindings", "worker_services",
                 "state_format", "portable_bundle"):
        assert not (output / f"src/scidiscovery/artifact_agent/{name}.py").exists()
    for name in ("analysis_artifacts", "calculation_proof", "pdf_tool"):
        assert (output / f"src/scidiscovery/operations/{name}.py").is_file()
    assert not (output / "src/scidiscovery/platforms/codex_worker.py").exists()
    assert not (output / "experiments/worker_process_v2").exists()
    # The release manifest is the explicit publication policy; archived research
    # documents outside it must not leak into the generated repository.
    import runpy
    release = runpy.run_path(str(project_root / "scripts/build_git_release.py"))
    assert {path.relative_to(output).as_posix()
            for path in (output / "docs").rglob("*") if path.is_file()} == set(release["DOCUMENTS"])
    # Process records and superseded architecture snapshots are local archives.
    for relative in ("docs/plans", "docs/history", "docs/architecture",
                                  "docs/SCIENTIFIC_PAPER_EVIDENCE_QUALIFICATION.zh-CN.md",
                     ".trial", "AGENTS.md"):
        assert not (output / relative).exists()
    assert (output / "deploy/install_transaction.py").is_file()
    assert not (output / "deploy/init_workspace.sh").exists()
    assert not (output / "deploy/systemd").exists()
    assert not (output / "roles").exists()
    assert not (output / "src/scidiscovery/artifact_agent/interfaces").exists()
    assert not (output / "src/scidiscovery/artifact_agent/approval_ui").exists()
    assert (
        output / "plugins/curve_figure_evidence/curve_figure_evidence/figure_worker_tool.py"
    ).is_file()
    assert (
        output
        / "plugins/curve_figure_evidence/curve_figure_evidence/plugin.py"
    ).is_file()
    assert not (output / "skills/scientific-paper-evidence").exists()
    manual_root = output / "skills/sentaurus-tcad-code/references/manuals"
    for relative, digest in (
        (
            "R-2020.09/sprocess_ug.pdf",
            "bcaf5cfe87bd276a2068fa6681a0b62b2526512b65a710a660d892c45ceaf5d9",
        ),
        (
            "R-2020.09/sdevice_ug.pdf",
            "dae2c94b29c92705d3b8d6124c2f0ab595541ed48e4b220a425f72fac42794ce",
        ),
        (
            "R-2020.09/sentaurus_relnote.pdf",
            "0e6679154406b2d12356be28ab901a3786f48ca9b91b14f6769ada508bdbfbf9",
        ),
    ):
        manual = manual_root / relative
        assert manual.is_file()
        assert _sha256_file(manual) == digest
    for name in ("manual_search.py", "manual_extract.py"):
        helper = output / "skills/sentaurus-tcad-code/scripts" / name
        assert helper.is_file()
        assert helper.stat().st_mode & 0o111
    assert not (output / "research").exists()
    assert not (output / "archive").exists()
    assert not (output / "workspace").exists()
    assert not (output / "deliverables").exists()
    assert not (output / "123").exists()
    assert not (output / ".codex").exists()
    assert not any(output.rglob("r5_g_*.py"))
    assert not any(output.rglob("test_r5_g_*_runner.py"))
    assert not any(output.rglob("__pycache__"))
    manifest = output / "MANIFEST.sha256"
    entries = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        digest, relative = line.split("  ", 1)
        entries[relative] = digest
    readme = output / "README.md"
    assert entries["README.md"] == hashlib.sha256(readme.read_bytes()).hexdigest()
    assert set(entries) == {
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file() and path != manifest
    }
    assert output.with_suffix(".tar.gz").is_file()
    # Independent consumer requirements: do not derive these from the builder's
    # own allowlists, which previously made missing CI/test inputs invisible.
    required = (
        "docs/PLUGIN_RUNTIME_API.md", "tests/conftest.py", "tests/operations/conftest.py",
        "tests/fixtures/plugins/blind_csv_operation_plugin/blind_csv_plugin/plugin.py",
        "tests/fixtures/plugins/architecture_operation_plugin/pyproject.toml",
        "tests/fixtures/plugins/table_observation_plugin/pyproject.toml",
        "scripts/run_tests.py", "scripts/test_resources.json", "scripts/test_resources_ci.json",
        "scripts/installed_candidates.py", "scripts/probe_native_code_mode.py",
        "scripts/compiled_worker_process_guard.py",
        "docs/ROOTLESS_CLI.zh-CN.md",
        "scripts/acceptance/fixtures/rootless_selection/selection.json",
        "scripts/acceptance/fixtures/rootless_selection/actions.json",
        "scripts/acceptance/rootless_cov_launcher.py",
        "scripts/acceptance/rootless_public_selection_replay.py",
        "scripts/acceptance/behavior_card.py", "scripts/acceptance/prelive_gate.py",
        "plugins/cov_execution/cov_execution/observer.py",
        "scripts/acceptance/fixtures/search_mvp/native-payloads.json",
         "scripts/prepare_covshift_local.py",
        "scripts/prepare_nbody_local.py",
        "scripts/native_usage_audit.py",
        "scripts/acceptance/fixtures/hourly_loop_memory/materials.json",
        "scripts/acceptance/fixtures/covshift_permissions/canary.py",
    )
    assert all((output / relative).is_file() for relative in required)
    expected_tests = {p.relative_to(project_root / "tests") for p in
                      (project_root / "tests").rglob("*") if p.is_file()
                      and not any(part in release["IGNORED_NAMES"] or
                                  part.endswith(release["IGNORED_SUFFIXES"])
                                  for part in p.relative_to(project_root / "tests").parts)}
    assert {p.relative_to(output / "tests") for p in (output / "tests").rglob("*")
            if p.is_file()} == expected_tests
    current_references = (
        "README.md", "README.zh-CN.md", "CHANGELOG.md", "CONTRIBUTING.md", "NOTICE.md",
        "SECURITY.md", "deploy/README.md", "deploy/README.zh-CN.md", "tests/README.md",
        "plugins/tcad_artifact/README.md", "plugins/tcad_artifact/README.zh-CN.md",
        "plugins/curve_score/README.md", "plugins/curve_score/README.zh-CN.md",
        "plugins/curve_figure_evidence/README.md", "plugins/curve_figure_evidence/README.zh-CN.md",
        *release["DOCUMENTS"],
    )
    for relative in current_references:
        document = output / relative
        text = document.read_text()
        # Markdown examples name runtime artifacts, not release documents.
        prose = re.sub(r'(?ms)^(`{3,}|~{3,})[^\n]*\n.*?^\1[ \t]*$', '', text)
        prose = re.sub(r'(`+).*?\1', '', prose, flags=re.DOTALL)
        for link in re.findall(r"\]\(([^)]+)\)", prose):
            if "://" in link:
                continue
            target, _, anchor = link.partition("#")
            linked = document.parent / target if target else document
            assert linked.is_file(), (relative, link)
            if anchor:
                linked_text = linked.read_text()
                headings = {re.sub(r'[^\w -]', '', heading.lower()).replace(' ', '-')
                            for heading in re.findall(r'^#{1,6} +(.+?) *#*$', linked_text, re.MULTILINE)}
                assert f'id="{anchor}"' in linked_text or anchor in headings, (relative, link)
        assert "plans/RESEARCH_TASK_REFACTOR_R4" not in text

    receipt_root = Path(os.environ["SCID_TEST_LOG_DIR"])
    receipt_root.mkdir(parents=True, exist_ok=True)
    (receipt_root / "release-manifest.log").write_text(manifest.read_text())
