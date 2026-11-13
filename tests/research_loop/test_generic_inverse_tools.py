"""Generic task execution/result/artifact handoff, using real CLIs and no models."""
from pathlib import Path
import shutil
import sys
import pytest

from scidiscovery.research_loop import contracts as c, controller, delivery, generic_delivery


@pytest.mark.parametrize('declaration', [None, 'explicit', 'missing-metric', 'missing-direction', 'missing-evaluation', 'artifact-only', 'missing-output', 'unhashable'])
def test_generic_candidate_requires_exact_retained_artifact_without_scientific_ranking(tmp_path, declaration):
    from scidiscovery.research_loop.local_experiments import LocalExecution
    from scidiscovery.research_loop import generic_results
    value = dict(version=1, metrics=[dict(name='quality', value=1, evaluation_key='public/v1', direction='max'),
        dict(name='cost', value=2, evaluation_key='cost/v1', direction='min')],
        outputs=[dict(path='artifacts/product.bin', label='Declared product')])
    if declaration is not None:
        value['candidates'] = [dict(label='Explicit method', metric='quality', artifact='artifacts/product.bin')]
        if declaration == 'missing-metric':value['candidates'][0]['metric'] = 'unstated'
        if declaration == 'missing-direction':value['metrics'][0].pop('direction')
        if declaration == 'missing-evaluation':value['metrics'][0].pop('evaluation_key')
        if declaration == 'artifact-only':value['candidates'] = [dict(artifact='artifacts/product.bin')]
        if declaration == 'missing-output':value['candidates'][0]['artifact'] = 'artifacts/unstated.bin'
        if declaration == 'unhashable':value['candidates'][0]['metric'] = []
    c.write(tmp_path / 'result.json', value)
    result = LocalExecution.read_result(tmp_path, [dict(name='product.bin')])
    assert result['status'] == 'available'
    assert result['metrics']==value['metrics'] and result['outputs']==value['outputs']
    row = dict(call_id='call-1', scope=dict(step=2, goal='original'), status='known_success',result_protocol='scidiscovery.local-result.v1',
        completed=dict(retention='archived'), execution=dict(result=result), outputs=[dict(name='product.bin')])
    candidates = generic_results.candidates([row])
    if declaration in {None,'missing-output'}:
        assert candidates == []
    else:
        candidate=candidates[0]
        assert len(candidates)==1
        if declaration in {'missing-metric','artifact-only','unhashable'}:
            assert candidate['metric'] is candidate['direction'] is candidate['evaluation_key'] is None
        else:
            assert candidate['metric']==1
            assert candidate['direction']==(None if declaration=='missing-direction' else 'max')
            assert candidate['evaluation_key']==(None if declaration=='missing-evaluation' else 'public/v1')
        assert generic_results.identity(row, value['candidates'][0]) == candidate['id']
        assert generic_results.candidates([dict(row, status='known_failed')]) == []


def test_generic_adapter_missing_required_audit_is_rejected_before_instance_creation(tmp_path):
    public = tmp_path / 'public';public.mkdir()
    objective = public / 'objective.md';objective.write_text('Explicit required scientific audit')
    target = tmp_path / 'target';target.mkdir()
    root = tmp_path / 'instance'
    policy = dict(closeout_a=dict(max_observed_tokens=100, max_seconds=10), inflight_tokens=0,
        cleanup_seconds=5, submit_seconds=10, confirm_seconds=5, adapter=generic_delivery.descriptor(target))
    with pytest.raises(ValueError, match='required.*no audit capability'):
        controller.prepare(root, objective, public, ['never-run'], max_tokens=1000, max_seconds=60, max_steps=5,
            model='no-model', reasoning_effort='high', search_policy=None, experiment_mode='local',
            node_budget_policy={role: dict(max_observed_tokens=100, max_seconds=10) for role in 'HWAE'},
            delivery_policy=policy, candidate_audit_policy=dict(version=1))
    assert not root.exists() and list(target.iterdir()) == []


@pytest.mark.process_e2e
def test_generic_isolated_delivery_keeps_large_manifest_and_all_objects(tmp_path):
    from scidiscovery.research_loop import protocol
    if shutil.which('bwrap') is None:pytest.skip('Actual namespace required')
    python = Path(sys.executable)
    stage=tmp_path/'stage';inputs=stage/'inputs';inputs.mkdir(parents=True)
    target=tmp_path/'target';target.mkdir()
    private = [tmp_path/'private-control/settings.json', tmp_path/'.codex/auth.json']
    for path in private:
        path.parent.mkdir(parents=True, exist_ok=True);path.write_text('private fixture; never mounted')
    source=inputs/'scid-current-protocol'
    source.write_text(Path(protocol.__file__).read_text() +
        '\nimport ssl\nfrom pathlib import Path\n' +
        'assert all(not Path(path).exists() for path in ' + repr([str(path) for path in private]) + ')\n')
    artifact=tmp_path/'original-artifact';artifact.write_bytes(b'exact managed artifact')
    (inputs/'artifact').write_bytes(b'')
    objects={'artifact':c.file_ref(artifact)}
    for index in range(150):
        name='outputs/'+str(index)+'x'*170
        item=tmp_path/'originals'/name;item.parent.mkdir(parents=True,exist_ok=True);item.write_text('exact output '+str(index))
        mount=inputs/name;mount.parent.mkdir(parents=True,exist_ok=True);mount.write_bytes(b'')
        objects[name]=c.file_ref(item)
    spec=generic_delivery.descriptor(target, python=str(python))
    assert set(spec['config']['runtime_roots']) == {sys.base_prefix,sys.prefix}
    request=dict(phase='promote',scope=dict(handoff_protocol=protocol.CURRENT),stage=str(stage),
        operation_id='e'*64,candidate_id='local-explicit',artifact_ref=objects['artifact'],
        config=spec['config'],target=spec['target'],object_refs=objects,
        context=dict(kind='managed_local_result',retained_metadata='x'*280000),
        inputs=[dict(label='scid-current-protocol',size_bytes=source.stat().st_size),
            dict(label='runtime-python',invoked_path=str(python.absolute()),path=str(python.resolve()))])
    evidence=tmp_path/'delivery-evidence'
    promoted=delivery._call(spec,request,10,evidence=evidence)
    diagnostic=evidence/(request['operation_id']+'-promote-1.stderr')
    assert promoted is not None and promoted['status']=='confirmed', (promoted,
        diagnostic.read_bytes()[-8192:].decode(errors='replace') if diagnostic.is_file() else 'no leaf stderr')
    saved=target/request['operation_id'];manifest=saved/'manifest.json'
    assert manifest.stat().st_size>256000
    assert c.read(manifest)['objects']==objects
    assert all((saved/name).read_bytes()==Path(reference['path']).read_bytes() for name,reference in objects.items())
    repeated=delivery._call(spec,dict(request,phase='confirm'),10)
    assert repeated['status']=='confirmed'
    (saved/next(name for name in objects if name.startswith('outputs/'))).write_text('tampered')
    assert delivery._call(spec,dict(request,phase='confirm'),10)['status']=='unknown'
