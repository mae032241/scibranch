"""Candidate/material identity consumes already accepted call facts."""
from scidiscovery.research_loop import accepted_prefix, experiments, generic_results


def test_equal_archived_bytes_keep_exact_call_candidate_and_material_identities(tmp_path):
    # The caller supplies accepted call facts and one immutable archive object;
    # these consumers do not grant execution permission or scientific qualification.
    source = tmp_path / 'host-history/objects/shared-observation'
    source.parent.mkdir(parents=True)
    body = b'One retained mechanical observation.\n'
    source.write_bytes(body)
    source.chmod(0o400)
    original = experiments.proof(source)
    calls, materials = [], []
    for call_id in ('call-a', 'call-b'):
        materials.append(dict(source=str(source), size_bytes=len(body),
            path=f'inputs/parents/002/artifacts/experiments/{call_id}/artifacts/observation.txt'))
        calls.append(dict(call_id=call_id, scope=dict(step=2), status='known_success',
            result_protocol=experiments.LOCAL_RESULT_PROTOCOL,
            completed=dict(path=f'calls/{call_id}/completed.json'),
            outputs=[dict(name='observation.txt', path=f'calls/{call_id}/outputs/observation.txt',
                snapshot='host-history/objects/shared-observation', size_bytes=len(body), retention='archived')],
            execution=dict(result=dict(status='available',
                metrics=[dict(name='score', value=.5, direction='max', evaluation_key='mechanical')],
                outputs=[dict(path='artifacts/observation.txt')],
                candidates=[dict(label='Declared observation', metric='score', artifact='artifacts/observation.txt')]))))
    diagnostics = []
    candidates = generic_results.candidates(calls, diagnostics=diagnostics)
    assert len(candidates) == 2 and diagnostics == []
    assert len({candidate['id'] for candidate in candidates}) == 2
    job = dict(role='W', step=2)
    seal = dict(materials=list(reversed(materials)))
    observation = dict(managed_calls=calls)
    bindings = [accepted_prefix._material_binding(tmp_path, job, seal, observation, candidate)
        for candidate in candidates]
    assert [binding['material'] for binding in bindings] == materials
    assert len({binding['implementation_id'] for binding in bindings}) == 2
    assert {binding['material']['source'] for binding in bindings} == {str(source)}
    assert accepted_prefix._material_binding(tmp_path, job, seal, observation,
        dict(candidates[0], id='local-002-unregistered-001')) is None
    assert experiments.proof(source) == original and source.read_bytes() == body
    assert source.stat().st_mode & 0o777 == 0o400
