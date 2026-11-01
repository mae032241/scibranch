"""Accepted unfinished originals, bounded reads and the existing one-tail redo."""
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import accepted_budget as accepted, budget
from scidiscovery.research_loop import contracts as c, delivery
from scidiscovery.research_loop import accepted_search, execution_recovery, managed_wait, originals
from check_accepted_budget_head import prepare


def test_audit_intent_tail_is_readable_before_control_files_and_head(tmp_path, monkeypatch):
    root = prepare(tmp_path)
    operation = 'a'*64
    path = root/'native-evidence/001/candidate-audit/intent.json'
    intent = dict(operation_id=operation, original='accepted-before-effects')
    def interrupted(*args):
        raise OSError('fixture stopped after ledger before originals/head')
    with monkeypatch.context() as patch:
        patch.setattr(accepted, '_install_indexes', interrupted)
        with pytest.raises(OSError, match='before originals/head'):
            accepted.accept_audit(root,step=1,operation_id=operation,path=path,intent=intent)
        before = (root/accepted.HEAD).read_bytes()
        assert not path.exists()
        head = accepted.read(root)
        assert head['active_audit'] == dict(step=1,operation_id=operation)
        assert head['audit_count'] == 1
        assert head.control_file(path) == intent
        assert not path.exists() and (root/accepted.HEAD).read_bytes() == before
    with budget._ledger_writer(root):
        accepted.recover(root,head)
    assert c.read(path) == intent
    accepted.accept_audit(root,step=1,operation_id=operation,path=path,intent=intent)
    retry = root/'host-delivery/audit-recovery'/operation/'intent.json'
    accepted.accept_audit(root,step=1,operation_id=operation,path=retry,intent=intent)
    accepted.accept_audit(root,step=1,operation_id=operation,path=retry,intent=intent)
    assert accepted.read(root)['audit_count'] == 2
    assert budget.used_steps(root) == 2


def test_preparation_queries_use_last_and_unresolved_original_only(tmp_path, monkeypatch):
    root = prepare(tmp_path)
    delivery.preparation_failure(root,'delivery_cleanup_unknown',[],candidate_id='original')
    for _ in range(8):
        delivery.preparation_failure(root,'delivery_source_missing',[],candidate_id='latest')
    head = accepted.read(root)
    summary = head['delivery_preparation']
    permitted = {root/summary[key]['path'] for key in ('last_ref','unresolved_ref')}
    read = c.read
    def bounded(path):
        path = Path(path)
        if path.parent == root/'host-delivery/preparation':
            assert path in permitted, 'A query reopened completed preparation history'
        return read(path)
    def no_enumeration(*args, **kwargs):
        pytest.fail('A query enumerated completed history')
    monkeypatch.setattr(c,'read',bounded)
    monkeypatch.setattr(Path,'glob',no_enumeration)
    monkeypatch.setattr(Path,'iterdir',no_enumeration)
    assert delivery.preparation_status(root) == dict(attempts=9,last_failure='delivery_source_missing',
        candidate_id='latest',cleanup_confirmed=True)
    assert delivery.preparation_blocked(root)


def test_preparation_tail_query_reads_redo_without_writing(tmp_path, monkeypatch):
    root = prepare(tmp_path)
    def interrupted(*args):
        raise OSError('fixture preparation tail')
    monkeypatch.setattr(accepted,'_install_indexes',interrupted)
    with pytest.raises(OSError,match='preparation tail'):
        delivery.preparation_failure(root,'delivery_cleanup_unknown',[],candidate_id='original')
    original = root/'host-delivery/preparation/001.json'
    before = (root/accepted.HEAD).read_bytes()
    assert delivery.preparation_blocked(root)
    assert delivery.preparation_status(root)['attempts'] == 1
    assert not original.exists() and (root/accepted.HEAD).read_bytes() == before


@pytest.mark.parametrize('damage',['old_version','missing_active_audit','missing_preparation'])
def test_missing_required_pending_head_state_is_rejected(tmp_path, damage):
    root = prepare(tmp_path)
    value = c.read(root/accepted.HEAD)
    if damage == 'old_version':value['version'] = 1
    else:value.pop('active_audit' if damage == 'missing_active_audit' else 'delivery_preparation')
    c.write(root/accepted.HEAD,value)
    with pytest.raises(ValueError,match='Unsupported accepted budget state'):
        accepted.read(root)


def _accept_facts(root, *, node, call=None, search_updates=None):
    """Control-only accepted fixtures; no provider execution or qualification."""
    head = accepted.read(root)
    summary = copy.deepcopy(head['search'])
    summary.update(copy.deepcopy(search_updates or {}))
    rows = {accepted_search.key('node',node['step']):node}
    if call is not None:
        rows[accepted_search.key('call',f'{call["source_step"]}:{call["call_id"]}')] = call
    summary['current_facts'].update(rows)
    with budget._ledger_writer(root):
        _, state = accepted.append(root,head,kind='execution_recovery',
            updates=dict(search=summary),rows=rows)
    return state


def _node(root, *, calls=(), unavailable=False, origin=None):
    return dict(step=1,role='W',pending_executions=None,
        pending_calls=[dict(call_id=call,recovery_ref=None) for call in calls],
        pending_namespace_unavailable=unavailable,
        origin_identity=origin or accepted.read(root).prefix['branch'])


def _forbid_discovery(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Pending query discovered historical originals')
    monkeypatch.setattr(Path,'glob',forbidden)
    monkeypatch.setattr(Path,'iterdir',forbidden)
    monkeypatch.setattr(execution_recovery,'prefix',forbidden)


def test_empty_pending_queries_ignore_completed_jobs_seals_and_waits(tmp_path, monkeypatch):
    root = prepare(tmp_path)
    completed = set()
    for step in range(1,33):
        for name in (f'jobs/{step:03d}.json',f'native-evidence/{step:03d}/result-identity.json',
                f'native-evidence/{step:03d}/execution-wait.json'):
            path = root/name
            c.write(path,dict(completed=True))
            completed.add(path)
    read, stat = c.read, Path.stat
    def current_only(path):
        assert Path(path) not in completed
        return read(path)
    def bounded_stat(path,*args,**kwargs):
        assert path not in completed
        return stat(path,*args,**kwargs)
    monkeypatch.setattr(c,'read',current_only)
    monkeypatch.setattr(Path,'stat',bounded_stat)
    _forbid_discovery(monkeypatch)
    assert not execution_recovery.pending(root)
    assert not managed_wait.pending(root)


@pytest.mark.parametrize('kind',['possibly_prepared','host_preparation','namespace_unavailable'])
def test_current_owner_retains_prevalidated_pending_control_metadata(tmp_path, monkeypatch, kind):
    root = prepare(tmp_path)
    job = c.build_job(root,1,'W',[])
    record = c.host_record(c.workspace(root,1))
    pending = dict(allocations=[],host_preparations=[],complete=True)
    if kind == 'possibly_prepared':
        pending['allocations'] = [dict(call_id='original',disposition=kind),
            dict(call_id='never-prepared',disposition='unprepared')]
    elif kind == 'host_preparation':
        path = root/'retained-host-preparation.json'
        c.write(path,dict(call_id='original'))
        pending['host_preparations'] = [dict(path=path.relative_to(root).as_posix())]
    else:
        pending.update(complete=False,unavailable_reason='Original namespace unavailable')
    path = record/'pending-execution-set.json'
    c.write(path,pending)
    reference = dict(path=path.relative_to(root).as_posix())
    head = accepted.read(root)
    # The reducer receives already validated controls from the seal owner. This
    # unit deliberately does not claim preparation admission or scientific facts.
    delta = accepted_search.prepare_node(root,job,{},dict(pending_executions=reference),
        summary=head['search'],lookup=lambda key:head.row(key))
    node = delta['rows'][accepted_search.key('node',1)]
    assert node['pending_executions'] == reference
    assert node['pending_namespace_unavailable'] == (kind == 'namespace_unavailable')
    assert node['pending_calls'] == ([] if kind == 'namespace_unavailable' else
        [dict(call_id='original',recovery_ref=None)])
    with budget._ledger_writer(root):
        accepted.append(root,head,kind='execution_recovery',
            updates=dict(search=delta['summary']),rows=delta['rows'])
    _forbid_discovery(monkeypatch)
    if kind == 'namespace_unavailable':
        with pytest.raises(ValueError,match='namespace was unavailable'):
            execution_recovery.pending(root)
    else:
        assert execution_recovery.pending(root)
        assert execution_recovery.pending(root,unknown_only=True)
        assert not execution_recovery.pending(root,ignore=(1,'original'))


def test_resume_intent_without_managed_row_is_accepted_before_original_file(tmp_path, monkeypatch):
    root = prepare(tmp_path)
    head = _accept_facts(root,node=_node(root))
    before_usage, before_prefix = copy.deepcopy(head['usage']), copy.deepcopy(head.prefix)
    call = 'original'
    reference = dict(path=f'{execution_recovery.DIRECTORY}/001/{call}/000001.json')
    value = dict(version=2,source_step=1,call_id=call,phase='resume_intent',row=None,native_state=None)
    def interrupted(*args):
        raise OSError('fixture recovery original/head tail')
    with monkeypatch.context() as patch:
        patch.setattr(accepted,'_install_indexes',interrupted)
        with pytest.raises(OSError,match='original/head tail'):
            execution_recovery._publish_fact(root,head,1,call,reference,value)
        before = (root/accepted.HEAD).read_bytes()
        assert not (root/reference['path']).exists()
        _forbid_discovery(patch)
        assert execution_recovery.pending(root)
        assert execution_recovery.pending(root,unknown_only=True)
        assert not execution_recovery.pending(root,ignore=(1,call))
        assert execution_recovery.latest(root,1,call) == value
        assert not (root/reference['path']).exists() and (root/accepted.HEAD).read_bytes() == before
    with budget._ledger_writer(root):
        accepted.recover(root,accepted.read(root))
    assert c.read(root/reference['path']) == value
    assert (root/reference['path']).stat().st_mode & 0o777 == 0o400
    head = accepted.read(root)
    running = dict(value,phase='status',row=dict(call_id=call,status='unknown'),native_state='running')
    running_ref = dict(path=f'{execution_recovery.DIRECTORY}/001/{call}/000002.json')
    execution_recovery._publish_fact(root,head,1,call,running_ref,running)
    assert execution_recovery.pending(root) and not execution_recovery.pending(root,unknown_only=True)
    head = accepted.read(root)
    terminal = dict(value,phase='terminal',row=dict(call_id=call,status='known_failed'))
    terminal_ref = dict(path=f'{execution_recovery.DIRECTORY}/001/{call}/000003.json')
    execution_recovery._publish_fact(root,head,1,call,terminal_ref,terminal)
    assert not execution_recovery.pending(root)
    head = accepted.read(root)
    assert head['usage'] == before_usage and head.prefix == before_prefix
    assert head['search']['opportunity_unknown_count'] == 0
    assert accepted_search.key('call',f'1:{call}') not in head['search']['current_facts']


def test_unknown_original_recovery_reads_its_exact_point_and_preserves_accounting(tmp_path, monkeypatch):
    root = prepare(tmp_path)
    call = dict(step=1,source_step=1,call_id='original',status='unknown',mechanism_binding=None,
        origin_identity=accepted.read(root).prefix['branch'],observation_ref=dict(path='original-observation.json'),
        call_index=0,recovery_ref=None)
    head = _accept_facts(root,node=_node(root,calls=['original']),call=call)
    value = dict(source_step=1,old_row=call,row=dict(call_id='original',status='known_failed',mechanism_binding=None))
    reference = dict(path=f'{execution_recovery.DIRECTORY}/001/original.json')
    execution_recovery._publish_fact(root,head,1,'original',reference,value)
    assert (root/reference['path']).stat().st_mode & 0o777 == 0o400
    unrelated = root/execution_recovery.DIRECTORY/'999/unrelated.json'
    c.write(unrelated,dict(unrelated=True))
    read, stat = c.read, Path.stat
    def bounded(path):
        assert Path(path) != unrelated
        return read(path)
    def bounded_stat(path,*args,**kwargs):
        assert path != unrelated
        return stat(path,*args,**kwargs)
    monkeypatch.setattr(c,'read',bounded)
    monkeypatch.setattr(Path,'stat',bounded_stat)
    _forbid_discovery(monkeypatch)
    assert not execution_recovery.pending(root)
    current = accepted.read(root)
    assert current.row(accepted_search.key('call','1:original'))['status'] == 'known_failed'
    assert current['usage'] == head['usage'] and current.prefix == head.prefix


@pytest.mark.parametrize('invalid',[False,True])
def test_legacy_recovery_validates_readonly_original_before_accepting_head(tmp_path, monkeypatch, invalid):
    root = prepare(tmp_path)
    call = dict(step=1,source_step=1,call_id='original',status='unknown',mechanism_binding=None,
        origin_identity=accepted.read(root).prefix['branch'],observation_ref=dict(path='original-observation.json'),
        call_index=0,recovery_ref=None)
    head = _accept_facts(root,node=_node(root,calls=['original']),call=call)
    old = dict(call_id='original',status='unknown',mechanism_binding=None,
        accepted=dict(snapshot='original-accepted.json'))
    recovered = dict(old,status='known_failed')
    c.write(root/'original-accepted.json',dict(original=True))
    c.write(root/'original-observation.json',dict(managed_calls=[old]))
    seal = dict(observations=dict(path='original-observation.json'))
    monkeypatch.setattr(originals,'inherited',lambda *_:False)
    monkeypatch.setattr(execution_recovery.history,'_source',lambda *_:(dict(role='W',step=1),None,seal,'seal'))
    monkeypatch.setattr(execution_recovery.history,'managed_calls',lambda *_:[recovered])
    monkeypatch.setattr(managed_wait,'_config',lambda *_:(None,{}))
    monkeypatch.setattr(execution_recovery.e,'Records',lambda _:SimpleNamespace(directory=lambda _:root/'records'))
    monkeypatch.setattr(execution_recovery.e,'retirement',lambda *_:dict(original='permanent fact'))
    before = (root/accepted.HEAD).read_bytes()
    path = root/execution_recovery.DIRECTORY/'001/original.json'
    checked = []
    def validate(actual_root,refs):
        assert actual_root == root and refs == [dict(path=path.relative_to(root).as_posix())]
        assert path.stat().st_mode & 0o777 == 0o400
        value = c.read(path)
        assert value == dict(source_step=1,seal_ref='seal',old_row=old,row=recovered)
        assert (root/accepted.HEAD).read_bytes() == before
        checked.append(value)
        if invalid:
            raise execution_recovery.history.ObserverIntegrityError('fixture invalid original recovery')
        return [value]
    monkeypatch.setattr(execution_recovery,'validate',validate)
    if invalid:
        with pytest.raises(execution_recovery.history.ObserverIntegrityError,match='invalid original recovery'):
            execution_recovery.create(root,1)
        assert (root/accepted.HEAD).read_bytes() == before
        assert accepted.read(root).row('execution-recovery/001/original') is None
    else:
        assert execution_recovery.create(root,1)['status'] == 'execution_recovered'
        current = accepted.read(root)
        assert current.row('execution-recovery/001/original')['ref']['path'] == path.relative_to(root).as_posix()
        assert current.row(accepted_search.key('call','1:original'))['status'] == 'known_failed'
        assert current['usage'] == head['usage'] and current.prefix == head.prefix
    assert len(checked) == 1


def _consumed_original(root, *, status='unknown'):
    head = accepted.read(root)
    call = dict(step=1,source_step=1,call_id='original',status=status,
        mechanism_binding=dict(mechanism_ref='h001.m1'),origin_identity=head.prefix['branch'],
        observation_ref=dict(path='original-observation.json'),call_index=0,recovery_ref=None)
    table = copy.deepcopy(head['search']['table'])
    table['mechanisms']['h001.m1'] = dict(status='pending',accepted_calls=1)
    table['ordinals'] = ['h001.m1']
    return call, _accept_facts(root,node=_node(root,calls=['original']),call=call,
        search_updates=dict(table=table,mechanism_count=1,pending_count=1,owed_count=0,
            opportunity_unknown_count=int(status == 'unknown'),
            extra_grant=dict(mechanism_ref='h001.m1',source_a=1,used_calls=1)))


@pytest.mark.parametrize('status',['known_failed','not_submitted'])
def test_created_original_terminal_can_resume_idempotently_without_dispatch(tmp_path, monkeypatch, status):
    from scidiscovery.research_loop import execution_authority, execution_resume
    root = prepare(tmp_path)
    call, _ = _consumed_original(root)
    job = dict(role='W',step=1)
    scope = dict(step=1,job_ref=dict(path=str(root/'jobs/001.json')))
    interface = dict(executor='fixture')
    config = dict(scope=scope,execution_interface=interface)
    record = root/'records'
    old = dict(call_id='original',status='unknown',scope=scope,
        mechanism_binding=copy.deepcopy(call['mechanism_binding']),accepted=dict(snapshot='original-accepted.json'))
    recovered = dict(old,status=status)
    c.write(root/'original-accepted.json',dict(original=True))
    c.write(root/'original-observation.json',dict(managed_calls=[old]))
    c.write(record/'experiment-config.json',config)
    allocation = root/'execution-authority/calls/original/allocation.json'
    c.write(allocation,dict(binding=dict(native_attempt=dict(number=0),interface=interface,scope=scope)))
    seal = dict(observations=dict(path='original-observation.json'))
    monkeypatch.setattr(originals,'inherited',lambda *_:False)
    monkeypatch.setattr(execution_recovery.history,'_source',lambda *_:(job,None,seal,'seal'))
    monkeypatch.setattr(execution_recovery.history,'managed_calls',lambda *_:[recovered])
    checked = []
    def validate_managed(actual_root,actual_job,rows,*,verify_files):
        assert actual_root == root and actual_job == job and rows == [recovered]
        assert not verify_files
        checked.append(rows)
    monkeypatch.setattr(execution_recovery.history,'_validate_managed',validate_managed)
    monkeypatch.setattr(managed_wait,'_config',lambda *_:(None,config))
    monkeypatch.setattr(execution_recovery.e,'Records',lambda _:SimpleNamespace(directory=lambda _:record))
    monkeypatch.setattr(execution_recovery.e,'retirement',lambda *_:dict(original='permanent fact'))
    assert execution_recovery.create(root,1)['status'] == 'execution_recovered'
    assert checked == [[recovered]]
    path = root/execution_recovery.DIRECTORY/'001/original.json'
    frozen = path.read_bytes()
    original = c.read(path)
    assert original == dict(source_step=1,seal_ref='seal',old_row=old,row=recovered)
    assert path.stat().st_mode & 0o777 == 0o400
    phase = 'not_submitted' if status == 'not_submitted' else 'terminal'
    assert execution_recovery.latest(root,1,'original') == dict(original,phase=phase,native_state=None)
    def pending(actual_root,actual_seal,actual_allocation,*,require_complete):
        assert actual_root == root and actual_seal == seal and not require_complete
        assert actual_allocation['path'] == allocation.relative_to(root).as_posix()
        return dict(disposition='possibly_prepared')
    def forbidden(*args, **kwargs):
        pytest.fail('A retained original terminal entered execution admission or dispatch')
    monkeypatch.setattr(execution_resume,'verify_pending_set',pending)
    monkeypatch.setattr(execution_resume.execution,'record',lambda *_:record)
    monkeypatch.setattr(execution_authority,'_host_context',lambda _:(root,job,None))
    monkeypatch.setattr(execution_resume,'_RecoveryAdmission',forbidden)
    monkeypatch.setattr(execution_recovery.e,'load',forbidden)
    before = (root/accepted.HEAD).read_bytes()
    for _ in range(2):
        result = execution_resume.resume(root,1,'original',max_seconds=1,authorization_note='fixture')
        assert result['status'] == 'execution_resume_recorded' and result['call_id'] == 'original'
    assert path.read_bytes() == frozen
    assert (root/accepted.HEAD).read_bytes() == before
    current = accepted.read(root)
    assert current.row('execution-recovery/001/original')['ref']['path'] == path.relative_to(root).as_posix()
    assert current.row(accepted_search.key('call','1:original'))['status'] == status
    assert current['search']['table']['mechanisms']['h001.m1']['accepted_calls'] == 1


@pytest.mark.parametrize('slot',['first','next'])
def test_unaccepted_recovery_snapshot_can_retry_changed_same_call_fact(tmp_path, monkeypatch, slot):
    root = prepare(tmp_path)
    call, head = _consumed_original(root)
    row = dict(call_id='original',status='unknown',mechanism_binding=copy.deepcopy(call['mechanism_binding']))
    value = dict(version=2,source_step=1,call_id='original',phase='status',row=row,native_state='running')
    first = dict(path=f'{execution_recovery.DIRECTORY}/001/original/000001.json')
    if slot == 'next':
        execution_recovery._publish_fact(root,head,1,'original',first,value)
        head = accepted.read(root)
    number = '000002' if slot == 'next' else '000001'
    reference = dict(path=f'{execution_recovery.DIRECTORY}/001/original/{number}.json')
    snapshot = root/execution_recovery.DIRECTORY/f'001/original/snapshots/{number}.json'
    before_point = head.row('execution-recovery/001/original')
    before = (root/accepted.HEAD).read_bytes()
    def interrupted(*args, **kwargs):
        assert snapshot.exists()
        raise OSError('fixture snapshot before ledger')
    with monkeypatch.context() as patch:
        patch.setattr(accepted,'publish_recovery',interrupted)
        with pytest.raises(OSError,match='snapshot before ledger'):
            execution_recovery._publish_fact(root,head,1,'original',reference,value)
    draft = snapshot.read_bytes()
    assert (root/accepted.HEAD).read_bytes() == before
    assert accepted.read(root).row('execution-recovery/001/original') == before_point
    assert not (root/reference['path']).exists()
    terminal = dict(value,phase='terminal',row=dict(row,status='known_failed'),native_state=None)
    execution_recovery._publish_fact(root,accepted.read(root),1,'original',reference,terminal)
    current = accepted.read(root)
    assert snapshot.read_bytes() != draft
    assert snapshot.stat().st_mode & 0o777 == 0o400
    assert current['search_ref']['path'] == snapshot.relative_to(root).as_posix()
    assert c.read(snapshot) == current['search']
    assert execution_recovery.latest(root,1,'original') == terminal
    recovered = current.row(accepted_search.key('call','1:original'))
    assert recovered['status'] == 'known_failed'
    assert recovered['origin_identity'] == call['origin_identity']
    assert current['usage'] == head['usage'] and current.prefix == head.prefix
    assert current['search']['table']['mechanisms']['h001.m1']['accepted_calls'] == 1
    assert current['search']['extra_grant'] == head['search']['extra_grant']
    assert current['search']['opportunity_unknown_count'] == 0


@pytest.mark.parametrize('accepted_state',['head','tail','historical'])
def test_accepted_recovery_snapshot_cannot_be_replaced(tmp_path, monkeypatch, accepted_state):
    root = prepare(tmp_path)
    call, head = _consumed_original(root)
    row = dict(call_id='original',status='unknown',mechanism_binding=copy.deepcopy(call['mechanism_binding']))
    value = dict(version=2,source_step=1,call_id='original',phase='status',row=row,native_state='running')
    reference = dict(path=f'{execution_recovery.DIRECTORY}/001/original/000001.json')
    snapshot = root/execution_recovery.DIRECTORY/'001/original/snapshots/000001.json'
    if accepted_state == 'tail':
        def interrupted(*args):
            raise OSError('fixture accepted snapshot tail')
        with monkeypatch.context() as patch:
            patch.setattr(accepted,'_install_indexes',interrupted)
            with pytest.raises(OSError,match='snapshot tail'):
                execution_recovery._publish_fact(root,head,1,'original',reference,value)
    else:
        execution_recovery._publish_fact(root,head,1,'original',reference,value)
    terminal = dict(value,phase='terminal',row=dict(row,status='known_failed'),native_state=None)
    if accepted_state == 'historical':
        later = dict(path=f'{execution_recovery.DIRECTORY}/001/original/000002.json')
        execution_recovery._publish_fact(root,accepted.read(root),1,'original',later,terminal)
    current = accepted.read(root)
    before, frozen = (root/accepted.HEAD).read_bytes(), snapshot.read_bytes()
    point = current.row('execution-recovery/001/original')
    message = ('outside the next unaccepted same-call slot' if accepted_state == 'historical'
        else 'Accepted execution recovery original changed')
    with pytest.raises(ValueError,match=message):
        execution_recovery._publish_fact(root,head,1,'original',reference,terminal)
    assert snapshot.read_bytes() == frozen
    assert (root/accepted.HEAD).read_bytes() == before
    unchanged = accepted.read(root)
    assert unchanged.row('execution-recovery/001/original') == point
    assert unchanged['usage'] == head['usage'] and unchanged.prefix == head.prefix
    assert unchanged['search']['table']['mechanisms']['h001.m1']['accepted_calls'] == 1
    if accepted_state == 'tail':
        assert unchanged.pending is not None
        assert not (root/reference['path']).exists()
    else:
        assert c.read(root/reference['path']) == value


@pytest.mark.parametrize('accepted_state',['head','tail'])
def test_same_accepted_recovery_ref_reuses_exact_original_without_reducing_again(tmp_path, monkeypatch, accepted_state):
    root = prepare(tmp_path)
    _, head = _consumed_original(root)
    row = dict(call_id='original',status='unknown',mechanism_binding=dict(mechanism_ref='h001.m1'))
    value = dict(version=2,source_step=1,call_id='original',phase='status',row=row,native_state='running')
    reference = dict(path=f'{execution_recovery.DIRECTORY}/001/original/000001.json')
    snapshot = root/execution_recovery.DIRECTORY/'001/original/snapshots/000001.json'
    if accepted_state == 'tail':
        def interrupted(*args):
            raise OSError('fixture accepted snapshot tail')
        with monkeypatch.context() as patch:
            patch.setattr(accepted,'_install_indexes',interrupted)
            with pytest.raises(OSError,match='snapshot tail'):
                execution_recovery._publish_fact(root,head,1,'original',reference,value)
    else:
        execution_recovery._publish_fact(root,head,1,'original',reference,value)
    before, frozen = (root/accepted.HEAD).read_bytes(), snapshot.read_bytes()
    def forbidden(*args, **kwargs):
        pytest.fail('An accepted recovery ref was reduced or published again')
    monkeypatch.setattr(accepted_search,'prepare_recovery',forbidden)
    monkeypatch.setattr(accepted,'publish_recovery',forbidden)
    execution_recovery._publish_fact(root,head,1,'original',reference,copy.deepcopy(value))
    with pytest.raises(ValueError,match='Accepted execution recovery original changed'):
        execution_recovery._publish_fact(root,head,1,'original',reference,dict(value,native_state='unknown'))
    assert snapshot.read_bytes() == frozen
    assert (root/accepted.HEAD).read_bytes() == before
    current = accepted.read(root)
    assert current.row('execution-recovery/001/original')['ref'] == reference
    assert current['usage'] == head['usage'] and current.prefix == head.prefix
    assert current['search']['table']['mechanisms']['h001.m1']['accepted_calls'] == 1
    if accepted_state == 'tail':
        assert current.pending is not None
        assert not (root/reference['path']).exists()
    else:
        assert c.read(root/reference['path']) == value


def test_unknown_to_not_submitted_preserves_consumption_identity_and_projection(tmp_path, monkeypatch):
    root = prepare(tmp_path)
    call, head = _consumed_original(root)
    row = dict(call_id='original',status='not_submitted',mechanism_binding=copy.deepcopy(call['mechanism_binding']))
    value = dict(version=2,source_step=1,call_id='original',phase='not_submitted',row=row,native_state=None)
    reference = dict(path=f'{execution_recovery.DIRECTORY}/001/original/000001.json')
    execution_recovery._publish_fact(root,head,1,'original',reference,value)
    current = accepted.read(root)
    recovered = current.row(accepted_search.key('call','1:original'))
    assert recovered['status'] == 'not_submitted'
    for field in ('source_step','step','call_id','mechanism_binding','origin_identity','observation_ref','call_index'):
        assert recovered[field] == call[field]
    assert recovered['recovery_ref']['path'] == reference['path']
    assert current['usage'] == head['usage'] and current.prefix == head.prefix
    assert current['search']['table'] == head['search']['table']
    assert current['search']['extra_grant'] == head['search']['extra_grant']
    assert current['search']['owed_count'] == head['search']['owed_count']
    assert current['search']['opportunity_unknown_count'] == 0
    assert 'last_terminal' not in current['search']['table']['mechanisms']['h001.m1']
    assert current.row(accepted_search.key('node',1))['pending_calls'] == []
    assert accepted_search.key('call','1:original') not in current['search']['current_facts']
    _forbid_discovery(monkeypatch)
    assert not execution_recovery.pending(root)
    assert execution_recovery.call_view(root,[],[reference],validated=[value])[(1,'original')]['row'] == row


@pytest.mark.parametrize('damage',['binding','known_fact'])
def test_not_submitted_recovery_cannot_change_binding_or_known_fact(tmp_path, damage):
    root = prepare(tmp_path)
    call, head = _consumed_original(root,status='known_success' if damage == 'known_fact' else 'unknown')
    row = dict(call_id='original',status='not_submitted',mechanism_binding=copy.deepcopy(call['mechanism_binding']))
    if damage == 'binding':row['mechanism_binding']['mechanism_ref'] = 'h001.m2'
    reference = dict(path=f'{execution_recovery.DIRECTORY}/001/original/000001.json')
    value = dict(version=2,source_step=1,call_id='original',phase='not_submitted',row=row,native_state=None)
    before = (root/accepted.HEAD).read_bytes()
    message = 'cannot replace known' if damage == 'known_fact' else 'changed the original scientific call binding'
    with pytest.raises(ValueError,match=message):
        execution_recovery._publish_fact(root,head,1,'original',reference,value)
    assert (root/accepted.HEAD).read_bytes() == before
    assert not (root/reference['path']).exists()
    assert accepted.read(root).row(accepted_search.key('call','1:original')) == call


@pytest.mark.parametrize('damage',[False,True])
def test_inherited_pending_recovery_keeps_its_explicit_issuer(tmp_path, monkeypatch, damage):
    root = prepare(tmp_path)
    original = dict(goal_id='original',settings_ref=dict(path='/original/settings.json'))
    issuer = dict(goal_id='recovery-issuer',settings_ref=dict(path='/issuer/settings.json'))
    ref = dict(path='retained-recovery.json',origin_identity=issuer)
    node = _node(root,calls=['inherited'],origin=original)
    node['pending_calls'][0]['recovery_ref'] = ref
    _accept_facts(root,node=node)
    path = root/'exact-inherited-recovery.json'
    c.write(path,dict(source_step=2 if damage else 1,call_id='inherited',
        row=dict(call_id='inherited',status='known_failed'),native_state=None))
    owner = SimpleNamespace(origin_identity=issuer)
    seen = []
    def for_origin(actual_root,identity):
        assert actual_root == root and identity == issuer
        seen.append(identity)
        return owner
    def resolve(actual_owner,proof):
        assert actual_owner is owner and proof == ref
        return path
    monkeypatch.setattr(originals,'for_origin',for_origin)
    monkeypatch.setattr(originals,'resolve_ref',resolve)
    _forbid_discovery(monkeypatch)
    if damage:
        with pytest.raises(ValueError,match='escaped its original call'):
            execution_recovery.pending(root)
    else:
        assert not execution_recovery.pending(root)
    assert seen == [issuer]


def test_unsealed_wait_uses_only_accepted_current_native_record(tmp_path, monkeypatch):
    root = prepare(tmp_path)
    job = c.build_job(root,1,'W',[])
    record = c.host_record(c.workspace(root,1))
    accepted.accept_native(root,1,record,dict(task_ref=c.record_ref(root/'jobs/001.json')))
    accepted.accept_terminal(root,1,record,meter=dict(input_tokens=1,cached_input_tokens=0,output_tokens=1),unknown=False)
    c.write(record/managed_wait.NAME,dict(original='wait'))
    call = root/'original-accepted.json'
    c.write(call,dict(call_id='original'))
    checked = []
    def validate(actual_root,actual_job,actual_record):
        assert actual_root == root and actual_job == job and actual_record == record
        checked.append(record)
        return dict(calls=[dict(accepted=dict(path=str(call)))])
    monkeypatch.setattr(managed_wait,'validate',validate)
    monkeypatch.setattr(managed_wait.execution,'record',lambda *args,**kwargs:
        pytest.fail('Waiting query rediscovered native attempt history'))
    _forbid_discovery(monkeypatch)
    assert managed_wait.pending(root) and checked == [record]
