"""Current resume admission observes originals; historical checks never probe PIDs."""
from contextlib import nullcontext
import json
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import (
    budget, contracts as c, execution, execution_authority as authority,
    execution_recovery, execution_resume as resume, history, qualification as q,
    runtime,
)


def _retain(root, path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')
    return q._ref(root, path)


@pytest.fixture
def original_writers(tmp_path, monkeypatch):
    root = tmp_path / 'instance'
    job = dict(step=6, role='W', execution_authority=True)
    job_ref = _retain(root, root / 'jobs/006.json', job)
    first = root / 'native-evidence/006'
    chain = [first, first / 'continuations/001']
    bindings = [dict(path=str(root / 'jobs/006.json')),
                dict(path=str(chain[1] / 'native-intent.json'))]
    processes = []
    attempts = []
    for number, record in enumerate(chain):
        proofs = {}
        for name, value in (
            ('native-intent.json', dict(task_ref=bindings[0])),
            ('native-outcome.json', dict(cleanup='observed_group_cleaned', returncode=0)),
        ):
            proofs[name] = _retain(root, record / name, value)
        owners = []
        for index, name in enumerate(('runner-process.json', 'native-process.json')):
            pid = 40000 + number * 10 + index
            owner = dict(pid=pid, pgid=pid, sid=pid, ppid=1, start_ticks=100,
                         boot_id='original-boot', task_ref=bindings[number])
            owners.append(owner)
            proofs[name] = _retain(root, record / name, owner)
        processes.append(owners)
        attempts.append(proofs)
    call = 'original-call'
    scope = dict(job_ref=bindings[0])
    config = dict(scope=scope, execution_interface=dict(fixture=True))
    packet = root / 'packet.json'
    packet_ref = _retain(root, packet, dict(payload='original'))
    allocation = _retain(root, root / 'execution-authority/calls' / call / 'allocation.json',
        dict(call_id=call, binding=dict(scope=scope, interface=config['execution_interface'],
            native_attempt=dict(number=1), payload_alias=dict(packet_ref, path=str(packet)))))
    _retain(root, chain[1] / 'experiment-config.json', config)
    contract = dict(path='original-contract')
    pending = dict(version=1, job=job_ref, contract=contract, attempts=attempts,
        allocations=[dict(call_id=call, allocation=allocation, requests=[], records=[],
            disposition='unprepared', prepare_intent=None, accepted=None)],
        calls_namespace=[], host_preparations=[], complete=True)
    pending_ref = _retain(root, first / 'pending-execution-set.json', pending)
    seal = dict(job_ref=bindings[0], pending_executions=pending_ref)
    monkeypatch.setattr(execution, 'origin', lambda root, step: first)
    monkeypatch.setattr(execution, 'records', lambda root, step: chain)
    monkeypatch.setattr(execution, 'identity', lambda root, step, number: bindings[number])
    monkeypatch.setattr(authority, 'contract', lambda *args: contract)
    # Only kernel observations are synthetic; no subprocess/PID probe is used.
    live = set()
    members = {}
    monkeypatch.setattr(runtime, 'process_matches', lambda owner, task: owner['pid'] in live)
    monkeypatch.setattr(runtime, '_observed_group_members', lambda owner: members.get(owner['pid'], []))
    return SimpleNamespace(root=root, job=job, seal=seal, pending=pending,
        allocation=allocation, config=config, call=call, chain=chain,
        bindings=bindings, processes=processes, live=live, members=members)


def _resume_boundary(case, monkeypatch, *, prior=None, after_prepare=None):
    """Mock unrelated admission/adapter services, retaining the real resume route."""
    from scidiscovery.research_loop import experiments as e, preparation_recovery, replay_check
    events = []
    state = dict(current_native=None)  # Sealing cleared the old current owner.
    monkeypatch.setattr(history, '_source', lambda *args: (case.job, None, case.seal, 'seal-sha'))
    monkeypatch.setattr(authority, '_host_context', lambda config: None)
    monkeypatch.setattr(authority, 'writer', lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(execution_recovery, 'latest', lambda *args: prior)
    monkeypatch.setattr(budget, 'execution_blocked', lambda *args, **kwargs: state['current_native'] is not None)
    monkeypatch.setattr(budget, 'transition', lambda *args, **kwargs: events.append('advance'))
    monkeypatch.setattr(preparation_recovery, 'retained_preparations', lambda *args, **kwargs: [])
    monkeypatch.setattr(replay_check, 'acceptance', lambda *args: {})
    monkeypatch.setattr(c, 'accept_recovery_call', lambda *args, **kwargs: dict(call_id=case.call))
    monkeypatch.setattr(resume, '_publish', lambda *args, **kwargs: None)
    monkeypatch.setattr(e, 'retirement', lambda *args: {})

    class Admission:
        def __init__(self, *args, writers, **kwargs):
            self.writers = writers
            self.context = object()
            self.base = dict(call_id=case.call)
            events.append('admission')

        def check(self):
            resume._observe_resume_writers(self.writers)

        def opportunity(self):
            pass

        def services(self):
            return None

    class Records:
        root = case.root / 'records'

        def calls(self):
            return []

        def directory(self, call):
            return self.root / 'calls' / call

        def accept(self, *args, recovery_admission, **kwargs):
            recovery_admission()
            events.append('accept')
            return dict(call_id=case.call)

        def external(self, call):
            return 'original-external'

        def bind_external(self, *args):
            events.append('bind')

    class Adapter:
        def prepare(self, descriptor, **kwargs):
            events.append('prepare')
            if after_prepare is not None:
                after_prepare()
            return descriptor

        def acceptance_metadata(self, *args, **kwargs):
            return {}

        def dispatch_lease(self, accepted):
            events.append('lease')

        def submit(self, prepared):
            events.append('submit')
            return 'original-external', {}

    monkeypatch.setattr(resume, '_RecoveryAdmission', Admission)
    monkeypatch.setattr(e, 'Records', lambda config: Records())
    monkeypatch.setattr(e, 'load', lambda *args, **kwargs: Adapter())
    return events, Records()


def _run(case):
    return resume.resume(case.root, 6, case.call, max_seconds=20,
                         authorization_note='Explicit finite fixture recovery')


@pytest.mark.parametrize('attempt', [0, 1])
@pytest.mark.parametrize('writer', [0, 1])
def test_sealed_original_writer_live_blocks_before_any_effect(original_writers, monkeypatch, attempt, writer):
    case = original_writers
    events, _ = _resume_boundary(case, monkeypatch)
    case.live.add(case.processes[attempt][writer]['pid'])
    with pytest.raises(ValueError, match='live or unknown'):
        _run(case)
    assert events == []


def test_exact_group_member_blocks_even_when_original_pid_is_gone(original_writers, monkeypatch):
    case = original_writers
    events, _ = _resume_boundary(case, monkeypatch)
    case.members[case.processes[0][1]['pid']] = [dict(pid=49999)]
    with pytest.raises(ValueError, match='live or unknown'):
        _run(case)
    assert events == []


@pytest.mark.parametrize('field,value', [
    ('task_ref', dict(path='other-task')), ('boot_id', ''), ('pid', None),
    ('start_ticks', None), ('pgid', None), ('sid', None), ('ppid', None),
])
def test_unknown_or_unbound_owner_is_not_non_submission(original_writers, field, value):
    case = original_writers
    writers = resume._resume_writers(case.root, case.job, case.pending)
    writers[0][1][0][field] = value
    with pytest.raises(ValueError, match='live or unknown'):
        resume._observe_resume_writers(writers)


def test_known_stopped_originals_allow_valid_current_resume(original_writers, monkeypatch):
    case = original_writers
    events, _ = _resume_boundary(case, monkeypatch)
    assert _run(case)['status'] == 'execution_resume_recorded'
    assert events == ['admission', 'advance', 'prepare', 'accept', 'lease', 'submit', 'bind']


def test_preparation_does_not_reuse_stale_stopped_observation(original_writers, monkeypatch):
    case = original_writers
    events, _ = _resume_boundary(case, monkeypatch,
        after_prepare=lambda: case.live.add(case.processes[0][0]['pid']))
    with pytest.raises(ValueError, match='live or unknown'):
        _run(case)
    assert events == ['admission', 'advance', 'prepare']


def test_dispatch_refreshes_writers_after_acceptance_publication(original_writers, monkeypatch):
    case = original_writers
    events, _ = _resume_boundary(case, monkeypatch)
    monkeypatch.setattr(resume, '_publish',
        lambda *args, **kwargs: case.live.add(case.processes[0][0]['pid']))
    with pytest.raises(ValueError, match='live or unknown'):
        _run(case)
    assert events == ['admission', 'advance', 'prepare', 'accept']


def test_real_operation_check_refreshes_original_owner_facts(original_writers):
    case = original_writers
    operation = object.__new__(resume._RecoveryAdmission)
    operation.writers = resume._resume_writers(case.root, case.job, case.pending)
    resume._observe_resume_writers(operation.writers)
    case.live.add(case.processes[0][0]['pid'])
    # Liveness refusal precedes even the budget/receipt refresh in check().
    with pytest.raises(ValueError, match='live or unknown'):
        operation.check()


@pytest.mark.parametrize('attempt', [0, 1])
@pytest.mark.parametrize('state', ['live', 'group_live', 'unknown_cleanup', 'unbound', 'stopped'])
def test_original_bridge_writer_retains_exact_group_and_cleanup_gates(original_writers, state, attempt):
    case = original_writers
    owner = dict(case.processes[attempt][0], pid=45000, pgid=45000, sid=45000)
    if state == 'unbound':
        owner['task_ref'] = dict(path='another-task')
    _retain(case.root, case.chain[attempt] / 'bridge-process.json', owner)
    if state != 'unknown_cleanup':
        _retain(case.root, case.chain[attempt] / 'bridge-outcome.json',
                dict(process=owner, cleanup='observed_group_cleaned'))
    if state == 'live':
        case.live.add(owner['pid'])
    elif state == 'group_live':
        case.members[owner['pid']] = [dict(pid=45999)]
    if state == 'stopped':
        resume._observe_resume_writers(resume._resume_writers(case.root, case.job, case.pending))
    else:
        with pytest.raises(ValueError, match='live or unknown'):
            resume._observe_resume_writers(resume._resume_writers(case.root, case.job, case.pending))


def test_query_only_reentry_does_not_gain_a_new_writer_gate(original_writers, monkeypatch):
    case = original_writers
    events, records = _resume_boundary(case, monkeypatch, prior=dict(version=2, phase='status'))
    _retain(case.root, records.directory(case.call) / 'accepted.json', {})

    def forbidden(*args):
        raise AssertionError('Query-only reentry must not acquire dispatch-writer facts')

    monkeypatch.setattr(runtime, 'process_matches', forbidden)
    monkeypatch.setattr(runtime, '_observed_group_members', forbidden)
    assert _run(case)['status'] == 'execution_resume_recorded'
    assert events == ['admission', 'advance']


@pytest.mark.parametrize('inherited', [False, True])
def test_historical_pending_verification_never_observes_current_pids(original_writers, monkeypatch, inherited):
    case = original_writers
    source = None
    seal = case.seal
    if inherited:
        from scidiscovery.research_loop import originals
        _retain(case.root, case.root / 'original-contract', dict(
            version=1, options=list(authority.OPTIONS), scope=authority.SCOPE,
            stage='decision_originals_resource_gate_separate'))
        source = SimpleNamespace(original_root=case.root)
        seal = dict(seal, execution_ref=dict(chain=[{}, {}]))
        monkeypatch.setattr(originals, 'source_settings', lambda source: dict(
            execution_authority_policy=dict(contract=case.pending['contract'])))
        monkeypatch.setattr(resume, '_source_path', lambda source, proof: case.root / proof['path'])

    def forbidden(*args):
        raise AssertionError('Historical pending validation must not observe current owners')

    monkeypatch.setattr(runtime, 'process_matches', forbidden)
    monkeypatch.setattr(runtime, 'bound_group_stopped', forbidden)
    monkeypatch.setattr(runtime, '_observed_group_members', forbidden)
    row = resume.verify_pending_set(case.root, seal, case.allocation, source=source)
    assert row['disposition'] == 'unprepared'
