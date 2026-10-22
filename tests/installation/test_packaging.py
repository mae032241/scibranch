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
from PIL import Image
import jsonschema
import pydantic
import cryptography

prefix = Path(sys.prefix).resolve()
assert "include-system-site-packages = false" in (prefix / "pyvenv.cfg").read_text()
for module in (scidiscovery, plugin_configuration, ui_errors, Image, jsonschema, pydantic, cryptography):
    assert Path(module.__file__).resolve().is_relative_to(prefix), module.__file__
package = distribution("scidiscovery")
for script, target in {
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
execution = entry_points().select(group="scidiscovery.execution_interfaces")
execution_names = {"local_execution"}
if cohort == "cov":
    execution_names.add("cov_execution")
if cohort == "tcad":
    execution_names.add("tcad_execution")
assert sorted(entry.name for entry in execution) == sorted(execution_names)
for entry in execution:
    assert entry.load() is not None
plugin_names = {"builtin", "general_science"}
if cohort == "tcad":
    plugin_names |= {"curve_score", "tcad_artifact"}
plugins = entry_points().select(group="scidiscovery.plugins")
assert sorted(entry.name for entry in plugins) == sorted(plugin_names)
for entry in plugins:
    assert entry.load() is not None

root = resources.files("scidiscovery")
assert root.joinpath("default_agent_settings.json").read_bytes()
assert any(item.name.endswith(".sql") for item in resources.files("scidiscovery.artifact_agent.storage").joinpath("migrations").iterdir())
assert {entry.name for entry in package.entry_points if entry.group == "console_scripts"} == {"scid-research", "scid-experiment"}
assert not resources.files("scidiscovery.artifact_agent").joinpath("interfaces").is_dir()
assert not resources.files("scidiscovery.artifact_agent").joinpath("approval_ui").is_dir()
assert not resources.files("scidiscovery.artifact_agent").joinpath("service").is_dir()
for name in ("runtime", "execution_bridge", "runtime_plugin_bindings", "worker_services", "state_format", "portable_bundle"):
    assert not resources.files("scidiscovery.artifact_agent").joinpath(name + ".py").is_file()
assert not (prefix / "share/scidiscovery/roles").exists()
assert not (prefix / "share/scidiscovery/deploy/systemd").exists()

if cohort == "core":
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
    from scidiscovery.research_loop import experiments
    declared, binding = experiments.registration("cov_execution:experiment")
    assert declared is cov.REGISTRATION and binding == "experiment"
    assert all(Path(module.__file__).resolve().is_relative_to(prefix)
               for module in (cov, lineage, delivery, receipt, observer))
    assert callable(observer.observe) and callable(delivery.leaf)
else:
    # One pip install of core + TCAD resolves curve from the same wheelhouse;
    # all_domains --no-deps was never equivalent evidence for this requirement.
    tcad = distribution("tcad-artifact")
    distribution("scidiscovery-curve-score")
    scripts = [entry for entry in tcad.entry_points if entry.group == "console_scripts"]
    assert sorted(entry.name for entry in scripts) == ["scidiscovery-tcad-transport", "tcad-control-daemon", "tcad-control-mcp"]
    for entry in scripts:
        assert callable(entry.load())
        result = subprocess.run([str(prefix / "bin" / entry.name), "--help"],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0 and "usage:" in result.stdout, result.stderr
    assert any(requirement.startswith("scidiscovery-curve-score") for requirement in tcad.requires or ())
    assert resources.files("tcad_artifact").joinpath("roles/parameter_evidence_extractor.md").read_bytes()
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
