"""Offline wheel candidates shared by installation tests and live host probes."""
from __future__ import annotations

import os
import csv
import io
import itertools
import shutil
import subprocess
import sys
import venv
from dataclasses import dataclass
from importlib.metadata import distribution
from pathlib import Path


@dataclass(frozen=True)
class InstalledEnvironment:
    python: Path
    workdir: Path


def _copy_runtime_distribution(name: str, destination: Path) -> None:
    """Copy one installed runtime dependency without exposing system site packages."""

    package = distribution(name)
    root = destination.resolve()
    record = package.read_text("RECORD")
    assert record is not None, f"offline dependency has no wheel RECORD: {name}"
    for row in csv.reader(io.StringIO(record)):
        relative = Path(row[0])
        if "__pycache__" in relative.parts or relative.suffix in {".pyc", ".pyo"}:
            continue
        source = Path(package.locate_file(relative))
        target = (destination / relative).resolve()
        if not target.is_relative_to(root) or not source.is_file():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _supply_runtime_dependencies(site_packages: Path) -> None:
    for package_name in (
        "annotated-types", "attrs", "jsonschema", "packaging",
        "jsonschema-specifications", "pydantic", "pydantic_core",
        "referencing", "rpds-py", "typing-extensions", "typing-inspection",
        "cryptography", "cffi", "pycparser",
    ):
        _copy_runtime_distribution(package_name, site_packages)
    if sys.version_info < (3, 11):
        _copy_runtime_distribution("tomli", site_packages)


_LOG_SEQUENCE = itertools.count(1)


def _run_logged(command, *, cwd: Path, timeout: int, env=None, read_stdout=False) -> str:
    """Fixture logs stay on disk and under the outer runner's sampled log budget."""
    log_root = Path(os.environ["SCID_TEST_LOG_DIR"])
    log_root.mkdir(parents=True, exist_ok=True)
    stem = str(next(_LOG_SEQUENCE))
    stdout_path, stderr_path = (log_root / f"{stem}.{channel}.log" for channel in ("stdout", "stderr"))
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=stdout, stderr=stderr,
                                timeout=timeout, check=False)
    limit = int(os.environ["SCID_TEST_LOG_BYTES"])
    assert stdout_path.stat().st_size + stderr_path.stat().st_size <= limit, f"fixture log budget exceeded: {log_root}"
    def tail(path):
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 4096))
            return stream.read(4096).decode("utf-8", errors="replace")
    assert result.returncode == 0, (f"installed command failed ({result.returncode}); logs: {log_root}\n"
                                    f"stdout tail: {tail(stdout_path)}\nstderr tail: {tail(stderr_path)}")
    return stdout_path.read_text() if read_stdout else ""


def _link_runtime_file(source, target):
    # The immutable per-session dependency cache is not the user's installation.
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)
    return target


def _candidate_creator(repository: Path, root: Path):
    """Construct only the wheel closure of one explicitly selected environment."""
    dependency_cache = root / "runtime-dependencies"
    _supply_runtime_dependencies(dependency_cache)
    wheelhouse = root / "wheelhouse"
    wheelhouse.mkdir()
    sources = {
        "core": (repository, "scidiscovery-"),
        "tcad": (repository / "plugins/tcad_artifact", "tcad_artifact-"),
        "cov": (repository / "plugins/cov_execution", "scidiscovery_cov_execution-"),
    }
    selections = {
        "core": ("core",),
        "all_domains": ("core", "tcad", "cov"),
        "cov": ("core", "cov"),
        "tcad_resolved": ("core", "tcad"),
    }
    built = {}

    def wheel(name):
        if name not in built:
            source, prefix = sources[name]
            stage = root / "sources" / name
            ignored = shutil.ignore_patterns("build", "dist", "*.egg-info", "__pycache__", "*.pyc")
            if name == "core":
                # Exactly the package/readme/data roots declared by pyproject.toml.
                # Release assembly and bundled manual scans have their own tests.
                stage.mkdir(parents=True)
                for relative in ("pyproject.toml", "README.md"):
                    shutil.copy2(source / relative, stage / relative)
                for relative in ("src",):
                    shutil.copytree(source / relative, stage / relative, ignore=ignored)
            else:
                shutil.copytree(source, stage, ignore=ignored)
            _run_logged([
                sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-index", "--no-cache-dir",
                "--no-build-isolation", "--wheel-dir", str(wheelhouse), str(stage),
            ], cwd=root, timeout=180)
            built[name] = next(path for path in wheelhouse.glob("*.whl") if path.name.startswith(prefix))
        return built[name]

    def create_environment(name: str) -> InstalledEnvironment:
        if name not in selections:
            raise KeyError(name)
        selected = tuple(wheel(package) for package in selections[name])
        environment_root = root / name
        venv.EnvBuilder(with_pip=False, symlinks=True, system_site_packages=False).create(environment_root)
        site_packages = environment_root / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
        shutil.copytree(dependency_cache, site_packages, dirs_exist_ok=True, copy_function=_link_runtime_file)
        python = environment_root / "bin/python"
        resolve_once = name == "tcad_resolved"
        if name == "tcad_resolved":
            selected = (wheel("core"), wheel("tcad"))
        _run_logged(
            [
                sys.executable, "-m", "pip", "--python", str(python),
                "install", "--no-cache-dir", "--no-compile",
                "--no-index",
                *(["--find-links", str(wheelhouse)] if resolve_once else ["--no-deps"]),
                *map(str, selected),
            ],
            cwd=root,
            timeout=180,
        )
        workdir = root / f"{name}-workdir"
        workdir.mkdir()
        return InstalledEnvironment(python=python, workdir=workdir)

    return create_environment


def create_environment(repository: Path, root: Path, *, cohort: str = "core") -> InstalledEnvironment:
    """Build one explicit environment; all_domains is the existing live option."""
    if cohort not in {"core", "tcad", "cov", "all_domains"}:
        raise ValueError("candidate cohort must be core, tcad, cov or all_domains")
    create = _candidate_creator(repository, root)
    return create("tcad_resolved" if cohort == "tcad" else cohort)
