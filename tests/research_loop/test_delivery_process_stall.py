"""Finite real leaf fault injection; no model, remote service or resubmission."""
import subprocess
import sys

import pytest

from scidiscovery.research_loop import delivery


@pytest.mark.process_e2e
@pytest.mark.parametrize('fault', ['identity', 'receipt', 'read', 'spool'])
def test_post_spawn_fault_reaps_owned_leaf_and_closes_pipes(tmp_path, monkeypatch, fault):
    adapter = tmp_path / 'leaf.py'
    adapter.write_text('import os,time\nwhile True:\n os.write(1,b"output\\n");time.sleep(.01)\n')
    spawned = []
    popen = subprocess.Popen
    def launch(*args, **kwargs):
        process = popen(*args, **kwargs)
        spawned.append(process)
        return process
    monkeypatch.setattr(delivery.subprocess, 'Popen', launch)
    def fail(*args, **kwargs):
        raise OSError('injected ' + fault)
    if fault == 'identity':monkeypatch.setattr(delivery.runtime, 'process_identity', fail)
    temporary = delivery.tempfile.TemporaryFile
    if fault == 'spool':
        class BrokenSpool:
            def __enter__(self):
                self.stream = temporary()
                return self
            def __exit__(self, *args):self.stream.close()
            def write(self, block):fail()
        def open_file(*args, **kwargs):
            return temporary(*args, **kwargs) if kwargs.get('mode') == 'w+' else BrokenSpool()
        monkeypatch.setattr(delivery.tempfile, 'TemporaryFile', open_file)
    def receipt(*args):
        if fault == 'receipt':fail()
        if fault == 'read':monkeypatch.setattr(delivery.os, 'read', fail)
    with pytest.raises(OSError, match='injected ' + fault):
        delivery._call(dict(launcher_argv=[sys.executable], adapter=str(adapter)),
            dict(operation_id='original-operation', phase='promote'), 1, on_process=receipt)
    assert len(spawned) == 1
    process = spawned[0]
    try:
        assert process.wait(timeout=1) is not None
        assert process.stdout.closed and process.stderr.closed
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=2)
