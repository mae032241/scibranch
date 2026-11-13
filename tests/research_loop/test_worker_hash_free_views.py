"""New scientific projections preserve original validation and scientific bytes."""
import json
import re
from pathlib import Path

import pytest

from scidiscovery.research_loop import contracts as c, controller, execution, originals
from test_history_handoff import finish
from test_lineage_view import scene
from test_native_execution_inputs import chain, _reviewed_execution


def assert_no_control_identity(value):
    if isinstance(value,dict):
        assert not any(key in {'sha256','source_ref','model_sha256','job_ref',
            'execution_key','source_h_ref','native_originals','native_originals_index',
            'retirement_proof','originals'} for key in value)
        for item in value.values():assert_no_control_identity(item)
    elif isinstance(value,list):
        for item in value:assert_no_control_identity(item)
    elif isinstance(value,str):
        assert re.search(r'(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])',value) is None


def test_new_scientific_task_keeps_machine_identities_private_and_original_science_exact(tmp_path):
    root=scene(tmp_path)
    science='The sha256 field and hashlib.sha256 algorithm are scientifically relevant.'
    source='import hashlib\nsha256 = hashlib.sha256(b"scientific input").hexdigest()\n'
    _,first,_=finish(root,1,'H',[],text=science,files={'algorithm.py':source})
    job=c.build_job(root,2,'W',[1]);work=c.workspace(root,2)
    assert job['worker_view_version']==1 and c.settings(root)['worker_view_version']==1
    assert_no_control_identity(c.read(work/'science-task.json'))
    material = next(row for row in job['direct_materials'] if row['path'].endswith('/algorithm.py'))
    assert originals.material_path(root,material).read_text()==source
    assert c.read(execution.raw(root,1))['scientific_result']==science
    assert (first/'artifacts/algorithm.py').read_text()==source
    # The host still binds and rejects changed original inputs.
    bound=originals.material_path(root,material)
    bound.chmod(0o600);bound.write_text(source+'# tampered\n')
    with pytest.raises(ValueError):c._validate_inputs(root,work,job)


def test_retained_route_checks_exact_original_parent_and_validates_original_input(tmp_path,monkeypatch):
    root=scene(tmp_path);write=c.write
    def compact_native(path,value):
        if path.name=='science-output.json':
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(value,separators=(',',':')))
        else:write(path,value)
    monkeypatch.setattr(c,'write',compact_native)
    finish(root,1,'H',[],action='experiment')
    job=c.build_job(root,2,'W',[1]);work=c.workspace(root,2)
    assert job['direct_originals'][0]['size_bytes']==execution.raw(root,1).stat().st_size
    assert controller._check_job(root,c.settings(root),2,'W',[1],'',None)==job
    c._validate_inputs(root,work,job)
    with pytest.raises(ValueError,match='sealed scientific route'):
        controller._check_job(root,c.settings(root),2,'W',[],'',None)
    original=execution.raw(root,1)
    original.chmod(0o600);original.write_text('{}')
    # Namespace aliases are supplied at launch; the host validates the bound
    # scientific original rather than recreating the retired conclusion copy.
    with pytest.raises(ValueError):c._validate_inputs(root,work,job)


def test_public_replay_diagnostic_keeps_private_originals_out_of_science(tmp_path):
    from scidiscovery.research_loop import replay_check
    directory=tmp_path/'call';directory.mkdir()
    c.write(directory/'accepted.json',{});c.write(directory/'completed.json',{})
    c.write(directory/replay_check.INTENT,dict(accepted_ref=c.record_ref(directory/'accepted.json'),
        completed_ref=c.record_ref(directory/'completed.json')))
    c.write(directory/replay_check.OUTCOME,dict(intent_ref=c.record_ref(directory/replay_check.INTENT),result=dict(
        status='dependency_error',note='dependency_unavailable_in_declared_sample_scope',stage='source_import',
        cleanup_confirmed=True,diagnostic='/private/host-runtime/'+'a'*64+'/source.py: missing dependency')))
    original=(directory/replay_check.OUTCOME).read_bytes()
    accepted=dict(engineering_check=dict(capability=dict(policy=dict(check_scope={})),deadline_boottime=1))
    public=replay_check._preview(directory,accepted,scientific=True)
    assert public['diagnostic']['present'] is True and public['status']=='dependency_error'
    assert '/private/' not in json.dumps(public) and 'a'*64 not in json.dumps(public)
    assert (directory/replay_check.OUTCOME).read_bytes()==original


@pytest.mark.parametrize('damage',['package','client'])
@pytest.mark.process_e2e
def test_compiled_subject_review_reads_science_and_preserves_exact_private_binding(chain,capsys,damage):
    from scidiscovery.research_loop import execution_inputs,formal_review
    root,_,output,_,_,_=_reviewed_execution(chain,capsys)
    job=c.read(root/'jobs/004.json');public=formal_review.public(root,job)
    from scidiscovery.research_loop import handoff_state
    source=originals.node(root,4)
    inputs=handoff_state.material_inputs(root,job,sources={},source=source)
    materials={row['path']:row for row in [*inputs['direct_materials'],*inputs['global_materials']]}
    from scidiscovery.research_loop import shared_input_views
    resolved=dict(job,direct_materials=inputs['direct_materials'],selected_inputs=inputs.get('selected_inputs',[]))
    facts=shared_input_views.load_source_facts(root,resolved,c.workspace(root,4))
    mappings=shared_input_views.materialize_selected_code(root,shared_input_views.selected_code_from_facts(facts))
    reviewed=next(source for source,target,_,_ in mappings
        if target==c.workspace(root,4)/public['review']['scientific_implementation'])
    assert reviewed.read_bytes()==chain[5].read_bytes()
    assert public['review']['scientific_inputs']
    assert originals.material_path(root,materials[public['review']['scientific_inputs'][0]]).read_bytes()==b'mesh'
    seal=c.read(execution.origin(root,2)/'result-identity.json');entry=seal['compiled_implementations'][0]
    material=entry['material'];package=execution_inputs.compiled_payload(root,material)
    assert package['path']!=material['source']
    target={'package':Path(package['path']),'client':output}[damage]
    target.chmod(0o600);target.write_bytes(target.read_bytes()+b' ')
    with pytest.raises(ValueError):execution_inputs.compiled_payload(root,material)
