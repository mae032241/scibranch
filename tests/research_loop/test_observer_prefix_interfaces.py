"""Current observer consumers use exact prefix points and retained source bytes.

The component fixture supplies accepted producer facts and stopped inventories;
the real capture, immutable-file and declared-parent consumers remain exercised.
"""
import copy
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import (
    accepted_prefix, accepted_search, candidate_refs, contracts as c, controller, experiments, handoff_state, history,
    originals, protocol, shared_originals,
)


def candidate(identity, *, implementation=None, metric=.5):
    return dict(id=identity, label=identity, implementation=implementation,
        evaluation_key='fixture', metric=metric, direction='max', status='ok',
        evidence=['fact.json'])


def observation(*, candidates=(), current=None, actual=None, contexts=None):
    value=dict(candidates=list(candidates), current_candidate=current, actual_submission=actual,
        coverage=dict(status='complete', note='Exact component observation'))
    if contexts is not None:value['contexts']=contexts
    return value


@pytest.fixture
def capture_case(tmp_path, monkeypatch):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective';objective.write_text('Retain exact observer evidence and limits.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['never-launched'],max_tokens=1000,
        max_seconds=60,max_steps=8,model='fixture',reasoning_effort='high',search_policy=None)
    adapter=tmp_path/'observer.py';adapter.write_text('# Injected read-only adapter transport\n')
    origin=dict(goal_id=c.settings(root)['goal_id'],settings_ref=dict(path=str(root/'settings.json')))
    prefix=accepted_prefix.empty(root,c.settings(root))
    points={};sources={};reads=[];requests=[]
    class Head(dict):
        def row(self,name):
            return next((row for identity,row in points.items()
                if name==accepted_search.key('candidate',identity)),None)
    head=Head(prefix=prefix,search=dict(current_facts={}))

    def member(current,selected,step,*,origin=None):
        assert current==root and selected is prefix
        if step not in sources or step>prefix['cutoff']:
            raise FileNotFoundError('Scientific member is absent from this branch')
        facts=sources[step]
        if origin is not None and origin!=facts['owner'].origin_identity:
            raise ValueError('Scientific member is outside its accepted branch prefix')
        return dict(step=step,origin_identity=facts['owner'].origin_identity,seal_ref=facts['seal_ref'])

    def point(current,selected,identity):
        assert current==root and selected is prefix
        if identity not in points:
            raise ValueError('Candidate reference is outside this frozen scientific task')
        row=points[identity]
        member(current,selected,row['first_step'],origin=row['origin_identity'])
        return row

    def sealed(current,step,*,owner,known=None,**kwargs):
        assert current==root and owner==sources[step]['owner']
        reads.append(step)
        return sources[step]

    monkeypatch.setattr(accepted_prefix,'member',member)
    monkeypatch.setattr(accepted_prefix,'candidate',point)
    def for_origin(current,identity,*,step):
        assert identity==sources[step]['owner'].origin_identity
        return sources[step]['owner']
    monkeypatch.setattr(originals,'for_origin',for_origin)
    monkeypatch.setattr(handoff_state,'sealed_facts',sealed)
    monkeypatch.setattr(handoff_state,'observation_from_facts',lambda current,facts:facts['observation_view'])
    def forbidden(*args,**kwargs):
        raise AssertionError('Observer capture traversed or rebuilt unrelated scientific history')
    monkeypatch.setattr(history,'source_sequence',forbidden)
    monkeypatch.setattr(history,'_source',forbidden)
    monkeypatch.setattr(accepted_prefix,'navigation',forbidden)

    def register(step,value,materials=(),*,origin_identity=None):
        selected_origin=origin if origin_identity is None else origin_identity
        owner=originals.NodeSource(selected_origin,step,str(root),root)
        sources[step]=dict(owner=owner,seal_ref=dict(path=str(root/f'native-evidence/{step:03d}/result-identity.json')),
            seal=dict(materials=list(materials)),observation_view=value)
        prefix.update(tip=dict(step=step,origin_identity=selected_origin),cutoff=step,count=len(sources))
        for index,row in enumerate(value['candidates']):
            points[row['id']]=dict(first_step=step,origin_identity=selected_origin,
                observation={key:item for key,item in row.items() if key!='evidence'})
        for index,row in enumerate(value.get('contexts',[])):
            if row.get('candidate_id') in points:
                points[row['candidate_id']].update(source_step=step,context_pointer=f'/contexts/{index}')

    def capture(step,value,*,version=1):
        job=dict(step=step,role='A',handoff_protocol=protocol.CURRENT,direct_refs=[],
            history=dict(prefix=prefix,inputs=[],view={}))
        record=c.host_record(c.workspace(root,step));record.mkdir(parents=True,exist_ok=True)
        c.write(record/'science-output.json',dict(summary='Stopped original'))
        config=dict(adapter=str(adapter),config={})
        if version==2:config['contract_version']=2
        inventory=c.CurrentInventory(root,job,record,0,[],{},policy=dict(history_observer=config),head=head)
        def transport(config,request):
            requests.append(request)
            return copy.deepcopy(value)
        monkeypatch.setattr(history,'read_adapter',transport)
        ref=history.capture(root,job,{},[],inventory=inventory)
        return c.read(root/ref['path']),job

    return SimpleNamespace(root=root,origin=origin,prefix=prefix,points=points,sources=sources,
        reads=reads,requests=requests,register=register,capture=capture)


def test_historical_implementation_and_existing_candidate_use_one_selected_producer(capture_case):
    case=capture_case;root=case.root
    (root/'fact.json').write_text('exact metadata')
    first,_=case.capture(1,observation(candidates=[candidate('original')]))
    code=c.workspace(root,1)/'artifacts/code.py';code.parent.mkdir(parents=True)
    code.write_text('retained implementation\n');code.chmod(0o400)
    material=dict(path='inputs/parents/001/artifacts/code.py',source=str(code),size_bytes=code.stat().st_size)
    output=shared_originals.publish_bytes(root,b'exact managed artifact')
    proof=dict(history._proof(root,output.relative_to(root).as_posix(),source_kind='managed_frozen'),name='model.bin')
    first['managed_calls']=[dict(call_id='original-call',outputs=[proof])]
    case.register(1,first,[material])
    current=dict(candidate_id='original',evidence=['fact.json'])
    actual=dict(candidate_id='original',artifact=output.relative_to(root).as_posix(),evidence=['fact.json'])
    value,_=case.capture(2,observation(candidates=[candidate('original'),candidate('new',
        implementation='history/nodes/001/artifacts/code.py')],current=current,actual=actual))

    assert [row['id'] for row in value['candidates']]==['new']
    assert value['current_candidate']['candidate_id']=='original'
    assert value['actual_submission']['candidate_id']=='original'
    assert value['actual_submission']['artifact']['retention']=='archived'
    assert value['coverage']['status']=='complete'
    assert case.reads==[1]
    assert 'known_candidates' not in case.requests[-1] and 'known_contexts' not in case.requests[-1]


def test_unknown_and_conflicting_observer_choices_do_not_acquire_prior_identity(capture_case):
    case=capture_case;(case.root/'fact.json').write_text('exact metadata')
    first,_=case.capture(1,observation(candidates=[candidate('original')]))
    case.register(1,first)
    value,_=case.capture(2,observation(candidates=[candidate('original',metric=.9),candidate('valid')],
        current=dict(candidate_id='missing',evidence=['fact.json']),
        actual=dict(candidate_id='missing',artifact='not-read',evidence=['fact.json'])))
    assert [row['id'] for row in value['candidates']]==['valid']
    assert value['current_candidate'] is None and value['actual_submission'] is None
    assert {row['code'] for row in value['candidate_diagnostics']}=={
        'candidate_identity_unresolved','current_candidate_unresolved','actual_submission_unresolved'}
    assert value['coverage']['status']=='partial'
    assert case.points['original']['observation']['metric']==.5


@pytest.mark.parametrize('implementation',[
    'history/nodes/001/artifacts/missing.py','history/nodes/999/artifacts/outside.py'])
def test_unbound_implementation_does_not_drop_other_current_facts(capture_case,implementation):
    case=capture_case;(case.root/'fact.json').write_text('exact metadata')
    first,_=case.capture(1,observation(candidates=[candidate('original')]))
    case.register(1,first)
    value,_=case.capture(2,observation(candidates=[candidate('unbound',implementation=implementation),
        candidate('valid')]))
    assert [row['id'] for row in value['candidates']]==['valid']
    assert value['candidate_diagnostics']==[dict(index=0,code='candidate_implementation_unresolved')]
    assert value['coverage']['status']=='partial' and case.reads==[1]


def test_unchanged_adjacent_metadata_is_copied_once_and_keeps_original_inode(capture_case,monkeypatch):
    case=capture_case;live=case.root/'fact.json';live.write_bytes(b'first')
    retain=shared_originals.retain;copied=[]
    def capture_copy(root,path,**options):
        copied.append(path)
        return retain(root,path,**options)
    monkeypatch.setattr(shared_originals,'retain',capture_copy)
    proofs=[]
    for step in range(1,4):
        value,_=case.capture(step,observation(candidates=[candidate(f'original-{step}')]))
        proofs.append(value['candidates'][0]['evidence'][0])
        case.register(step,value)
    assert proofs==[proofs[0]]*3 and copied==[live]
    retained=case.root/proofs[0]['snapshot']
    assert retained.stat().st_ino!=live.stat().st_ino and live.stat().st_nlink==1
    assert retained.read_bytes()==b'first' and retained.stat().st_mode&0o222==0
    assert case.reads==[1,2]


def test_changed_live_metadata_keeps_both_exact_generations(capture_case):
    case=capture_case;live=case.root/'fact.json';live.write_bytes(b'first')
    first,_=case.capture(1,observation(candidates=[candidate('original')]))
    case.register(1,first)
    replacement=case.root/'replacement';replacement.write_bytes(b'other');replacement.replace(live)
    second,_=case.capture(2,observation(candidates=[candidate('next')]))
    old=first['candidates'][0]['evidence'][0];new=second['candidates'][0]['evidence'][0]
    assert old['snapshot']!=new['snapshot']
    assert (case.root/old['snapshot']).read_bytes()==b'first'
    assert (case.root/new['snapshot']).read_bytes()==b'other'
    assert live.stat().st_nlink==1


def test_corrupt_retained_metadata_is_not_replaced_by_another_copy(capture_case,monkeypatch):
    case=capture_case;(case.root/'fact.json').write_bytes(b'first')
    first,_=case.capture(1,observation(candidates=[candidate('original')]))
    case.register(1,first)
    retained=case.root/first['candidates'][0]['evidence'][0]['snapshot']
    retained.chmod(0o600);retained.write_bytes(b'other')
    def no_copy(*args,**kwargs):
        raise AssertionError('A damaged original cannot be replaced by a new snapshot')
    monkeypatch.setattr(shared_originals,'retain',no_copy)
    with pytest.raises(history.ObserverIntegrityError,match='Archived observer original changed'):
        case.capture(2,observation(candidates=[candidate('next')]))
    assert not (case.root/'host-history/observations/002.json').exists()


def context_scope(root,step):
    reference=dict(path=str(root/f'jobs/{step:03d}.json'))
    return dict(step=step,role='A',handoff_protocol=protocol.CURRENT,job_ref=reference,
        execution_key=reference,mechanism_ref=None)


def context_original(case,step,identity,*,parent=None,parent_status='known_success'):
    directory=case.root/f'component-calls/{step:03d}';directory.mkdir(parents=True)
    scope=context_scope(case.root,step)
    declared=(dict(status='declared_root',candidate_id=None,source='none') if parent is None
        else dict(status='declared',candidate_id=parent['candidate_id'],source='sealed'))
    accepted=dict(call_id=f'call-{step}',scope=scope,parent=declared,change_intent='Exact retained change')
    if parent is not None:
        completed=next(proof for proof in parent['proofs'] if proof['path'].endswith('/completed.json'))
        accepted['parent_binding']=dict(call_id=parent['call_id'],step=parent['scope']['step'],
            completion_ref=dict(path=str(case.root/completed['path']),size_bytes=completed['size_bytes']))
    c.write(directory/'accepted.json',accepted)
    c.write(directory/'completed.json',dict(call_id=accepted['call_id'],status=parent_status))
    return dict(call_id=accepted['call_id'],scope=scope,status=parent_status,candidate_id=identity,
        parent=declared,change_intent=accepted['change_intent'],note='Retained terminal fixture',
        proofs=[dict(path=path.relative_to(case.root).as_posix(),size_bytes=path.stat().st_size)
            for path in (directory/'accepted.json',directory/'completed.json')])


@pytest.mark.parametrize('negative',[None,'unknown_parent','wrong_completion','outside_prefix'])
def test_declared_parent_context_uses_exact_prefix_producer_and_keeps_terminal_gates(
        capture_case,monkeypatch,negative):
    case=capture_case;(case.root/'fact.json').write_text('exact metadata')
    parent=context_original(case,1,'original',parent_status='unknown' if negative=='unknown_parent' else 'known_success')
    parent['proofs']=[history._proof(case.root,row['path']) for row in parent['proofs']]
    first,_=case.capture(1,observation(candidates=[candidate('original')]))
    first['contexts']=[parent]
    case.register(1,first)
    child=context_original(case,2,'child',parent=parent)
    if negative=='wrong_completion':
        path=case.root/'component-calls/002/accepted.json'
        altered=c.read(path);altered['parent_binding']['completion_ref']['path']=str(case.root/'different-original')
        c.write(path,altered);child['proofs'][0]['size_bytes']=path.stat().st_size
    elif negative=='outside_prefix':
        case.points['original']['first_step']=99
    monkeypatch.setattr(protocol,'stage_scope',lambda root,job:context_scope(root,job['step']))
    monkeypatch.setattr(history,'_execution_interface',lambda root,accepted:dict(executor='fixture:context'))
    monkeypatch.setattr(experiments,'registration',lambda executor:(SimpleNamespace(context_validator=None),None))
    authored=observation(candidates=[candidate('child')],contexts=[child])

    if negative is None:
        value,_=case.capture(2,authored,version=2)
        assert value['contexts'][0]['status']=='known_success'
        assert value['contexts'][0]['parent']['candidate_id']=='original'
        assert value['coverage']['status']=='complete'
    else:
        with pytest.raises(history.ObserverIntegrityError,match='Declared parent'):
            case.capture(2,authored,version=2)
        assert not (case.root/'host-history/observations/002.json').exists()
    assert case.reads==[1]


@pytest.mark.parametrize('wrong_owner',[False,True])
def test_latest_context_uses_prefix_origin_separately_from_first_candidate_origin(
        capture_case,monkeypatch,wrong_owner):
    case=capture_case;(case.root/'fact.json').write_text('exact metadata')
    first,_=case.capture(1,observation(candidates=[candidate('original')]))
    case.register(1,first)
    later_origin=dict(goal_id='child-origin',settings_ref=dict(path=str(case.root/'child-settings.json')))
    parent=context_original(case,2,'original')
    parent['proofs']=[history._proof(case.root,row['path']) for row in parent['proofs']]
    case.register(2,observation(contexts=[parent]),origin_identity=later_origin)
    child=context_original(case,3,'child',parent=parent)
    monkeypatch.setattr(protocol,'stage_scope',lambda root,job:context_scope(root,job['step']))
    monkeypatch.setattr(history,'_execution_interface',lambda root,accepted:dict(executor='fixture:context'))
    monkeypatch.setattr(experiments,'registration',lambda executor:(SimpleNamespace(context_validator=None),None))
    if wrong_owner:
        accepted=handoff_state.sealed_facts
        def changed(current,step,**kwargs):
            facts=accepted(current,step,**kwargs)
            return (dict(facts,seal_ref=dict(path='different-origin-seal')) if step==2 else facts)
        monkeypatch.setattr(handoff_state,'sealed_facts',changed)
    authored=observation(candidates=[candidate('child')],contexts=[child])

    if wrong_owner:
        with pytest.raises(history.ObserverIntegrityError,match='source differs from its frozen prefix'):
            case.capture(3,authored,version=2)
        assert not (case.root/'host-history/observations/003.json').exists()
    else:
        value,_=case.capture(3,authored,version=2)
        assert value['contexts'][0]['parent']['candidate_id']=='original'
        assert value['coverage']['status']=='complete'
    assert case.points['original']['origin_identity']==case.origin
    assert case.points['original']['source_step']==2
    assert case.reads==[1,2]


@pytest.mark.parametrize('choice,action,expected',[
    ('original','continue_w','original'),('missing','continue_w',None),
    ('outside','continue_w',None),('original','audit',None),
])
def test_source_record_recommendation_uses_supplied_observations_and_its_frozen_cut(
        capture_case,monkeypatch,choice,action,expected):
    case=capture_case;(case.root/'fact.json').write_text('exact metadata')
    first,_=case.capture(1,observation(candidates=[candidate('original')]))
    case.register(1,first)
    outside=observation(candidates=[candidate('outside')])
    outside['binding']=dict(source_ref=dict(path='outside-raw',size_bytes=0))
    sources=[];facts={}
    for step,observed in ((1,first),(2,outside)):
        worker=dict(step=step,role='W',direct_refs=[],history=dict(prefix=case.prefix))
        seal=dict(materials=[],raw_ref=observed['binding']['source_ref'],observations=dict(path='provided-observation'))
        sources.append((worker,dict(summary='Provided worker',limitations=[]),seal,dict(path=f'worker-seal-{step}')))
        facts[step]=dict(owner=originals.NodeSource(case.origin,step,str(case.root),case.root),observation_view=observed)
    job=dict(step=3,role='A',direct_refs=[],history=dict(prefix=case.prefix,inputs=[],view={}))
    payload=dict(summary='Authored decision',limitations=[],next_action=action,candidate_recommendation=choice)
    seal=dict(materials=[],raw_ref=dict(path='exact-raw',size_bytes=0))
    sources.append((job,payload,seal,dict(path='exact-seal')))
    facts[3]=dict(owner=originals.NodeSource(case.origin,3,str(case.root),case.root))
    forbid_record_reads(monkeypatch)
    rows=history.load_records_from_source_facts(case.root,sources,
        source_facts=facts,write_cache=False,include_details=False)
    assert rows[-1]['recommendation']==expected
    assert case.reads==[]


def forbid_record_reads(monkeypatch):
    def forbidden(*args,**kwargs):
        raise AssertionError('From-facts record projection reopened source or prefix records')
    for module,name in ((c,'read'),(c,'settings'),(c,'regular'),(c,'write'),
            (candidate_refs,'is_available'),(accepted_prefix,'candidate'),(accepted_prefix,'member'),
            (handoff_state,'observation_from_facts'),(originals,'resolve_ref'),(originals,'source_job')):
        monkeypatch.setattr(module,name,forbidden)


def test_record_projection_does_not_import_unlisted_source_facts(capture_case,monkeypatch):
    case=capture_case;(case.root/'fact.json').write_text('exact metadata')
    first,_=case.capture(1,observation(candidates=[candidate('original')]))
    case.register(1,first)
    job=dict(step=2,role='A',direct_refs=[],history=dict(prefix=case.prefix))
    payload=dict(summary='Scoped projection',limitations=[],next_action='continue_w',candidate_recommendation='original')
    facts={1:case.sources[1],2:dict(owner=originals.NodeSource(case.origin,2,str(case.root),case.root))}
    forbid_record_reads(monkeypatch)
    records=history.load_records_from_source_facts(case.root,[(job,payload,
        dict(materials=[],raw_ref=dict(path='raw-a',size_bytes=0)),dict(path='seal-a'))],
        source_facts=facts,write_cache=False,include_details=False)
    assert records[0]['recommendation'] is None


@pytest.mark.parametrize('choice',['raw','canonical','public'])
def test_inherited_recommendation_projects_from_memory_after_original_root_is_removed(
        tmp_path,monkeypatch,choice):
    parent=tmp_path/'removed-parent'
    origin=dict(goal_id='parent',settings_ref=dict(path=str(parent/'settings.json')))
    canonical=candidate_refs.origin_candidate(origin,'run-7')
    prefix=dict(version=1,branch=origin,cutoff=1,count=1,
        visibility_root=dict(path='host-history/accepted-public/parent'))
    worker=dict(step=1,role='W',direct_refs=[],history=dict(prefix=dict(prefix,cutoff=0,count=0)))
    observed=observation(candidates=[dict(candidate('run-7'),id=canonical)])
    observed['binding']=dict(source_ref=dict(path=str(parent/'native-evidence/001/science-output.json'),size_bytes=0))
    first_seal=dict(materials=[],raw_ref=observed['binding']['source_ref'],observations=dict(path='retained-observation'))
    author=dict(step=2,role='A',direct_refs=[],history=dict(prefix=prefix))
    recommendation={'raw':'run-7','canonical':canonical,'public':'W001-C001'}[choice]
    payload=dict(summary='Original authored choice',limitations=[],next_action='continue_w',candidate_recommendation=recommendation)
    sources=[(worker,dict(summary='Retained worker',limitations=[]),first_seal,dict(path='original-worker-seal')),
        (author,payload,dict(materials=[],raw_ref=dict(path='retained-a',size_bytes=0)),dict(path='original-a-seal'))]
    facts={1:dict(owner=originals.NodeSource(origin,1,str(parent),tmp_path/'frozen'),observation_view=observed),
        2:dict(owner=originals.NodeSource(origin,2,str(parent),tmp_path/'frozen'))}
    assert not parent.exists()
    forbid_record_reads(monkeypatch)
    records=history.load_records_from_source_facts(tmp_path/'child',sources,source_facts=facts,
        write_cache=False,include_details=False)
    assert records[-1]['recommendation']==canonical


@pytest.mark.parametrize('choice,expected',[
    ('run-7',None),('canonical','canonical'),('W001-C001','canonical'),
    ('shadow','canonical'),('machine','canonical')])
def test_ambiguous_foreign_raw_ids_need_an_exact_original_or_public_reference(tmp_path,monkeypatch,choice,expected):
    sources=[];facts={};canonical=None
    for step,goal in ((1,'first'),(2,'second')):
        origin=dict(goal_id=goal,settings_ref=dict(path=str(tmp_path/goal/'settings.json')))
        raw='W001-C001' if choice=='shadow' and step==2 else 'run-7'
        identity=('local-001-exact-call-001' if choice=='machine'
            else candidate_refs.origin_candidate(origin,raw))
        if step==1:canonical=identity
        observed=observation(candidates=[dict(candidate(raw),id=identity)])
        if choice=='machine':
            observed['managed_calls']=[dict(call_id='exact-call',scope=dict(step=1),
                execution=dict(result=dict(candidates=[dict(artifact='artifacts/model.bin')])))]
        observed['binding']=dict(source_ref=dict(path=f'raw-{step}',size_bytes=0))
        seal=dict(materials=[],raw_ref=observed['binding']['source_ref'],observations=dict(path=f'obs-{step}'))
        sources.append((dict(step=step,role='W',direct_refs=[]),dict(summary='Retained candidate',limitations=[]),seal,dict(path=f'seal-{step}')))
        facts[step]=dict(owner=originals.NodeSource(origin,step,str(tmp_path/goal),tmp_path/'frozen'),observation_view=observed)
    origin=dict(goal_id='author',settings_ref=dict(path=str(tmp_path/'author/settings.json')))
    job=dict(step=3,role='A',direct_refs=[],history=dict(prefix=dict(version=1,count=2,cutoff=2)))
    value=canonical if choice in {'canonical','machine'} else 'W001-C001' if choice=='shadow' else choice
    sources.append((job,dict(summary='Bounded choice',limitations=[],next_action='continue_w',candidate_recommendation=value),
        dict(materials=[],raw_ref=dict(path='raw-a',size_bytes=0)),dict(path='seal-a')))
    facts[3]=dict(owner=originals.NodeSource(origin,3,str(tmp_path/'author'),tmp_path/'frozen'))
    forbid_record_reads(monkeypatch)
    records=history.load_records_from_source_facts(tmp_path/'child',sources,source_facts=facts,
        write_cache=False,include_details=False)
    assert records[-1]['recommendation']==(canonical if expected=='canonical' else expected)


def test_a_cannot_recommend_a_candidate_first_observed_in_its_own_record(tmp_path,monkeypatch):
    origin=dict(goal_id='author',settings_ref=dict(path=str(tmp_path/'settings.json')))
    observed=observation(candidates=[candidate('self')])
    observed['binding']=dict(source_ref=dict(path='raw-a',size_bytes=0))
    job=dict(step=1,role='A',direct_refs=[],history=dict(prefix=dict(version=1,count=0,cutoff=0)))
    payload=dict(summary='Authored decision',limitations=[],next_action='continue_w',candidate_recommendation='self')
    seal=dict(materials=[],raw_ref=observed['binding']['source_ref'],observations=dict(path='obs-a'))
    facts={1:dict(owner=originals.NodeSource(origin,1,str(tmp_path),tmp_path),observation_view=observed)}
    forbid_record_reads(monkeypatch)
    records=history.load_records_from_source_facts(tmp_path,[(job,payload,seal,dict(path='seal-a'))],
        source_facts=facts,write_cache=False,include_details=False)
    assert records[0]['recommendation'] is None
