"""Fork consumers retain exact candidate evidence without replaying effects."""
import shutil

import pytest

from execution_fixtures import execution_registry
from test_composed_execution_results import composed_registry

from scidiscovery.research_loop import (budget, candidate_refs, contracts as c,
    controller, delivery, fork_service, history, lineage, originals, shortlist)
from test_history_handoff import finish


OBSERVER = '''import json,sys
request=json.load(sys.stdin);step=request['step']
path='project/nodes/002/artifacts/evidence.txt'
candidates=[]
if step in (2,5):
 candidates=[dict(id='original' if step==2 else 'suffix-only',label='Fixture evidence',
  implementation=None,evaluation_key='fixture',metric=1,direction='max',status='ok',
  evidence=[path])]
actual=dict(candidate_id='original',artifact=path,evidence=[path]) if step==3 else None
print(json.dumps(dict(candidates=candidates,current_candidate=None,actual_submission=actual,
 coverage=dict(status='complete',note='Mechanical fixture only'))))
'''


@pytest.fixture
def inherited_candidates(tmp_path):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Keep candidate origins and historical effects separate.')
    observer=tmp_path/'observer.py';observer.write_text(OBSERVER)
    parent=tmp_path/'parent'
    controller.prepare(parent,objective,public,['no-model'],max_tokens=10000,max_seconds=120,
        max_steps=10,model='fixture',reasoning_effort='high',search_policy=None,
        history_observer=dict(adapter=str(observer),config={}))
    for step,role,parents,action in [(1,'W',[],'wait'),(2,'W',[1],'wait'),
            (3,'A',[1,2],'continue_w'),(4,'W',[3],'wait'),(5,'W',[4],'wait'),
            (6,'A',[4,5],'continue_w')]:
        _,_,record=finish(parent,step,role,parents,action=action,
            files={'evidence.txt':'Retained original candidate bytes.\n'} if step==2 else None)
        c.write(record/'native-outcome.json',dict(c.read(record/'native-outcome.json'),native_started=False))
    with controller._writer(parent):
        pass
    child=tmp_path/'child';limits=dict(max_tokens=1000,max_seconds=30,max_steps=2)
    preview=fork_service.preview(parent,4,child,budget=limits)
    fork_service.create(parent,4,child,budget=limits,expected_preview_id=preview['preview_id'],
        operation_id='candidate-consumers')
    owner=originals.node(child,2)
    identity=candidate_refs.origin_candidate(owner.origin_identity,'original')
    # Scientific originals stay direct references into the live parent by design.
    return child,owner,identity


def test_catalogue_and_shortlist_keep_cutoff_and_origin_for_inherited_parent(inherited_candidates,monkeypatch):
    root,_,identity=inherited_candidates
    sources=history.source_sequence(root,4)
    receipts=[dict(step=job['step'],seal_ref=sha) for job,_,_,sha in sources]
    # This test reads a directory; it cannot invoke or qualify an adapter.
    monkeypatch.setattr(delivery,'_adapter',lambda *args: {})
    catalogue=delivery.catalogue_sources(root,receipts)
    assert catalogue['items']==[dict(candidate_id=identity,status='available',source_step=2)]
    assert delivery.rebind_sources(root,dict(source_nodes=receipts,
        items=[dict(candidate_id='original',status='confirmed')],omitted_count=0))==catalogue
    members=shortlist._members(root,dict(source_cutoff=2),before_step=4)
    assert [row['candidate_id'] for row in members['members']]==[identity]
    projection=lineage.project(root,4,source_cutoff=2)
    assert projection['candidates']['actual_submission'] is None
    historical=lineage.project(root,4)['candidates']['actual_submission']
    assert historical['candidate_id']==identity and historical['observed_at']==3
    assert budget.closeout_state(root)['actions']==[]
    assert not (root/'host-delivery/resolved').exists()
    assert not (root/'jobs/002.json').exists()


def test_inherited_delivery_proof_rejects_changed_copied_original(inherited_candidates):
    root,owner,_=inherited_candidates
    job,_,seal,_=history._source(root,2)
    fact=history.read_observation(root,job,seal)
    evidence=fact['candidates'][0]['evidence'][0]
    proof=dict(path=evidence['snapshot'],size_bytes=evidence['size_bytes'],file_stat=evidence['file_stat'])
    path=delivery._proof(root,proof,source=owner)
    assert path.read_text()=='Retained original candidate bytes.\n'
    path.chmod(0o600);path.write_text('Changed copied evidence.\n')
    with pytest.raises(ValueError,match='Original size changed'):
        delivery._proof(root,proof,source=owner)


@pytest.mark.parametrize('foreign_id',[False,True])
def test_adapter_resolution_uses_original_id_and_rejects_foreign_reply(inherited_candidates,monkeypatch,foreign_id):
    root,_,identity=inherited_candidates
    sources=history.source_sequence(root,4)
    receipts=[dict(step=job['step'],seal_ref=sha) for job,_,_,sha in sources]
    settings=c.settings(root)
    monkeypatch.setattr(delivery,'_settings',lambda *args: dict(settings,
        delivery_policy=dict(cleanup_seconds=5)))
    monkeypatch.setattr(delivery,'_adapter',lambda *args,**kwargs: dict(inputs=[],source_roots=[str(root)],readonly_inputs_version=1,
        config={},target={},capabilities=[]))
    def resolve(spec,request,*args,**kwargs):
        assert request['phase']=='resolve'
        assert [row['candidate_id'] for row in request['candidates']]==['original']
        # An unavailable transport response proves identity plumbing only.
        return dict(items=[dict(candidate_id='unbound' if foreign_id else 'original',
            status='unavailable',reason='delivery_source_unavailable')])
    monkeypatch.setattr(delivery,'_call',resolve)
    if foreign_id:
        with pytest.raises(ValueError,match='escaped its exact original instance'):
            delivery.freeze_sources(root,receipts,candidate_id=identity)
    else:
        resolved=delivery.freeze_sources(root,receipts,candidate_id=identity)
        assert resolved['items']==[dict(candidate_id=identity,status='unavailable',reason='delivery_source_unavailable')]
    assert budget.closeout_state(root)['actions']==[]


@pytest.mark.parametrize('value,valid',[
    ('ordinary:parent',True),('x'*80,True),('x'*81,False),
    ('界'*26+'ab',True),('界'*27,False),
    ('source:'+'a'*64+':run-10000',True),
    # Origin keys are goal identifiers now, not lowercase digests: only the raw part is bounded.
    ('source:'+'A'*64+':run-10000',True),
    ('source:'+'A'*64+':'+'x'*81,False),
    ('source:'+'a'*64+':'+'x'*81,False),
])
def test_parent_id_bounds_keep_raw_contract_and_exact_origin_shape(value,valid):
    assert candidate_refs.valid_parent_id(value) is valid
