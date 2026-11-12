"""Archived scientific assets retain exact producer identity through reuse."""
import json
from pathlib import Path

import pytest

from execution_fixtures import execution_registry
from scidiscovery.research_loop import author_closeout, candidate_refs, contracts as c, execution, experiments, history
from test_history_handoff import finish
from test_worker_hash_free_views import local_candidate


def archived_source(tmp_path, *, duplicate=False):
    root, config, _ = local_candidate(tmp_path)
    if duplicate:
        directory = c.workspace(root, 2) / 'experiments/measurement'
        reply = experiments.dispatch(config, directory / 'run.py', new=True,
            command_options=dict(experiment_directory=str(directory)))
        assert reply['status'] == 'known_success'
    finish(root, 2, 'W', [1], files={'implementation.py': 'retained implementation\n'})
    seal = c.read(execution.origin(root, 2) / 'result-identity.json')
    options = history.implementation_options(root, 2)
    materials = sorted(seal['materials'], key=lambda row: row['path'])
    archived = [(option['id'], row) for option, row in zip(options, materials)
        if Path(row['source']).parent == root / 'host-history/objects'
        and row['path'].endswith('/observation.txt')]
    assert len(archived) == (2 if duplicate else 1)
    local = next((option['id'], row) for option, row in zip(options, materials)
        if row['path'] == 'inputs/parents/002/artifacts/implementation.py')
    return root, archived, local


@pytest.mark.process_e2e
def test_public_archive_ids_seal_validate_and_forward_exact_mixed_materials(tmp_path, execution_registry):
    root, archived, local = archived_source(tmp_path, duplicate=True)
    assert archived[0][1]['source'] == archived[1][1]['source']
    assert archived[0][1]['path'] != archived[1][1]['path']
    chosen = [*archived, local]
    ids = [object_id for object_id, _ in chosen]
    _, work, record = finish(root, 3, 'A', [2], selection=[*ids, ids[0]], action='continue_w')
    raw = execution.raw(root, 3)
    original = raw.read_bytes()
    assert c.read(raw)['implementation_selection'] == [*ids, ids[0]]
    sealed = c.read(record / 'result-identity.json')['selected_inputs']
    assert [row['material'] for row in sealed] == [row for _, row in chosen]
    assert all(row['origin_step'] == 2 for row in sealed)
    assert c.validate(work)['implementation_selection'] == [*ids, ids[0]]
    successor = c.build_job(root, 4, 'W', [3])
    assert successor['selected_inputs'] == sealed
    for _, row in chosen:
        assert (c.workspace(root, 4) / row['path']).read_bytes() == Path(row['source']).read_bytes()
    # Null inherits the exact rows, including both same-byte call aliases.
    finish(root, 4, 'W', [3])
    assert c.read(execution.origin(root, 4) / 'result-identity.json')['selected_inputs'] == sealed
    assert c.validate(c.workspace(root, 4))['implementation_selection'] is None
    assert raw.read_bytes() == original
    finish(root, 5, 'A', [4], selection=[], action='continue_w')
    assert not c.build_job(root, 6, 'W', [5]).get('selected_inputs')


@pytest.mark.process_e2e
@pytest.mark.parametrize('damage', ['size', 'symlink', 'seal'])
def test_archive_selection_rejects_changed_exact_originals(tmp_path, execution_registry, damage):
    root, archived, _ = archived_source(tmp_path)
    object_id, row = archived[0]
    job = c.build_job(root, 3, 'A', [2])
    source = Path(row['source'])
    if damage == 'seal':
        path = execution.origin(root, 2) / 'result-identity.json'
        seal = c.read(path)
        seal['scope'] += ' changed'
        c.write(path, seal)
    elif damage == 'symlink':
        target = tmp_path / 'same-bytes'
        target.write_bytes(source.read_bytes())
        source.unlink()
        source.symlink_to(target)
    else:
        original = source.read_bytes()
        source.chmod(0o600)
        source.write_bytes(original + b' ')
    with pytest.raises(ValueError):
        c.selected_inputs(c.workspace(root, 3), dict(implementation_selection=[object_id]), job)
    assert not (execution.origin(root, 3) / 'result-identity.json').exists()


@pytest.mark.process_e2e
def test_archive_bound_copy_is_checked_after_selection_seals(tmp_path, execution_registry):
    root, archived, _ = archived_source(tmp_path)
    object_id, row = archived[0]
    _, work, _ = finish(root, 3, 'A', [2], selection=[object_id])
    copied = work / row['path']
    original = copied.read_bytes()
    copied.chmod(0o600)
    copied.write_bytes(original + b' ')
    with pytest.raises(ValueError, match='direct material actually read'):
        c.validate(work)


@pytest.mark.process_e2e
@pytest.mark.parametrize('damage', ['unlisted', 'foreign', 'future', 'public_path'])
def test_inherited_archive_requires_exact_same_instance_seal_membership(tmp_path, execution_registry, damage):
    root, archived, _ = archived_source(tmp_path)
    object_id, material = archived[0]
    job = c.build_job(root, 3, 'A', [2])
    row = dict(material)
    if damage == 'foreign':
        source = tmp_path / 'foreign/host-history/objects/foreign-original'
        source.parent.mkdir(parents=True)
        source.write_bytes(Path(row['source']).read_bytes())
        row['source'] = str(source)
    elif damage == 'future':
        row['path'] = row['path'].replace('/002/', '/004/', 1)
    elif damage == 'public_path':
        row['path'] = row['path'].replace('/artifacts/', '/artifacts/../artifacts/', 1)
    else:
        row['path'] += '.unlisted'
    job['selected_inputs'] = [dict(label=object_id, material=row)]
    with pytest.raises(ValueError):
        c.selected_inputs(c.workspace(root, 3), dict(implementation_selection=None), job)


@pytest.mark.process_e2e
@pytest.mark.parametrize('identity', ['wrong_role', 'out_of_range', 'future'])
def test_archive_ids_remain_bound_to_exact_frozen_role_step_and_index(tmp_path, execution_registry, identity):
    root, archived, _ = archived_source(tmp_path)
    object_id, _ = archived[0]
    job = c.build_job(root, 3, 'A', [2])
    invalid = ('H' + object_id[1:] if identity == 'wrong_role' else
        'W004' + object_id[4:] if identity == 'future' else 'W002-I999')
    diagnostics = []
    assert c.selected_inputs(c.workspace(root, 3),
        dict(implementation_selection=[object_id, invalid]), job, diagnostics) == []
    assert diagnostics == [dict(code='implementation_unresolved', subject=invalid)]


@pytest.mark.process_e2e
def test_registered_candidate_and_material_refs_resolve_the_same_exact_call_not_equal_bytes(tmp_path,execution_registry,monkeypatch):
    root,archived,local=archived_source(tmp_path,duplicate=True)
    job=c.build_job(root,3,'A',[2])
    mapping=history.candidate_materials(root,job)
    assert len(mapping)==2
    assert {row['material']['path'] for row in mapping}=={material['path'] for _,material in archived}
    assert len({row['candidate_id'] for row in mapping})==2
    assert len({row['implementation_id'] for row in mapping})==2
    assert len({row['material']['source'] for row in mapping})==1
    chosen=mapping[0];reference=candidate_refs.public_reference(root,job,chosen['candidate_id'])
    write=c.write
    def scientific_extra(path,value):
        if path.name=='science-output.json':
            value=dict(value,scientific_result='A long scientific conclusion. '*3000,extra_science='overflow-sentinel')
            path.write_text(json.dumps(value).replace('"overflow-sentinel"','1e999'))
        else:write(path,value)
    with monkeypatch.context() as patch:
        patch.setattr(c,'write',scientific_extra)
        _,work,record=finish(root,3,'A',[2],recommendation=chosen['implementation_id'],selection=[reference])
    original=execution.raw(root,3).read_bytes()
    assert c.validate(work)['candidate_recommendation']==chosen['candidate_id']
    assert c.read(record/'result-identity.json')['selected_inputs'][0]['material']==chosen['material']
    assert execution.raw(root,3).read_bytes()==original
    successor=c.build_job(root,4,'W',[3]);next_work=c.workspace(root,4)
    originals=[row for row in successor['history']['files'] if row.get('kind')=='scientific_original']
    assert len(originals)==1 and originals[0]['size_bytes']>history.MAX_PAGE_BYTES
    c._validate_inputs(root,next_work,successor)
    proof=author_closeout.science_context(root,successor,execution.origin(root,4))
    context=author_closeout.validate_science_context(root,successor,proof)
    assert (context/originals[0]['path']).read_bytes()==original
    assert (context/'inputs/parents/003/scientific-original.json').read_bytes()==original
    assert len(execution.records(root,4))==1
    # The unrelated source file is an implementation, not a registered candidate.
    result=candidate_refs.control_payload(job,dict(candidate_recommendation=local[0]),root=root)
    assert result['candidate_recommendation']==local[0]
    assert history.validate_recommendation(job,result)==[dict(code='candidate_recommendation_unresolved',subject=local[0])]
    copied=next_work/originals[0]['path'];copied.chmod(0o600);copied.write_bytes(original+b' ')
    with pytest.raises(ValueError,match='Bound scientific history page changed'):
        c._validate_inputs(root,next_work,successor)
