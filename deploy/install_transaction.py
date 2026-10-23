#!/usr/bin/env python3
"""Snapshot and restore exact deployment targets for installer rollback."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Sequence


_MANAGED_DIRECTORY_MARKER = ".scidiscovery-managed.json"


def begin_transaction(
    root: Path | str,
    *,
    targets: Sequence[tuple[str, Path | str]],
    databases: Sequence[tuple[str, Path | str]] = (),
) -> None:
    transaction = Path(root).absolute()
    if transaction.exists():
        raise RuntimeError("transaction root already exists")
    transaction.mkdir(parents=True, mode=0o700)
    files_root = transaction / "files"
    databases_root = transaction / "databases"
    files_root.mkdir(mode=0o700)
    databases_root.mkdir(mode=0o700)
    entries: list[dict[str, object]] = []
    names: set[str] = set()
    for name, raw_path in (*targets, *databases):
        _validate_name(name, names)
        names.add(name)
        path = _safe_target(raw_path)
        kind = "sqlite" if (name, raw_path) in databases else "path"
        existed = path.exists() or path.is_symlink()
        entries.append(
            {"name": name, "path": str(path), "kind": kind, "existed": existed}
        )
        if not existed:
            continue
        if kind == "sqlite":
            _backup_sqlite(path, databases_root / f"{name}.sqlite3")
        else:
            _copy_path(path, files_root / name)
    (transaction / "manifest.json").write_bytes(_canonical({
        "schema_version": 1,
        "state": "prepared",
        "entries": entries,
    }))
    os.chmod(transaction / "manifest.json", 0o600)


def rollback_transaction(root: Path | str) -> None:
    transaction = Path(root).absolute()
    manifest = _load_manifest(transaction, expected_state="prepared")
    for entry in reversed(manifest["entries"]):
        path = _safe_target(entry["path"])
        _remove_path(path)
        if entry["kind"] == "sqlite":
            for suffix in ("-wal", "-shm"):
                _remove_path(Path(str(path) + suffix))
        if not entry["existed"]:
            continue
        if entry["kind"] == "sqlite":
            path.parent.mkdir(parents=True, exist_ok=True)
            _copy_file(
                transaction / "databases" / f"{entry['name']}.sqlite3", path
            )
        else:
            _copy_path(transaction / "files" / entry["name"], path)
    manifest["state"] = "rolled_back"
    (transaction / "manifest.json").write_bytes(_canonical(manifest))


def seal_transaction(root: Path | str, destination: Path | str) -> None:
    transaction = Path(root).absolute()
    destination_path = Path(destination).absolute()
    manifest = _load_manifest(transaction, expected_state="prepared")
    if destination_path.exists():
        raise RuntimeError("completed transaction destination already exists")
    manifest["state"] = "complete"
    (transaction / "manifest.json").write_bytes(_canonical(manifest))
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(transaction, destination_path)


def mark_managed_directory(path: Path | str, *, name: str) -> None:
    target = _safe_target(path)
    if not target.is_dir() or target.is_symlink():
        raise RuntimeError("managed directory target is unsafe")
    marker = target / _MANAGED_DIRECTORY_MARKER
    if marker.is_symlink() or marker.exists() and not marker.is_file():
        raise RuntimeError("managed directory ownership marker is unsafe")
    payload = {
        "manager": "scidiscovery-install",
        "name": name,
        "schema_version": 2,
    }
    temporary = marker.with_name(f".{marker.name}.tmp")
    with temporary.open("xb") as stream:
        stream.write(_canonical(payload))
    os.replace(temporary, marker)


def remove_transaction_target(
    root: Path | str,
    *,
    name: str,
    path: Path | str,
    managed_directory_name: str | None = None,
) -> None:
    transaction = Path(root).absolute()
    manifest = _load_manifest(transaction, expected_state="prepared")
    target = _safe_target(path)
    matches = [
        entry
        for entry in manifest["entries"]
        if entry.get("name") == name
        and entry.get("path") == str(target)
        and entry.get("kind") == "path"
    ]
    if len(matches) != 1:
        raise RuntimeError("target is not bound to the active install transaction")
    if managed_directory_name is not None and (target.exists() or target.is_symlink()):
        _verify_managed_directory(target, name=managed_directory_name)
    _remove_path(target)


def _verify_managed_directory(path: Path, *, name: str) -> None:
    if not path.is_dir() or path.is_symlink():
        raise RuntimeError("managed directory ownership is unavailable")
    marker = path / _MANAGED_DIRECTORY_MARKER
    if not marker.is_file() or marker.is_symlink():
        raise RuntimeError("managed directory ownership is unavailable")
    try:
        raw = marker.read_bytes()
        value = json.loads(raw)
    except (OSError, ValueError) as error:
        raise RuntimeError("managed directory ownership is unavailable") from error
    expected = {
        "manager": "scidiscovery-install",
        "name": name,
        "schema_version": 2,
    }
    if value != expected or raw != _canonical(expected):
        raise RuntimeError("managed directory ownership has changed")


def _backup_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(source)
    try:
        target_connection = sqlite3.connect(destination)
        try:
            source_connection.backup(target_connection)
        finally:
            target_connection.close()
    finally:
        source_connection.close()
    shutil.copystat(source, destination)
    _copy_owner(source, destination)


def _copy_path(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        destination.symlink_to(os.readlink(source))
        _copy_owner(source, destination)
    elif source.is_dir():
        shutil.copytree(source, destination, symlinks=True)
        for source_entry in (source, *source.rglob("*")):
            destination_entry = destination / source_entry.relative_to(source)
            _copy_owner(source_entry, destination_entry)
    else:
        _copy_file(source, destination)


def _copy_file(source: Path, destination: Path) -> None:
    shutil.copy2(source, destination, follow_symlinks=False)
    _copy_owner(source, destination)


def _copy_owner(source: Path, destination: Path) -> None:
    metadata = source.lstat()
    os.chown(
        destination,
        metadata.st_uid,
        metadata.st_gid,
        follow_symlinks=False,
    )


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def _safe_target(value: object) -> Path:
    if not isinstance(value, (str, Path)):
        raise TypeError("transaction target must be a path")
    raw = Path(value).expanduser()
    if not raw.is_absolute():
        raise ValueError(f"transaction target must be absolute: {raw}")
    path = raw.absolute()
    if path in {Path("/"), Path("/opt"), Path("/etc"), Path("/var")}:
        raise ValueError(f"unsafe transaction target: {path}")
    return path


def _validate_name(name: str, known: set[str]) -> None:
    if not name or not name.replace("-", "").replace("_", "").isalnum():
        raise ValueError(f"invalid transaction target name: {name}")
    if name in known:
        raise ValueError(f"duplicate transaction target name: {name}")


def _load_manifest(root: Path, *, expected_state: str) -> dict[str, object]:
    raw = (root / "manifest.json").read_bytes()
    value = json.loads(raw)
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "state", "entries"}
        or value.get("schema_version") != 1
        or value.get("state") != expected_state
        or not isinstance(value.get("entries"), list)
        or _canonical(value) != raw
    ):
        raise RuntimeError("install transaction manifest is invalid")
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    begin = subparsers.add_parser("begin")
    begin.add_argument("--root", required=True)
    begin.add_argument("--target", action="append", default=[])
    begin.add_argument("--sqlite", action="append", default=[])
    rollback = subparsers.add_parser("rollback")
    rollback.add_argument("--root", required=True)
    seal = subparsers.add_parser("seal")
    seal.add_argument("--root", required=True)
    seal.add_argument("--destination", required=True)
    mark = subparsers.add_parser("mark-managed-directory")
    mark.add_argument("--path", required=True)
    mark.add_argument("--name", required=True)
    remove = subparsers.add_parser("remove-target")
    remove.add_argument("--root", required=True)
    remove.add_argument("--name", required=True)
    remove.add_argument("--path", required=True)
    remove.add_argument("--managed-directory-name")
    arguments = parser.parse_args(argv)
    if arguments.operation == "begin":
        begin_transaction(
            arguments.root,
            targets=tuple(_parse_assignment(item) for item in arguments.target),
            databases=tuple(_parse_assignment(item) for item in arguments.sqlite),
        )
    elif arguments.operation == "rollback":
        rollback_transaction(arguments.root)
    elif arguments.operation == "seal":
        seal_transaction(arguments.root, arguments.destination)
    elif arguments.operation == "mark-managed-directory":
        mark_managed_directory(arguments.path, name=arguments.name)
    else:
        remove_transaction_target(
            arguments.root,
            name=arguments.name,
            path=arguments.path,
            managed_directory_name=arguments.managed_directory_name,
        )
    return 0


def _parse_assignment(value: str) -> tuple[str, str]:
    name, separator, path = value.partition("=")
    if not separator or not path:
        raise ValueError("transaction target must use name=/absolute/path")
    return name, path


if __name__ == "__main__":
    raise SystemExit(main())
