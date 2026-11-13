"""Bounded role history and independent candidate facts, without model calls."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from scidiscovery.research_loop import contracts as c, history, lineage, originals, protocol
from test_history_handoff import finish, source, scene, REUSED


def bind(root, step):
    return lineage.bind(root, step, 'H', protocol.CURRENT)[0]


def bound_history_file(root, work, binding, path):
    row=next(item for item in binding['files'] if item['path']==path)
    return originals.resolve_root_ref(root,dict(row,path=str(work/path)))


def read_pages(work, start, key):
    rows = [];path = start
    while path:
        page = c.read(work / path);rows += page[key];path = page['next_page']
    return rows










def test_oversized_navigation_has_explicit_omission_without_discarding_originals(scene, monkeypatch):
    source(scene)
    work = c.workspace(scene, 3);work.mkdir()
    original = (c.workspace(scene, 2) / 'artifacts/candidate.py').read_bytes()
    monkeypatch.setattr(history, 'MAX_PAGE_BYTES', 512)
    _, paged, files = history.page_writer(work,root=scene)
    paths = paged([dict(step=2, summary='Scientific detail ' * 1000)], 8, 'records', 'inputs/history/large')
    row = c.read(work / paths[0])['records'][0]
    assert row['step'] == 2 and row['view_omitted'] and row['omission_reason']
    assert all(item['size_bytes'] <= 512 for item in files)
    assert (c.workspace(scene, 2) / 'artifacts/candidate.py').read_bytes() == original


def test_candidate_slots_do_not_overwrite_each_other_or_compare_other_evaluation():
    def candidate(name, metric, key):
        return dict(id=name, label=name, implementation=None, evaluation_key=key,
                    metric=metric, direction='max', status='ok', evidence=[])
    records = [dict(step=2, role='W', observation=dict(candidates=[candidate('low', .6, 'proxy-a'),
                    candidate('high', .9, 'proxy-a'), candidate('different', 900, 'proxy-b')],
                    actual_submission=dict(candidate_id='low')), recommendation=None),
               dict(step=3, role='A', observation=None, recommendation='different')]
    view = history.candidate_overview(records)
    assert [row['id'] for row in view['candidate_facts']] == ['low','high','different']
    assert view['a_recommended']['candidate_id'] == 'different'
    assert view['actual_submission']['candidate_id'] == 'low'
    assert history.candidate_overview(records) == view
    records.append(dict(step=4, role='A', observation=None, recommendation='unknown'))
    with pytest.raises(ValueError, match='unknown candidate'):
        history.candidate_overview(records)


def inventory_for(scene, job):
    """The stopped node's produced inventory facts; capture() only consumes them."""
    record = c.host_record(c.workspace(scene, job['step']))
    return c.CurrentInventory(scene, job, record, 0, [], {}, policy=c.settings(scene, job))


def setup_observer(scene, tmp_path, body):
    path = tmp_path / 'adapter.py';path.write_text(body)
    config = history.observer_config(dict(adapter=str(path), config={}))
    settings = c.settings(scene);settings['history_observer'] = config
    c.write(scene / 'settings.json', settings)
    c.write(scene / 'settings-identity.json', dict(path='settings.json'))
    return path


def test_generic_observer_snapshot_is_bounded_frozen_and_replay_stable(scene, tmp_path):
    setup_observer(scene, tmp_path, '''import json,sys
q=json.load(sys.stdin)
if q["step"] != 2:
 print(json.dumps({"candidates":[],"current_candidate":None,"actual_submission":None,"coverage":{"status":"complete","note":"Fixture"}}));raise SystemExit
print(json.dumps({"current_candidate":None,"coverage":{"status":"complete","note":"Fixture"},"candidates":[{"id":"mechanism-a","label":"A measured implementation","implementation":"history/nodes/002/artifacts/candidate.py","evaluation_key":"measured-v1","metric":0.7,"direction":"max","status":"ok","evidence":["fact.json"]}],"actual_submission":{"candidate_id":"mechanism-a","artifact":"artifact.bin","evidence":["fact.json"]}}))
''')
    (scene / 'fact.json').write_text('{"measured":0.7}')
    (scene / 'artifact.bin').write_bytes(b'not a pickle, never executed')
    source(scene)
    job = c.read(scene / 'jobs/002.json');raw = c.read(c.host_record(c.workspace(scene, 2)) / 'science-output.json')
    materials = c.read(c.host_record(c.workspace(scene, 2)) / 'result-identity.json')['materials']
    first = history.capture(scene, job, raw, materials, inventory=inventory_for(scene, job))
    observation = c.read(scene / first['path'])
    assert observation['actual_submission']['artifact']['size_bytes'] == (scene / 'artifact.bin').stat().st_size
    assert observation['actual_submission']['artifact']['retention']=='unavailable'
    assert observation['coverage']['status']=='partial'
    (scene / 'fact.json').write_text('{"measured":999}')
    assert history.capture(scene, job, raw, materials, inventory=inventory_for(scene, job)) == first
    assert c.read(scene / first['path']) == observation






def test_observer_tool_upgrade_preserves_the_original_observation(scene, tmp_path):
    adapter = setup_observer(scene, tmp_path, 'print(\'{"candidates":[],"current_candidate":null,"actual_submission":null,"coverage":{"status":"complete","note":"Fixture"}}\')')
    source(scene)
    job = c.read(scene / 'jobs/002.json')
    original=history.capture(scene, job, {}, [], inventory=inventory_for(scene, job))
    retained=(scene/original['path']).read_bytes()
    adapter.write_text('raise Exception("upgraded tool must not rebuild an observation")')
    assert history.capture(scene, job, {}, [], inventory=inventory_for(scene, job))==original
    assert (scene/original['path']).read_bytes()==retained


def test_cov_adapter_uses_public_facts_and_keeps_unexported_code_unknown(scene):
    import importlib.util
    path = Path(__file__).resolve().parents[2] / 'plugins/cov_execution/cov_execution/observer.py'
    spec = importlib.util.spec_from_file_location('cov_observer', path)
    adapter = importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)
    runs = scene / 'runs';runs.mkdir()
    (runs / 'results.jsonl').write_text(json.dumps(dict(run_id=1, val_score=.91, ok=True,
        script=str(scene / 'project/nodes/002/work/unexported.py'))) + '\n')
    (runs / 'submitted.json').write_text(json.dumps(dict(run_id=1)))
    (runs / 'submitted_model.pkl').write_bytes(b'Never unpickle this')
    # This unrelated private file is not part of the adapter input surface.
    (scene / 'hidden-score.json').write_text('not even valid JSON')
    request = dict(root=str(scene), config=dict(runs='runs', evaluation_key='public-proxy-v1'),
                   materials=[], known_candidates=[])
    value = adapter.observe(request)
    assert value['candidates'][0]['implementation'] is None
    assert value['candidates'][0]['metric'] == .91
    assert value['actual_submission']['candidate_id'] == 'run-1'
    request['known_candidates'] = ['run-1']
    assert adapter.observe(request)['candidates'] == []


def test_current_history_packets_stay_fixed_and_legacy_write_schema_is_rejected(scene):
    source(scene)
    job = c.build_job(scene, 3, 'A', [2])
    finish(scene, 3, 'A', [2])
    work = c.workspace(scene, 3)
    original = {path: path.read_bytes() for path in (scene / 'jobs/003.json', work / 'science-task.json',
        c.host_record(work) / 'result-identity.json')}
    with pytest.raises(protocol.UnsupportedProtocol):
        c.result_schema('A', 2)
    assert c.build_job(scene, 3, 'A', [2]) == job
    assert all(path.read_bytes() == raw for path, raw in original.items())
    assert c.science_view(job, scene)['lineage']['node_count'] == 2
    c.validate(work)




def test_nodes_paginate_by_bytes_as_well_as_row_count(scene):
    source(scene)
    work = c.workspace(scene, 3);work.mkdir()
    _, paged, files = history.page_writer(work,root=scene)
    rows = [dict(step=index, summary='界' * 4000) for index in range(1, 7)]
    paths = paged(rows, 8, 'records', 'inputs/history/byte-packing')
    assert len(paths) > 1  # Six rows exceed the byte bound before the count bound.
    assert all(item['size_bytes'] <= history.MAX_PAGE_BYTES for item in files)
    assert read_pages(work, paths[0], 'records') == rows


def test_observer_originals_survive_append_overwrite_and_reject_size_changes(scene, tmp_path):
    adapter = setup_observer(scene, tmp_path, '''import json,sys
q=json.load(sys.stdin)
print(json.dumps({"candidates":[],"current_candidate":None,"coverage":{"status":"complete","note":"Fixture"},"actual_submission":{"candidate_id":None,"artifact":"artifact.bin","evidence":["fact.json"]}}))
''')
    (scene / 'fact.json').write_text('{"observation":1}')
    (scene / 'artifact.bin').write_bytes(b'original submitted bytes')
    source(scene)
    old = c.read(scene / 'host-history/observations/002.json')
    proof = old['actual_submission']['evidence'][0]
    assert old['actual_submission']['artifact']['retention']=='unavailable'
    assert proof['retention'] == 'archived'
    assert (scene / proof['snapshot']).read_text() == '{"observation":1}'
    (scene / 'fact.json').write_text('{"observation":2}')
    (scene / 'artifact.bin').write_bytes(b'new submitted bytes')
    finish(scene, 3, 'A', [2])
    assert (scene / proof['snapshot']).read_text() == '{"observation":1}'
    c.validate(c.workspace(scene, 2))  # Mutable public sources are not mistaken for frozen originals.
    (scene / proof['snapshot']).chmod(0o600);(scene / proof['snapshot']).write_bytes(b'tampered')
    with pytest.raises(ValueError, match='Archived observer original changed'):
        c.validate(c.workspace(scene, 2))




def test_actual_unknown_id_is_unavailable_not_a_false_submission(scene, tmp_path):
    setup_observer(scene, tmp_path, '''print('{"candidates":[],"current_candidate":null,"coverage":{"status":"complete","note":"Fixture"},"actual_submission":{"candidate_id":"invented","artifact":"artifact.bin","evidence":["fact.json"]}}')''')
    (scene / 'fact.json').write_text('{}');(scene / 'artifact.bin').write_bytes(b'unknown')
    finish(scene, 1, 'H', [])
    snapshot = c.read(scene / 'host-history/observations/001.json')
    assert snapshot['coverage']['status'] == 'partial' and snapshot['actual_submission'] is None
    assert snapshot['candidate_diagnostics']==[dict(code='actual_submission_unresolved')]


def test_cov_adapter_retains_all_new_records_and_excludes_known_identities(scene):
    import importlib.util
    path = Path(__file__).resolve().parents[2] / 'plugins/cov_execution/cov_execution/observer.py'
    spec = importlib.util.spec_from_file_location('cov_observer_many', path)
    adapter = importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)
    runs = scene / 'runs';runs.mkdir()
    (runs / 'results.jsonl').write_text(''.join(json.dumps(dict(run_id=i, val_score=i/1000, ok=True))+'\n' for i in range(1, 201)))
    request = dict(root=str(scene), config=dict(runs='runs', evaluation_key='public-proxy-v1'), materials=[], known_candidates=[f'run-{i}' for i in range(1, 200)])
    value = adapter.observe(request)
    assert [row['id'] for row in value['candidates']] == ['run-200']
    assert value['coverage']['status'] == 'complete'
    request['known_candidates'] = []
    value = adapter.observe(request)
    assert len(value['candidates']) == 200 and value['coverage']['status'] == 'complete'
    assert {row['id'] for row in value['candidates']} == {f'run-{i}' for i in range(1, 201)}




def test_unknown_a_recommendation_preserves_science_with_local_diagnostic(scene):
    source(scene)
    _,work,record=finish(scene,3,'A',[2],recommendation='invented')
    original=(record/'science-output.json').read_bytes()
    seal=c.read(record/'result-identity.json')
    assert dict(code='candidate_recommendation_unresolved',subject='invented') in seal['control_outcome']['diagnostics']
    assert c.validate(work)['candidate_recommendation']=='invented'
    assert (record/'science-output.json').read_bytes()==original
