"""Explicit deferred CLI diagnostics; excluded from default test discovery.

The six source nodes use dictionaries only. The process node is for the user
to run independently after G3. It exercises an installed sandbox dependency,
not the production launcher, Worker tool, registered Cov, or scientific work.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat

import pytest

from scripts.verify_g1_g3 import (
    BACKEND_CHECKS, DEPENDENCY_CHECKS, REPORT_LIMIT, REPORT_NAME, STREAM_LIMIT,
    bounded_text, case_plan, classify_case, coverage_not_run,
)


def _complete_batch():
    terminal = dict(state="terminal", cleanup_complete=True, returncode=0, stop_reason="exited")
    return dict(status="running", case="sandbox-startup", returncode=0,
                required_checks=list(DEPENDENCY_CHECKS), guard_resource=dict(terminal),
                guard_result=dict(status="passed", batches=[dict(terminal)]),
                report=dict(case_id="sandbox-startup-dependency", scope="sandbox_dependency",
                            start_marker=True, native_exit=0, assertions_status="complete",
                            fixture_export_cleanup=dict(status="passed"),
                            checks={name: "passed" for name in DEPENDENCY_CHECKS}))


def test_dependency_classifier_requires_complete_evidence():
    batch = _complete_batch()
    assert classify_case(batch) == "dependency_passed"
    batch["report"]["checks"]["readonly_private_git"] = "failed"
    assert classify_case(batch) == "assertion_failed"
    batch["interrupted"] = True
    assert classify_case(batch) == "interrupted"
    batch["interrupted"] = False
    batch["guard_resource"]["stop_reason"] = "timeout"
    assert classify_case(batch) == "guard_stopped"
    batch = _complete_batch()
    batch["returncode"] = 1
    assert classify_case(batch) == "assertion_failed"


def test_evidence_size_is_bounded():
    value = bounded_text("é" * (STREAM_LIMIT + 1))
    assert value["truncated"] is True
    assert len(value["text"].encode("utf-8")) <= STREAM_LIMIT
    assert bounded_text(b"short")["truncated"] is False
    batch = _complete_batch()
    batch["report"]["stderr"] = dict(text="x" * (REPORT_LIMIT + 1))
    assert classify_case(batch) == "evidence_incomplete"


def test_missing_cleanup_is_incomplete():
    for location in ("resource", "record", "export"):
        batch = _complete_batch()
        if location == "resource":
            batch["guard_resource"].pop("cleanup_complete")
        elif location == "record":
            batch["guard_result"]["batches"][0]["cleanup_complete"] = False
        else:
            batch["report"].pop("fixture_export_cleanup")
        assert classify_case(batch) == "evidence_incomplete"


def test_missing_start_marker_leaves_assertions_not_run():
    batch = _complete_batch()
    batch["returncode"] = 1
    batch["guard_result"]["status"] = "failed"
    batch["report"].update(start_marker=False, native_exit=1, assertions_status="not_run")
    batch["report"]["checks"].update({name: "not_run" for name in BACKEND_CHECKS})
    assert classify_case(batch) == "startup_failed"
    batch["report"]["checks"][BACKEND_CHECKS[0]] = "passed"
    assert classify_case(batch) == "evidence_incomplete"


def test_missing_required_checks_cannot_pass():
    for status in (None, "not_run", "skipped"):
        batch = _complete_batch()
        if status is None:
            batch["report"]["checks"].pop("readonly_export")
        else:
            batch["report"]["checks"]["readonly_export"] = status
        assert classify_case(batch) == "evidence_incomplete"
    batch = _complete_batch()
    batch["required_checks"] = []
    assert classify_case(batch) == "evidence_incomplete"
    batch = _complete_batch()
    batch["guard_result"]["batches"] = []
    assert classify_case(batch) == "evidence_incomplete"


def test_unreached_cases_remain_not_run():
    batches = case_plan("source")
    assert batches[0]["selected"] is True
    assert batches[1]["selected"] is False
    assert all(classify_case(batch) == "not_run" for batch in batches)
    assert all(item["status"] == "not_run" for item in coverage_not_run())
    batch = _complete_batch()
    batch["status"] = "not_run"
    assert classify_case(batch) == "not_run"


def _directory_facts(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return dict(exists=False, ordinary_directory=False, mode=None, owner_matches=None)
    return dict(exists=True, ordinary_directory=stat.S_ISDIR(info.st_mode),
                mode=f"{stat.S_IMODE(info.st_mode):04o}", owner_matches=info.st_uid == os.getuid())


def _read_stream(path):
    with path.open("rb") as stream:
        return bounded_text(stream.read(STREAM_LIMIT + 1))


@pytest.mark.process_e2e
def test_sandbox_startup_dependency(tmp_path):
    """User-only, after G3: actual selected binary, inert payload, tiny Git fixture."""
    import shutil
    import subprocess
    import sys

    log_directory = os.environ.get("SCID_TEST_LOG_DIR")
    assert log_directory, "Run this exact node through scripts/run_tests.py after G3"
    logs = Path(log_directory)
    logs.mkdir(parents=True, exist_ok=True)
    report_path = logs / REPORT_NAME
    report = dict(case_id="sandbox-startup-dependency", scope="sandbox_dependency",
                  last_phase="dependency_preflight", reached_layer="host_preflight",
                  start_marker=False, assertions_status="not_run", native_exit=None,
                  startup=dict(native="not_run", worker_tool="not_run", backend="not_run"),
                  launcher_provenance=dict(actual_production_launcher=False,
                      selector="scidiscovery.research_loop.native_execution._native_program",
                      selected_launcher="codex", native=None, bwrap=None,
                      configuration_source="test_git_namespace_capabilities.py dependency configuration"),
                  checks={name: "not_run" for name in DEPENDENCY_CHECKS},
                  fixture_code_commit=None, fixture_call_id="native-dependency", fixture_step=1,
                  configured_directories={},
                  socket_directory=dict(status="unresolved", path=None,
                      reason="Configured temporary directories do not resolve the app-server socket directory"),
                  fixture_export_cleanup=dict(status="not_run"),
                  product_export_cleanup=dict(status="not_run", reason="No production attempt"),
                  evidence_errors=[])

    def save():
        encoded = (json.dumps(report, indent=2) + "\n").encode("utf-8")
        assert len(encoded) <= REPORT_LIMIT, "Diagnostic report exceeds evidence byte limit"
        report_path.write_bytes(encoded)

    def check(name, condition):
        report["checks"][name] = "passed" if condition else "failed"
        save()
        assert condition, name

    save()
    root = export = versions = None
    launched = False
    process_finished = False
    error = None
    try:
        assert sys.platform == "linux", "Sandbox startup dependency requires Linux"
        bwrap = shutil.which("bwrap")
        assert bwrap is not None, "Sandbox startup dependency requires installed bwrap"
        from scidiscovery.research_loop import code_versions as versions
        from scidiscovery.research_loop.native_execution import _native_program

        native = _native_program(["codex"])
        python = Path("/usr/bin/python3")
        assert python.is_file(), "Sandbox startup dependency requires /usr/bin/python3"
        report["launcher_provenance"].update(native=str(native), bwrap=str(Path(bwrap).resolve()))
        base = tmp_path.resolve()
        root = base / "private/nested/instance"
        root.mkdir(parents=True)
        work = base / "workspace"
        code = work / "drafts"
        (code / "artifacts").mkdir(parents=True)
        (code / "scratch").mkdir()
        (work / "scratch").mkdir()
        v1, v2 = b'print("pinned-v1")\n', b'print("draft-v2")\n'
        (code / "main.py").write_bytes(v1)
        (code / "artifacts/seed.txt").write_text("tiny captured directory\n")
        (code / "scratch/seed.txt").write_text("tiny captured scratch slot\n")
        limits = dict(max_file_bytes=128, max_total_bytes=384, max_files=3, max_scan_entries=5)
        report["last_phase"] = "capture_fixture"
        version = versions.capture(root, step=1, call_id="native-dependency", code_root=code,
                                   entry="main.py", selection="directory", logical_root="drafts", limits=limits)
        report["fixture_code_commit"] = version["commit"]
        (code / "main.py").write_bytes(v2)
        versions.verify(root, step=1, call_id="native-dependency", code_version=version, limits=limits)
        check("capture_pinned_v1", (code / "main.py").read_bytes() == v2)
        export = versions.materialize(root, step=1, call_id="native-dependency", code_version=version, limits=limits)
        check("export_bytes_v1", (export / "main.py").read_bytes() == v1
              and stat.S_IMODE((export / "main.py").stat().st_mode) == 0o444
              and stat.S_IMODE(export.stat().st_mode) == 0o555)
        repository = root / "host-runtime/code-versions/repository.git"
        private_names = ("HEAD", "config", "refs/scidiscovery/steps/001/calls/native-dependency",
                         f"objects/{version['commit'][:2]}/{version['commit'][2:]}")
        originals = {name: (repository / name).read_bytes() for name in private_names}
        modes = {name: (repository / name).stat().st_mode for name in private_names}
        home = base / "codex-home"
        home.mkdir(mode=0o700)
        configuration = home / "config.toml"
        configuration.write_text(
            'default_permissions = "scid-local-attempt"\n'
            '[permissions.scid-local-attempt]\n'
            'extends = ":workspace"\n'
            'filesystem = {}\n'
            '[permissions.scid-local-attempt.network]\n'
            'enabled = false\n')
        configuration.chmod(0o400)
        scratch = base / "native-tmp"
        scratch.mkdir(mode=0o700)
        scratch.chmod(0o700)
        output = base / "output"
        output.mkdir()
        external = base / "external-input"
        external.mkdir()
        (external / "evidence.txt").write_text("external original\n")
        report["configured_directories"] = {
            "CODEX_HOME": _directory_facts(home), "HOME": _directory_facts(scratch),
            "TMPDIR": _directory_facts(scratch), "XDG_RUNTIME_DIR": _directory_facts(scratch)}
        argv = [bwrap, "--unshare-user", "--unshare-pid", "--die-with-parent",
                "--bind", "/", "/", "--proc", "/proc"]
        # Match the existing dependency probe. Rename checks are confined to
        # strict descendants of this new synthetic fixture, never real ancestors.
        for parent in reversed(repository.parents):
            if parent != Path("/"):
                argv.extend(["--bind", str(parent), str(parent)])
        argv.extend(["--bind", str(work), str(work),
                     "--ro-bind", str(repository), str(repository),
                     "--ro-bind", str(external), str(external),
                     "--ro-bind", str(configuration), str(configuration),
                     "--ro-bind", str(export), str(export),
                     "--bind", str(output), str(export / "artifacts"),
                     "--bind", str(work / "scratch"), str(export / "scratch"),
                     "--clearenv"])
        environment = dict(PATH="/usr/bin:/bin", HOME=str(scratch), CODEX_HOME=str(home),
                           TMPDIR=str(scratch), XDG_RUNTIME_DIR=str(scratch), LANG="C.UTF-8",
                           PYTHONDONTWRITEBYTECODE="1")
        for key, value in environment.items():
            argv.extend(["--setenv", key, value])
        # No provider, model, server or production Worker protocol is involved.
        # START is flushed before any permission assertion in this inert payload.
        payload = '''import json,pathlib,sys
print('SCID_SANDBOX_START',flush=True)
repo,base,work,external=map(pathlib.Path,sys.argv[1:5])
private_names=json.loads(sys.argv[5])
here=pathlib.Path.cwd()
def done(name): print('SCID_CHECK '+name,flush=True)
def denied(action):
    try: action()
    except OSError: return
    raise AssertionError('A readonly or pinned fixture path was mutable')
assert all(not (here/name).exists() for name in ('.git','.agents','.codex','.aws'))
done('clean_export_metadata')
assert (here/'main.py').read_bytes()==b'print("pinned-v1")\\n'
for action in (lambda: (here/'main.py').write_text('changed'),
               lambda: (here/'main.py').chmod(0o600),
               lambda: (here/'created').write_text('changed'),
               lambda: (here/'created-directory').mkdir(),
               lambda: (here/'main.py').rename(here/'moved.py')):
    denied(action)
done('readonly_export')
for name in private_names:
    path=repo/name
    denied(lambda: path.write_text('changed'))
    denied(lambda: path.chmod(0o600))
denied(lambda: (repo/'created').write_text('changed'))
denied(lambda: (repo/'created-directory').mkdir())
denied(lambda: (repo/'HEAD').rename(repo/'moved-HEAD'))
done('readonly_private_git')
for path in (repo,*repo.parents):
    if path!=base and path.is_relative_to(base):
        denied(lambda: path.rename(path.with_name(path.name+'-moved')))
done('pinned_ancestors')
assert (external/'evidence.txt').read_text()=='external original\\n'
denied(lambda: (external/'evidence.txt').write_text('changed'))
denied(lambda: (external/'evidence.txt').chmod(0o600))
done('external_inputs')
(here/'artifacts/native-probe.txt').write_text('native artifact\\n')
(here/'scratch/temporary').write_text('legitimate scratch\\n')
done('writable_outputs')
'''
        argv.extend(["--chdir", str(export), "--", str(native), "sandbox", "-P", "scid-local-attempt",
                     "-C", str(export), "--", str(python), "-I", "-c", payload,
                     str(repository), str(base), str(work), str(external), json.dumps(private_names)])
        report.update(last_phase="sandbox_launch", reached_layer="installed_sandbox_dependency")
        report["startup"].update(native="attempted", backend="not_reached")
        save()
        stdout, stderr = logs / "native.stdout.log", logs / "native.stderr.log"
        try:
            with stdout.open("wb") as out, stderr.open("wb") as err:
                launched = True
                # Existing run_tests.py owns the outer resource/tree guard and
                # bounds these fixture .log files; do not introduce another guard.
                result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=err, timeout=15)
                process_finished = True
                report["native_exit"] = result.returncode
        finally:
            for name, path in (("stdout", stdout), ("stderr", stderr)):
                if path.is_file():
                    report[name] = _read_stream(path)
            lines = report.get("stdout", {}).get("text", "").splitlines()
            started = "SCID_SANDBOX_START" in lines
            report["start_marker"] = started
            if started:
                report["reached_layer"] = "inert_sandbox_payload"
                report["startup"].update(native="started", backend="started")
                for name in BACKEND_CHECKS:
                    if f"SCID_CHECK {name}" in lines:
                        report["checks"][name] = "passed"
                report["assertions_status"] = "complete" if all(
                    report["checks"][name] == "passed" for name in BACKEND_CHECKS) else "incomplete"
            else:
                report["startup"]["native"] = "unresolved"
                report["assertions_status"] = "not_run"
            save()
        assert report["start_marker"], "Sandbox dependency failed before START; permission assertions not_run"
        assert report["native_exit"] == 0, "Sandbox dependency payload failed; see bounded stdout/stderr and raw logs"
        assert report["assertions_status"] == "complete", "Required permission checks did not all run"
        report["last_phase"] = "host_fixture_verification"
        check("private_git_unchanged", all((repository / name).read_bytes() == content
              and (repository / name).stat().st_mode == modes[name] for name, content in originals.items()))
        check("legitimate_outputs", (export / "main.py").read_bytes() == v1
              and not (export / "created").exists()
              and not (export / "artifacts/native-probe.txt").exists()
              and (output / "native-probe.txt").read_text() == "native artifact\n"
              and (code / "main.py").read_bytes() == v2
              and (work / "scratch/temporary").read_text() == "legitimate scratch\n"
              and (external / "evidence.txt").read_text() == "external original\n")
    except Exception as exc:
        error = exc
        report["error"] = dict(type=type(exc).__name__, message=str(exc)[:1024])
    finally:
        if export is not None and versions is not None and (not launched or process_finished):
            try:
                versions.cleanup_export(root, step=1, call_id="native-dependency")
                complete = not export.exists()
                report["fixture_export_cleanup"] = dict(status="passed" if complete else "failed",
                    scope="synthetic code_versions export only; not production lifecycle cleanup")
                report["checks"]["fixture_export_cleanup"] = "passed" if complete else "failed"
            except Exception as exc:
                report["fixture_export_cleanup"] = dict(status="failed", error=type(exc).__name__)
                report["checks"]["fixture_export_cleanup"] = "failed"
                if error is None:
                    error = exc
        elif launched and not process_finished:
            report["fixture_export_cleanup"] = dict(status="not_run",
                reason="Native subprocess did not return normally; existing guard owns remaining observed-tree and temporary-fixture cleanup")
            report["checks"]["fixture_export_cleanup"] = "not_run"
        if error is None and process_finished:
            report["last_phase"] = "dependency_complete"
        save()
    if error is not None:
        raise error
    assert all(report["checks"][name] == "passed" for name in DEPENDENCY_CHECKS)
