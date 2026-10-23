from __future__ import annotations

import fcntl
import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _source_subprocess_paths(monkeypatch, request):
    """Preserve independent package imports in source-launched child interpreters."""
    if Path(request.node.path).is_relative_to(Path(__file__).parent / "installation"):
        return
    root = Path(__file__).resolve().parents[1]
    paths = [str(root / "src"), str(root / "plugins/cov_execution")]
    inherited = os.environ.get("PYTHONPATH")
    if inherited:
        paths.append(inherited)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(paths))


_LANES = {"installed": "installed", "process_e2e": "process", "stress": "stress", "live_platform": "live"}


def pytest_addoption(parser):
    parser.addoption("--test-lane", choices=("source", "installed", "process", "stress", "live"),
                     default="source", help="Explicit test layer; installation is isolated from functional collection")
    parser.addoption("--installation-cohort", choices=("core", "tcad", "cov"), default="core",
                     help="One explicitly selected packaging cohort; used only by the installed lane")


def pytest_configure(config):
    # Explicit paths can bypass recursive ignore hooks. Reject before importing tests.
    installation = Path(__file__).parent.resolve() / "installation"
    installed = config.getoption("test_lane") == "installed"
    for argument in config.args:
        target = Path(str(argument).split("::", 1)[0]).resolve()
        inside = target.is_relative_to(installation)
        ancestor = installation.is_relative_to(target)
        if (not installed and inside) or (installed and not (inside or ancestor)):
            raise pytest.UsageError("installation and functional targets must be selected separately")
    if (config.getoption("numprocesses", default=0) not in (None, 0, "0")
            or "PYTEST_XDIST_WORKER" in os.environ):
        raise pytest.UsageError("xdist is disabled: use scripts/run_tests.py serially")
    lock_path = Path("/tmp") / f"scid-tests-{os.getuid()}.lock"
    inherited = os.environ.get("SCID_TEST_LOCK_FD")
    if inherited is not None:
        try:
            fd = int(inherited)
            inherited_stat, expected_stat = os.fstat(fd), lock_path.stat()
            if (inherited_stat.st_ino, inherited_stat.st_dev) != (expected_stat.st_ino, expected_stat.st_dev):
                raise ValueError("wrong lock file")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, ValueError) as exc:
            raise pytest.UsageError("invalid inherited serial test lock") from exc
    else:
        lock = lock_path.open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            lock.close()
            raise pytest.UsageError("another pytest/runner owns this repository's serial lock") from exc
        config._scid_test_lock = lock


def pytest_unconfigure(config):
    lock = getattr(config, "_scid_test_lock", None)
    if lock is not None:
        lock.close()


def pytest_ignore_collect(collection_path, config):
    installation = Path(__file__).parent.resolve() / "installation"
    path = Path(collection_path).resolve()
    inside = path.is_relative_to(installation)
    if config.getoption("test_lane") == "installed":
        return not (inside or installation.is_relative_to(path))
    return True if inside else None


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    selected, deselected = [], []
    lane = config.getoption("test_lane")
    for item in items:
        in_installation = Path(item.path).resolve().is_relative_to(Path(__file__).parent.resolve() / "installation")
        if not in_installation and {"installed_probe", "installed_environments", "installed_environment", "installation_environment"}.intersection(item.fixturenames):
            raise pytest.UsageError("functional tests must not depend on installation fixtures")
        if in_installation:
            item.add_marker(pytest.mark.installed)
        elif item.get_closest_marker("installed") is not None:
            raise pytest.UsageError("installation checks belong exclusively in tests/installation")
        # A stress/live test can also use installation; choose the strongest opt-in.
        marked = {marker.name for marker in item.iter_markers()}
        actual = next((_LANES[name] for name in ("live_platform", "stress", "installed", "process_e2e")
                       if name in marked), "source")
        (selected if actual == lane else deselected).append(item)
    items[:] = selected
    config.hook.pytest_deselected(items=deselected)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    if item.config.getoption("test_lane") != "source" and os.environ.get("SCID_TEST_GUARD") != "1":
        raise pytest.UsageError("heavy test lanes require scripts/run_tests.py resource supervision")
