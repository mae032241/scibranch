"""Explicit deferred CLI diagnostics; excluded from default test discovery.

Source nodes check report dictionaries and explicit inputs only. Process/live
nodes are user-only after G3. The live node uses a real selected model W and
the original bridge/Local launcher; it is a finite engineering fixture, with
no H/A, scientific qualification, registered Cov or full V1-V12 claim.
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
    WORKER_CHECKS, WORKER_INPUT_ENV, WORKER_REPORT_NAME, worker_options,
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


def _worker_inputs():
    return dict(launcher=["codex"], model="operator-selected", reasoning_effort="high",
        max_tokens=5000, max_seconds=90,
        native_options=dict(workspace="workspace-write", network=False, web="disabled", subagents=False,
            sandbox_workspace_write=dict(writable_roots=[], network_access=False,
                exclude_tmpdir_env_var=False, exclude_slash_tmp=False)))


def test_worker_classifier_requires_actual_layers_and_all_checks():
    batch = _complete_batch()
    batch.update(case="worker-managed", required_checks=list(WORKER_CHECKS))
    batch["report"] = dict(case_id="worker-managed", scope="real_worker_managed_local",
        worker_started=True, backend_started=True, assertions_status="complete",
        checks={name: "passed" for name in WORKER_CHECKS})
    assert classify_case(batch) == "engineering_passed"
    batch["report"]["backend_started"] = False
    assert classify_case(batch) == "startup_failed"
    batch["report"]["backend_started"] = True
    batch["report"]["checks"]["terminal_exports_removed"] = "not_run"
    assert classify_case(batch) == "evidence_incomplete"
    batch["report"]["checks"]["terminal_exports_removed"] = "failed"
    assert classify_case(batch) == "assertion_failed"
    batch["report"]["unsupported"] = "Original namespace is unsupported"
    assert classify_case(batch) == "unsupported"


def test_worker_inputs_are_explicit_and_bounded():
    from copy import deepcopy
    original = _worker_inputs()
    assert worker_options(original) == original
    for key in tuple(original):
        value = deepcopy(original)
        value.pop(key)
        with pytest.raises(ValueError):
            worker_options(value)
    for key, invalid in (("max_tokens", True), ("max_tokens", 50001), ("max_seconds", float("inf")),
                         ("max_seconds", 181), ("launcher", ["codex", "--yolo"])):
        value = deepcopy(original)
        value[key] = invalid
        with pytest.raises(ValueError):
            worker_options(value)
    for field, invalid in (("workspace", "danger-full-access"), ("network", "false")):
        value = deepcopy(original)
        value["native_options"][field] = invalid
        with pytest.raises(ValueError):
            worker_options(value)
    value = deepcopy(original)
    value["native_options"]["sandbox_workspace_write"]["writable_roots"] = ["/tmp"]
    with pytest.raises(ValueError):
        worker_options(value)


def test_components_and_live_scope_do_not_overclaim():
    default = case_plan()
    assert not next(row for row in default if row["case"] == "worker-managed")["selected"]
    live = [row for row in case_plan("worker-managed") if row["selected"]]
    assert len(live) == 1 and live[0]["lane"] == "live"
    components = [row for row in case_plan("components") if row["selected"]]
    assert len(components) == 5
    assert all(row["expected_tests"] > 0 and "namespace" not in row["scope"] for row in components)
    assert {row["id"] for row in coverage_not_run()} >= {
        "registered-cov", "hwa-direct-originals", "cross-filesystem"}


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


# These helpers are fixed engineering payloads, not native/model stand-ins.
# Only the user-selected real W can run the driver through its original shell.
PERMISSION_PROBE = r'''
def permissions(configuration,observations=None,audit=None):
    base=pathlib.Path(configuration['fixture_base'])
    if observations is None:observations=[]
    for name,text in configuration['git_files'].items():
        path=pathlib.Path(text)
        assert path.is_relative_to(base) and path!=base
        visible=path.exists()
        for operation in ('write','chmod'):
            try:
                if operation=='write':
                    with path.open('r+b') as stream:stream.write(b'G3-MUST-BE-DENIED')
                else:path.chmod(0o600)
            except OSError as error:
                observations.append(dict(target=name,operation=operation,denied=True,
                    errno=error.errno,visible=visible))
            else:
                observations.append(dict(target=name,operation=operation,denied=False,visible=visible))
                if audit:audit()
                raise AssertionError('Private Git fixture was writable: '+name+'.'+operation)
            if audit:audit()
    for name,text in configuration['git_parents'].items():
        path=pathlib.Path(text)
        assert path.is_relative_to(base) and path!=base
        visible=path.exists()
        try:path.rename(path.with_name(path.name+'-g3-moved'))
        except OSError as error:
            observations.append(dict(target=name,operation='rename',denied=True,
                errno=error.errno,visible=visible))
        else:
            observations.append(dict(target=name,operation='rename',denied=False,visible=visible))
            if audit:audit()
            raise AssertionError('Private Git fixture ancestor was renameable: '+name)
        if audit:audit()
    return observations
'''

ENGINEERING_BACKEND = r'''
import importlib.util,json,pathlib,subprocess,time
VERSION='__VERSION__'
here=pathlib.Path.cwd();work=here.parents[1];code=pathlib.Path(__file__).parent
configuration=json.loads((work/'work/g3-targets.json').read_text())
print('SCID_G3_BACKEND_'+VERSION,flush=True)
__PERMISSION_PROBE__
# The host instance inode is not mounted in this backend. Its visible parent
# can be a synthetic namespace mountpoint, so never treat renaming that object
# as a write to the host original. The outer actual W tests the host parent.
backend_targets=dict(configuration,git_parents={name:path for name,path in
    configuration['git_parents'].items() if name!='instance'})
observations=permissions(backend_targets)
public=pathlib.Path(configuration['public_input'])
assert public.read_bytes()==b'G3 external original\n'
try:public.write_bytes(b'must remain original')
except OSError:pass
else:raise AssertionError('Backend public input was writable')
(here/'scratch/backend-positive.txt').write_text('backend-'+VERSION)
if VERSION=='v1':
    (here/'scratch/started').write_text('v1 entered its actual namespace')
    deadline=time.monotonic()+15
    while not (here/'scratch/release').exists():
        assert time.monotonic()<deadline,'No post-capture draft release'
        time.sleep(.05)
    assert importlib.util.find_spec('uncaptured') is None,'Uncaptured draft module leaked into v1'
else:
    import uncaptured
    assert uncaptured.VALUE=='captured-v2'
import helper
assert helper.VALUE==VERSION
assert (code/'resource.txt').read_text()==VERSION
assert "VERSION='"+VERSION+"'" in pathlib.Path(__file__).read_text()
assert subprocess.check_output([str(code/'helper.sh')]).decode()==VERSION
try:(code/'resource.txt').write_text('must remain fixed')
except OSError:pass
else:raise AssertionError('Fixed code was writable')
payload=dict(version=VERSION,backend_started=True,git_attempts=observations,
    allowed_write=True,uncaptured_absent=VERSION=='v1',fixed_code_readonly=True,
    helper_import=helper.VALUE,resource=(code/'resource.txt').read_text(),
    host_instance_parent='not_run: host original is not mounted; visible synthetic parent is not its identity')
(here/'artifacts/probe.json').write_text(json.dumps(payload))
(here/'artifacts/result.json').write_text(json.dumps(dict(version=1,
    metrics=[dict(name='engineering_probe',value=1)],
    outputs=[dict(path='artifacts/probe.json',label='Finite engineering observation; no scientific verdict')])))
print('SCID_G3_BACKEND_DONE_'+VERSION,flush=True)
'''

ENGINEERING_DRIVER = r'''
import json,pathlib,subprocess,sys,time
work=pathlib.Path.cwd();slot=work/'work/experiment';code=work/'code'
configuration=json.loads((work/'work/g3-targets.json').read_text())
report=dict(started=True,git_attempts=[],allowed_write=False,calls=[],cached_original=False)
destination=work/'work/g3-driver-result.json'
def save():destination.write_text(json.dumps(report))
save()
__PERMISSION_PROBE__
permissions(configuration,report['git_attempts'],save)
(work/'work/native-positive.txt').write_text('legitimate workspace write')
(work/'artifacts/native-positive.txt').write_text('legitimate artifact write')
report['allowed_write']=True;save()
command=[configuration['python'],'-m','scidiscovery.research_loop.worker_execution_bridge',
    '--submission-directory',str(slot)]
serial=0
def request(arguments):
    global serial
    serial+=1
    out=work/'work'/('g3-bridge-%02d.stdout'%serial)
    err=out.with_suffix('.stderr')
    with out.open('wb') as stdout,err.open('wb') as stderr:
        result=subprocess.run(command+arguments,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,timeout=35)
    raw=out.read_bytes();assert len(raw)<=256000,'Bounded bridge response required'
    value=json.loads(raw)
    assert result.returncode==0,{'bridge_exit':result.returncode,'status':value.get('status')}
    return value
assert request(['capabilities'])
request(['describe','--key','local_command','--output','work/g3-local-contract.json'])
runtime=work/'experiments/v1'
out=work/'work/g3-first.stdout';err=work/'work/g3-first.stderr'
arguments=['run','--experiment-dir',str(runtime),'--script',str(code/'run.py')]
with out.open('wb') as stdout,err.open('wb') as stderr:
    original=subprocess.Popen(command+arguments,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr)
    deadline=time.monotonic()+18
    while not (runtime/'scratch/started').exists():
        assert original.poll() is None,'Backend did not reach the actual namespace'
        assert time.monotonic()<deadline,'No backend start marker; preserve original call'
        time.sleep(.05)
    # This is deliberately AFTER capture: evidence for V3, never a V2 race claim.
    (code/'run.py').write_text((work/'work/g3-v2-source.txt').read_text())
    (code/'helper.py').write_text("VALUE='v2'\n")
    (code/'resource.txt').write_text('v2')
    (code/'helper.sh').write_text('#!/bin/sh\nprintf v2\n');(code/'helper.sh').chmod(0o755)
    (code/'uncaptured.py').write_text("VALUE='captured-v2'\n")
    (runtime/'scratch/release').write_text('draft v2 exists; release original v1')
    assert original.wait(timeout=20)==0,'Original run failed; never retry'
first=json.loads(out.read_bytes());assert first['status']=='known_success',first.get('status')
report['calls'].append(first['call_id']);save()
# Replay the SAME ordinary reference only; never send a new run request.
raw=(slot/'submit.json').read_bytes();before=json.loads((slot/'response.json').read_bytes())
assert before['result']['call_id']==first['call_id']
# The watcher deduplicates identical consecutive bytes. Finish a safe read of
# this known terminal call first, so the saved run raw reaches its claim cache.
assert request(['query','--call-id',first['call_id']])['state']=='succeeded'
assert (slot/'submit.json').read_bytes()!=raw
(slot/'response.json').unlink()
pending=slot/'g3-cache.pending';pending.write_bytes(raw);pending.replace(slot/'submit.json')
deadline=time.monotonic()+5
while True:
    if (slot/'response.json').exists():
        cached=json.loads((slot/'response.json').read_bytes())
        if cached.get('submission_ref')==before['submission_ref']:break
    assert time.monotonic()<deadline,'Same-reference cached response unavailable; preserve original'
    time.sleep(.05)
assert cached['request']==before['request'] and cached['result']['call_id']==first['call_id']
assert cached['code']==0 and cached['result']['status']=='known_success'
report['cached_original']=True
report['cached_submission_ref']=before['submission_ref'];save()
# Restore the already completed query reference after replaying the older run
# reference; later commands must advance beyond that existing claim number.
assert request(['query','--call-id',first['call_id']])['state']=='succeeded'
one=request(['collect','--call-id',first['call_id']])
two=request(['collect','--call-id',first['call_id']])
assert one==two
second=request(['run','--experiment-dir',str(work/'experiments/v2'),'--script',str(code/'run.py')])
assert second['status']=='known_success' and second['call_id']!=first['call_id']
report['calls'].append(second['call_id'])
assert request(['query','--call-id',second['call_id']])['state']=='succeeded'
request(['collect','--call-id',second['call_id']])
report['complete']=True;save()
print('SCID_G3_DRIVER_COMPLETE',flush=True)
'''


def _fixture_identity(path):
    info = path.stat()
    return dict(device=info.st_dev, inode=info.st_ino, mode=stat.S_IMODE(info.st_mode),
                bytes=info.st_size, mtime_ns=info.st_mtime_ns)


@pytest.mark.live_platform
def test_actual_worker_managed_local():
    """User-only real model W -> original bridge -> real managed Local namespace."""
    from scripts.verify_g1_g3 import read_evidence

    log_directory = os.environ.get("SCID_TEST_LOG_DIR")
    assert log_directory, "Use scripts/verify_g1_g3.py --case worker-managed after G3"
    logs = Path(log_directory)
    logs.mkdir(parents=True, exist_ok=True)
    report_path = logs / WORKER_REPORT_NAME
    report = dict(case_id="worker-managed", scope="real_worker_managed_local",
        last_phase="operator_preflight", worker_started=False, backend_started=False,
        assertions_status="not_run", checks={name: "not_run" for name in WORKER_CHECKS},
        product_export_cleanup=dict(status="not_run"), original_calls=[],
        limitations=["Engineering evidence only; no scientific qualification or extra execution budget.",
            "No registered Cov, H/A lifecycle, full V2 capture race, cancellation, crash injection or cross-filesystem run.",
            "Same-user host access and unobserved descriptors remain a soft boundary.",
            "Output inode reuse does not measure every write or prove V7 full lifecycle deduplication.",
            "Unknown calls and exports are retained; this probe never retries native execution or submits an unknown call."],
        unreached=dict(registered_cov="not_run", real_hwa="not_run", cross_filesystem="not_run",
            cancellation="not_run", delayed_finish_replay="not_run", crash_recovery="not_run"))

    def save():
        raw = (json.dumps(report, indent=2) + "\n").encode()
        assert len(raw) <= REPORT_LIMIT, "Bounded engineering report required"
        report_path.write_bytes(raw)

    def check(name, condition):
        report["checks"][name] = "passed" if condition else "failed"
        save()
        assert condition, name

    save()
    root = record = work = None
    error = None
    try:
        raw_options = os.environ.get(WORKER_INPUT_ENV)
        assert raw_options and len(raw_options) <= 8192, "Explicit bounded operator inputs required; not_run"
        options = worker_options(json.loads(raw_options))
        # No copied HOME/config/auth and no new permission profile. Production
        # receives the exact operator declarations and inherited native auth.
        from scidiscovery.research_loop import (budget, code_versions as versions,
            contracts as c, controller, execution, experiments as e, native, runtime)
        import sys

        base = logs / "worker-managed-fixture"
        base.mkdir()  # Persistent new output, outside guard's disposable basetemp.
        public = base / "public"
        public.mkdir()
        objective = public / "objective.md"
        objective.write_text("Finite engineering fixture only. Run the provided driver once and report observed limitations.\n")
        external = public / "input.txt"
        external.write_bytes(b"G3 external original\n")
        external_before = (_fixture_identity(external), external.read_bytes())
        objective_before = (_fixture_identity(objective), objective.read_bytes())
        root = base / "instance"
        controller.prepare(root, objective, public, options["launcher"],
            max_tokens=options["max_tokens"], max_seconds=options["max_seconds"], max_steps=1,
            model=options["model"], reasoning_effort=options["reasoning_effort"],
            search_policy=None, experiment_mode="local", native_options=options["native_options"],
            execution_config=dict(executor="local_execution:command",
                                  configuration=dict(run_argv=[sys.executable], max_seconds=35)))
        c.write(root / "host-started.json", budget.clock_stamp())
        budget.transition(root, "advance", "User-selected finite CLI engineering fixture")
        instruction = ("This task is an explicitly selected engineering fixture, with no scientific validity claim. "
            "Use your normal native shell once to run " + repr(sys.executable) + " work/g3-driver.py from this workspace. "
            "The fixed driver exercises only tiny synthetic files in this new fixture and the existing managed tool. "
            "Do not edit the driver, targets, sources or fixture, independently inspect host/private data, invent receipts, "
            "retry a failed command, resubmit an unknown call, spawn agents or run other experiments. "
            "Do not read credentials/configuration or print environment/session information. "
            "Read the driver's short result in work/g3-driver-result.json and return your own final scientific JSON, "
            "with stage_intent=review and limitations that these are finite engineering observations only. "
            "The host records mechanical evidence; do not compute hashes, inventories or qualifications.")
        c.build_job(root, 1, "W", [], instruction=instruction)
        work = c.workspace(root, 1)
        record = execution.origin(root, 1)
        code = work / "code"
        code.mkdir()
        backend = ENGINEERING_BACKEND.replace("__PERMISSION_PROBE__", PERMISSION_PROBE)
        v1 = backend.replace("__VERSION__", "v1")
        v2 = backend.replace("__VERSION__", "v2")
        (code / "run.py").write_text(v1)
        (code / "helper.py").write_text("VALUE='v1'\n")
        (code / "resource.txt").write_text("v1")
        (code / "helper.sh").write_text("#!/bin/sh\nprintf v1\n")
        (code / "helper.sh").chmod(0o755)
        for version in ("v1", "v2"):
            (work / "experiments" / version / "scratch").mkdir(parents=True)
            (work / "experiments" / version / "artifacts").mkdir()
        # Create precise existing protection targets before W starts. This seed
        # is a test fixture pin, never an accepted experiment or permission.
        seed = versions.capture(root, step=1, call_id="g3-permission-seed", code_root=code,
                                entry="run.py", selection="directory", logical_root="code")
        repository = root / "host-runtime/code-versions/repository.git"
        index = repository / "index"
        index.write_bytes(b"G3 unused default index sentinel\n")
        git_files = dict(head=repository / "HEAD", config=repository / "config", index=index,
            ref=repository / "refs/scidiscovery/steps/001/calls/g3-permission-seed",
            object=repository / "objects" / seed["commit"][:2] / seed["commit"][2:])
        git_originals = {name: (_fixture_identity(path), path.read_bytes()) for name, path in git_files.items()}
        report["private_git_before"] = {name: facts for name, (facts, _) in git_originals.items()}
        targets = dict(fixture_base=str(base), python=sys.executable, public_input=str(external),
            git_files={name: str(path) for name, path in git_files.items()},
            git_parents={name: str(path) for name, path in (
                ("repository", repository), ("holder", repository.parent),
                ("host_runtime", root / "host-runtime"), ("instance", root))})
        targets_path = work / "work/g3-targets.json"
        targets_bytes = json.dumps(targets).encode()
        targets_path.write_bytes(targets_bytes)
        (work / "work/g3-driver.py").write_text(ENGINEERING_DRIVER.replace("__PERMISSION_PROBE__", PERMISSION_PROBE))
        (work / "work/g3-v2-source.txt").write_text(v2)
        frozen_settings = (root / "settings.json").read_bytes()
        report["selection"] = dict(native_launcher=options["launcher"], backend_argv=[sys.executable],
            model=options["model"], reasoning_effort=options["reasoning_effort"], native_options=options["native_options"],
            max_tokens=options["max_tokens"], max_seconds=options["max_seconds"], step=1, role="W")
        # Count native streams against the EXISTING guard's *.log limit.
        # These are local evidence aliases, not copied science or exported auth.
        for name in ("native.jsonl", "native.stderr"):
            # The existing guard calls stat on *.log; keep targets present even
            # before production opens/truncates these ordinary stream files.
            (record / name).touch()
            (logs / ("worker-" + name.replace(".", "-") + ".log")).symlink_to(record / name)
        report["last_phase"] = "actual_native_attempt"
        report["startup"] = dict(native="attempted", worker_tool="not_run", backend="not_run")
        save()
        # Exactly one real model turn; execute_native returns sealed science,
        # not its private host receipt. Read facts from the bound original.
        native.execute_native(root, 1)
        outcome = c.read(c.regular(record / "native-outcome.json"))
        report["worker_started"] = outcome.get("native_started") is True
        report["native_outcome"] = {key: outcome.get(key) for key in
            ("native_started", "native_backend", "returncode", "stop_reason", "cleanup", "native_permissions", "shared_inputs")}
        report["usage"] = {key: outcome.get("usage", {}).get(key) for key in
            ("input_tokens", "cached_input_tokens", "output_tokens", "observed_tokens", "unknown_steps")}
        report["last_phase"] = "host_observation"
        permissions = c.read(record / "native-execution-permissions.json")
        report["effective_permissions"] = {key: permissions.get(key) for key in
            ("policy", "permission_profile", "backend_permission_profile", "unsupported")}
        # Never serialize developer instructions, auth, native identities, homes,
        # session IDs or arbitrary -c values from the complete production argv.
        applied = permissions.get("applied_native_argv", [])
        report["native_argv"] = dict(executable=applied[0] if applied else None,
            command="exec", sandbox=(applied[applied.index("--sandbox") + 1] if "--sandbox" in applied else None),
            model=options["model"], complete_private_argv_record="native-execution-permissions.json",
            scope="allowlisted generated argv facts; original private record remains in fixture")
        driver, driver_error = read_evidence(work / "work/g3-driver-result.json")
        assert driver_error is None, "W did not publish a bounded engineering driver result"
        check("worker_started", report["worker_started"] and driver.get("started") is True)
        check("worker_allowed_writes", driver.get("allowed_write") is True
            and (work / "work/native-positive.txt").read_text() == "legitimate workspace write"
            and (work / "artifacts/native-positive.txt").read_text() == "legitimate artifact write")
        report["worker_git_attempts"] = driver.get("git_attempts")
        check("worker_git_denied", len(driver.get("git_attempts", [])) == 14
            and all(item.get("denied") is True for item in driver["git_attempts"]))
        check("private_git_unchanged", all((_fixture_identity(path), path.read_bytes()) == git_originals[name]
            for name, path in git_files.items()) and targets_path.read_bytes() == targets_bytes)
        report["private_git_after"] = {name: _fixture_identity(path) for name, path in git_files.items()}
        check("external_original_unchanged", (_fixture_identity(external), external.read_bytes()) == external_before
            and (_fixture_identity(objective), objective.read_bytes()) == objective_before)
        report["external_original"] = dict(before=external_before[0], after=_fixture_identity(external), bytes_equal=True)
        check("same_submission_cached", driver.get("cached_original") is True)
        ids = driver.get("calls", [])
        config = c.read(record / "experiment-config.json")
        records = e.Records(config)
        check("exactly_two_original_calls", len(ids) == 2 and len(set(ids)) == 2 and len(records.calls()) == 2)
        rows = []
        for number, call_id in enumerate(ids, 1):
            accepted = records.accepted(call_id)
            directory = records.directory(call_id)
            observation = c.read(directory / "execution-observation.json")
            terminal = c.read(directory / "terminal.json")
            output = next(item for item in observation["scientific_outputs"] if item["name"] == "probe.json")
            probe_path = Path(output["path"])
            probe = c.read(probe_path)
            version = accepted["code_version"]
            context = versions.Deadline(10)
            private = versions._private(root, 1, call_id, create=False, context=context)
            files, _ = versions._tree(private, version, versions._limits(None), context)
            rows.append(dict(call_id=call_id, step=1, code_version=version,
                logical_entry=accepted["source_script"], terminal=terminal,
                original_records={name: _fixture_identity(directory / name) for name in
                    ("accepted.json", "submission.json", "terminal.json", "completed.json", "execution-observation.json")},
                tree={name: dict(mode=mode.decode(), object=oid, bytes=count)
                      for name, (mode, oid, count) in files.items()},
                output_identity=_fixture_identity(probe_path), backend_probe=probe,
                export_absent=not private.export.exists(), stdout_started=("SCID_G3_BACKEND_v" + str(number)).encode()
                    in (directory / "stdout.raw").read_bytes()))
            # Direct read-only production consumers AFTER export removal.
            originals = {name: (directory / name).read_bytes() for name in
                ("accepted.json", "submission.json", "terminal.json", "completed.json", "execution-observation.json")}
            identity = _fixture_identity(probe_path)
            adapter = e.load(config)
            assert adapter.status_details(call_id)["state"] == "succeeded"
            assert adapter.result(call_id)["status"] == "known_success"
            adapter.collect_original(call_id)
            rows[-1]["post_terminal_controls_equal"] = all((directory / name).read_bytes() == raw for name, raw in originals.items())
            rows[-1]["post_terminal_output_equal"] = _fixture_identity(probe_path) == identity
            rows[-1]["backend_group_stopped"] = not runtime.process_matches(c.read(directory / "process.json"),
                c.read(directory / "process.json")["task_ref"])
        report["original_calls"] = rows
        report["backend_started"] = all(row["stdout_started"] and row["backend_probe"].get("backend_started") is True for row in rows)
        report["startup"].update(native="started", worker_tool="completed", backend="started" if report["backend_started"] else "not_reached")
        check("backend_started", report["backend_started"])
        check("backend_git_denied", all(len(row["backend_probe"].get("git_attempts", [])) == 13
            and all(item.get("denied") is True for item in row["backend_probe"]["git_attempts"]) for row in rows))
        check("backend_allowed_writes", all(row["backend_probe"].get("allowed_write") is True for row in rows)
            and all((work / "experiments" / version / "scratch/backend-positive.txt").read_text() == "backend-" + version
                    for version in ("v1", "v2")))
        check("version_v1_pinned", rows[0]["backend_probe"]["version"] == "v1"
            and rows[0]["backend_probe"]["helper_import"] == "v1" and (code / "run.py").read_text() == v2)
        check("version_v2_pinned", rows[1]["backend_probe"]["version"] == "v2"
            and rows[0]["code_version"]["commit"] != rows[1]["code_version"]["commit"])
        check("uncaptured_module_absent_v1", rows[0]["backend_probe"].get("uncaptured_absent") is True
            and "uncaptured.py" not in rows[0]["tree"] and "uncaptured.py" in rows[1]["tree"])
        check("exact_code_trees", set(rows[0]["tree"]) == {"run.py", "helper.py", "helper.sh", "resource.txt"}
            and set(rows[1]["tree"]) == set(rows[0]["tree"]) | {"uncaptured.py"}
            and all(row["tree"]["helper.sh"]["mode"] == "100755" for row in rows))
        check("collected_entity_unchanged", all(row["post_terminal_output_equal"] for row in rows))
        check("post_terminal_reads_preserve_controls", all(row["post_terminal_controls_equal"] for row in rows))
        check("backend_groups_stopped", all(row["backend_group_stopped"] and row["terminal"].get("cleanup_confirmed") is True for row in rows))
        check("terminal_exports_removed", all(row["export_absent"] for row in rows))
        report["product_export_cleanup"] = dict(status="passed", scope="two exact successful Local calls after original consumers ended")
        check("native_group_stopped", outcome.get("cleanup") == "observed_group_cleaned")
        bridge = c.read(record / "bridge-outcome.json")
        check("bridge_group_stopped", bridge.get("cleanup") == "observed_group_cleaned")
        check("frozen_settings_unchanged", (root / "settings.json").read_bytes() == frozen_settings)
        report["assertions_status"] = "complete"
        report["last_phase"] = "engineering_observation_complete"
    except Exception as exc:
        error = exc
        # Exception details can contain private launch/auth context. Keep type
        # and assertion label only; exact originals stay in the private fixture.
        report["error"] = dict(type=type(exc).__name__, check=str(exc) if isinstance(exc, AssertionError)
            and str(exc) in WORKER_CHECKS else "see original local fixture logs")
    finally:
        if record is not None and (record / "native-outcome.json").is_file():
            from scidiscovery.research_loop import contracts as c
            outcome = c.read(record / "native-outcome.json")
            report["worker_started"] = outcome.get("native_started") is True
            report.setdefault("native_outcome", {key: outcome.get(key) for key in
                ("native_started", "returncode", "stop_reason", "cleanup", "native_permissions")})
            permissions_path = record / "native-execution-permissions.json"
            if permissions_path.is_file():
                original_permissions = c.read(permissions_path)
                if original_permissions.get("unsupported"):
                    report["unsupported"] = original_permissions["unsupported"]
        # A native/output failure can follow a real backend effect. Observe
        # those exact fixture originals without query/reconcile/replay/cleanup.
        # Do not infer "not_run" merely because the W turn failed to finish.
        if work is not None:
            driver, _ = read_evidence(work / "work/g3-driver-result.json")
            if isinstance(driver, dict):
                report.setdefault("worker_git_attempts", driver.get("git_attempts", []))
                if any(item.get("denied") is False for item in driver.get("git_attempts", [])):
                    report["checks"]["worker_git_denied"] = "failed"
        if record is not None and not report["original_calls"] and (record / "experiment-config.json").is_file():
            try:
                from scidiscovery.research_loop import experiments as e
                config = c.read(record / "experiment-config.json")
                records = e.Records(config)
                calls = records.calls()
                report["observed_call_count"] = len(calls)
                for directory in calls[:3]:
                    call_id = directory.name
                    accepted = records.accepted(call_id)
                    path = root / "host-runtime/code-versions/exports/001" / call_id / "tree"
                    stream = directory / "stdout.raw"
                    started = False
                    if stream.is_file():
                        with stream.open("rb") as source:
                            started = b"SCID_G3_BACKEND_" in source.read(STREAM_LIMIT)
                    row = dict(call_id=call_id, step=1, code_version=accepted.get("code_version"),
                        logical_entry=accepted.get("source_script"), export_present=path.exists(),
                        backend_started=started, backend_attempted=(directory / "process.json").exists(),
                        scope="failure-path observations only; terminal/assertion/cleanup success not inferred")
                    terminal_path = directory / "terminal.json"
                    if terminal_path.is_file():
                        row["terminal"] = c.read(terminal_path)
                    report["original_calls"].append(row)
                report["backend_started"] = any(row.get("backend_started") is True for row in report["original_calls"])
                report["startup"] = dict(native="started" if report["worker_started"] else "not_reached",
                    worker_tool="attempted" if calls else "not_reached",
                    backend="started" if report["backend_started"] else
                        "attempted" if any(row.get("backend_attempted") for row in report["original_calls"]) else "not_reached")
            except (OSError, ValueError, KeyError, TypeError) as observation_error:
                report["evidence_errors"] = [dict(phase="failure_observation", type=type(observation_error).__name__)]
        report["fixture"] = dict(status="retained", reason="local engineering originals; unknown is never deleted or resubmitted",
            path=str(logs / "worker-managed-fixture"))
        save()
    if error is not None:
        # Do not let pytest echo arbitrary native exceptions (potential private
        # paths/auth); the report and original streams retain the local facts.
        raise AssertionError("Real W/managed Local engineering probe incomplete; see worker-managed.json and fixture logs") from None
    assert all(report["checks"][name] == "passed" for name in WORKER_CHECKS)
