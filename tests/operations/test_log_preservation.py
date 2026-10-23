from __future__ import annotations

import hashlib
import sys

import pytest

from scidiscovery.artifact_agent.schema.execution import LocalFileDescriptor


def _descriptor(name, path, media):
    raw = path.read_bytes()
    return LocalFileDescriptor(name=name, local_path=str(path), media_type=media,
                               size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


@pytest.mark.parametrize("returncode", [0, 1])
@pytest.mark.process_e2e
def test_command_transport_error_retains_stderr_instead_of_only_message(tmp_path, returncode):
    from tcad_artifact.command_adapter import CommandAdapterConfig, CommandTCADExecutorAdapter
    raw = b"FIRST ERROR\n" + b"trailing detail\n" * 1000
    script=tmp_path/'broken_transport.py'
    script.write_text('import sys,json\nassert json.load(sys.stdin)["operation"]=="capabilities"\n'
        f'sys.stderr.buffer.write({raw!r})\n'
        'print(\'{"schema_version":1,"operation":"capabilities","ok":false,"error":"protocol failure"}\')\n'
        f'raise SystemExit({returncode})\n')
    adapter = CommandTCADExecutorAdapter(CommandAdapterConfig(executable=sys.executable,arguments=(str(script),)), local_result_root=tmp_path)
    with pytest.raises(RuntimeError):
        adapter.capabilities()
    assert next((tmp_path / "logs").glob("*.stderr.log")).read_bytes() == raw
    assert b"protocol failure" in next((tmp_path / "logs").glob("*.stdout.log")).read_bytes()
