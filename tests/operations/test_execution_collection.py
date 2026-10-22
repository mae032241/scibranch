"""Regression boundaries for observation, resumable collection and diagnostics."""
import hashlib
import subprocess
import pytest
from types import SimpleNamespace

from scidiscovery.plugin_runtime.observation import read_summary
from tcad_artifact.ssh_transport import SSHTCADTransport


def test_completed_download_is_reused(tmp_path, monkeypatch):
    raw = b"solver data\n"
    calls = []
    def get_to(path, destination, max_bytes):
        calls.append(path)
        assert len(raw) <= max_bytes
        destination.write_bytes(raw)
    remote = SimpleNamespace(get_to=get_to)
    transport = SSHTCADTransport(remote, local_result_root=tmp_path)
    monkeypatch.setattr(transport, "_rpc", lambda *args: {"outputs": [{
        "name": "profile", "local_path": "/remote/profile.dat", "media_type": "text/plain",
        "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}]})
    first = transport._collect("run_" + "a" * 32)
    assert transport._collect("run_" + "a" * 32) == first
    assert calls == ["/remote/profile.dat"]


def test_observation_missing_and_corrupt_are_distinct(tmp_path):
    missing = read_summary(tmp_path)
    directory = tmp_path / "scratch/.analysis-process"
    directory.mkdir(parents=True)
    (directory / "latest.json").write_text("invalid json")
    invalid = read_summary(tmp_path)
    assert missing["coverage"] == invalid["coverage"] == "unobserved"
    assert missing["reason"] != invalid["reason"]


@pytest.mark.process_e2e
def test_transport_differentiates_no_progress_and_total_deadline():
    import sys
    import pytest
    from scidiscovery.plugin_runtime.collection import run_bounded
    for idle, expected in ((.05, "transfer_no_progress"), (None, "operation_total")):
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            run_bounded([sys.executable, "-c", "import time; time.sleep(30)"],
                input=b"", timeout=.15, idle_seconds=idle)
        assert caught.value.timeout_kind == expected


@pytest.mark.process_e2e
def test_common_launcher_preserves_environment_and_full_stdout(tmp_path, monkeypatch):
    import sys
    from scidiscovery.plugin_runtime.observation import materialize_launcher
    materialize_launcher(tmp_path)
    import json
    from datetime import datetime, timedelta, timezone
    (tmp_path/'assignment.json').write_text(json.dumps({'budget':{'deadline_at':
        (datetime.now(timezone.utc)+timedelta(seconds=10)).isoformat()}}))
    monkeypatch.setenv("OMP_NUM_THREADS", "3")
    result = subprocess.run([sys.executable, str(tmp_path / "tools/local_process_observation.py"),
        "--timeout", "4", "--command", sys.executable, "-c",
        "import os; print(os.environ['OMP_NUM_THREADS']); print('x'*140000)"], capture_output=True, timeout=6)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(b"3\n") and len(result.stdout) > 140000
    assert read_summary(tmp_path)["coverage"] == "local_launcher"


def test_partial_manifest_resumes_43_files_without_cross_execution_cache(tmp_path, monkeypatch):
    import pytest
    from scidiscovery.plugin_runtime.collection import CollectionContext
    outputs = [{"name": f"profile_{i}", "local_path": f"/remote/p{i}", "media_type": "text/plain",
        "size_bytes": 4, "sha256": hashlib.sha256(b"data").hexdigest()} for i in range(43)]
    calls = []
    def get_to(path, destination, max_bytes):
        assert max_bytes == 4
        calls.append(path)
        if len(calls) == 18:
            raise TimeoutError("transfer stopped at file 18")
        destination.write_bytes(b"data")
    transport = SSHTCADTransport(SimpleNamespace(get_to=get_to), local_result_root=tmp_path)
    monkeypatch.setattr(transport, "_rpc", lambda *args: {"outputs": outputs})
    run = "run_" + "a" * 32
    with pytest.raises(TimeoutError):
        transport._collect(run, context=CollectionContext.for_seconds(10))
    result = transport._collect(run, context=CollectionContext.for_seconds(10))
    assert len(result['outputs']) == 43 and len(calls) == 44
    # Same-sized corrupted file must be fetched again; the other 42 are reused.
    from pathlib import Path
    Path(result['outputs'][0]['local_path']).write_bytes(b"oops")
    transport._collect(run, context=CollectionContext.for_seconds(10))
    assert len(calls) == 45
    transport._collect("run_" + "b" * 32, context=CollectionContext.for_seconds(10))
    assert len(calls) == 88


def test_nested_transport_cause_survives_multiple_wrappers():
    from scidiscovery.plugin_runtime.diagnostics import exception_facts
    transport = RuntimeError('remote command failed')
    transport.engineering = {'layer':'ssh_transfer','causes':[{'type':'TimeoutExpired','message':'file stalled',
        'timeout':30,'timeout_kind':'transfer_no_progress'}]}
    outer = RuntimeError('debug collection unavailable')
    outer.__cause__ = transport
    facts = exception_facts(outer,layer='worker',action='debug')
    assert facts['category'] == 'timeout' and facts['origin_layer'] == 'ssh_transfer'
    assert any(item.get('timeout_kind') == 'transfer_no_progress' for item in facts['causes'])
