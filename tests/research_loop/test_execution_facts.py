"""Pure bounded execution navigation and its stopped-owner publication seam."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import checkpoint, contracts as c, execution_facts as facts


def fact(call_id='0001-fixture', status='known_success', *, result=None, outputs=()):
    accepted=dict(call_id=call_id,native_attempt=dict(number=2),
        code_version=dict(commit='a'*40,entry='/private/secret.py',logical_root='/private/token'))
    observed=dict(public_result=result) if result is not None else None
    return facts.accepted_fact(call_id=call_id,status=status,accepted=accepted,observed=observed,
        outputs=list(outputs),observation_basis='accepted_terminal' if observed is not None else 'accepted_call')


def view(rows, *, node='W002', coverage='accepted_scope', omissions=0, refs=None):
    return facts.render(facts=rows,scope=dict(kind='stopped_worker',node=node,attempt=2),
        public_refs={} if refs is None else refs,coverage=coverage,omitted_calls=omissions)


def test_pure_view_separates_execution_result_and_scientific_validity(monkeypatch):
    rows=[fact(result=dict(status='known_success',result=dict(status='missing'))),
        fact('0002-failed','known_failed',outputs=['accepted diagnostic']),
        fact('0003-unknown','unknown'),fact('0004-retired','not_submitted')]
    def forbidden(*args,**kwargs):pytest.fail('Pure execution rendering touched the filesystem')
    for method in ('open','read_text','read_bytes','stat','resolve'):
        monkeypatch.setattr(Path,method,forbidden)
    rendered=view(rows)
    assert rendered['status_counts']['known_success']==1
    assert rendered['status_counts']['known_failed']==1
    assert rendered['unresolved_calls']==1
    assert rendered['calls'][0]['scientific_result']=='missing'
    assert rendered['calls'][1]['scientific_result']=='available'
    assert rendered['calls'][2]['observation_basis']=='accepted_call'
    assert 'not scientific validity' in rendered['interpretation']
    encoded=json.dumps(rendered)
    assert '/private' not in encoded and 'secret.py' not in encoded and 'token' not in encoded
    assert rendered['calls'][0]['selected_code']['commit']=='a'*40


def test_explicit_unknown_coverage_and_public_navigation():
    rendered=view([fact()],coverage='partial',omissions=None,refs={'0001-fixture':dict(result=[
        '/private/result.json','inputs/../secret','work/retained-experiments/0001-fixture/outputs/result.json',
        'work/sessions/private','https://host/token'])})
    assert rendered['coverage']=='partial' and rendered['additional_calls'] is None
    assert rendered['calls'][0]['navigation']==dict(result=['work/retained-experiments/0001-fixture/outputs/result.json'])
    assert rendered['calls'][0]['navigation_availability']=='public_locator'
    assert view([fact()])['calls'][0]['navigation_availability']=='unavailable'
    with pytest.raises(TypeError):facts.render(scope={},public_refs={},coverage='unknown')
    with pytest.raises(ValueError):view([fact(),fact()])


def test_actual_json_bytes_bound_rows_without_dropping_status_counts():
    rows=[fact(f'{index:04d}-fixture','known_failed' if index%2 else 'unknown') for index in range(200)]
    aliases={row['call_id']:dict(result=['work/retained-experiments/'+row['call_id']+'/outputs/'+'中文'+chr(34)*60]) for row in rows}
    rendered=view(rows,refs=aliases)
    assert len(facts.serialized_bytes(rendered))<=4096
    assert rendered['observed_calls']==200 and rendered['unresolved_calls']==100
    assert rendered['omitted_rows']==200-len(rendered['calls'])>0
    assert rendered['status_counts']['known_failed']==100
    assert rendered==view(rows,refs=aliases)


def test_cumulative_prefix_preserves_older_failure_and_unknown_missing_history():
    first=view([fact('0001-failed','known_failed')],node='W001')
    second=view([fact('0001-success','known_success')],node='W002')
    combined=facts.combine(first,second,scope=dict(kind='accepted_sources',node='current'),
        source_cutoff=2,node_count=2,navigation=['inputs/history/index.json'])
    assert combined['status_counts']['known_failed']==combined['status_counts']['known_success']==1
    assert {row['node'] for row in combined['calls']}=={'W001','W002'}
    assert combined['counts_are']=='exact_within_accepted_scope'
    missing=facts.combine(None,second,scope=dict(kind='accepted_sources',node='current'),
        source_cutoff=2,node_count=2,navigation=[])
    assert missing['coverage']=='unknown' and missing['additional_calls'] is None
    assert missing['counts_are']=='lower_bound'
    assert missing['status_counts']['known_success']==1
    many=first
    for index in range(2,35):
        many=facts.combine(many,view([fact(f'{index:04d}-call','unknown')],node=f'W{index:03d}'),
            scope=dict(kind='accepted_sources',node='current'),source_cutoff=index,node_count=index,navigation=[])
    assert many['status_counts']['known_failed']==1 and many['unresolved_calls']==33
    assert many['omitted_rows']+len(many['calls'])==34
    assert len(facts.serialized_bytes(many))<=4096


def test_checkpoint_capture_freezes_brief_from_loaded_metadata(tmp_path,monkeypatch):
    root=tmp_path/'instance';record=root/'native-evidence/002';record.mkdir(parents=True)
    job=dict(role='W',step=2,history={})
    call=dict(call_id='0001-pending',status='unknown')
    inventory=SimpleNamespace(root=root,job=job,record=record,attempt=2,task_ref=dict(path='original-task'),
        managed_calls=[call],managed_call_metadata={'0001-pending':dict(accepted=dict(native_attempt=dict(number=2)))},
        scientific_outputs={},native_outcome=None,science_error=None,payload=None,
        execution_fact_view=view([fact('0001-pending','unknown')]))
    monkeypatch.setattr(c,'_inventory_identity',lambda *args:None)
    def forbidden(*args,**kwargs):pytest.fail('Checkpoint consumer reopened an accepted record')
    monkeypatch.setattr(c,'read',forbidden)
    saved=checkpoint._capture(root,job,record,inventory=inventory)
    assert saved['resume_brief']['execution_facts']['unresolved_calls']==1
    expected=deepcopy(saved['resume_brief'])
    monkeypatch.setattr(checkpoint,'_science_for_inventory',forbidden)
    monkeypatch.setattr(Path,'read_text',forbidden)
    assert checkpoint.brief(checkpoint=saved)==expected
    assert len(checkpoint._bytes(expected))<=4096
    saved['resume_brief']['execution_facts']['calls'].clear()
    assert expected['execution_facts']['calls']


def test_fragment_brief_bounds_multibyte_escaped_author_claims_and_execution_rows():
    rows=[fact(f'{index:04d}-call','unknown') for index in range(60)]
    value=dict(science=dict(status='present'),materials=[],omitted_materials=0,
        material_status='observed',missing_native_originals=[])
    science=dict(summary='中文\\\"'*1000,scientific_result='中文\\\"'*4000,
        limitations=['中文\\\"'*200]*40,stage_intent='decision')
    brief=checkpoint._make_brief(dict(history={}),value,science=science,execution_view=view(rows))
    assert len(checkpoint._bytes(brief))<=4096
    assert brief['execution_facts']['unresolved_calls']==60
    assert brief['truncated']
    assert brief['execution_facts']['omitted_rows']+len(brief['execution_facts']['calls'])==60


def test_result_locators_require_exact_published_slots_and_do_not_cross_context(tmp_path):
    work=tmp_path/'worktree';call_id='0001-fixture'
    alias=f'work/retained-experiments/{call_id}/outputs/result.json'
    output=work/alias;output.parent.mkdir(parents=True);output.write_text('accepted retained output')
    accepted_outputs=[dict(name='result.json',path=str(output))]
    slots=[dict(name='result.json',source=str(output),path=alias)]
    locators=facts.output_refs(observed_outputs=accepted_outputs,published_slots=slots)
    assert locators==[alias]
    current=view([fact(outputs=accepted_outputs)],refs={call_id:dict(result=locators)})
    assert current['calls'][0]['scientific_result']=='available'
    assert current['calls'][0]['result_access']=='directly_readable'
    value=dict(science=dict(status='missing'),materials=[],omitted_materials=0,
        material_status='observed',missing_native_originals=[])
    frozen=checkpoint._make_brief(dict(history={}),value,science=None,execution_view=current)
    saved=checkpoint.brief(checkpoint=dict(resume_brief=frozen))
    public=saved['execution_facts']['calls'][0]['navigation']['result'][0]
    assert (work/public).read_text()=='accepted retained output'
    # Accepted descriptors elsewhere, including real private files, do not gain
    # a readable public alias merely by sharing a scientific filename.
    wrong=[dict(name='result.json',path=str(tmp_path/'private/result.json'))]
    assert facts.output_refs(observed_outputs=wrong,published_slots=slots)==[]
    logical=[dict(name='result.json',path='history/nodes/002/artifacts/experiments/0001-fixture/artifacts/result.json')]
    assert facts.output_refs(observed_outputs=logical,published_slots=slots)==[]
    assert facts.output_refs(observed_outputs=logical,published_slots=[dict(name='result.json',
        source=logical[0]['path'],path=logical[0]['path'])])==[]
    assert facts.output_refs(observed_outputs=[],published_slots=slots)==[]
    unavailable=view([fact(outputs=wrong)],refs={call_id:dict(result=[])} )
    assert unavailable['calls'][0]['scientific_result']=='available'
    assert unavailable['calls'][0]['result_access']=='not_directly_readable'
    cumulative=facts.combine(None,current,scope=dict(kind='accepted_sources',node='current'),
        source_cutoff=2,node_count=1,navigation=['inputs/history/nodes/002/index.json'])
    row=cumulative['calls'][0]
    assert row['scientific_result']=='available' and row['navigation']=={}
    assert row['navigation_availability']=='unavailable'
    assert row['result_access']=='not_directly_readable'
    assert alias not in json.dumps(cumulative)
    assert cumulative['navigation']==['inputs/history/nodes/002/index.json']


@pytest.mark.parametrize('entry,retained',[
    ('run.py',True),('src/domain/run.py',True),('src/domain model.py',True),
    ('/private/root/run.py',False),('private/run.py',False),
    ('sessions/attempt/run.py',False),('host-runtime/bindings/run.py',False),
    ('../run.py',False),('./run.py',False),('src/../run.py',False),('src//run.py',False),
    ('src\\run.py',False),('C:/Users/run.py',False),('https://host/run.py',False),
    ('src/run.py?token=secret',False),('src/%2e%2e/run.py',False),
    ('src/run.py'+chr(0),False),('界'*180+'.py',False),
])
def test_selected_code_retains_only_safe_relative_captured_entry(entry,retained):
    accepted=dict(call_id='0001-entry',code_version=dict(commit='b'*40,entry=entry))
    item=facts.accepted_fact(call_id='0001-entry',status='unknown',accepted=accepted,
        observed=None,outputs=None,observation_basis='accepted_call')
    projected=view([item])['calls'][0]['selected_code']
    assert projected['commit']=='b'*40
    assert projected['scope']=='accepted selected code only'
    if retained:assert projected['entry']==entry
    else:assert 'entry' not in projected


@pytest.mark.parametrize('status',sorted(facts.PENDING_EXECUTION_STATES))
def test_exact_pending_execution_states_are_unresolved_without_io(status,monkeypatch):
    accepted=dict(call_id='0001-pending',native_attempt=dict(number=0))
    def forbidden(*args,**kwargs):pytest.fail('Pending execution facts performed filesystem IO')
    for method in ('open','read_text','read_bytes','stat','resolve'):
        monkeypatch.setattr(Path,method,forbidden)
    item=facts.accepted_fact(call_id='0001-pending',status=status,accepted=accepted,
        observed=None,outputs=None,observation_basis='current_observation',native_state=status)
    current=view([item])
    assert current['calls'][0]['status']==current['calls'][0]['native_state']==status
    assert current['status_counts'][status]==1 and current['unresolved_calls']==1
    cumulative=facts.combine(None,current,scope=dict(kind='accepted_sources',node='current'),
        source_cutoff=2,node_count=1,navigation=[])
    assert cumulative['status_counts'][status]==1 and cumulative['unresolved_calls']==1
