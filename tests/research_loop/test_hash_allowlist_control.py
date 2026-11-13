"""Exact references do not turn scientific payloads into control digests."""
from pathlib import Path

from scidiscovery.research_loop import candidate_refs, contracts, experiments, runtime


def test_scientific_references_do_not_open_payloads(tmp_path, monkeypatch):
    source = tmp_path / 'large-result.bin'
    source.write_bytes(b'x' * (2 * 1024 * 1024))

    def content_read_forbidden(*args, **kwargs):
        raise AssertionError('A reference must not read scientific content')

    monkeypatch.setattr(Path, 'open', content_read_forbidden)
    monkeypatch.setattr(Path, 'read_bytes', content_read_forbidden)
    monkeypatch.setattr(Path, 'read_text', content_read_forbidden)
    assert contracts.record_ref(source) == {'path': str(source)}
    expected = {'path': str(source), 'size_bytes': 2 * 1024 * 1024}
    assert contracts.file_ref(source) == expected
    proof = experiments.proof(source)
    assert {key: proof[key] for key in expected} == expected
    stat = source.stat()
    assert proof['file_stat'] == [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]


def test_private_control_references_are_absent_from_scientific_projection():
    view = candidate_refs.public_fields(dict(
        step=3, mechanism_ref='H001-M001', candidate_id='local:3:call:model',
        job_ref={'path': '/private/jobs/003.json'},
        raw_ref={'path': '/private/native-evidence/003/science-output.json', 'size_bytes': 12},
        original_source='/private/native-evidence/source.py'),
        {'local:3:call:model': 'W003-C001/model'})
    assert view == dict(step=3, mechanism_ref='H001-M001', candidate_id='W003-C001/model')


def test_process_binding_does_not_follow_another_call(monkeypatch):
    original = dict(path='/instance/records/calls/original/accepted.json')
    other = dict(path='/instance/records/calls/other/accepted.json')
    observed = dict(pid=5, boot_id='boot', start_ticks=10, pgid=5, sid=5,
                    state='S', task_ref=original)
    monkeypatch.setattr(runtime, 'process_identity', lambda pid, ref: dict(observed, task_ref=ref))
    assert runtime.process_matches(observed, original)
    assert not runtime.process_matches(observed, other)
