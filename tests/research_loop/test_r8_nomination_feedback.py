"""Optional nomination advice keeps originals, warnings and the full candidate pool."""
import copy
import json

import pytest

from scidiscovery.research_loop import (accepted_budget, candidate_nominations as nominations,
    contracts as c, history, lineage, ui_components, ui_view)
from execution_fixtures import execution_registry
from test_breadth_science import findings, h, prepare, worker as current_worker
from test_history_handoff import finish


@pytest.fixture
def retained_pool(tmp_path, execution_registry):
    root=prepare(tmp_path)
    h(root,labels=('P','Q'))
    _,config=current_worker(root,tmp_path,2,[1])
    work=c.workspace(root,2)
    directory=work/'experiments/models'
    directory.mkdir(parents=True)
    artifacts=[f'artifacts/model-{index}.bin' for index in range(25)]
    result=dict(version=1,metrics=[],outputs=[dict(path=path) for path in artifacts],
        candidates=[dict(artifact=path,label=f'Retained model {index}') for index,path in enumerate(artifacts)])
    script=work/'code/run.py'
    script.parent.mkdir(exist_ok=True)
    script.write_text('from pathlib import Path\n'
        'for path in '+repr(artifacts)+': Path(path).write_bytes(b"retained fixture model")\n'
        'Path("artifacts/result.json").write_text('+repr(json.dumps(result))+')\n')
    from breadth_execution_fixtures import run as qualified_run
    reply=qualified_run(root,config,script,'h001.m1',directory)
    assert reply['status']=='known_success'
    return root,reply['call_id'],artifacts


def seal_worker(root,value):
    return finish(root,2,'W',[1],candidate_nominations=value,
        mechanism_findings=findings('h001.m1',blocked=('h001.m2',)),preserve_native_intent=True)


def next_views(root):
    job=c.build_job(root,3,'A',[2])
    public=c.science_view(job,root)['lineage']['candidates']['nominations']
    projection=ui_view.candidate_projection(root,3,job=job,history_only=True)
    return public,projection


@pytest.mark.process_e2e
def test_r8_mapping_labels_and_declarations_survive_seal_as_nonblocking_warning(retained_pool):
    root,_,_=retained_pool
    declaration=dict(representatives={'h001.m1':'Retained model 0','h001.m2':'Retained model 1'},
        extras=['Retained model 2'],declarations='artifacts/candidate-nominations.json')
    _,_,record=seal_worker(root,declaration)
    original=(record/'science-output.json').read_bytes()
    assert c.read(record/'science-output.json')['candidate_nominations']==declaration
    assert c.read(record/'result-identity.json')['control_outcome']['worker_delivery']['status']=='partial'
    head=accepted_budget.read(root)['search']
    assert head['candidate_count']==25
    assert not head['table']['nominations']['by_mechanism_ref']
    assert len(head['table']['nominations']['diagnostics'])==3
    public,projection=next_views(root)
    assert public['distinct_candidate_count']==0
    assert public['pending_mechanisms']==['h001.m1','h001.m2']
    assert {row['pointer'] for row in public['diagnostics']}=={
        '/candidate_nominations','/candidate_nominations/representatives','/candidate_nominations/extras'}
    assert all(row['severity']=='warning' and row['source_step']==2 and row['original'].endswith('/002/science.json')
        for row in public['diagnostics'])
    rows=ui_view.candidate_rows(projection)
    assert rows['total']==0 and len(rows['nomination_diagnostics'])==3
    rendered=str(ui_components.candidate_table(rows))
    assert '部分可选提名无法生成预览' in rendered and '/nodes/2/science' in rendered
    assert '/candidate_nominations/representatives' in rendered and '未记录提名' not in rendered
    assert ui_view.candidate_rows(projection,all_candidates=True)['total']==25
    assert (record/'science-output.json').read_bytes()==original


@pytest.mark.process_e2e
@pytest.mark.parametrize('known',[True,False])
def test_typed_locator_resolves_or_warns_without_repair_or_phantom_nomination(retained_pool,known):
    root,call_id,artifacts=retained_pool
    declaration=dict(representatives=[dict(mechanism_ref='h001.m1',
        candidate=dict(call_id=call_id if known else 'unknown-call',artifact=artifacts[0]),
        reason='Authored suitability remains for independent A.')],extras=[],gaps=[])
    _,_,record=seal_worker(root,declaration)
    assert c.read(record/'science-output.json')['candidate_nominations']==declaration
    public,projection=next_views(root)
    assert public['distinct_candidate_count']==int(known)
    assert bool(public['diagnostics']) is not known
    assert len(projection['nominated_ids'])==int(known)
    assert ui_view.candidate_rows(projection,all_candidates=True)['total']==25
    if known:
        assert public['representatives'][0]['candidate_id'].endswith('/'+artifacts[0])
    else:
        assert public['diagnostics'][0]['pointer']=='/candidate_nominations/representatives'


def test_null_and_empty_declarations_keep_existing_per_mechanism_and_extra_semantics():
    declaration=dict(representatives=[dict(mechanism_ref='p',candidate='candidate-p',reason='Authored p')],
        extras=[dict(candidate='candidate-q',reason='Authored alternative')],gaps=[])
    first=dict(step=2,role='W',candidate_nominations=declaration)
    state=dict(mechanisms={'p':dict(status='pending'),'q':dict(status='pending')})
    for later in (None,{},dict(representatives=[],extras=[],gaps=[])):
        before=copy.deepcopy(declaration)
        value=nominations.project([first,dict(step=4,role='W',candidate_nominations=later)],state)
        assert value['representatives']==declaration['representatives']
        assert value['extras']==(declaration['extras'] if later is None else [])
        assert value['source_step']==(2 if later is None else 4)
        assert value['pending_mechanisms']==['q'] and value['diagnostics']==[]
        assert declaration==before


@pytest.mark.parametrize('declaration',['scientific nomination prose',['candidate label'],{'declarations':'do-not-follow.json'}])
def test_unreadable_optional_advice_has_original_pointer_without_file_following(monkeypatch,declaration):
    def forbidden(*args,**kwargs):
        pytest.fail('Optional nomination declarations must not follow authored paths')
    monkeypatch.setattr(c,'read',forbidden)
    job=dict(role='W',step=2,worker_view_version=1,scientific_response_fields=['candidate_nominations'])
    raw=copy.deepcopy(declaration)
    resolved=nominations.resolve(None,job,declaration)
    projected=nominations.project([dict(step=2,role='W',candidate_nominations=c.scientific_field(
        job,dict(candidate_nominations=resolved),'candidate_nominations'))],None)
    warning=nominations.preview(projected,'index.json')['diagnostics'][0]
    assert warning['severity']=='warning' and warning['pointer']=='/candidate_nominations'
    assert warning['original']=='inputs/history/nodes/002/0001.json'
    assert projected['representatives']==projected['extras']==projected['gaps']==[]
    assert declaration==raw


@pytest.mark.parametrize('kind',['representatives','extras','gaps'])
@pytest.mark.parametrize('value',[False,0,'',{}])
def test_present_falsy_unsupported_sections_warn_without_manufacturing_nominees(kind,value):
    projected=nominations.project([dict(step=2,role='W',candidate_nominations={kind:value})],None)
    assert projected['representatives']==projected['extras']==projected['gaps']==[]
    assert [row['pointer'] for row in projected['diagnostics']]==['/candidate_nominations/'+kind]


@pytest.mark.parametrize('value',[{},dict(representatives=None,extras=None,gaps=None),
    dict(representatives=[],extras=[],gaps=[])])
def test_empty_or_null_sections_keep_optional_no_warning_semantics(value):
    assert nominations._preview(value)['diagnostics']==[]


def test_unknown_falsy_declaration_warns_but_authored_notes_do_not_police_valid_entries():
    assert nominations._preview({'declarations':False})['diagnostics']
    declaration=dict(representatives=[dict(mechanism_ref='p',candidate='candidate-p')],
        scientific_note={'authored':'Free scientific content remains open.'})
    assert nominations._preview(declaration)['diagnostics']==[]


def test_compact_lineage_keeps_warning_notice_and_existing_index(monkeypatch):
    nomination=nominations.project([dict(step=2,role='W',candidate_nominations={'extras':['label']})],None)
    preview=nominations.preview(nomination,'inputs/history/breadth-table.json')
    preview['diagnostics'][0]['reason']='Original scientific detail '*200
    value=dict(node_count=0,recent=[],mechanisms=dict(availability='available',count=0,pending_count=0,focus=None),
        candidates=dict(count=0,coverage=None,current=None,a_recommended=None,historical_a_recommendation=None,
            actual=None,candidate_facts=[],candidate_facts_omitted_count=0,nominations=preview))
    monkeypatch.setattr(history,'MAX_BRIEF_BYTES',1200)
    compact=lineage.bounded_brief(value)['candidates']['nominations']
    assert compact['preview_in_index'] and compact['diagnostics_in_index']
    assert compact['diagnostic_count']==1 and compact['index']=='inputs/history/breadth-table.json'
