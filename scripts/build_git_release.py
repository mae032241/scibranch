#!/usr/bin/env python3
"""Build a clean, identity-free SciDiscovery Git source repository."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import re
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path


ROOT_FILES = (
    ".gitattributes",
    "CHANGELOG.md",
    ".gitignore",
    "CONTRIBUTING.md",
    "NOTICE.md",
    "README.md",
    "README.zh-CN.md",
    "SECURITY.md",
    "conftest.py",
    "pyproject.toml",
    "research.example.toml",
)

SOURCE_TREES = (
    ".github",
    "deploy",
    "plugins/tcad_artifact",
    "plugins/cov_execution",
    "plugins/curve_score",
    "plugins/curve_figure_evidence",
    "skills/sentaurus-tcad-code",
    "src/scidiscovery",
    "tests",
)

DOCUMENTS = (
    "docs/README.md",
    "docs/NATIVE_TRIAL_LAUNCH.zh-CN.md",
    "docs/ARCHITECTURE.md",
    "docs/ARCHITECTURE.zh-CN.md",
    "docs/PLUGIN_RUNTIME_API.md",
    "docs/TCAD_QUALIFICATION_STATUS.md",
    "docs/TCAD_QUALIFICATION_STATUS.zh-CN.md",
    "docs/INSTALL.md",
    "docs/INSTALL.zh-CN.md",
    "docs/RELEASE.md",
    "docs/RELEASE.zh-CN.md",
    "docs/ROOTLESS_CLI.zh-CN.md",
    "docs/RESEARCH_CONFIGURATION.md",
    "docs/role-result-json-protocol-v1.md",
    "docs/tcad_transport_contract.md",
)

TOOLS = (
    "scripts/build_git_release.py",
    "scripts/run_tests.py",
    "scripts/installed_candidates.py",
    "scripts/probe_native_code_mode.py",
    "scripts/acceptance/rootless_cov_launcher.py",
    "scripts/acceptance/rootless_public_selection_replay.py",
    "scripts/acceptance/behavior_card.py",
    "scripts/acceptance/prelive_gate.py",
    "scripts/prepare_covshift_local.py",
    "scripts/prepare_nbody_local.py",
    "scripts/native_usage_audit.py",
    "scripts/acceptance/fixtures/rootless_selection/selection.json",
    "scripts/acceptance/fixtures/rootless_selection/actions.json",
    "scripts/acceptance/fixtures/search_mvp/native-payloads.json",
    "scripts/acceptance/fixtures/hourly_loop_memory/materials.json",
    "scripts/acceptance/fixtures/covshift_permissions/canary.py",
    "scripts/test_resources.json",
    "scripts/test_resources_ci.json",
    "scripts/compiled_worker_process_guard.py",
    "scripts/evaluate_tcad_skill_ab.py",
    "scripts/evaluate_tcad_skill_ab_holdout.py",
)


IGNORED_NAMES = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "build",
    "dist",
}

IGNORED_SUFFIXES = (
    ".egg-info",
    ".pyc",
    ".pyo",
    ":Zone.Identifier",
)

FORBIDDEN_TEXT = {
    "BEGIN OPENSSH " + "PRIVATE KEY": "private SSH key",
    "BEGIN " + "PRIVATE KEY": "private key",
}

FORBIDDEN_PATTERNS = (
    (
        re.compile(
            r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|"
            r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b"
        ),
        "private live-address pattern",
    ),
    (
        re.compile(
            r"(?:/home/(?!user(?:/|$)|tcad(?:/|$))[A-Za-z0-9._-]+/|"
            r"C:[/\\]Users[/\\](?!user(?:[/\\]|$))[A-Za-z0-9._-]+[/\\])",
            re.IGNORECASE,
        ),
        "machine-specific user home path",
    ),
)

# These release-matched manuals are the only binary source references shipped
# with a release. Pinning both size and digest prevents a modified catalog from
# turning the source-release scanner into a generic binary-file bypass.
BUNDLED_BINARY_REFERENCES = {
    Path("skills/sentaurus-tcad-code/references/manuals/R-2020.09/sprocess_ug.pdf"): (
        10_670_223,
        "bcaf5cfe87bd276a2068fa6681a0b62b2526512b65a710a660d892c45ceaf5d9",
    ),
    Path("skills/sentaurus-tcad-code/references/manuals/R-2020.09/sdevice_ug.pdf"): (
        9_523_113,
        "dae2c94b29c92705d3b8d6124c2f0ab595541ed48e4b220a425f72fac42794ce",
    ),
    Path(
        "skills/sentaurus-tcad-code/references/manuals/R-2020.09/sentaurus_relnote.pdf"
    ): (
        37_980,
        "0e6679154406b2d12356be28ab901a3786f48ca9b91b14f6769ada508bdbfbf9",
    ),
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="source repository root",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dist/scidiscovery-agent"),
        help="clean repository directory",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--init-git", action="store_true")
    return parser.parse_args()


def _ignored(_directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name in IGNORED_NAMES
        or name.endswith(IGNORED_SUFFIXES)
    }


def _copy_path(source: Path, destination: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(f"release source is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, destination, ignore=_ignored)
    else:
        shutil.copy2(source, destination)


def _iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_file() and ".git" not in path.relative_to(root).parts:
            yield path


def _scan_release(root: Path) -> None:
    failures: list[str] = []
    for path in _iter_files(root):
        relative = path.relative_to(root)
        allowed_binary = BUNDLED_BINARY_REFERENCES.get(relative)
        if allowed_binary is not None:
            expected_size, expected_sha256 = allowed_binary
            raw = path.read_bytes()
            if (
                len(raw) != expected_size
                or hashlib.sha256(raw).hexdigest() != expected_sha256
            ):
                failures.append(f"bundled binary reference differs: {relative}")
            continue
        if path.stat().st_size > 10 * 1024 * 1024:
            failures.append(f"oversized source file: {relative}")
            continue
        raw = path.read_bytes()
        if b"\x00" in raw:
            failures.append(f"binary source file: {relative}")
            continue
        text = raw.decode("utf-8")
        for needle, label in FORBIDDEN_TEXT.items():
            if needle in text:
                failures.append(f"{label}: {relative}")
        for pattern, label in FORBIDDEN_PATTERNS:
            if pattern.search(text):
                failures.append(f"{label}: {relative}")
        if relative.suffix.lower() in {".lic", ".pem", ".key"}:
            failures.append(f"credential-like file: {relative}")
    if failures:
        raise RuntimeError("release scan failed:\n  " + "\n  ".join(failures))


def _normalize_modes(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            path.chmod(0o755)
            continue
        executable = path.suffix == ".sh" or path.name in {
            "build_git_release.py",
            "evaluate_tcad_skill_ab.py",
            "evaluate_tcad_skill_ab_holdout.py",
            "manual_extract.py",
            "manual_search.py",
            "validate_deck_project.py",
        }
        path.chmod(0o755 if executable else 0o644)


def _normalize_text(root: Path) -> None:
    """Make the generated repository stable across source worktrees."""
    for path in _iter_files(root):
        raw = path.read_bytes()
        if b"\x00" in raw:
            continue
        text = raw.decode("utf-8")
        lines = [line.rstrip() for line in text.splitlines()]
        while lines and not lines[-1]:
            lines.pop()
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_manifest(root: Path) -> Path:
    manifest = root / "MANIFEST.sha256"
    lines = []
    for path in _iter_files(root):
        if path == manifest:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(root).as_posix()}")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest.chmod(0o644)
    return manifest


def _write_archive(root: Path) -> Path:
    archive = root.parent / f"{root.name}.tar.gz"
    temporary = archive.with_suffix(archive.suffix + ".tmp")
    with temporary.open("wb") as target:
        with gzip.GzipFile(fileobj=target, mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as tar:
                for path in [root, *sorted(root.rglob("*"))]:
                    if ".git" in path.relative_to(root.parent).parts:
                        continue
                    arcname = path.relative_to(root.parent).as_posix()
                    info = tar.gettarinfo(str(path), arcname=arcname)
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = 0
                    if path.is_file():
                        with path.open("rb") as content:
                            tar.addfile(info, content)
                    else:
                        tar.addfile(info)
    os.replace(temporary, archive)
    return archive


def _initialize_git(root: Path) -> None:
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(root)],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    subprocess.run(["git", "-C", str(root), "add", "--all"], check=True)


def main() -> int:
    args = _parse_args()
    source = args.source.expanduser().resolve(strict=True)
    output = args.output.expanduser().absolute()
    if output.exists() or output.is_symlink():
        if not args.force:
            raise FileExistsError(f"output already exists: {output}")
        shutil.rmtree(output)
    output.mkdir(parents=True)

    for relative in ROOT_FILES:
        _copy_path(source / relative, output / relative)
    for relative in SOURCE_TREES:
        _copy_path(source / relative, output / relative)
    for relative in DOCUMENTS:
        _copy_path(source / relative, output / relative)
    for relative in TOOLS:
        _copy_path(source / relative, output / relative)

    _normalize_text(output)
    _normalize_modes(output)
    _scan_release(output)
    manifest = _write_manifest(output)
    archive = _write_archive(output)
    if args.init_git:
        _initialize_git(output)

    files = sum(1 for _ in _iter_files(output))
    print(f"release directory: {output}")
    print(f"archive: {archive}")
    print(f"manifest: {manifest}")
    print(f"tracked source files: {files}")
    print(f"git initialized: {'yes' if args.init_git else 'no'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
