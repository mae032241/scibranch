"""One opt-in cohort: package metadata, supported entry points and shipped resources.

This does not run scientific tasks or compare source and installed catalogs.
Other historical plugin combinations have no installation acceptance in this lane.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.installed


def test_selected_cohort_packaging(installed_probe, pytestconfig):
    cohort = pytestconfig.getoption("installation_cohort")
    installed_probe("cohort = " + repr(cohort) + r'''
from importlib import import_module, resources
from importlib.metadata import PackageNotFoundError, distribution, entry_points
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

import scidiscovery
from scidiscovery import plugin_configuration
from scidiscovery.research_loop import ui_errors
import jsonschema
import pydantic
import cryptography
import packaging

prefix = Path(sys.prefix).resolve()
assert "include-system-site-packages = false" in (prefix / "pyvenv.cfg").read_text()
for module in (scidiscovery, plugin_configuration, ui_errors, jsonschema, pydantic, cryptography, packaging):
    assert Path(module.__file__).resolve().is_relative_to(prefix), module.__file__
package = distribution("scidiscovery")
for script, target in {
    "scid": "scidiscovery.app_cli:main",
    "scid-research": "scidiscovery.research_loop.cli:main",
    "scid-experiment": "scidiscovery.research_loop.experiments:main",
}.items():
    matches = [item for item in package.entry_points if item.group == "console_scripts" and item.name == script]
    assert len(matches) == 1, matches
    entry = matches[0]
    assert entry.value == target and callable(entry.load())
    result = subprocess.run([str(prefix / "bin" / script), "--help"],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and "usage:" in result.stdout, result.stderr
result = subprocess.run([str(prefix / "bin/scid"), "web", "--help"],
                        capture_output=True, text=True, timeout=10)
assert result.returncode == 0 and "--foreground" in result.stdout, result.stderr
execution = entry_points().select(group="scidiscovery.execution_interfaces")
execution_names = {"local_execution"}
if cohort == "cov":
    execution_names.add("cov_execution")
if cohort == "tcad":
    execution_names.add("tcad_execution")
assert sorted(entry.name for entry in execution) == sorted(execution_names)
for entry in execution:
    assert entry.load() is not None
assert not tuple(entry_points().select(group="scidiscovery.plugins"))
from scidiscovery import browser_access
from scidiscovery.research_loop import instance_ui
assert {entry.name for entry in package.entry_points if entry.group == "console_scripts"} == {"scid", "scid-research", "scid-experiment"}
assert not resources.files("scidiscovery.artifact_agent").joinpath("interfaces").is_dir()
assert not resources.files("scidiscovery.artifact_agent").joinpath("approval_ui").is_dir()
assert not resources.files("scidiscovery.artifact_agent").joinpath("service").is_dir()
for name in ("runtime", "execution_bridge", "runtime_plugin_bindings", "worker_services", "state_format", "portable_bundle"):
    assert not resources.files("scidiscovery.artifact_agent").joinpath(name + ".py").is_file()
assert not (prefix / "share/scidiscovery/roles").exists()
assert not (prefix / "share/scidiscovery/deploy/systemd").exists()

if cohort == "core":
    from importlib.util import find_spec
    assert find_spec("PIL") is None
    # Retired wheel surfaces are checked against this already installed cohort;
    # no second core wheel build/unpack is needed.
    installed_files = {str(path) for path in package.files or ()}
    for name in ("analysis_artifacts", "calculation_proof", "pdf_tool", "catalog", "invoke", "spec", "tooling"):
        assert "scidiscovery/operations/" + name + ".py" not in installed_files
        assert find_spec("scidiscovery.operations." + name) is None
    for module in ("scidiscovery.platforms.codex", "scidiscovery.platforms.codex_root_host"):
        assert find_spec(module) is None
    assert not any(entry.group == "scidiscovery.instance_views" for entry in package.entry_points)
    for name in ("tcad-artifact", "scidiscovery-curve-score", "scidiscovery-curve-figure-evidence", "scidiscovery-cov-execution"):
        try:
            distribution(name)
        except PackageNotFoundError:
            pass
        else:
            raise AssertionError(f"core unexpectedly included {name}")
    for module in ("scidiscovery.artifact_agent.service.agent_dispatch", "scidiscovery.platforms.codex_worker",
                   "scidiscovery.artifact_agent.schema.device_parameters", "architecture_operation_test_plugin",
                   "cov_execution", "scidiscovery.research_loop.cov_experiments"):
        try:
            import_module(module)
        except ModuleNotFoundError:
            pass
        else:
            raise AssertionError(f"core unexpectedly exposed {module}")
elif cohort == "cov":
    distribution("scidiscovery-cov-execution")
    from cov_execution import experiments as cov, lineage, delivery, receipt, observer
    from importlib.util import find_spec
    assert find_spec("cov_execution.launcher") is None
    from scidiscovery.research_loop import experiments
    declared, binding = experiments.registration("cov_execution:experiment")
    assert declared is cov.REGISTRATION and binding == "experiment"
    assert declared.native_launcher_module is None
    assert all(Path(module.__file__).resolve().is_relative_to(prefix)
               for module in (cov, lineage, delivery, receipt, observer))
    assert callable(observer.observe) and callable(delivery.leaf)
    # Check the installed selected Python through real entry point discovery,
    # without creating an instance, constructing an adapter, or running science.
    selected = dict(executor="cov_execution:experiment", configuration=dict(
        run_argv=[sys.executable], python_requirements=[dict(requirement="packaging>=23,<27", import_name="packaging")]))
    assert experiments.operator_configuration(selected) == selected
    missing = dict(executor="cov_execution:experiment", configuration=dict(
        run_argv=[sys.executable], python_requirements=[dict(
            requirement="scidiscovery-absent-installation-dependency-fixture>=1", import_name="absent_module")]))
    try:
        experiments.operator_configuration(missing)
    except plugin_configuration.PluginConfigurationError as error:
        assert error.code == "python_dependency_missing"
        assert error.field_path.endswith("python_requirements.0.requirement")
    else:
        raise AssertionError("installed Cov admitted a missing declared Python dependency")
else:
    # Installed TCAD discovers the real execution registration without curve plugins.
    tcad = distribution("tcad-artifact")
    scripts = [entry for entry in tcad.entry_points if entry.group == "console_scripts"]
    assert sorted(entry.name for entry in scripts) == ["scidiscovery-tcad-transport", "tcad-control-daemon"]
    for entry in scripts:
        assert callable(entry.load())
        result = subprocess.run([str(prefix / "bin" / entry.name), "--help"],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0 and "usage:" in result.stdout, result.stderr
    assert not any("curve" in requirement for requirement in tcad.requires or ())
    assert tuple((prefix / "share/tcad-artifact/systemd").glob("*.in"))
    assert tuple((prefix / "share/tcad-artifact/config").glob("*.json"))
    from scidiscovery.research_loop import experiments
    from tcad_artifact import managed_execution
    declared, binding = experiments.registration("tcad_execution:tcad")
    assert declared is managed_execution.REGISTRATION and binding == "tcad"
    assert declared.factory.requires_config_file is True
    assert callable(declared.factory.configure)
    # Installed configuration admission uses real discovery but no transport.
    with TemporaryDirectory() as scratch, patch.object(managed_execution, "build_adapter",
            side_effect=AssertionError("configuration admission constructed an adapter")):
        directory = Path(scratch)
        expected = dict(executor="tcad_execution:tcad", configuration=dict(
            transport="socket", socket_path=str(directory / "unopened.sock")))
        for extension, text in (("json", '{"transport":"socket","socket_path":"./unopened.sock"}'),
                ("toml", 'transport = "socket"\nsocket_path = "./unopened.sock"\n')):
            source = directory / ("tcad." + extension)
            source.write_text(text, encoding="utf-8")
            selected = experiments.operator_configuration(
                dict(executor="tcad_execution:tcad", config_file=source.name), base=directory)
            assert selected == expected
        try:
            experiments.operator_configuration(expected)
        except plugin_configuration.PluginConfigurationError as error:
            assert error.code == "plugin_config_file_required"
        else:
            raise AssertionError("installed TCAD admitted inline configuration")
        source.write_text('transport = "invalid"\n', encoding="utf-8")
        try:
            experiments.operator_configuration(
                dict(executor="tcad_execution:tcad", config_file=str(source)))
        except plugin_configuration.PluginConfigurationError as error:
            assert error.code == "tcad_configuration_invalid"
        else:
            raise AssertionError("installed TCAD bypassed configuration validation")
''')
