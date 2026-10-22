from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scidiscovery.operations import pdf_tool
from scidiscovery.plugin_runtime import collection


def _context(tmp_path, *, remaining=60):
    return SimpleNamespace(workspace=tmp_path, remaining_seconds=remaining,
        input_media_type=lambda _: "application/pdf", input_path=lambda _: tmp_path / "input paper.pdf")


def _request(first=1, last=None, max_chars=131072):
    return SimpleNamespace(name="paper", first_page=first, last_page=last, max_chars=max_chars)


def _stream(monkeypatch, chunks, *, stderr=b"", returncode=0):
    def run(command, **options):
        assert command[:2] == ["pdftotext", "-layout"] and command[-1] == "-"
        assert Path(command[2]).name == "input paper.pdf"
        assert options["input"] == b""
        assert 0 < options["timeout"] <= 60
        assert options["max_output_bytes"] == pdf_tool._STDERR_BYTES + 1
        for chunk in chunks:
            options["sink"](chunk)
        return subprocess.CompletedProcess(command, returncode, b"", stderr)
    monkeypatch.setattr(pdf_tool, "run_bounded", run)


@pytest.mark.parametrize(("chunks", "first", "last", "expected", "total", "selected_last"), [
    ([b"one\ftwo\fthree\f"], 2, 2, "two", 3, 2),
    ([b"one\ftwo\fthree"], 2, None, "two\fthree", 3, 3),
    ([b"one\f\fthree\f"], 2, 2, "", 3, 2),
    ([b"one\f"], 1, 9, "one", 1, 1),
    ([b"one", b"\f", b"\f"], 1, None, "one\f", 2, 2),
    ([b"\xe4", b"\xbd\xa0\f", b"\xe5\xa5", b"\xbd\f"], 2, None, "好", 2, 2),
])
def test_streamed_page_selection_and_exact_document_count(tmp_path, monkeypatch, chunks, first, last,
                                                         expected, total, selected_last):
    _stream(monkeypatch, chunks)
    result = pdf_tool.extract_pdf_text_local(_request(first, last), _context(tmp_path))
    destination = Path(result["local_path"])
    assert destination.read_text() == expected
    assert destination.stat().st_mode & 0o777 == 0o400
    assert result["size_bytes"] == len(expected.encode())
    assert (result["first_page"], result["last_page"], result["available_page_count"]) == (first, selected_last, total)
    assert result["truncated"] is False


@pytest.mark.parametrize("chunks", [[], [b""], [b"\f"], [b"\f", b"\f"]])
def test_empty_document_has_no_text(tmp_path, monkeypatch, chunks):
    _stream(monkeypatch, chunks)
    with pytest.raises(ValueError, match="produced no text"):
        pdf_tool.extract_pdf_text_local(_request(), _context(tmp_path))


def test_page_outside_document_is_rejected(tmp_path, monkeypatch):
    _stream(monkeypatch, [b"one\f"])
    with pytest.raises(ValueError, match="first_page exceeds"):
        pdf_tool.extract_pdf_text_local(_request(2), _context(tmp_path))


def test_selected_excerpt_is_bounded_while_all_pages_are_counted(tmp_path, monkeypatch):
    _stream(monkeypatch, [b"unselected\f", "你好世界".encode(), b"\flast\f"])
    result = pdf_tool.extract_pdf_text_local(_request(2, 2, 2), _context(tmp_path))
    assert Path(result["local_path"]).read_text() == "你好"
    assert result["truncated"] and result["available_page_count"] == 3
    assert result["size_bytes"] == 6


def test_pdf_stderr_file_keeps_first_error_outside_display_tail(tmp_path, monkeypatch):
    raw = b"FIRST ACTIONABLE ERROR\n" + b"trailing noise\n" * 1000
    _stream(monkeypatch, [], stderr=raw, returncode=1)
    with pytest.raises(ValueError, match="stderr log"):
        pdf_tool.extract_pdf_text_local(_request(), _context(tmp_path))
    assert next((tmp_path / ".operation-tools/pdf").glob("stderr_*.log")).read_bytes() == raw


def _producer(monkeypatch, script):
    calls = []
    def run(command, **options):
        calls.append((command, options))
        return collection.run_bounded([sys.executable, "-c", script], **options)
    monkeypatch.setattr(pdf_tool, "run_bounded", run)
    return calls


@pytest.mark.process_e2e
def test_stdout_sink_overflow_terminates_and_reaps_producer(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_tool, "_TEXT_BYTES", 32)
    processes = []
    popen = collection.subprocess.Popen
    def track(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(collection.subprocess, "Popen", track)
    _producer(monkeypatch, "import os,time; os.write(1,b'x'*65536); time.sleep(60)")
    with pytest.raises(ValueError, match="text extraction exceeds"):
        pdf_tool.extract_pdf_text_local(_request(), _context(tmp_path))
    assert len(processes) == 1 and processes[0].poll() is not None
    assert not tuple(tmp_path.rglob("excerpt_*.txt"))


@pytest.mark.process_e2e
def test_stderr_overflow_keeps_bounded_prefix_and_reports_truncation(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_tool, "_STDERR_BYTES", 32)
    calls = _producer(monkeypatch,
        "import os; os.write(2,b'FIRST ACTIONABLE ERROR\\n'+b'x'*10000); os.write(1,b'page\\f')")
    with pytest.raises(ValueError, match="stderr truncated after 32 bytes"):
        pdf_tool.extract_pdf_text_local(_request(), _context(tmp_path))
    logs = tuple(tmp_path.rglob("stderr_*.log"))
    assert len(logs) == 1 and logs[0].read_bytes().startswith(b"FIRST ACTIONABLE ERROR\n")
    assert logs[0].stat().st_size == 32 and calls[0][1]["max_output_bytes"] == 33
    assert not tuple(tmp_path.rglob("excerpt_*.txt"))


@pytest.mark.process_e2e
def test_timeout_preserves_bounded_diagnostics_and_reaps_producer(tmp_path, monkeypatch):
    processes = []
    popen = collection.subprocess.Popen
    def track(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(collection.subprocess, "Popen", track)
    _producer(monkeypatch, "import os,time; os.write(2,b'FIRST ERROR'); time.sleep(60)")
    with pytest.raises(ValueError, match="timed out"):
        pdf_tool.extract_pdf_text_local(_request(), _context(tmp_path, remaining=1))
    assert processes[0].poll() is not None
    assert next(tmp_path.rglob("stderr_*.log")).read_bytes() == b"FIRST ERROR"


@pytest.mark.parametrize("remaining", [0, -1, float("nan"), float("inf")])
def test_invalid_remaining_time_starts_no_process(tmp_path, monkeypatch, remaining):
    monkeypatch.setattr(pdf_tool, "run_bounded", lambda *a, **k: pytest.fail("started a process"))
    with pytest.raises(ValueError, match="finite remaining time"):
        pdf_tool.extract_pdf_text_local(_request(), _context(tmp_path, remaining=remaining))


def test_missing_pdftotext_has_actionable_error(tmp_path, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("pdftotext")
    monkeypatch.setattr(pdf_tool, "run_bounded", missing)
    with pytest.raises(ValueError, match="pdftotext is not installed"):
        pdf_tool.extract_pdf_text_local(_request(), _context(tmp_path))
