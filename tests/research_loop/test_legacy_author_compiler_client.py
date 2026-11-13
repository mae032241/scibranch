"""Old compiler jobs are refused; no author-client bridge is constructed."""
import pytest

from scidiscovery.research_loop import contracts as c, execution_inputs as inputs, originals, protocol


@pytest.mark.parametrize('reader', ['local', 'original'])
@pytest.mark.parametrize('controls_only', [False, True])
def test_old_job_compiler_is_rejected_before_author_or_permit_reads(tmp_path, monkeypatch, reader, controls_only):
    root = tmp_path / 'instance'
    # Current scientific behavior and material references, but no current job view markers.
    job = dict(step=2, role='W', handoff_protocol=protocol.CURRENT, research_strategy_version=3,
        advisory_scope_version=1, material_refs=[])
    job_path = root / 'jobs/002.json'
    c.write(job_path, job)
    job_ref = dict(path='jobs/002.json', size_bytes=job_path.stat().st_size)
    compiler_path = root / 'native-evidence/002/compiled-implementations/original/compiler.json'
    c.write(compiler_path, dict(version=1, job=job_ref))
    compiler_ref = dict(path=compiler_path.relative_to(root).as_posix(), size_bytes=compiler_path.stat().st_size)
    before = {path: path.read_bytes() for path in (job_path, compiler_path)}
    read = c.read
    def bounded_read(path):
        assert path in before, 'Old compiler reader opened an author/permit/control original'
        return read(path)
    monkeypatch.setattr(c, 'read', bounded_read)
    source = originals.NodeSource(dict(goal_id='old-compiler', settings_ref=dict(path=str(root/'settings.json'))),
        2, str(root), root)
    with pytest.raises(protocol.UnsupportedProtocol, match='Scientific job view'):
        if reader == 'local':
            inputs.verify_compilation(root, compiler_ref, controls_only=controls_only)
        else:
            inputs.verify_original_compilation(source, compiler_ref, controls_only=controls_only)
    assert {path: path.read_bytes() for path in before} == before
