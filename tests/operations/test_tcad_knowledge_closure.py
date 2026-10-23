from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPOSITORY = Path(__file__).resolve().parents[2]
SKILL = REPOSITORY / "skills/sentaurus-tcad-code"




@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="pdftotext is required")
@pytest.mark.process_e2e
def test_all_manual_topics_have_real_anchors_and_helpers_preserve_skill(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    env = {**os.environ, "TMPDIR": str(scratch), "XDG_CACHE_HOME": str(scratch),
           "PYTHONDONTWRITEBYTECODE": "1"}
    catalog=SKILL/'references/manuals/catalog.json'
    declared=json.loads(catalog.read_text())['manuals']
    originals=[catalog,SKILL/'references/manuals/topics.json',
        *(SKILL/'references/manuals'/item['relative_path'] for item in declared),
        *(SKILL/'scripts'/name for name in ('manual_extract.py','manual_search.py','validate_deck_project.py'))]
    def pins():
        return {str(path):(path.stat().st_dev,path.stat().st_ino,path.stat().st_mode,
            path.stat().st_size,path.stat().st_mtime_ns) for path in originals}
    before=pins()

    def helper(script, *args, expected=0):
        result = subprocess.run([sys.executable, str(SKILL / "scripts" / script), *args],
                                cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
        assert result.returncode == expected, result.stderr
        return json.loads(result.stdout) if expected == 0 else result.stderr

    anchors = {
        "preflight_modes": ("--FastMode", "--syntax"),
        "structure_1d": ("line command", "region command", "init command"),
        "mesh_refinement": ("refinebox", "grid remesh"),
        "custom_conservative_state": ("solution name=CX add !negative !damp solve", "pdbSetString Silicon CX Equation"),
        "equation_callback": ("EquationProc", "UserDiffPreProcess"),
        "diffuse_command": ("diffuse", "[isolve]", "[maxstep=<n>]"),
        "raw_plx_output": ("SetPlxList {Temperature Potential}", "WritePlx T_and_P.plx"),
        "development_modes": ("-i", "Prints the initial solution", "-P <commandfile>"),
    }
    topics = json.loads((SKILL / "references/manuals/topics.json").read_text("utf-8"))["topics"]
    assert {topic["topic"] for topic in topics} == set(anchors)
    for topic in topics:
        common = ("--release", topic["release"], "--solver-kind", topic["solver_kind"])
        found = helper("manual_search.py", *common, "--topic", topic["topic"])
        extract = helper("manual_extract.py", *common, "--start-page", str(found["start_page"]),
                         "--end-page", str(found["end_page"]))
        assert extract["manual_ref"] == found["manual_ref"]
        for anchor in anchors[topic["topic"]]:
            assert anchor in extract["text"], (topic["topic"], anchor)
    common = ("--release", "R-2020.09", "--solver-kind", "sprocess")
    assert helper("manual_search.py", *common, "--query", "solution name=CX")["hits"]
    assert "unavailable" in helper("manual_search.py", *common, "--topic", "unknown", expected=1)
    assert "unavailable" in helper("manual_search.py", "--release", "unknown", "--solver-kind",
                                   "sprocess", "--topic", "structure_1d", expected=1)
    assert "exceeds" in helper("manual_extract.py", *common, "--start-page", "9999",
                               "--end-page", "9999", expected=1)
    assert "positive ordered" in helper("manual_extract.py", *common, "--start-page", "0",
                                       "--end-page", "1", expected=1)
    assert pins() == before
    assert list(scratch.iterdir()) == []
    assert {path.name for path in tmp_path.iterdir()} == {"scratch"}
