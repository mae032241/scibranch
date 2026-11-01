"""Rare recovery consumers retain current accepted facts and exact originals."""
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import (
    accepted_budget, accepted_prefix, budget, contracts as c,
    delivery, engineering, execution_inputs, experiment_client, history,
    preparation_recovery, qualification,
)


@pytest.mark.parametrize('kind', ['code_version', 'tcad'])
def test_direct_snapshot_tree_uses_saved_preparation_without_live_payload_reads(
        tmp_path, monkeypatch, kind):
    snapshot = tmp_path / '.prepared-original'
    snapshot.mkdir()
    prepared = dict(name='retained-call', local_path=str(tmp_path / 'retired-packet'),
        size_bytes=17, media_type='application/octet-stream')
    script = dict(path=prepared['local_path'], size_bytes=17, file_stat=[1, 2, 17, 4])
    accepted = (dict(code_version={'commit': 'retained'}) if kind == 'code_version'
        else dict(tcad=dict(prepared=dict(prepared, file_stat=script['file_stat'])),
            script=script))
    value = dict(acceptance_base=accepted, prepared=prepared, input_inventory=[],
        directory_identity=preparation_recovery._identity(snapshot))

    def forbidden(*args, **kwargs):
        raise AssertionError('Historical preparation reopened live payload acceptance')
    monkeypatch.setattr(experiment_client, '_observe_payload_path', forbidden)

    preparation_recovery._tree(snapshot, value, dict(worker_view_version=1), complete=True)
    assert not (tmp_path / 'retired-packet').exists()
    assert list(snapshot.iterdir()) == []


def test_direct_snapshot_rejects_changed_saved_tcad_preparation(tmp_path):
    prepared = dict(name='retained-call', local_path=str(tmp_path / 'packet'),
        size_bytes=17, media_type='application/octet-stream')
    value = dict(prepared=prepared, acceptance_base=dict(
        tcad=dict(prepared=dict(prepared, size_bytes=18)),
        script=dict(path=prepared['local_path'], size_bytes=17)))
    with pytest.raises(preparation_recovery.PreparationError, match='prepared input binding'):
        preparation_recovery._direct_input(value, dict(worker_view_version=1))


@pytest.mark.parametrize('selected', [False, True])
def test_observer_source_validation_compares_canonical_job_and_keeps_identity_check(
        tmp_path, monkeypatch, selected):
    job = dict(step=2, role='W', direct_materials=[], material_refs=[])
    if selected:
        group = dict(list_ref=dict(origin_identity=dict(goal_id='source'), step=1,
            path='original-producer-seal'), ids=['H001-I001'])
        material = dict(path='inputs/parents/001/artifacts/code.py', source='retained-code',
            size_bytes=4)
        job.update(direct_materials=[material], material_refs=[group], selected_inputs=[
            dict(label='Original implementation', origin_step=1,
                origin_result_ref=dict(path='original-result'), material=material,
                material_ref=dict(list_ref=group['list_ref'], id='H001-I001'))])
    jobs = tmp_path / 'jobs'
    jobs.mkdir()
    path = jobs / '002.json'
    c.write(path, c.job_record(job))
    raw = tmp_path / 'science-output.json'
    raw.write_text('{}\n')
    contract = dict(contract=execution_inputs.source_contract())
    policy = dict(history_observer=dict(source_contract=contract))
    view = dict(exact_original='preserved')
    observed = dict(binding=dict(source_ref=c.file_ref(raw)), input_sources=dict(
        contract=contract, job=qualification._ref(tmp_path, path),
        raw=qualification._ref(tmp_path, raw), view=view))
    monkeypatch.setattr(c, 'settings', lambda root, job: policy)
    monkeypatch.setattr(c, '_execution_raw', lambda root, step: raw)
    calls = []
    def validate(root, owner, authored_view, **kwargs):
        assert owner is job and authored_view is view
        calls.append(authored_view)
        return dict(complete=True)
    monkeypatch.setattr(execution_inputs, 'validate_source_view', validate)

    assert history.observer_input_sources(tmp_path, job, observed) == dict(complete=True)
    assert calls == [view]
    c.write(path, dict(c.job_record(job), role='H'))
    with pytest.raises(qualification.IntegrityError, match='original task'):
        history.observer_input_sources(tmp_path, job, observed)
    assert calls == [view]


@pytest.mark.parametrize('worker_role,expected', [('W', True), ('H', False)])
def test_engineering_request_resolves_candidate_from_current_frozen_prefix(
        tmp_path, monkeypatch, worker_role, expected):
    origin_identity = dict(goal_id='original', settings_ref=dict(path=str(tmp_path / 'settings.json')))
    prefix = dict(version=1, cutoff=2, count=2)
    decision = dict(step=3, role='A', history=dict(prefix=prefix, inputs=[], view={}))
    worker = dict(step=2, role=worker_role,
        engineering_repair=dict(original_candidate='first-original'))
    member = dict(step=2, seal_ref=dict(path='exact-retained-worker-seal'))
    calls = []
    monkeypatch.setattr(engineering.originals, 'node', lambda root, step: SimpleNamespace(step=step))
    monkeypatch.setattr(engineering.originals, 'source_job',
        lambda owner: decision if owner.step == 3 else worker)

    def candidate(root, selected, identity):
        assert selected is prefix and identity == 'retained-candidate'
        calls.append('candidate')
        return dict(first_step=2, origin_identity=origin_identity)

    def membership(root, selected, step, *, origin):
        assert selected is prefix and step == 2 and origin == origin_identity
        calls.append('member')
        return member

    monkeypatch.setattr(accepted_prefix, 'candidate', candidate)
    monkeypatch.setattr(accepted_prefix, 'member', membership)
    failure = (dict(status='engineering_error', cleanup_confirmed=True,
        recovery='author_repair', note='missing_implementation'), tmp_path / 'failure.json',
        dict(candidate_id='retained-candidate', source=dict(objects={})))

    result = engineering._request(tmp_path, 3, failure)

    assert calls == ['candidate', 'member']
    if expected:
        assert result['source'] == member
        assert result['candidate_id'] == 'retained-candidate'
        assert result['original_candidate'] == 'first-original'
        assert result['failure'] == dict(path='failure.json')
    else:
        assert result is None


@pytest.mark.parametrize('retained_job', [False, True])
def test_engineering_closeout_sources_reuse_one_head_for_reserved_successor(
        tmp_path, monkeypatch, retained_job):
    revision = dict(revision=1, path='frozen-policy.json')
    reservation = dict(step=5, configuration_revision=revision)
    bindings = tmp_path / 'host-control/configuration/bindings'
    bindings.mkdir(parents=True)
    c.write(bindings / '000005.json', reservation)
    job = dict(step=5, role='A', configuration_revision=revision)
    if retained_job:
        (tmp_path / 'jobs').mkdir()
        c.write(tmp_path / 'jobs/005.json', job)
    begin = dict(source_cutoff=3,
        delivery_sources=dict(source_nodes=[dict(step=2, seal_ref=dict(path='original-seal'))]))
    events = []
    reads = []

    class Head:
        def row(self, name):
            assert name == 'configuration-binding/000005'
            return reservation

    head = Head()
    def read_head(root):
        reads.append(root)
        return head
    monkeypatch.setattr(accepted_budget, 'read', read_head)
    monkeypatch.setattr(budget, '_events_from_state', lambda selected: (events, None)
        if selected is head else pytest.fail('Closeout changed its accepted head'))
    def closeout(root, *, events):
        assert events is accepted_events
        return dict(begin=begin)
    accepted_events = events
    monkeypatch.setattr(budget, 'closeout_state', closeout)
    monkeypatch.setattr(engineering, 'steps', lambda root, before_step: [4])
    monkeypatch.setattr(engineering.originals, 'node', lambda root, step: SimpleNamespace(step=step))
    monkeypatch.setattr(engineering.history, 'source_seal_ref', lambda owner: dict(path='repaired-seal'))
    def catalogue(root, nodes, *, job):
        assert nodes == [*begin['delivery_sources']['source_nodes'],
            dict(step=4, seal_ref=dict(path='repaired-seal'))]
        return job
    monkeypatch.setattr(delivery, 'catalogue_sources', catalogue)

    result = engineering.sources(tmp_path, 5)

    assert result == (job if retained_job else reservation)
    assert reads == [tmp_path]
