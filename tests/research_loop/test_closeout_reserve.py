"""The unique FINAL cannot dispatch optional audits or their follow-up author."""
import pytest

from scidiscovery.research_loop import budget, candidate_audit, contracts as c, native
from test_closeout_protocol import command, scene


@pytest.mark.process_e2e
def test_final_audit_request_preserves_original_without_audit_or_followup_author(tmp_path):
    root=scene(tmp_path,[dict(action='audit',candidate='run-1'),dict(action='deliver',candidate='run-1')])
    settings=(root/'settings.json').read_bytes()
    assert c.settings(root)['candidate_audit_policy']==dict(version=1)
    originals={path:path.read_bytes() for path in (root/'jobs/002.json',
        root/'native-evidence/002/result-identity.json',root/'native-evidence/002/science-output.json')}
    assert c.read(root/'jobs/002.json')['role']=='W' and native.usage(root)['observed_tokens']==20
    result=command(root)
    assert result['status']=='closeout_native_unsealed' and result['goal_completion']=='not_asserted',result
    phase=budget.closeout_state(root)['begin']
    assert phase['mode']=='final' and phase['source_cutoff']==2 and phase['first_step']==3
    grant=budget.final_a_barrier(root)
    assert grant['step']==3 and grant['basis']=='final_state'
    assert grant['anchor']['shared_limits']==dict(max_tokens=100000,max_seconds=600)
    job=c.read(root/'jobs/003.json')
    assert job['role']=='A' and job['final_only']
    prepared=(root/'jobs/003.json').read_bytes()
    record=c.host_record(c.workspace(root,3))
    assert 'audit' not in c.read(record/'science-result.schema.json')['properties']['next_action']['enum']
    science=record/'science-output.json'
    original=science.read_bytes()
    assert c.read(science)['next_action']=='audit' and c.read(science)['candidate_recommendation']=='run-1'
    assert not (record/'result-identity.json').exists() and not (record/'continuations').exists()
    assert not (candidate_audit._directory(root,3)/'intent.json').exists()
    assert not (candidate_audit._directory(root,3)/'outcome.json').exists()
    assert not list(candidate_audit.facts(root))
    assert [c.read(path)['role'] for path in sorted((root/'jobs').glob('*.json'))]==['H','W','A']
    assert not budget.closeout_state(root)['actions'] and not (root/'submission/model.pkl').exists() and not (root/'runs/submitted.json').exists()
    assert native.usage(root)['observed_tokens']==30 and budget.used_steps(root)==3
    assert command(root,'run')['status']=='closeout_native_unsealed'
    assert science.read_bytes()==original and (root/'settings.json').read_bytes()==settings
    assert (root/'jobs/003.json').read_bytes()==prepared
    assert all(path.read_bytes()==raw for path,raw in originals.items())
    assert native.usage(root)['observed_tokens']==30 and budget.used_steps(root)==3
    assert budget.final_a_barrier(root)==grant and budget.closeout_state(root)['begin']==phase
    assert not (record/'result-identity.json').exists() and not (record/'continuations').exists()
    assert not budget.closeout_state(root)['actions'] and not (root/'runs/submitted.json').exists()
    assert not (root/'jobs/004.json').exists() and not list(candidate_audit.facts(root))


def test_multibyte_candidate_preview_keeps_whole_ids_and_reports_omissions():
    from scidiscovery.research_loop import delivery
    rows=[dict(candidate_id='候'*77+f'{n:02}',status='available',source_step=2) for n in range(32)]
    refs=dict(catalogue=True,items=rows,omitted_count=0,source_nodes=[])
    view=delivery.public_sources(refs)
    assert len(delivery._bytes(view))<=4096
    assert view['omitted_count']==len(rows)-len(view['items'])
    assert view['catalogue']=='inputs/delivery-candidates.json'
    assert {r['candidate_id'] for r in view['items']} <= {r['candidate_id'] for r in rows}
