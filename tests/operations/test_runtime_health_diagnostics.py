import json
from types import SimpleNamespace

import pytest

from scidiscovery.operation_contract import DiagnosticError, contract_diagnostic
from scidiscovery.plugin_runtime.diagnostics import exception_facts
from scidiscovery.plugin_runtime.transport import forward_request


def test_configuration_repair_is_safe_and_distinct_from_scientific_rejection():
    error = DiagnosticError("token=secret /private/config.json", details=(contract_diagnostic(
        "tcad_transport_configuration_invalid", phase="tool_execution", affected_action="tool_call"),))
    error.failure_stage = "configuration"
    facts = exception_facts(error, layer="tool", action="execute")
    assert facts["recovery"]["stage"] == "configuration"
    assert facts["recovery"]["responsibility"] == "administrator"
    assert "secret" not in json.dumps(facts) and "/private" not in json.dumps(facts)
    rejection = ValueError("denied")
    rejection.failure_stage = "pre_execution_rejected"
    assert exception_facts(rejection, layer="execution", action="start")["recovery"]["stage"] == "pre_execution_rejected"


def test_failed_connection_is_known_unsent_but_send_timeout_is_not(monkeypatch):
    from scidiscovery.plugin_runtime import transport
    class Socket:
        phase = "connect"
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def settimeout(self, seconds): pass
        def connect(self, path):
            if self.phase == "connect": raise ConnectionRefusedError("refused")
        def sendall(self, raw): raise TimeoutError("partial send possible")
    socket = Socket()
    monkeypatch.setattr(transport.socket, "socket", lambda *args: socket)
    from pathlib import Path
    for phase in ("connect", "send"):
        socket.phase = phase
        with pytest.raises(OSError) as captured:
            forward_request(Path("/private/socket"), b'{"method":"tools/call"}', timeout=1)
        facts = exception_facts(captured.value, layer="transport", action="forward")
        if phase == "connect":
            assert facts["recovery"]["stage"] == "connection"
        else:
            assert "recovery" not in facts  # Generic transport cannot infer an execution outcome.


@pytest.mark.parametrize("operation", ["capabilities", "execution_policy", "status"])
def test_readonly_command_probe_uses_one_configured_total_query_budget(tmp_path, monkeypatch, operation):
    import sys
    from tcad_artifact import command_adapter
    from tcad_artifact.ssh_transport import SSHTCADTransport
    calls = []
    payload = {"run_id": "existing-job"} if operation == "status" else {}
    class Remote:
        def rpc(self, request):
            assert request["params"] == {"name": "tcad_" + operation, "arguments": payload}
            value = {"capabilities": [{"profile_id": "fixture", "solver_kind": "sdevice",
                "launch_name": "sdevice", "public_release_label": "Fixture",
                "private_fixed_argument_count": 0, "private_fixed_arguments_sha256": "a" * 64,
                "private_release_evidence_bytes": 1, "private_release_evidence_sha256": "b" * 64,
                "capability_sha256": "c" * 64}]} if operation == "capabilities" else {"state": "succeeded"}
            return {"id": request["id"], "result": {"structuredContent": value}}
    transport = SSHTCADTransport(Remote(), local_result_root=tmp_path / "ssh-results")
    def run(argv, **kwargs):
        request = json.loads(kwargs["input"])
        assert request == {"schema_version": 2, "operation": operation, "payload": payload}
        calls.append((request["operation"], kwargs["timeout"]))
        return SimpleNamespace(returncode=0, stderr=b"", stdout=json.dumps({
            "schema_version": 2, "operation": operation, "ok": True,
            "payload": transport.handle(request["operation"], request["payload"])}).encode())
    monkeypatch.setattr(command_adapter, "run_bounded", run)
    adapter = command_adapter.CommandTCADExecutorAdapter(command_adapter.CommandAdapterConfig(
        executable=sys.executable, query_timeout_seconds=.25, operation_timeout_seconds=30),
        local_result_root=tmp_path)
    adapter._call(operation, payload)
    assert len(calls) == 1 and calls[0][0] == operation
    assert 0 < calls[0][1] <= .25


@pytest.mark.process_e2e
def test_command_capabilities_reaches_ssh_handler_with_empty_payload(tmp_path):
    import os
    import sys
    from tcad_artifact.command_adapter import CommandAdapterConfig, CommandTCADExecutorAdapter
    script = tmp_path / "transport.py"
    script.write_text('''import json, sys
from pathlib import Path
from tcad_artifact.ssh_transport import SSHTCADTransport
class Remote:
    def rpc(self, request):
        assert request['params'] == {'name':'tcad_capabilities', 'arguments':{}}
        capability = dict(profile_id='fixture', solver_kind='sdevice', launch_name='sdevice',
            public_release_label='Fixture', private_fixed_argument_count=0,
            private_fixed_arguments_sha256='a'*64, private_release_evidence_bytes=1,
            private_release_evidence_sha256='b'*64, capability_sha256='c'*64)
        return {'id':request['id'], 'result':{'structuredContent':{'capabilities':[capability]}}}
request = json.load(sys.stdin)
assert request == {'schema_version':2, 'operation':'capabilities', 'payload':{}}
transport = SSHTCADTransport(Remote(), local_result_root=Path(__file__).parent/'remote-results')
value = transport.handle(request['operation'], request['payload'])
print(json.dumps({'schema_version':2, 'operation':request['operation'], 'ok':True, 'payload':value}))
''')
    adapter = CommandTCADExecutorAdapter(CommandAdapterConfig(executable=sys.executable,
        arguments=(str(script),), environment={"PYTHONPATH": os.pathsep.join(sys.path)},
        query_timeout_seconds=5.), local_result_root=tmp_path / "results")
    assert [capability.key for capability in adapter.capabilities()] == ["fixture"]


@pytest.mark.process_e2e
@pytest.mark.parametrize("operation", ["capabilities", "execution_policy", "status"])
def test_query_total_timeout_stops_command_and_its_child(tmp_path, operation):
    import os
    from pathlib import Path
    import signal
    import subprocess
    import sys
    import time
    from tcad_artifact.command_adapter import CommandAdapterConfig, CommandTCADExecutorAdapter
    script = '''import json, os, sys, time
request = json.load(sys.stdin)
assert request['payload'] == ({'run_id':'existing-job'} if request['operation']=='status' else {})
child = os.fork()
if child == 0:
    time.sleep(30)
else:
    print(json.dumps([os.getpid(), child]), flush=True)
    time.sleep(30)
'''
    adapter = CommandTCADExecutorAdapter(CommandAdapterConfig(executable=sys.executable,
        arguments=("-c", script), query_timeout_seconds=.5, operation_timeout_seconds=30),
        local_result_root=tmp_path)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="short bound") as caught:
        adapter._call(operation, {"run_id": "existing-job"} if operation == "status" else {})
    cause = caught.value.__cause__
    assert isinstance(cause, subprocess.TimeoutExpired)
    assert 0 < cause.timeout <= .5
    assert time.monotonic() - started < 3
    pids = json.loads(cause.stdout)
    try:
        for pid in pids:
            stat = Path(f"/proc/{pid}/stat")
            assert not stat.exists() or stat.read_text().split()[2] == "Z"
    finally:
        # Cleanup remains explicit if a regression leaves a descendant running.
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
