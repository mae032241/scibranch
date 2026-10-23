#!/usr/bin/env python3
"""Search one bundled release-matched Sentaurus manual with bounded output."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = SKILL_ROOT / "references" / "manuals" / "catalog.json"
TOPICS_PATH = SKILL_ROOT / "references" / "manuals" / "topics.json"
MAX_SNIPPET_CHARS = 1200


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", required=True)
    parser.add_argument(
        "--solver-kind", required=True, choices=("sprocess", "sdevice", "release_notes")
    )
    lookup = parser.add_mutually_exclusive_group(required=True)
    lookup.add_argument("--query")
    lookup.add_argument("--topic")
    parser.add_argument("--max-hits", type=int, default=8, choices=range(1, 21))
    return parser


def _manual(release: str, solver_kind: str) -> tuple[dict[str, object], Path]:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    matches = [
        item
        for item in catalog["manuals"]
        if item["release"] == release and item["solver_kind"] == solver_kind
    ]
    if len(matches) != 1:
        raise ValueError("exact release-matched manual is unavailable")
    item = matches[0]
    path = CATALOG_PATH.parent / str(item["relative_path"])
    if path.is_symlink() or not path.is_file() or path.stat().st_size != item['size_bytes']:
        raise ValueError("bundled manual original is unavailable")
    return item, path


def _snippet(page: str, index: int) -> str:
    lines = page.splitlines()
    start = max(0, index - 2)
    end = min(len(lines), index + 3)
    value = "\n".join(line.rstrip() for line in lines[start:end]).strip()
    return value[:MAX_SNIPPET_CHARS]


def _topic(release: str, solver_kind: str, topic: str) -> dict[str, object]:
    catalog = json.loads(TOPICS_PATH.read_text(encoding="utf-8"))
    matches = [
        item
        for item in catalog["topics"]
        if item["release"] == release
        and item["solver_kind"] == solver_kind
        and item["topic"] == topic
    ]
    if len(matches) != 1:
        available = sorted(
            str(item["topic"])
            for item in catalog["topics"]
            if item["release"] == release and item["solver_kind"] == solver_kind
        )
        suffix = f"; available topics: {', '.join(available)}" if available else ""
        raise ValueError(f"exact release-matched manual topic is unavailable{suffix}")
    return matches[0]


def _manual_text(pdftotext: str, path: Path) -> bytes:
    return subprocess.run([pdftotext,"-layout",str(path),"-"],check=True,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90).stdout


def main() -> int:
    args = _parser().parse_args()
    if args.query is not None and (
        not args.query.strip() or len(args.query) > 256
    ):
        raise SystemExit("query must contain 1-256 nonblank characters")
    try:
        item, path = _manual(args.release, args.solver_kind)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(str(error)) from error
    if args.topic is not None:
        try:
            topic = _topic(args.release, args.solver_kind, args.topic)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise SystemExit(str(error)) from error
        print(
            json.dumps(
                {
                    "release": item["release"],
                    "solver_kind": item["solver_kind"],
                    "title": item["title"],
                    "manual_ref": {"path":item["relative_path"],"size_bytes":item["size_bytes"]},
                    "topic": topic["topic"],
                    "start_page": topic["start_page"],
                    "end_page": topic["end_page"],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    pdftotext = shutil.which("pdftotext")
    if pdftotext is None:
        raise SystemExit("pdftotext is required for literal manual search")
    pages = _manual_text(pdftotext, path).decode(
        "utf-8", errors="replace"
    ).split("\f")
    needle = args.query.casefold()
    hits: list[dict[str, object]] = []
    for page_number, page in enumerate(pages, start=1):
        for line_number, line in enumerate(page.splitlines(), start=1):
            if needle not in line.casefold():
                continue
            hits.append(
                {
                    "pdf_page": page_number,
                    "line": line_number,
                    "snippet": _snippet(page, line_number - 1),
                }
            )
            if len(hits) >= args.max_hits:
                break
        if len(hits) >= args.max_hits:
            break
    print(
        json.dumps(
            {
                "release": item["release"],
                "solver_kind": item["solver_kind"],
                "title": item["title"],
                "manual_ref": {"path":item["relative_path"],"size_bytes":item["size_bytes"]},
                "query": args.query,
                "hits": hits,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if hits else 1


if __name__ == "__main__":
    sys.exit(main())
