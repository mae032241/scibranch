#!/usr/bin/env python3
"""Extract a bounded page interval from one bundled Sentaurus manual."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys

from manual_search import _manual


MAX_PAGES = 20
MAX_OUTPUT_BYTES = 128 * 1024


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", required=True)
    parser.add_argument(
        "--solver-kind", required=True, choices=("sprocess", "sdevice", "release_notes")
    )
    parser.add_argument("--start-page", required=True, type=int)
    parser.add_argument("--end-page", required=True, type=int)
    args = parser.parse_args()
    if (
        args.start_page < 1
        or args.end_page < args.start_page
        or args.end_page - args.start_page + 1 > MAX_PAGES
    ):
        raise SystemExit("page interval must contain 1-20 positive ordered pages")
    pdftotext = shutil.which("pdftotext")
    if pdftotext is None:
        raise SystemExit("pdftotext is required for manual extraction")
    try:
        item, path = _manual(args.release, args.solver_kind)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(str(error)) from error
    if args.end_page > int(item["pages"]):
        raise SystemExit("page interval exceeds the manual page count")
    completed = subprocess.run(
        [
            pdftotext,
            "-f",
            str(args.start_page),
            "-l",
            str(args.end_page),
            "-layout",
            str(path),
            "-",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    if len(completed.stdout) > MAX_OUTPUT_BYTES:
        raise SystemExit("manual extract exceeds its output bound")
    print(
        json.dumps(
            {
                "release": item["release"],
                "solver_kind": item["solver_kind"],
                "title": item["title"],
                "manual_ref": {"path":item["relative_path"],"size_bytes":item["size_bytes"]},
                "start_page": args.start_page,
                "end_page": args.end_page,
                "text": completed.stdout.decode("utf-8", errors="replace"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
