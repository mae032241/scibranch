"""Native strict output schemas and immutable legacy packets; no model calls."""
import pytest

from scidiscovery.research_loop import contracts as c, protocol
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from test_history_handoff import finish, scene, source, selection_explanation


def tree(root):
    return {path.relative_to(root): (path.lstat().st_mode, path.read_bytes() if path.is_file() else None)
            for path in [root, *root.rglob('*')]}


@pytest.mark.parametrize('role', ['H', 'W', 'A', 'E'])
def test_current_native_schema_requires_every_declared_property(role):
    schema = c.result_schema(role, c.HANDOFF_PROTOCOL)
    # Native strict response_format requires this equality, beyond JSON Schema validity.
    def strict(value):
        if not isinstance(value, dict):
            return
        if 'properties' in value:
            assert set(value['required']) == set(value['properties'])
            assert value['additionalProperties'] is False
        for nested in value.values():
            if isinstance(nested, dict):
                strict(nested)
            elif isinstance(nested, list):
                for item in nested:
                    strict(item)
    strict(schema)
    if role not in {'H', 'A'}:
        with pytest.raises(protocol.UnsupportedProtocol, match='Scientific result schema'):
            c.result_schema(role, 3)


@pytest.mark.parametrize('recommendation', [None, 'run-33'])
def test_current_a_scientific_recommendation_can_be_null_or_candidate(tmp_path, recommendation):
    raw = tmp_path / 'science-output.json'
    payload = dict(summary='Independent finding', scientific_result='Bounded result', limitations=[],
                   next_action='wait', route_reason='More evidence needed', candidate_recommendation=recommendation,
                   search_review=None, progress_assessment=None, implementation_selection=None,
                   decision_request=None, portfolio_review=None)
    payload['selection_explanation']=selection_explanation() if recommendation is not None else None
    c.write(raw, payload)
    assert c.science_payload(raw, 'A', c.HANDOFF_PROTOCOL) == payload
    payload.pop('candidate_recommendation')
    c.write(raw, payload)
    assert c.science_payload(raw,'A',c.HANDOFF_PROTOCOL)==payload


@pytest.mark.parametrize('retired', [1, 2, 3])
def test_legacy_a_schema_and_raw_validation_reject_without_rewriting_original(tmp_path, retired):
    raw = tmp_path / 'science-output.json'
    payload = dict(summary='Legacy finding', scientific_result='Original science', limitations=[],
                   next_action='wait', route_reason='Bounded uncertainty')
    c.write(raw, payload)
    before = tree(tmp_path)
    with pytest.raises(protocol.UnsupportedProtocol, match='Scientific result schema'):
        c.result_schema('A', retired)
    assert tree(tmp_path) == before
    with pytest.raises(protocol.UnsupportedProtocol, match='Scientific result schema'):
        c.science_payload(raw, 'A', retired)
    assert tree(tmp_path) == before


def test_current_a_seals_without_any_scientific_recommendation(scene):
    source(scene)
    job, work, record = finish(scene, 3, 'A', [2], action='wait')
    assert job['handoff_protocol'] == protocol.CURRENT
    assert c.read(record / 'science-output.json')['candidate_recommendation'] is None
    assert c.validate(work)['next_action'] == 'wait'
    before = tree(scene)
    assert c.build_job(scene, 3, 'A', [2]) == job
    assert tree(scene) == before


@pytest.mark.parametrize('retired', [3, 4])
def test_legacy_a_packet_cannot_seal_or_rebuild_without_changing_originals(tmp_path, retired):
    # These are retained archive bytes, never output from a retired write path.
    root = tmp_path / 'archive'
    c.write(root / 'settings.json', dict(handoff_protocol=retired))
    c.write(root / 'settings-identity.json', dict(path='settings.json'))
    c.write(root / 'jobs/003.json', dict(step=3, role='A', handoff_protocol=retired))
    work = c.workspace(root, 3);record = c.host_record(work)
    c.write(work / 'science-task.json', dict(preserved_task='Original scientific limitations'))
    c.write(record / 'science-result.schema.json', dict(preserved_schema='Original archived schema'))
    payload = dict(summary='Legacy finding', scientific_result='Original science', limitations=[],
                   next_action='revise_h', route_reason='Bounded uncertainty')
    if retired == 4:
        payload['candidate_recommendation'] = None
    c.write(record / 'science-output.json', payload)
    before = tree(root)
    for write in (lambda: c.seal_result(root, 3), lambda: c.build_job(root, 3, 'A', [2]),
                  lambda: c.build_job(root, 4, 'H', [3])):
        with pytest.raises(protocol.UnsupportedProtocol, match='Instance'):
            write()
        assert tree(root) == before
        assert not (record / 'result-identity.json').exists()
        assert not (root / 'jobs/004.json').exists()
