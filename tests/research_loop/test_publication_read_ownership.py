"""Publication consumes current inventory; only its writer refreshes admission."""
import copy
from pathlib import Path

import pytest

from scidiscovery.research_loop import (
    accepted_budget as accepted, accepted_prefix, accepted_search, atomic_files,
    contracts as c, originals,
)
from test_history_handoff import scene, finish


def _publication(root, monkeypatch, *, step=1, role='H', parents=()):
    """Stop a real accepted fixture at the first publication consumer."""
    class PublicationReady(Exception):
        pass

    facts = {}

    def stopped(root, job, payload, seal, *, inventory):
        facts.update(job=job, payload=payload, seal=seal, inventory=inventory)
        raise PublicationReady

    with monkeypatch.context() as patch:
        patch.setattr(accepted_prefix, 'prepare_node', stopped)
        with pytest.raises(PublicationReady):
            finish(root, step, role, list(parents), action='wait')
    return facts


def _prepare(root, facts):
    pending = accepted_prefix.prepare_node(root, facts['job'], facts['payload'],
        facts['seal'], inventory=facts['inventory'])
    c.write(root/'native-evidence'/f'{facts["job"]["step"]:03d}'/'result-identity.json', facts['seal'])
    return pending


def _commit(root, pending, inventory, seal):
    return accepted_prefix.commit_node_from_facts(root, pending,
        base=inventory.base, head=inventory.head, seal=seal)


def test_publication_reads_no_settings_and_only_writer_refreshes_head(scene, monkeypatch):
    facts = _publication(scene, monkeypatch)
    inventory = facts['inventory']
    raw = inventory.record/'science-output.json'
    result = inventory.record/'result.json'
    retained = (raw.read_bytes(), result.read_bytes())
    read = c.read
    reads = []

    def observed(path):
        reads.append(Path(path))
        return read(path)

    def forbidden(*args, **kwargs):
        pytest.fail('Publication reopened already produced settings, head or local origin')

    monkeypatch.setattr(c, 'read', observed)
    monkeypatch.setattr(c, 'settings', forbidden)
    monkeypatch.setattr(accepted, 'read', forbidden)
    monkeypatch.setattr(originals, 'node', forbidden)
    pending = _prepare(scene, facts)
    assert reads.count(scene/accepted.HEAD) == 0
    row = accepted.AcceptedState.row

    def no_job_reopen(state, key):
        if key == 'job/001':
            pytest.fail('Publication reopened its writer-owned current job')
        return row(state, key)

    monkeypatch.setattr(accepted.AcceptedState, 'row', no_job_reopen)
    assert _commit(scene, pending, inventory, facts['seal']) == pending['prefix']

    assert reads.count(scene/accepted.HEAD) == 1
    assert reads.count(scene/'native-evidence/001/result-identity.json') == 0
    assert scene/'settings.json' not in reads
    assert inventory.head.prefix['count'] == 0
    overview = read(scene/'host-history/overview.json')
    assert overview['node_count'] == 1
    published = accepted_prefix.visibility(scene, pending['prefix'])/Path(
        pending['prefix']['tip']['navigation']['path']).parent
    assert (published/'science.json').samefile(result)
    assert (published/'scientific-original.json').samefile(raw)
    assert (raw.read_bytes(), result.read_bytes()) == retained

    frozen = (scene/accepted.HEAD).read_bytes()
    assert _commit(scene, pending, inventory, facts['seal']) == pending['prefix']
    assert (scene/accepted.HEAD).read_bytes() == frozen
    assert reads.count(scene/accepted.HEAD) == 2


def test_search_publication_uses_supplied_policy_and_original_identity(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('A strict publication consumer reconstructed policy or origin')

    monkeypatch.setattr(c, 'settings', forbidden)
    monkeypatch.setattr(originals, 'node', forbidden)
    origin = dict(goal_id='retained-owner', settings_ref=dict(path='/retained/settings.json'))
    delta = accepted_search.prepare_node_from_facts(tmp_path, dict(step=1, role='H'),
        dict(next_action='wait'), {}, summary=accepted_search.empty(),
        lookup=lambda name: None, observation=None,
        policy=dict(portfolio_policy=dict(review_after_cycles=7)), origin=origin)

    assert delta['summary']['portfolio']['review_after_cycles'] == 7
    assert delta['rows'][accepted_search.key('node', 1)]['origin_identity'] == origin
    assert delta['rows'][accepted_search.key('node', 1)]['origin_identity'] is not origin


@pytest.mark.parametrize('changed', ['prefix', 'step', 'job_ref'])
def test_stale_inventory_cannot_bypass_fresh_publication_owner(scene, monkeypatch, changed):
    facts = _publication(scene, monkeypatch)
    pending = _prepare(scene, facts)
    inventory = facts['inventory']
    current = copy.deepcopy(dict(inventory.head))
    if changed == 'prefix':
        current['prefix']['cutoff'] += 10
    elif changed == 'step':
        current['current_job']['step'] += 1
    else:
        current['current_job']['job_ref'] = dict(path=str(scene/'jobs/replaced.json'))
    # Alter only the mutable control observation after preparation. The passed
    # inventory still contains its old accepted owner and cannot admit writing.
    accepted._replace(scene/accepted.HEAD, current)
    frozen = (scene/accepted.HEAD).read_bytes()

    with pytest.raises(ValueError, match='lost its active view owner'):
        _commit(scene, pending, inventory, facts['seal'])

    assert (scene/accepted.HEAD).read_bytes() == frozen
    assert not (scene/'control-ledger'/f'{inventory.head["seq"]+1:06d}.json').exists()


def test_old_publication_cannot_overwrite_shared_files_after_a_new_node(scene, monkeypatch):
    def navigation_rows(pending, generation):
        # Synthetic mechanical navigation entries exercise each mutable public
        # destination; they make no claim about scientific candidate acceptance.
        pending['implementation_candidates']['fixture-implementation'] = dict(
            count=generation, candidate_id='fixture-candidate' if generation == 1 else None)
        pending['search_delta']['rows'][accepted_search.key('candidate', 'fixture-candidate')] = dict(
            first_step=1, public_reference='fixture-candidate', observation=dict(id='fixture-candidate'))
        pending['search_delta']['rows'][accepted_search.key('work', 'fixture-work')] = dict(
            work_ref='fixture-work', status='deferred', reason=f'Fixture navigation {generation}')

    first = _publication(scene, monkeypatch)
    old_pending = _prepare(scene, first)
    navigation_rows(old_pending, 1)
    old_inventory = first['inventory']
    _commit(scene, old_pending, old_inventory, first['seal'])
    second = _publication(scene, monkeypatch, step=2, role='E', parents=(1,))
    new_pending = _prepare(scene, second)
    navigation_rows(new_pending, 2)
    _commit(scene, new_pending, second['inventory'], second['seal'])
    current = accepted.read(scene)
    assert current.current_job['step'] == 2 and current.prefix['count'] == 2
    assert current['seq'] > old_inventory.head['seq']
    public = accepted_prefix.visibility(scene, current.prefix)
    members = scene/accepted_prefix.MEMBERS/accepted_prefix._view_key(current.prefix)
    shared = [
        accepted_prefix._key_file(members/'implementation-candidates', 'fixture-implementation'),
        accepted_prefix._key_file(public/'entities/candidate', 'fixture-candidate'),
        accepted_prefix._key_file(public/'entities/work', 'fixture-work'),
        public/'breadth-table.json',
        scene/'host-history/overview.json',
    ]
    retained = {path: path.read_bytes() for path in shared}
    frozen_head = (scene/accepted.HEAD).read_bytes()
    write = c.write
    overwritten = []

    def observed(path, value):
        if Path(path) in retained:
            overwritten.append(Path(path))
        return write(path, value)

    monkeypatch.setattr(c, 'write', observed)
    with pytest.raises(ValueError, match='lost its active view owner'):
        _commit(scene, old_pending, old_inventory, first['seal'])

    assert {path: path.read_bytes() for path in shared} == retained
    assert overwritten == []
    assert (scene/accepted.HEAD).read_bytes() == frozen_head
    assert not (scene/'control-ledger'/f'{current["seq"]+1:06d}.json').exists()


def test_publication_recovers_its_single_writer_tail_and_refreshes_new_state(scene, monkeypatch):
    facts = _publication(scene, monkeypatch)
    pending = _prepare(scene, facts)
    inventory = facts['inventory']
    before = inventory.head
    previous_job = before.row('job/001')
    frozen_head = (scene/accepted.HEAD).read_bytes()
    replace = atomic_files.replace_bytes

    def interrupted(path, payload, *args, **kwargs):
        if Path(path) == scene/accepted.HEAD:
            raise OSError('fixture publication interrupted before head')
        return replace(path, payload, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(atomic_files, 'replace_bytes', interrupted)
        with pytest.raises(OSError, match='publication interrupted'):
            _commit(scene, pending, inventory, facts['seal'])
    assert (scene/accepted.HEAD).read_bytes() == frozen_head
    event_path = scene/'control-ledger'/f'{before["seq"]+1:06d}.json'
    frozen_event = event_path.read_bytes()
    event = c.read(event_path)
    assert event['index_updates']['job/001']['previous'] == previous_job

    assert _commit(scene, pending, inventory, facts['seal']) == pending['prefix']

    after = accepted.read(scene)
    assert after['seq'] == before['seq']+1
    assert after.prefix == pending['prefix'] and after.current_job['status'] == 'sealed'
    assert after.row('job/001') == event['index_updates']['job/001']['current']
    assert before.row('job/001') == previous_job
    assert c.read(scene/'host-history/overview.json')['node_count'] == 1
    assert after['usage'] == before['usage']
    assert event_path.read_bytes() == frozen_event
    assert not (scene/'control-ledger'/f'{after["seq"]+1:06d}.json').exists()
