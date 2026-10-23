"""Runtime dependency preflight for raster and PDF evidence scripts."""
from __future__ import annotations
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess


def command_path(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f"missing executable: {name}; PDF inspection requires Poppler pdfimages")
    return str(Path(path).resolve(strict=True))


def inspect_dependencies(*, pdf: bool = False, contract: dict | None = None) -> dict:
    versions = {"python_packages": {}}
    for package in ("pydantic", "Pillow"):
        try:
            versions["python_packages"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError as error:
            raise RuntimeError(f"missing dependency: {package}; install this Skill's requirements.txt") from error
    if int(versions["python_packages"]["pydantic"].split('.')[0]) != 2:
        raise RuntimeError("Pydantic major version 2 is required")
    if int(versions["python_packages"]["Pillow"].split('.')[0]) < 10:
        raise RuntimeError("Pillow version 10 or newer is required")
    if pdf:
        executable = command_path("pdfimages")
        result = subprocess.run([executable, "-v"], capture_output=True, check=True, timeout=15)
        fields = (result.stdout + result.stderr).decode().splitlines()[0].split()
        if len(fields) != 3 or fields[:2] != ["pdfimages", "version"]:
            raise RuntimeError("cannot identify Poppler pdfimages version")
        versions["poppler_version"] = fields[2]
        versions["executables"] = {"pdfimages": executable}
    if contract is not None and contract != versions:
        raise RuntimeError("runtime dependencies differ from the supplied exact version contract")
    return versions
