"""Offline dependency admission for the selected installer source cohort."""

from importlib.metadata import PackageNotFoundError
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from deploy.python_dependencies import DependencyError, validate_dependencies


ROOT = Path(__file__).resolve().parents[2]


def project(path, name, *, version="2.0", dependencies=(), build=()):
    path.mkdir(parents=True, exist_ok=True)
    (path / "pyproject.toml").write_text(
        "[project]\n"
        f"name = {json.dumps(name)}\nversion = {json.dumps(version)}\n"
        f"dependencies = {json.dumps(list(dependencies))}\n"
        "[build-system]\n"
        f"requires = {json.dumps(list(build))}\n",
        encoding="utf-8",
    )


def lookup(versions):
    def installed(name):
        if name not in versions:
            raise PackageNotFoundError(name)
        return versions[name]
    return installed


def test_inactive_markers_skip_missing_and_unsupported_dependencies(tmp_path):
    project(tmp_path, "scidiscovery", dependencies=(
        "missing>=3; python_version < '3'",
        "missing[feature]>=3; python_version < '3'",
        "missing @ https://invalid.example/package.whl ; python_version < '3'",
    ))
    assert validate_dependencies(
        tmp_path, installed_version=lookup({}),
        marker_environment={"python_version": "3.12"},
    ) == ()


@pytest.mark.parametrize("section", ["dependencies", "build"])
@pytest.mark.parametrize("observed", [None, "1.9", "4.0"])
def test_selected_plugin_external_requirements_must_be_installed_and_compatible(
    tmp_path, section, observed,
):
    project(tmp_path, "scidiscovery")
    project(tmp_path / "plugins/cov_execution", "scidiscovery-cov-execution",
            **{section: ("external-package>=2,<4",)})
    versions = {} if observed is None else {"external-package": observed}
    with pytest.raises(DependencyError) as caught:
        validate_dependencies(tmp_path, "cov_execution", installed_version=lookup(versions))
    assert "external-package<4,>=2" in str(caught.value)
    assert sys.executable in str(caught.value)
    assert ("missing" if observed is None else "incompatible") in str(caught.value)


def test_selected_plugins_and_core_use_source_versions_as_one_cohort(tmp_path):
    project(tmp_path, "scidiscovery", dependencies=("selected-plugin>=3",))
    project(tmp_path / "plugins/cov_execution", "selected-plugin", version="3.0",
            dependencies=("ScIdIsCoVeRy>=2", "external-package>=4"),
            build=("build-package>=5",))
    # Installed core/plugin versions are stale and must not override this cohort.
    checked = validate_dependencies(tmp_path, "cov_execution", installed_version=lookup({
        "scidiscovery": "0.1", "selected-plugin": "0.1",
        "external-package": "4.1", "build-package": "5.1",
    }))
    assert "source cohort dependency: ScIdIsCoVeRy=2.0" in checked
    assert "source cohort dependency: selected-plugin=3.0" in checked
    assert "base Python dependency: external-package=4.1" in checked
    assert "base Python dependency: build-package=5.1" in checked


def test_incompatible_internal_requirement_uses_selected_source_version(tmp_path):
    project(tmp_path, "scidiscovery")
    project(tmp_path / "plugins/cov_execution", "selected-plugin",
            dependencies=("scidiscovery>=3",))
    with pytest.raises(DependencyError, match="found 2.0 in source cohort"):
        validate_dependencies(tmp_path, "cov_execution",
                              installed_version=lookup({"scidiscovery": "4.0"}))


def test_unused_plugin_requirements_do_not_enter_the_cohort(tmp_path):
    project(tmp_path, "scidiscovery", dependencies=("external-package>=2",))
    project(tmp_path / "plugins/unused", "unused-plugin",
            dependencies=("missing-runtime>=99",), build=("missing-builder>=99",))
    assert validate_dependencies(tmp_path, installed_version=lookup({
        "external-package": "2.0",
    })) == ("base Python dependency: external-package=2.0",)


def test_dynamic_dependency_declarations_fail_closed(tmp_path):
    project(tmp_path, "scidiscovery")
    source = tmp_path / "pyproject.toml"
    source.write_text(source.read_text().replace(
        "[project]\n", '[project]\ndynamic = ["dependencies"]\n'), encoding="utf-8")
    with pytest.raises(DependencyError, match="dynamic dependency declarations"):
        validate_dependencies(tmp_path, installed_version=lookup({}))


@pytest.mark.parametrize("requirement, fragment", [
    ("external-package[feature]>=2", "extras cannot be checked"),
    ("external-package @ https://secret-token@invalid.example/package.whl",
     "direct URL identity cannot be checked"),
])
def test_active_extras_and_urls_are_rejected_without_disclosing_url_credentials(
    tmp_path, requirement, fragment,
):
    project(tmp_path, "scidiscovery", dependencies=(requirement,))
    with pytest.raises(DependencyError) as caught:
        validate_dependencies(tmp_path, installed_version=lookup({"external-package": "2.0"}))
    error = str(caught.value)
    assert fragment in error and "external-package" in error
    assert sys.executable in error
    assert "secret-token" not in error and "invalid.example" not in error


@pytest.mark.process_e2e
@pytest.mark.parametrize("selected", [False, True])
def test_shell_preflight_checks_selected_plugin_before_any_publication(tmp_path, selected):
    source = tmp_path / "source"
    project(source, "scidiscovery", dependencies=("packaging>=0",))
    project(source / "plugins/cov_execution", "selected-plugin",
            dependencies=("scid-selected-preflight-fixture-missing>=123",))
    deployment = source / "deploy"
    deployment.mkdir()
    for filename in ("install.sh", "python_dependencies.py", "plugin_selection.py"):
        (deployment / filename).write_bytes((ROOT / "deploy" / filename).read_bytes())
    patch = tmp_path / "metadata-patch"
    patch.mkdir()
    (patch / "sitecustomize.py").write_text(
        "import importlib.metadata as metadata\n"
        "original = metadata.version\n"
        "def version(name):\n"
        "    if name == 'scid-selected-preflight-fixture-missing':\n"
        "        raise metadata.PackageNotFoundError(name)\n"
        "    return original(name)\n"
        "metadata.version = version\n",
        encoding="utf-8",
    )
    environment = {**os.environ, "SCID_PYTHON": sys.executable,
        "SCID_PLUGINS": "cov_execution" if selected else "",
        "SCID_INSTALL_ROOT": str(tmp_path / "prefix"),
        "SCID_BIN_ROOT": str(tmp_path / "bin"),
        "SCID_BACKUP_ROOT": str(tmp_path / "backups"),
        "PYTHONPATH": str(patch)}
    environment.pop("SCID_DEPLOY_MODE", None)
    result = subprocess.run(
        ["bash", "-c", 'source "$1"\nvalidate_base_python', "bash",
         str(deployment / "install.sh")],
        env=environment, capture_output=True, text=True, timeout=10, check=False,
    )
    if selected:
        assert result.returncode != 0
        assert "scid-selected-preflight-fixture-missing>=123" in result.stderr
        assert sys.executable in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert "base Python dependency check: pass" in result.stdout
    for path in ("prefix", "bin", "backups"):
        assert not (tmp_path / path).exists()
