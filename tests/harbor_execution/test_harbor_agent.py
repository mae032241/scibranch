"""Host shim tests. SDK stubs are mechanical evidence, not Harbor qualification."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import types

import pytest


PLUGIN = Path(__file__).resolve().parents[2] / "plugins" / "harbor_execution"
ENTRY = PLUGIN / "harbor_execution" / "harbor_agent.py"
TEMPLATE = PLUGIN / "harbor_execution" / "examples" / "symbolic-regression.research.toml"


@pytest.fixture
def entry(monkeypatch):
    # The ordinary provider test environment deliberately needs no Harbor SDK.
    for name in ("harbor", "harbor.agents", "harbor.agents.base", "harbor.environments",
                 "harbor.environments.docker", "harbor.environments.docker.docker"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    class BaseAgent:
        def __init__(self, logs_dir, model_name=None, **kwargs):
            self.logs_dir, self.model_name = logs_dir, model_name
            self.extra_env = dict(kwargs.get("extra_env") or {})
    sys.modules["harbor.agents.base"].BaseAgent = BaseAgent
    sys.modules["harbor.environments.docker.docker"].DockerEnvironment = type("DockerEnvironment", (), {})
    spec = importlib.util.spec_from_file_location("_harbor_agent_mechanical_test", ENTRY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _confirmed(tmp_path, *, operation="1" * 64, content=b"def predict(x): return x\n"):
    target = tmp_path / operation
    target.mkdir()
    artifact = target / "artifact"
    artifact.write_bytes(content)
    reference = {"path": "/exact/retained/regressor.py", "size_bytes": len(content)}
    confirmed = dict(operation_id=operation, candidate_id="candidate-exact", artifact_ref=reference,
        status="confirmed", cleanup_confirmed=True)
    manifest = {key: confirmed[key] for key in ("operation_id", "candidate_id", "artifact_ref")}
    manifest["objects"] = {"artifact": reference}
    (target / "manifest.json").write_text(json.dumps(manifest))
    return {"closeout": {"status": "delivery_confirmed", "last_confirmed": confirmed}}, artifact


def test_usage_uses_existing_cumulative_input_including_cache(entry):
    context = types.SimpleNamespace()
    entry.publish_usage({"usage": {"input_tokens": 200, "cached_input_tokens": 150,
        "output_tokens": 25, "observed_tokens": 225}}, context)
    assert (context.n_input_tokens, context.n_cache_tokens, context.n_output_tokens) == (200, 150, 25)


def test_final_selection_requires_exact_confirmation_and_manifest(entry, tmp_path):
    status, artifact = _confirmed(tmp_path)
    _confirmed(tmp_path, operation="2" * 64, content=b"newer-but-unselected")
    assert entry.confirmed_artifact(status, tmp_path) == artifact
    status["closeout"]["last_confirmed"]["candidate_id"] = "different"
    with pytest.raises(ValueError, match="differs"):
        entry.confirmed_artifact(status, tmp_path)


def test_unconfirmed_cleanup_never_exports(entry, tmp_path):
    status, _ = _confirmed(tmp_path)
    status["closeout"]["last_confirmed"]["cleanup_confirmed"] = False
    with pytest.raises(ValueError):
        entry.confirmed_artifact(status, tmp_path)


def test_template_keeps_frozen_first_trial_profile_and_budget(entry, tmp_path):
    bindings = dict(INSTANCE_ROOT=str(tmp_path / "instance"), OBJECTIVE=str(tmp_path / "objective"),
        PUBLIC_ROOT=str(tmp_path / "public"), NAVIGATION=str(tmp_path / "navigation"),
        DELIVERY_ADAPTER={"target": {"directory": str(tmp_path / "delivery")}},
        CONTAINER_ID="a" * 64, CONTAINER_ROOT="/tmp/scid-exact", DOCKER_ARGV=["docker"],
        CONTAINER_ENV={}, CONTAINER_USER="0", DEADLINE_EPOCH=123456.)
    _, parsed = entry.render_configuration(TEMPLATE.read_text(), bindings,
        max_seconds=1800, model_name="gpt-6.1-sol")
    assert parsed["budget"] == {"max_tokens": 1000000, "max_seconds": 1800, "max_steps": 80}
    assert parsed["agent"] == dict(backend="codex", launcher=["codex"], model="gpt-6.1-sol",
        reasoning_effort="high", service_tier="default")
    assert parsed["executor"]["config"]["executor"] == "harbor_execution:experiment"
    assert parsed["executor"].get("mode") != "external"
    assert "breadth" not in parsed["budget"]
    local_template = TEMPLATE.read_text().replace("[executor.config]",
        '[executor]\nmode = "local"\n\n[executor.config]', 1)
    entry.render_configuration(local_template, bindings, max_seconds=1800, model_name="gpt-6.1-sol")
    with pytest.raises(ValueError, match="registered harbor_execution:experiment provider"):
        entry.render_configuration(local_template.replace('mode = "local"', 'mode = "external"'),
            bindings, max_seconds=1800, model_name="gpt-6.1-sol")
    with pytest.raises(ValueError, match="same max_seconds"):
        entry.render_configuration(TEMPLATE.read_text(), bindings, max_seconds=5, model_name=None)


@pytest.mark.parametrize("native_user", ["", "root:1000"])
def test_binding_uses_only_exact_main_query_and_canonical_id(entry, monkeypatch, tmp_path, native_user):
    calls = []
    full = "a" * 64
    class Environment:
        session_id, context_id = "Exact_Trial", "context-id"
        @classmethod
        def runtime(cls):
            return types.SimpleNamespace(engine=("docker",))
        async def _run_docker_compose_command(self, command, **kwargs):
            calls.append(command)
            return types.SimpleNamespace(stdout=full[:12])
        def _merge_env(self, _value):
            return {"MODEL_API_KEY": "test-secret"}
        def _resolve_user(self, _value):
            return None
        async def exec(self, command, **kwargs):
            raise AssertionError("public exec carries the agent inference overlay")
    environment = Environment()
    monkeypatch.setattr(entry, "DockerEnvironment", Environment)
    monkeypatch.setattr(entry, "version", lambda name: "0.25.0")
    async def command(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "inspect":
            return json.dumps([{"Id": full, "State": {"Running": True},
                "Config": {"User": native_user, "Labels": {"com.docker.compose.service": "main",
                "com.docker.compose.project": "exact_trial"}}, "HostConfig": {"Memory": 2048}}])
        if argv[1] == "exec":
            assert argv[:8] == ["docker", "exec", "--user", native_user or "0", full,
                "/usr/local/bin/python", "-I", "-c"]
            return "scid-original-environment-ok\n"
        raise AssertionError("binding must not issue another Docker command")
    monkeypatch.setattr(entry, "_command", command)
    result = asyncio.run(entry.bind_main_container(environment, tmp_path))
    assert calls[0] == ["ps", "--all", "--quiet", "main"]
    assert result["container_id"] == full
    assert result["environment"] == {}
    assert "test-secret" not in (tmp_path / "binding.json").read_text()
    minimal = ('[budget]\nmax_seconds=1800\n'
        '[executor.config]\nexecutor="harbor_execution:experiment"\n'
        '[executor.config.configuration]\nenvironment=__SCID_CONTAINER_ENV__\n')
    rendered, _ = entry.render_configuration(minimal,
        {"CONTAINER_ENV": result["environment"]}, max_seconds=1800, model_name=None)
    assert "test-secret" not in rendered
    assert result["user"] == (native_user or "0")
    assert len(calls) == 3


def test_binding_rejects_ambiguous_main_before_inspection(entry, monkeypatch, tmp_path):
    class Environment:
        @classmethod
        def runtime(cls):
            return types.SimpleNamespace(engine=("docker",))
        async def _run_docker_compose_command(self, *args, **kwargs):
            return types.SimpleNamespace(stdout="a" * 64 + "\n" + "b" * 64)
    monkeypatch.setattr(entry, "DockerEnvironment", Environment)
    monkeypatch.setattr(entry, "version", lambda name: "0.25.0")
    with pytest.raises(ValueError, match="one Docker identity"):
        asyncio.run(entry.bind_main_container(Environment(), tmp_path))


# This fake framework performs no scientific computation and calls no model.
FAKE_CLI = r'''
import json, os, pathlib, subprocess, sys, time, tomllib
verb = sys.argv[1]
if verb in ('config-check', 'prepare'):
    config = tomllib.loads(pathlib.Path(sys.argv[3]).read_text())
    if verb == 'prepare':
        root = pathlib.Path(config['research']['root']); root.mkdir()
        (root/'fake-config.json').write_text(json.dumps(config))
    raise SystemExit(0)
root = pathlib.Path(sys.argv[3]); config=json.loads((root/'fake-config.json').read_text())
if verb == 'status':
    path=root/'status.json'
    print(path.read_text() if path.exists() else json.dumps({'usage': {'input_tokens': 20, 'cached_input_tokens': 10, 'output_tokens': 5}}))
    raise SystemExit(0)
if verb == 'run':
    (root/'host-env-check.json').write_text(json.dumps({'sentinel': os.environ.get('SCID_HOST_TEST'), 'home': os.environ.get('HOME'), 'codex_home': os.environ.get('CODEX_HOME')}))
    if config['ui'].get('open_browser'):
        child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'], start_new_session=True)
        (root/'native-child.pid').write_text(str(child.pid))
        time.sleep(30)
    else:
        time.sleep(.1)
        operation='1'*64
        target=pathlib.Path(config['delivery']['adapter']['target']['directory'])/operation
        target.mkdir(); payload=b'def predict(x): return x\n'; (target/'artifact').write_bytes(payload)
        ref={'path': '/exact/retained/regressor.py', 'size_bytes': len(payload)}
        confirmed={'operation_id': operation, 'candidate_id': 'exact', 'artifact_ref': ref, 'status': 'confirmed', 'cleanup_confirmed': True}
        manifest={key: confirmed[key] for key in ('operation_id', 'candidate_id', 'artifact_ref')}
        manifest['objects']={'artifact': ref}; (target/'manifest.json').write_text(json.dumps(manifest))
        (root/'status.json').write_text(json.dumps({'usage': {'input_tokens': 100, 'cached_input_tokens': 60, 'output_tokens': 25}, 'closeout': {'status': 'delivery_confirmed', 'last_confirmed': confirmed}}))
'''


def _fake_agent(entry, monkeypatch, tmp_path, *, deadline=False, cancel=False, extra_env=None):
    import scidiscovery.research_loop.generic_delivery as generic_delivery
    monkeypatch.setattr(generic_delivery, "descriptor", lambda path: {"target": {"directory": str(path)}})
    template = tmp_path / "template.toml"
    seconds = .8 if deadline else 30 if cancel else 5
    text = TEMPLATE.read_text().replace("max_seconds = 1800", "max_seconds = " + str(seconds))
    if deadline or cancel:
        text = text.replace("open_browser = false", "open_browser = true")
    template.write_text(text)
    cli = tmp_path / "fake_cli.py"
    cli.write_text(FAKE_CLI)
    agent = entry.SciDiscoveryHarborAgent(logs_dir=tmp_path / "logs", config_template=template,
        scid_argv=[sys.executable, str(cli)], max_seconds=seconds,
        poll_seconds=.05, model_name="gpt-6.1-sol", extra_env=extra_env)
    agent.binding = dict(container_id="a" * 64, docker_argv=["docker"], environment={}, user="0")
    events = []
    async def stopped():
        events.append("container-stopped")
    monkeypatch.setattr(agent, "_stop_container", stopped)
    class Environment:
        async def download_file(self, source, destination):
            assert source == "/app/regressor.py"
            Path(destination).write_bytes(b"public-starter")
        async def upload_file(self, source, destination):
            assert destination == "/app/regressor.py"
            events.append(Path(source).read_bytes())
    return agent, Environment(), events


@pytest.mark.process_e2e
@pytest.mark.skipif(sys.platform != "linux", reason="Linux subreaper contract")
def test_foreground_fake_framework_usage_and_exact_export(entry, monkeypatch, tmp_path):
    agent, environment, events = _fake_agent(entry, monkeypatch, tmp_path,
        extra_env={"SCID_HOST_TEST": "configured-value"})
    context = types.SimpleNamespace()
    instruction = "original task instruction\nexact bytes"
    asyncio.run(agent.run(instruction, environment, context))
    assert (agent.private_root / "objective.md").read_text() == instruction
    assert events == [b"def predict(x): return x\n"]
    assert (context.n_input_tokens, context.n_cache_tokens, context.n_output_tokens) == (100, 60, 25)
    assert json.loads((agent.private_root / "instance" / "host-env-check.json").read_text()) == {
        "sentinel": "configured-value", "home": os.environ.get("HOME"),
        "codex_home": os.environ.get("CODEX_HOME")}
    assert json.loads((agent.private_root / "host-terminal.json").read_text())["cleanup_confirmed"] is True


@pytest.mark.process_e2e
@pytest.mark.skipif(sys.platform != "linux", reason="Linux subreaper contract")
def test_nonzero_host_exit_reports_original_failure_with_confirmed_cleanup(entry, monkeypatch, tmp_path):
    agent, environment, events = _fake_agent(entry, monkeypatch, tmp_path)
    Path(agent.scid_argv[-1]).write_text(FAKE_CLI.replace("if verb == 'run':", """if verb == 'run':
    def finaldelivery():
        raise KeyError('command')
    finaldelivery()"""))
    with pytest.raises(RuntimeError, match="foreground host work failed") as error:
        asyncio.run(agent.run("exact objective", environment, types.SimpleNamespace()))
    message = str(error.value)
    run_log = agent.private_root / "run.log"
    receipt_path = agent.private_root / "host-terminal.json"
    assert "reason exited, exit code 1" in message
    assert str(run_log) in message
    assert str(receipt_path) in message
    assert "did not finish with confirmed cleanup" not in message
    assert "KeyError" not in message
    assert events == ["container-stopped"]
    receipt = json.loads(receipt_path.read_text())
    assert receipt["reason"] == "exited"
    assert receipt["returncode"] == 1
    assert receipt["cleanup_confirmed"] is True
    original_error = run_log.read_text()
    assert "Traceback (most recent call last):" in original_error
    assert "in finaldelivery" in original_error
    assert "KeyError: 'command'" in original_error


@pytest.mark.process_e2e
@pytest.mark.skipif(sys.platform != "linux", reason="Linux subreaper contract")
def test_deadline_stops_foreground_native_session_and_container(entry, monkeypatch, tmp_path):
    agent, environment, events = _fake_agent(entry, monkeypatch, tmp_path, deadline=True)
    with pytest.raises((TimeoutError, RuntimeError)):
        asyncio.run(agent.run("exact objective", environment, types.SimpleNamespace()))
    assert events == ["container-stopped"]
    receipt = json.loads((agent.private_root / "host-terminal.json").read_text())
    assert receipt["cleanup_confirmed"] is True
    pid = int((agent.private_root / "instance" / "native-child.pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.process_e2e
@pytest.mark.skipif(sys.platform != "linux", reason="Linux subreaper contract")
def test_outer_cancellation_waits_for_native_and_container_cleanup(entry, monkeypatch, tmp_path):
    agent, environment, events = _fake_agent(entry, monkeypatch, tmp_path, cancel=True)
    marker = agent.private_root / "instance" / "native-child.pid"
    async def cancel_active():
        task = asyncio.create_task(agent.run("exact objective", environment, types.SimpleNamespace()))
        try:
            for _ in range(100):
                if marker.exists():
                    break
                await asyncio.sleep(.05)
            assert marker.exists(), "fake native child did not start"
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
    asyncio.run(cancel_active())
    assert events == ["container-stopped"]
    assert json.loads((agent.private_root / "host-terminal.json").read_text())["cleanup_confirmed"] is True
    with pytest.raises(ProcessLookupError):
        os.kill(int(marker.read_text()), 0)


@pytest.mark.parametrize("key", ["HOME", "CODEX_HOME"])
def test_host_environment_rejects_native_auth_home_changes(entry, tmp_path, key):
    with pytest.raises(ValueError, match="original host HOME and CODEX_HOME"):
        entry.SciDiscoveryHarborAgent(logs_dir=tmp_path / "logs", config_template=TEMPLATE,
            extra_env={key: "/different/native/home"})


def _technical_status(root, *, attempt=0):
    stopped=dict(phase='native_prelaunch',stage='technical_pause',pending_step=1,
        pending_role='H',reason='native_metadata_unsupported',repair_scope='worker_attempt',
        recovery_available=True,recheck_required=True,expected_attempt=attempt,
        supported_actions=['worker-resume'])
    return dict(instance=str(root.resolve()),goal_id='same-research',technical_stop=stopped,
        nodes=[dict(step=1,role='H',state='native_not_submitted',technical_stop=stopped)],
        closeout=None,usage=dict(input_tokens=0,cached_input_tokens=0,output_tokens=0))


@pytest.mark.parametrize('change',[
    {'returncode':1},{'reason':'deadline'},{'cleanup_confirmed':False},
])
def test_only_clean_technical_cli_exit_can_hold_original_container(entry,tmp_path,change):
    terminal=dict(reason='exited',returncode=2,cleanup_confirmed=True)
    status=_technical_status(tmp_path)
    assert entry.technical_pause_hold(status,terminal,instance=str(tmp_path.resolve()))
    terminal.update(change)
    assert not entry.technical_pause_hold(status,terminal,instance=str(tmp_path.resolve()))


@pytest.mark.parametrize('change',[
    {'recovery_available':False},{'phase':'native_running'},
    {'supported_actions':[]},{'reason':'native_error_unknown'},
])
def test_unknown_or_unavailable_original_never_enters_technical_hold(entry,tmp_path,change):
    status=_technical_status(tmp_path)
    status['technical_stop'].update(change)
    assert not entry.technical_pause_hold(status,dict(reason='exited',returncode=2,
        cleanup_confirmed=True),instance=str(tmp_path.resolve()))


def _explicit_recovery(root, *, sequence=1, state='applied', report=None, expected=0):
    operation_id=str(sequence)*32
    return dict(operation_id=str(sequence)*32,sequence=sequence,kind='controller',status=state,
        action=dict(kind='worker_resume',step=1,expected_attempt=expected,
            job_ref=dict(path=str(root/'jobs/001.json'))),
        process=dict(task_ref=operation_id,boot_id='same-boot',pid=2_000_000_000,
            start_ticks=1,pgid=2_000_000_000,sid=2_000_000_000,ppid=1),result=report)


def test_technical_hold_observes_explicit_repair_and_existing_controller_completion(entry,monkeypatch,tmp_path):
    agent=entry.SciDiscoveryHarborAgent(logs_dir=tmp_path/'logs',config_template=TEMPLATE,
        max_seconds=5,poll_seconds=.05)
    root=agent.private_root/'instance';root.mkdir()
    initial=_technical_status(root)
    running=dict(initial,technical_stop=None,nodes=[dict(step=1,role='H',state='running')])
    finished=dict(initial,technical_stop=None,nodes=[dict(step=1,role='A',state='seal_present_unverified')],
        closeout=dict(status='delivery_confirmed'))
    statuses=iter([initial,running,finished])
    observations=iter([[],[_explicit_recovery(root,state='running')],
        [_explicit_recovery(root,report=dict(status='delivery_confirmed'))]])
    async def status(*args,**kwargs):return next(statuses)
    async def operations(*args,**kwargs):return next(observations)
    monkeypatch.setattr(agent,'_status',status)
    monkeypatch.setattr(agent,'_operations',operations)
    monkeypatch.setattr(entry,'_command',lambda *args,**kwargs:pytest.fail('hold submitted a command'))
    from scidiscovery.research_loop import runtime
    monkeypatch.setattr(runtime,'process_matches',lambda *args:False)
    result,report=asyncio.run(agent._wait_technical_recovery(root,types.SimpleNamespace(),
        deadline=time.time()+5,monotonic_deadline=time.monotonic()+5,initial_status=initial))
    assert result is finished and report['status']=='delivery_confirmed'
    assert agent.binding is None  # Observation never rebinds or starts an environment.


def test_technical_hold_retains_original_deadline_without_an_explicit_repair(entry,monkeypatch,tmp_path):
    agent=entry.SciDiscoveryHarborAgent(logs_dir=tmp_path/'logs',config_template=TEMPLATE,
        max_seconds=5,poll_seconds=.05)
    root=agent.private_root/'instance';root.mkdir()
    initial=_technical_status(root)
    async def status(*args,**kwargs):return initial
    async def operations(*args,**kwargs):return []
    monkeypatch.setattr(agent,'_status',status);monkeypatch.setattr(agent,'_operations',operations)
    with pytest.raises(TimeoutError,match='original Harbor trial deadline'):
        asyncio.run(agent._wait_technical_recovery(root,types.SimpleNamespace(),
            deadline=time.time()+5,monotonic_deadline=time.monotonic()+.02,initial_status=initial))


def test_recovery_operation_must_name_exact_failed_attempt(entry,tmp_path):
    stopped=_technical_status(tmp_path)['technical_stop']
    original=_explicit_recovery(tmp_path)
    assert entry._recovery_action(original,stopped,instance=str(tmp_path))
    assert not entry._recovery_action(_explicit_recovery(tmp_path,expected=1),stopped,instance=str(tmp_path))
    changed=dict(original,action=dict(original['action'],job_ref=dict(path='/different/jobs/001.json')))
    assert not entry._recovery_action(changed,stopped,instance=str(tmp_path))


def _native_failure_status(root,*,attempt=2):
    status=_technical_status(root,attempt=attempt)
    record=root/'native-evidence/001'
    if attempt:record=record/'continuations'/f'{attempt:03d}'
    status['technical_stop'].update(phase='native_failure',reason='automatic_recovery_attempt_limit',
        native_intent_ref=dict(path=str(record/'native-intent.json')),after_interrupt=True,new_context=False)
    status['nodes'][-1]['state']='native_outcome_unsealed'
    return status


@pytest.mark.parametrize('change',[None,'attempt','reference','node','cleanup'])
def test_native_failure_hold_requires_exact_clean_original(entry,tmp_path,change):
    status=_native_failure_status(tmp_path)
    terminal=dict(reason='exited',returncode=2,cleanup_confirmed=True)
    if change=='attempt':status['technical_stop']['expected_attempt']=1
    if change=='reference':status['technical_stop']['native_intent_ref']={'path':'/foreign/native-intent.json'}
    if change=='node':status['nodes'][-1]['step']=2
    if change=='cleanup':terminal['cleanup_confirmed']=False
    assert entry.technical_pause_hold(status,terminal,instance=str(tmp_path.resolve())) is (change is None)


def test_native_failure_recovery_operation_requires_original_resume_proof(entry,tmp_path):
    stopped=_native_failure_status(tmp_path)['technical_stop']
    operation=_explicit_recovery(tmp_path,expected=2)
    assert not entry._recovery_action(operation,stopped,instance=str(tmp_path))
    operation['action'].update(after_interrupt=True,new_context=False,native_intent_ref=stopped['native_intent_ref'])
    assert entry._recovery_action(operation,stopped,instance=str(tmp_path))
    operation['action']['native_intent_ref']={'path':'/foreign/native-intent.json'}
    assert not entry._recovery_action(operation,stopped,instance=str(tmp_path))


def test_native_failure_hold_keeps_original_finite_deadline(entry,monkeypatch,tmp_path):
    agent=entry.SciDiscoveryHarborAgent(logs_dir=tmp_path/'logs',config_template=TEMPLATE,
        max_seconds=5,poll_seconds=.05)
    root=agent.private_root/'instance';root.mkdir()
    status=_native_failure_status(root)
    async def current(*args,**kwargs):return status
    async def operations(*args,**kwargs):return []
    monkeypatch.setattr(agent,'_status',current);monkeypatch.setattr(agent,'_operations',operations)
    monkeypatch.setattr(entry,'_command',lambda *args,**kwargs:pytest.fail('Hold submitted new work'))
    with pytest.raises(TimeoutError,match='original Harbor trial deadline'):
        asyncio.run(agent._wait_technical_recovery(root,types.SimpleNamespace(),deadline=time.time()+5,
            monotonic_deadline=time.monotonic()+.02,initial_status=status))


def test_applied_recovery_requires_exact_original_controller_receipt(entry,tmp_path):
    operation=_explicit_recovery(tmp_path)
    assert entry._controller_process(operation)==operation['process']
    operation['process']['task_ref']='different-operation'
    with pytest.raises(ValueError,match='exact controller process identity'):
        entry._controller_process(operation)


def _fake_technical_exit(agent):
    status=_technical_status(agent.private_root/'instance')
    source=("if verb == 'run':\n"
        f"    retained=json.loads({json.dumps(json.dumps(status))})\n"
        "    (root/'status.json').write_text(json.dumps(retained))\n"
        "    print(json.dumps({'status':'technical_pause'}))\n"
        "    raise SystemExit(2)\n"
        "if verb == 'run':")
    Path(agent.scid_argv[-1]).write_text(FAKE_CLI.replace("if verb == 'run':",source))


@pytest.mark.process_e2e
@pytest.mark.skipif(sys.platform != 'linux',reason='Linux subreaper contract')
def test_clean_technical_exit_holds_same_container_until_explicit_recovery_delivery(entry,monkeypatch,tmp_path):
    agent,environment,events=_fake_agent(entry,monkeypatch,tmp_path)
    _fake_technical_exit(agent)
    root=agent.private_root/'instance'
    receipt_bytes=[]
    original_binding=dict(agent.binding)
    async def operations(*args,**kwargs):
        receipt=agent.private_root/'host-terminal.json'
        if not receipt_bytes:
            receipt_bytes.append(receipt.read_bytes())
            status,_=_confirmed(agent.private_root/'delivery')
            status.update(instance=str(root.resolve()),goal_id='same-research',
                nodes=[dict(step=1,role='A',state='seal_present_unverified')])
            (root/'status.json').write_text(json.dumps(status))
        return [_explicit_recovery(root,report=dict(status='delivery_confirmed'))]
    monkeypatch.setattr(agent,'_operations',operations)
    from scidiscovery.research_loop import runtime
    monkeypatch.setattr(runtime,'process_matches',lambda *args:False)
    asyncio.run(agent.run('same original objective',environment,types.SimpleNamespace()))
    assert events==[b'def predict(x): return x\n']
    assert agent.binding==original_binding
    assert (agent.private_root/'host-terminal.json').read_bytes()==receipt_bytes[0]
    assert json.loads(receipt_bytes[0])['returncode']==2
    assert json.loads((agent.private_root/'run.log').read_text())['status']=='technical_pause'


@pytest.mark.process_e2e
@pytest.mark.skipif(sys.platform != 'linux',reason='Linux subreaper contract')
@pytest.mark.parametrize('accept_race',[False,True])
def test_cancellation_during_technical_hold_cleans_original_container(entry,monkeypatch,tmp_path,accept_race):
    agent,environment,events=_fake_agent(entry,monkeypatch,tmp_path)
    _fake_technical_exit(agent)
    confirmations=[]
    from scidiscovery.research_loop import control_operations
    def confirmed(selected,**binding):
        confirmations.append((selected,binding))
        return dict(status='cleanup_unknown' if accept_race else 'held_recovery_stopped',
            cleanup_confirmed=not accept_race,no_replacement=not accept_race)
    monkeypatch.setattr(control_operations,'confirm_no_held_recovery',confirmed)
    monkeypatch.setattr(control_operations,'stop_held_recovery',lambda *args,**kwargs:
        pytest.fail('cleanup guessed an unobserved repair process'))
    async def cancelled():
        observed=asyncio.Event();calls=0
        async def operations(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls>1:return []
            observed.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(agent,'_operations',operations)
        task=asyncio.create_task(agent.run('same original objective',environment,types.SimpleNamespace()))
        await asyncio.wait_for(observed.wait(),3)
        before=(agent.private_root/'host-terminal.json').read_bytes()
        task.cancel()
        if accept_race:
            with pytest.raises(RuntimeError,match='cleanup_unknown'):await task
        else:
            with pytest.raises(asyncio.CancelledError):await task
        assert (agent.private_root/'host-terminal.json').read_bytes()==before
    asyncio.run(cancelled())
    assert events==['container-stopped']
    root=agent.private_root/'instance'
    assert confirmations==[(root,dict(goal_id='same-research',step=1,expected_attempt=0))]
    receipt=json.loads((agent.private_root/'held-recovery-cleanup.json').read_text())
    assert receipt['cleanup_confirmed'] is (not accept_race)


@pytest.mark.process_e2e
@pytest.mark.skipif(sys.platform != 'linux',reason='Linux subreaper contract')
def test_cancellation_stops_exact_explicit_recovery_owner_before_container_cleanup(entry,monkeypatch,tmp_path):
    agent,environment,events=_fake_agent(entry,monkeypatch,tmp_path)
    _fake_technical_exit(agent)
    root=agent.private_root/'instance'
    from scidiscovery.research_loop import control_operations
    stops=[]
    def stopped(selected,**bindings):
        stops.append((selected,bindings))
        return dict(status='held_recovery_stopped',cleanup_confirmed=True)
    monkeypatch.setattr(control_operations,'stop_held_recovery',stopped)
    async def cancelled():
        observed=asyncio.Event();calls=0
        async def operations(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==2:
                observed.set()
                await asyncio.Event().wait()
            return [_explicit_recovery(root,state='running')]
        monkeypatch.setattr(agent,'_operations',operations)
        task=asyncio.create_task(agent.run('same original objective',environment,types.SimpleNamespace()))
        await asyncio.wait_for(observed.wait(),3)
        before=(agent.private_root/'host-terminal.json').read_bytes()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert (agent.private_root/'host-terminal.json').read_bytes()==before
    asyncio.run(cancelled())
    assert events==['container-stopped']
    assert stops==[(root,dict(held_operation_id='1'*32,active_operation_id='1'*32))]
    assert json.loads((agent.private_root/'held-recovery-cleanup.json').read_text())['cleanup_confirmed'] is True


def test_unconfirmed_recovery_cleanup_is_explicit_and_still_stops_container(entry,monkeypatch,tmp_path):
    agent=entry.SciDiscoveryHarborAgent(logs_dir=tmp_path/'logs',config_template=TEMPLATE)
    root=agent.private_root/'instance';root.mkdir()
    agent._held_recovery_operation_id='1'*32;agent._held_recovery_sequence=1
    events=[]
    async def operations(*args,**kwargs):return [_explicit_recovery(root,state='running')]
    async def container():events.append('container-stopped')
    async def status(*args,**kwargs):return _technical_status(root)
    monkeypatch.setattr(agent,'_operations',operations);monkeypatch.setattr(agent,'_stop_container',container)
    monkeypatch.setattr(agent,'_status',status)
    from scidiscovery.research_loop import control_operations
    monkeypatch.setattr(control_operations,'stop_held_recovery',lambda *args,**kwargs:
        dict(status='cleanup_unknown',cleanup_confirmed=False))
    with pytest.raises(RuntimeError,match='cleanup_unknown'):
        asyncio.run(agent._cleanup(root,types.SimpleNamespace()))
    assert events==['container-stopped']
    assert json.loads((agent.private_root/'held-recovery-cleanup.json').read_text())['status']=='cleanup_unknown'


@pytest.mark.process_e2e
@pytest.mark.skipif(sys.platform != 'linux',reason='Linux subreaper contract')
@pytest.mark.parametrize('blocked',['status','operations'])
def test_cancel_before_first_hold_poll_stops_already_accepted_exact_repair(entry,monkeypatch,tmp_path,blocked):
    agent,environment,events=_fake_agent(entry,monkeypatch,tmp_path)
    _fake_technical_exit(agent)
    root=agent.private_root/'instance'
    from scidiscovery.research_loop import control_operations
    stops=[]
    def stopped(selected,**bindings):
        stops.append((selected,bindings))
        return dict(status='held_recovery_stopped',cleanup_confirmed=True)
    monkeypatch.setattr(control_operations,'stop_held_recovery',stopped)
    original_status=agent._status
    async def cancelled():
        observed=asyncio.Event();interrupted=False
        # The explicit operator has accepted this exact repair before the
        # first hold poll returns. Cleanup must discover it independently.
        accepted=[_explicit_recovery(root,state='running')]
        async def status(*args,**kwargs):
            nonlocal interrupted
            if blocked=='status' and hasattr(agent,'_technical_hold_binding') and not interrupted:
                interrupted=True;observed.set()
                await asyncio.Event().wait()
            return await original_status(*args,**kwargs)
        async def operations(*args,**kwargs):
            nonlocal interrupted
            if blocked=='operations' and not interrupted:
                interrupted=True;observed.set()
                await asyncio.Event().wait()
            return accepted
        monkeypatch.setattr(agent,'_status',status);monkeypatch.setattr(agent,'_operations',operations)
        task=asyncio.create_task(agent.run('same original objective',environment,types.SimpleNamespace()))
        await asyncio.wait_for(observed.wait(),3)
        assert not hasattr(agent,'_held_recovery_operation_id')
        assert agent._technical_hold_binding['instance']==str(root.resolve())
        before=(agent.private_root/'host-terminal.json').read_bytes()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert (agent.private_root/'host-terminal.json').read_bytes()==before
    asyncio.run(cancelled())
    assert stops==[(root,dict(held_operation_id='1'*32,active_operation_id='1'*32))]
    assert events==['container-stopped']
    assert json.loads((agent.private_root/'held-recovery-cleanup.json').read_text())['cleanup_confirmed'] is True
