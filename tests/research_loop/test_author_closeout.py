"""Bounded author recovery preserves terminal calls and denies native effects."""

import pytest

from execution_fixtures import execution_registry
from scidiscovery.research_loop import author_closeout,budget,contracts as c,native
from scidiscovery.research_loop.experiment_client import record_directory
from test_cov_delivery_lineage import managed_scene
from test_operator_usage_reserve import unknown_original


@pytest.mark.process_e2e
def test_terminal_facts_use_retained_transport_without_an_adapter(tmp_path,execution_registry,monkeypatch):
    root,_,first,*_=managed_scene(tmp_path)
    from scidiscovery.research_loop import experiments
    monkeypatch.setattr(experiments,'load',lambda *a,**kw:pytest.fail('No backend or adapter loading for author closeout'))
    calls=record_directory(root,2)/'calls'
    before={p:p.read_bytes() for p in calls.rglob('*') if p.is_file()}
    assert before and len(list(calls.glob('*/accepted.json')))==2
    facts=author_closeout.terminal_facts(root,2)
    assert len(facts['calls'])==2
    assert facts['calls'][0]['candidate_id']==first['candidate_id']
    assert all(row['status']=='known_success' for row in facts['calls'])
    assert all('val_score' not in row and 'audit_pass' not in row for row in facts['calls'])
    assert before=={p:p.read_bytes() for p in calls.rglob('*') if p.is_file()}
    accepted=next(calls.glob('*/accepted.json'));accepted.chmod(0o600)
    c.write(accepted,dict(c.read(accepted),call_id='another-call'))
    with pytest.raises(ValueError,match='another frozen execution interface'):
        author_closeout.terminal_facts(root,2)


def test_author_start_requires_positive_actual_shared_balance(unknown_original):
    root,record,_=unknown_original
    receipt=dict(pid=999999999,start_ticks=1,pgid=999999999,sid=999999999,ppid=1,
        boot_id=budget.clock_stamp()['boot_id'],task_ref=c.record_ref(root/'jobs/001.json'))
    for name in ('runner-process.json','native-process.json'):c.write(record/name,receipt)
    meter=native.usage(root)
    assert meter['observed_tokens']==meter['accounted_tokens']==120 and meter['unknown_steps']==[1]
    budget.amend(root,max_tokens=meter['observed_tokens'],authorization_note='Reported usage has reached its actual ceiling.')
    before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError,match='Finite author closeout and delivery reserve exceed remaining authorization'):
        author_closeout.start(root,1,max_tokens=10,max_seconds=10,authorization_note='Explicit bounded author closeout')
    assert before=={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('worker_view_version',[None,1])
def test_author_call_cache_still_enumerates_new_originals(tmp_path,worker_view_version):
    root=tmp_path;job=dict(step=1,role='W')
    if worker_view_version is not None:
        job['worker_view_version']=worker_view_version
    c.write(root/'jobs/001.json',job)
    directory=record_directory(root,1)/'calls/original'
    directory.mkdir(parents=True);(directory/'accepted.json').write_text('first')
    original=author_closeout._calls(root,1)
    assert author_closeout._calls(root,1)==original
    (directory/'new-original.json').write_text('new')
    assert author_closeout._calls(root,1)!=original
    refreshed=author_closeout._calls(root,1)
    added=directory.parent/'new-call';added.mkdir()
    (added/'accepted.json').write_text('another call')
    updated=author_closeout._calls(root,1)
    assert len(updated)==len(refreshed)+1
    assert author_closeout._ref(root,added/'accepted.json') in updated


@pytest.mark.parametrize('unknown', [None, 'native', 'bridge', 'backend'])
def test_context_export_cleanup_follows_native_group_cleanup(tmp_path, monkeypatch, unknown):
    """Execute the real runner finalizer with only physical process boundaries replaced."""
    import ast
    import inspect
    from types import SimpleNamespace
    from scidiscovery.research_loop import code_versions, local_experiments, runtime
    events=[];turn_input_context=object()
    backend=SimpleNamespace(end_turn_input=lambda context:events.append(('turn_input_ended',context)))
    identity=dict(task_ref=dict(path='original-task'),pgid=900000001)
    def bridge_close():
        events.append('bridge_stopped')
        if unknown=='bridge':raise ValueError('bridge cleanup is unknown')
    def group_cleaned(*_):
        events.append('native_group_stopped')
        return 'cleanup_unknown' if unknown=='native' else 'observed_group_cleaned'
    def backend_cleaned(*_):
        events.append('backend_stopped')
        return 'not_configured' if unknown=='backend' else 'observed_group_cleaned'
    monkeypatch.setattr(runtime,'process_matches',lambda *_:False)
    monkeypatch.setattr(runtime,'cleanup_owned_group',group_cleaned)
    monkeypatch.setattr(local_experiments,'cleanup',backend_cleaned)
    monkeypatch.setattr(code_versions,'cleanup_export',lambda root,**kw:events.append(('export_removed',kw['step'],kw['call_id'])))
    process=SimpleNamespace(wait=lambda **kw:events.append('native_reaped'),stdout=None,stdin=None)
    tree=ast.parse(inspect.getsource(native.run_native))
    finalizer=next(node for node in tree.body[0].body if isinstance(node,ast.Try)).finalbody
    code=compile(ast.fix_missing_locations(ast.Module(body=finalizer,type_ignores=[])),'native-finalizer','exec')
    scope=dict(native.__dict__,root=tmp_path,job=dict(step=3,role='W',experiment_mode='local'),
        backend=backend,turn_input_context=turn_input_context,
        scientific_lifetime=SimpleNamespace(close=bridge_close),process=process,process_id=identity,
        experiment_config=dict(execution_interface=dict(executor='local_execution:command')),
        code_exports=[(2,'a'*32)],launch_started=True,bridge_stopped=False,
        cleanup=None,stop=None,error=None)
    exec(code,scope)
    assert events[:5]==[('turn_input_ended',turn_input_context),'bridge_stopped','native_reaped','native_group_stopped','backend_stopped']
    if unknown is None:
        assert events[5:]==[('export_removed',2,'a'*32)]
        assert scope['stop'] is None
    else:
        assert events[5:]==[] and scope['cleanup']=='cleanup_unknown'
        assert scope['stop']=='cleanup_unknown'
    events.clear()
    native._cleanup_code_exports(tmp_path,[(2,'a'*32)],launch_started=True,
        process=None,cleanup=None,bridge_stopped=True)
    assert not events, 'An uncertain Popen failure retains the exact export'
