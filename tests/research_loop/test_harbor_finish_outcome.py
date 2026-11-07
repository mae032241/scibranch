"""Optional host-entry lifecycle checks using SDK stubs, without Docker or models.

These exercise the source consumer and retained control records, not compatibility
with the real Harbor SDK. Import stubs exist only while loading that consumer.
"""
from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest


@pytest.fixture
def harbor_agent_module(monkeypatch):
    class BaseAgent:
        def __init__(self, *, logs_dir, model_name=None, **kwargs):
            self.model_name = model_name
            self.extra_env = kwargs.get("extra_env", {})

    names = ("harbor", "harbor.agents", "harbor.agents.base",
        "harbor.environments", "harbor.environments.docker",
        "harbor.environments.docker.docker")
    stubs = {name: types.ModuleType(name) for name in names}
    stubs["harbor.agents.base"].BaseAgent = BaseAgent
    stubs["harbor.environments.docker.docker"].DockerEnvironment = type("DockerEnvironment", (), {})
    path = (Path(__file__).resolve().parents[2]
        / "plugins/harbor_execution/harbor_execution/harbor_agent.py")
    spec = importlib.util.spec_from_file_location("harbor_finish_source_consumer", path)
    module = importlib.util.module_from_spec(spec)
    with monkeypatch.context() as sdk_imports:
        for name, stub in stubs.items():
            sdk_imports.setitem(sys.modules, name, stub)
        spec.loader.exec_module(module)
    assert all(sys.modules.get(name) is not stub for name, stub in stubs.items())
    return module


def _template(max_seconds):
    seconds = "unlimited" if max_seconds is None else max_seconds
    epoch = "" if max_seconds is None else "\ndeadline_epoch = __SCID_DEADLINE_EPOCH__"
    return f'''[research]
root = __SCID_INSTANCE_ROOT__
objective = __SCID_OBJECTIVE__
public_root = __SCID_PUBLIC_ROOT__
user_updates = [__SCID_NAVIGATION__]
[agent]
model = "test-model"
[budget]
max_seconds = {json.dumps(seconds)}
[executor.config]
executor = "harbor_execution:experiment"
[executor.config.configuration]
container_id = __SCID_CONTAINER_ID__
container_root = __SCID_CONTAINER_ROOT__
docker_argv = __SCID_DOCKER_ARGV__
environment = __SCID_CONTAINER_ENV__
user = __SCID_CONTAINER_USER__
max_seconds = 900
collection_seconds = 120{epoch}
[delivery]
adapter = __SCID_DELIVERY_ADAPTER__
'''


def _confirmed():
    return dict(operation_id="a" * 64, status="confirmed", cleanup_confirmed=True,
        candidate_id="candidate", artifact_ref=dict(size_bytes=3))


def _write_delivery(directory, confirmed):
    selected = directory / confirmed["operation_id"]
    selected.mkdir()
    (selected / "artifact").write_bytes(b"abc")
    (selected / "manifest.json").write_text(json.dumps({**confirmed,
        "objects": {"artifact": confirmed["artifact_ref"]}}))
    return selected


@pytest.fixture
def run_agent(harbor_agent_module, monkeypatch, tmp_path):
    module = harbor_agent_module

    def exercise(*, max_seconds=None, delivered=False, change=None,
                 runner_returncode=0, report_bytes=None):
        config = tmp_path / "template.toml"
        config.write_text(_template(max_seconds))
        agent = module.SciDiscoveryHarborAgent(tmp_path / "logs", model_name="test-model",
            config_template=str(config), scid_argv=["fake-scid"], max_seconds=max_seconds,
            poll_seconds=.05)
        agent.binding = dict(container_id="c" * 64, docker_argv=["fake-docker"],
            environment={}, user="0")
        root = agent.private_root / "instance"
        status = dict(instance=str(root), goal_id="scid-hwa-test", closeout=None,
            nodes=[dict(step=3, role="A", state="seal_present_unverified")],
            usage=dict(input_tokens=17, cached_input_tokens=3, output_tokens=5))
        report = dict(status="finish", scientific_route="finish", role="A",
            goal_id=status["goal_id"], last_step=3, completed_steps=3,
            goal_completion="not_asserted")
        receipt = dict(reason="exited", returncode=0, cleanup_confirmed=True)
        if delivered:
            status["closeout"] = dict(status="delivery_confirmed", last_confirmed=_confirmed())
        if change is not None:
            change(status, report, receipt)
        commands, launches, transfers, report_reads = [], [], [], []

        async def command(argv, **options):
            commands.append((argv, options))
            if argv[1] == "status":
                return json.dumps(status)
            if argv[1] == "inspect":
                return json.dumps([{"State": {"Running": False}}])
            return ""

        class Process:
            returncode = None

            async def wait(self):
                self.returncode = runner_returncode
                return self.returncode

        async def launch(*argv, **options):
            launches.append(argv)
            root.mkdir()
            Path(argv[argv.index("--receipt") + 1]).write_text(json.dumps(receipt))
            options["stdout"].write(json.dumps(report).encode() if report_bytes is None else report_bytes)
            options["stdout"].flush()
            if delivered:
                _write_delivery(agent.private_root / "delivery", status["closeout"]["last_confirmed"])
            return Process()

        class Environment:
            async def download_file(self, original, target):
                transfers.append(("download", original))
                target.write_text("original public file")

            async def upload_file(self, selected, original):
                transfers.append(("upload", original, Path(selected).read_bytes()))

        original_open = Path.open

        def observed_open(path, mode="r", *args, **kwargs):
            if path == agent.private_root / "run.log" and mode == "rb":
                report_reads.append(path)
            return original_open(path, mode, *args, **kwargs)

        context = types.SimpleNamespace()
        error = None
        with monkeypatch.context() as runtime:
            runtime.setattr(module, "_command", command)
            runtime.setattr(module.asyncio, "create_subprocess_exec", launch)
            runtime.setattr(Path, "open", observed_open)
            try:
                asyncio.run(agent.run("test objective", Environment(), context))
            except Exception as caught:
                error = caught
        return types.SimpleNamespace(agent=agent, status=status, report=report, error=error,
            context=context, commands=commands, launches=launches, transfers=transfers,
            report_reads=report_reads)

    return exercise


@pytest.mark.parametrize("max_seconds", [None, 1800])
def test_ordinary_finish_retains_usage_and_task_for_original_verifier(
        run_agent, harbor_agent_module, max_seconds):
    case = run_agent(max_seconds=max_seconds)

    assert case.error is None
    assert case.transfers == [("download", "/app/regressor.py")]
    assert not any(argv[0] == "fake-docker" for argv, _ in case.commands)
    assert len(case.report_reads) == 1
    # One progress poll and the existing final producer, with no new status read.
    assert sum(argv[1] == "status" for argv, _ in case.commands) == 2
    assert (case.context.n_input_tokens, case.context.n_cache_tokens,
        case.context.n_output_tokens) == (17, 3, 5)
    assert case.report["goal_completion"] == "not_asserted"
    assert json.loads((case.agent.private_root / "final-status.json").read_text()) == case.status
    rendered = harbor_agent_module.tomllib.loads((case.agent.private_root / "research.toml").read_text())
    provider = rendered["executor"]["config"]["configuration"]
    assert (provider["max_seconds"], provider["collection_seconds"]) == (900, 120)
    # The original finite deadline is kept; unlimited trials gain no epoch.
    assert ("--deadline-epoch" in case.launches[0]) is (max_seconds is not None)
    if max_seconds is None:
        assert "deadline_epoch" not in provider
        assert rendered["budget"]["max_seconds"] == "unlimited"
    else:
        argv = case.launches[0]
        assert float(argv[argv.index("--deadline-epoch") + 1]) == provider["deadline_epoch"]
        assert rendered["budget"]["max_seconds"] == max_seconds


@pytest.mark.parametrize("max_seconds", [None, 1800])
def test_exact_confirmed_delivery_still_publishes_original_bytes(run_agent, max_seconds):
    case = run_agent(max_seconds=max_seconds, delivered=True, report_bytes=b"not needed for delivery")

    assert case.error is None
    assert ("upload", "/app/regressor.py", b"abc") in case.transfers
    assert case.report_reads == []
    assert not any(argv[1] == "stop" for argv, _ in case.commands)
    assert (case.context.n_input_tokens, case.context.n_cache_tokens,
        case.context.n_output_tokens) == (17, 3, 5)


@pytest.mark.parametrize("terminal_status", [
    "paused", "wait", "unknown", "native_outcome_unsealed", "sealed_node_invalid",
    "error", "final_a_completed", "delivery_confirmed",
])
def test_null_closeout_does_not_complete_nonordinary_terminal_state(run_agent, terminal_status):
    case = run_agent(change=lambda status, report, receipt: report.update(status=terminal_status))

    assert isinstance(case.error, ValueError)
    assert not any(row[0] == "upload" for row in case.transfers)
    assert any(argv[1] == "stop" for argv, _ in case.commands)


@pytest.mark.parametrize("change", [
    lambda status, report, receipt: report.update(scientific_route="wait"),
    lambda status, report, receipt: report.update(role="H"),
    lambda status, report, receipt: report.update(goal_id="another-goal"),
    lambda status, report, receipt: report.update(last_step=2),
    lambda status, report, receipt: report.update(last_step=True),
    lambda status, report, receipt: status.update(instance="/another-instance"),
    lambda status, report, receipt: status.update(nodes=[]),
    lambda status, report, receipt: status["nodes"][-1].update(state="native_outcome_unsealed"),
    lambda status, report, receipt: status["nodes"][-1].update(state="unknown"),
    lambda status, report, receipt: status["nodes"][-1].update(role="W"),
    lambda status, report, receipt: status.pop("closeout"),
    lambda status, report, receipt: status.update(closeout={"status": "closeout_waiting"}),
])
def test_finish_requires_same_instance_goal_and_latest_sealed_a_node(run_agent, change):
    case = run_agent(change=change)

    assert isinstance(case.error, ValueError)
    assert not any(row[0] == "upload" for row in case.transfers)
    assert any(argv[1] == "stop" for argv, _ in case.commands)


@pytest.mark.parametrize("report_bytes", [b"", b"not json", b"null", b"{}", b"{}\n{}"])
def test_finish_requires_one_terminal_control_object(run_agent, report_bytes):
    case = run_agent(report_bytes=report_bytes)

    assert isinstance(case.error, ValueError)
    assert len(case.report_reads) == 1
    assert not any(row[0] == "upload" for row in case.transfers)
    assert any(argv[1] == "stop" for argv, _ in case.commands)


def test_terminal_control_report_read_is_bounded(run_agent, harbor_agent_module):
    case = run_agent(report_bytes=b" " * (harbor_agent_module.MAX_STATUS_BYTES + 1))

    assert isinstance(case.error, ValueError)
    assert "read bound" in str(case.error)
    assert len(case.report_reads) == 1
    assert not any(row[0] == "upload" for row in case.transfers)


@pytest.mark.parametrize("delivered", [False, True])
@pytest.mark.parametrize("runner_returncode,receipt_change", [
    (1, {}), (0, {"returncode": 1}), (0, {"returncode": False}),
    (0, {"reason": "cancelled"}), (0, {"reason": "deadline"}),
    (0, {"cleanup_confirmed": False}), (0, {"cleanup_confirmed": None}),
])
def test_host_exit_and_receipt_remain_required(run_agent, delivered, runner_returncode, receipt_change):
    case = run_agent(delivered=delivered, runner_returncode=runner_returncode,
        change=lambda status, report, receipt: receipt.update(receipt_change))

    assert isinstance(case.error, RuntimeError)
    assert not any(row[0] == "upload" for row in case.transfers)
    assert any(argv[1] == "stop" for argv, _ in case.commands)


@pytest.mark.parametrize("change", [
    lambda manifest: manifest.update(operation_id="b" * 64),
    lambda manifest: manifest.update(candidate_id="another-candidate"),
    lambda manifest: manifest.update(artifact_ref={"size_bytes": 4}),
    lambda manifest: manifest.update(objects={"artifact": {"size_bytes": 4}}),
])
def test_confirmed_delivery_manifest_bindings_stay_exact(harbor_agent_module, tmp_path, change):
    confirmed = _confirmed()
    selected = _write_delivery(tmp_path, confirmed)
    manifest_path = selected / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    change(manifest)
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(ValueError):
        harbor_agent_module.confirmed_artifact(
            {"closeout": {"status": "delivery_confirmed", "last_confirmed": confirmed}}, tmp_path)


def test_confirmed_delivery_artifact_size_stays_exact(harbor_agent_module, tmp_path):
    confirmed = _confirmed()
    selected = _write_delivery(tmp_path, confirmed)
    (selected / "artifact").write_bytes(b"altered")

    with pytest.raises(ValueError, match="size differs"):
        harbor_agent_module.confirmed_artifact(
            {"closeout": {"status": "delivery_confirmed", "last_confirmed": confirmed}}, tmp_path)


@pytest.mark.parametrize("change", [
    lambda confirmed: confirmed.update(operation_id="not-an-operation"),
    lambda confirmed: confirmed.update(status="unknown"),
    lambda confirmed: confirmed.update(cleanup_confirmed=False),
])
def test_confirmed_delivery_operation_qualification_stays_required(harbor_agent_module, tmp_path, change):
    confirmed = copy.deepcopy(_confirmed())
    change(confirmed)

    with pytest.raises(ValueError, match="operation identity"):
        harbor_agent_module.confirmed_artifact(
            {"closeout": {"status": "delivery_confirmed", "last_confirmed": confirmed}}, tmp_path)
