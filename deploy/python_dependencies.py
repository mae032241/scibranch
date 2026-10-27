#!/usr/bin/env python3
"""Check declared offline install dependencies in the selected base Python."""

from __future__ import annotations

import argparse
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import sys
from typing import Callable, Mapping, Sequence

try:
    from packaging.markers import default_environment
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.utils import canonicalize_name
    from packaging.version import InvalidVersion, Version
except ModuleNotFoundError:
    raise SystemExit(
        f"missing base Python dependency: packaging; interpreter: {sys.executable}"
    ) from None

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 deployment path
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        raise SystemExit(
            "missing base Python dependency: tomli>=2; "
            f"required for Python <3.11; interpreter: {sys.executable}"
        ) from None

if __package__:
    from .plugin_selection import resolve_plugins
else:
    from plugin_selection import resolve_plugins


class DependencyError(ValueError):
    """A declared dependency cannot be satisfied by this install cohort."""


def _failure(message: str) -> DependencyError:
    return DependencyError(f"{message}; interpreter: {sys.executable}")


def validate_dependencies(
    source_root: Path | str,
    specification: str = "",
    *,
    installed_version: Callable[[str], str] | None = None,
    marker_environment: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Validate core and selected plugins without downloading or importing them.

    The source projects form one replacement cohort. Other requirements must
    already be installed in the interpreter that is running this helper.
    Extras and direct URLs are rejected when active: version metadata alone
    cannot establish their transitive dependencies or source identity.
    """
    root = Path(source_root)
    selected = resolve_plugins(root, specification) if specification else ()
    paths = [root / "pyproject.toml", *(
        root / "plugins" / plugin.plugin_id / "pyproject.toml"
        for plugin in selected
    )]
    cohort: dict[str, str] = {}
    declarations: list[tuple[str, str]] = []
    for path in paths:
        metadata = tomllib.loads(path.read_text(encoding="utf-8"))
        project = metadata.get("project", {})
        build_system = metadata.get("build-system", {})
        if not isinstance(project, dict) or not isinstance(build_system, dict):
            raise _failure("invalid source cohort project or build-system metadata")
        name, source_version = project.get("name"), project.get("version")
        if not isinstance(name, str) or not isinstance(source_version, str):
            raise _failure("source cohort requires static project name and version")
        try:
            Version(source_version)
        except InvalidVersion:
            raise _failure(f"invalid source cohort version for {name}") from None
        normalized = canonicalize_name(name)
        if normalized in cohort:
            raise _failure(f"duplicate source cohort distribution: {name}")
        cohort[normalized] = source_version
        if "dependencies" in project.get("dynamic", []):
            raise _failure(f"dynamic dependency declarations cannot be checked for {name}")
        for requirements in (
            project.get("dependencies", []),
            build_system.get("requires", []),
        ):
            if not isinstance(requirements, list) or not all(
                isinstance(raw, str) for raw in requirements
            ):
                raise _failure(f"invalid dependency declarations for {name}")
            declarations.extend((name, raw) for raw in requirements)

    environment = default_environment()
    if marker_environment is not None:
        environment.update(marker_environment)
    environment["extra"] = ""
    lookup = installed_version if installed_version is not None else version
    checked = []
    for owner, raw in declarations:
        try:
            requirement = Requirement(raw)
        except InvalidRequirement:
            # A malformed URL may contain credentials; never echo its raw text.
            raise _failure(f"invalid dependency declaration for {owner}") from None
        if requirement.marker is not None and not requirement.marker.evaluate(environment):
            continue
        if requirement.url is not None:
            raise _failure(
                f"unsupported base Python dependency: {requirement.name} @ [direct URL]; "
                "direct URL identity cannot be checked for --no-deps deployment"
            )
        if requirement.extras:
            raise _failure(
                f"unsupported base Python dependency: {requirement}; "
                "extras cannot be checked for --no-deps deployment"
            )
        target = canonicalize_name(requirement.name)
        if target in cohort:
            observed = cohort[target]
            location = "source cohort"
        else:
            location = "base Python"
            try:
                observed = lookup(requirement.name)
            except PackageNotFoundError:
                raise _failure(
                    f"missing base Python dependency: {requirement}; "
                    "install it before --no-deps deployment"
                ) from None
        try:
            compatible = not requirement.specifier or observed in requirement.specifier
        except InvalidVersion:
            compatible = False
        if not compatible:
            raise _failure(
                f"incompatible base Python dependency: {requirement}; "
                f"found {observed} in {location} (required by {owner})"
            )
        checked.append(f"{location} dependency: {requirement.name}={observed}")
    return tuple(checked)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--plugins", default="")
    arguments = parser.parse_args(argv)
    try:
        checked = validate_dependencies(arguments.source_root, arguments.plugins)
    except DependencyError as error:
        parser.exit(1, f"{error}\n")
    except (OSError, ValueError):
        # Parser errors may echo a malformed credential-bearing URL or private
        # path. Preserve the interpreter identity without exposing raw input.
        parser.exit(1, "base Python dependency preflight failed: "
                    "unavailable or invalid source cohort metadata; "
                    f"interpreter: {sys.executable}\n")
    for line in checked:
        print(line)
    print("base Python dependency check: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
