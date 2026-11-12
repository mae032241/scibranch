"""Final W owns its scientific finding while exact prior observations are reused."""
import pytest

from scidiscovery.research_loop import contracts as c, search, worker_delivery, execution
from execution_fixtures import execution_registry
from test_breadth_science import prepare, h, worker, run, findings, a
from test_history_handoff import finish


@pytest.mark.process_e2e
@pytest.mark.parametrize('p_observed',[True,False])
def test_final_w_reuses_prior_p_without_new_call_or_cycle_and_a_checks_its_own_finding(tmp_path,execution_registry,p_observed):
    root=prepare(tmp_path,portfolio=True);h(root,labels=('P','Q'))
    _,first=worker(root,tmp_path,2,[1]);run(root,first,'P')
    finish(root,2,'W',[1],mechanism_findings=findings('P',blocked=('Q',)))
    a(root,3,[1,2])
    job,second=worker(root,tmp_path,4,[1,2,3]);run(root,second,'Q')
    rows=findings('P','Q') if p_observed else findings('Q',blocked=('P',))
    finish(root,4,'W',[1,2,3],mechanism_findings=rows)
    record=execution.record(root,4)
    receipt=c.read(record/'result-identity.json')
    assert receipt['control_outcome']['worker_delivery']['status']==('complete' if p_observed else 'partial')
    facts=worker_delivery.validate(root,job,record)
    assert {row['mechanism_ref'] for row in facts['evidence']}=={'h001.m1','h001.m2'}
    observed=c.read(root/receipt['observations']['path'])
    assert [row['mechanism_binding']['mechanism_ref'] for row in observed['managed_calls']]==['h001.m2']
    selected=('h001.m1','h001.m2') if p_observed else ('h001.m2',)
    a(root,5,[1,4],action='finish',dispositions=[dict(mechanism_ref=ref,status='screened',
        reason='Assess this final W scientific finding using its exact bound observations and limitations')
        for ref in selected])
    cutoff=6
    if not p_observed:
        a(root,6,[1,4],action='finish',dispositions=[dict(mechanism_ref='h001.m1',status='screened',
            reason='Attempt to screen a branch that the final W reports as blocked')])
        rejected=c.read(execution.record(root,6)/'result-identity.json')['control_outcome']
        assert rejected['search_status']=='unresolved'
        assert any(row['code']=='screen_source_unavailable' and row['subject']=='h001.m1' for row in rejected['diagnostics'])
        cutoff=7
    state=search.project(root,cutoff)
    assert state['mechanisms']['h001.m1']['status']==('screened' if p_observed else 'pending')
    assert state['mechanisms']['h001.m2']['status']=='screened'
    assert state['opportunity_counts']=={'h001.m1':1,'h001.m2':1}
    assert len(state['order']['seen_w'])==2
    assert state['portfolio']['invested']=={'h001.m1':1,'h001.m2':1}
    assert state['portfolio']['last_branches']==['h001.m2']
    before={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    c.validate(c.workspace(root,4));search.project(root,cutoff)
    assert {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}==before
