"""Exact pending-marker reconciliation with inert bytes and no child processes."""
import copy
import json

import pytest

from cov_execution import delivery as cov_delivery


@pytest.mark.parametrize('with_stat', [False, True])
def test_adapter_checks_only_frozen_input_fields_and_preserves_saved_stat(tmp_path, monkeypatch, with_stat):
    from scidiscovery.research_loop import delivery, experiments
    path=tmp_path/'public-input';path.write_bytes(b'exact public bytes')
    original=experiments.proof(path)
    proof={key:original[key] for key in ('path','size_bytes','file_stat') if with_stat or key!='file_stat'}
    spec=dict(readonly_inputs_version=1,launcher_files=[],adapter=cov_delivery.__file__,
        inputs=[dict(proof,label='harness/input')])
    monkeypatch.setattr(delivery,'_settings',lambda root,job=None:dict(delivery_policy=dict(adapter=spec)))
    assert delivery._adapter(tmp_path)['inputs'][0]==spec['inputs'][0]
    replacement=tmp_path/'replacement';replacement.write_bytes(path.read_bytes());replacement.replace(path)
    if with_stat:
        with pytest.raises(ValueError,match='public harness changed'):delivery._adapter(tmp_path)
    else:
        assert delivery._adapter(tmp_path)['inputs'][0]==spec['inputs'][0]
        path.write_bytes(b'different size')
        with pytest.raises(ValueError,match='public harness changed'):delivery._adapter(tmp_path)


def _scene(tmp_path):
    runs = tmp_path/'runs';runs.mkdir()
    submission = tmp_path/'submission';submission.mkdir()
    artifact = tmp_path/'artifact';artifact.write_bytes(b'original model bytes')
    request = dict(operation_id='original-operation', candidate_id='run-1',
        artifact_ref=dict(path='selected/artifact', size_bytes=artifact.stat().st_size),
        artifact_origin=dict(path=str(artifact), size_bytes=artifact.stat().st_size),
        context=dict(row=dict(run_id=1)), target=dict(runs=str(runs), submission=str(submission)))
    # Synthetic contract fields exercise identity checks, not scientific audit.
    audit = dict(run_id=1, leakage_flag=False, audit_score=.5, audit_gap=.1,
        audit_note='audit took 0.01s on inert fixture', model_path=str(runs/'submitted_model.pkl'))
    (runs/'submitted_model.pkl').write_bytes(artifact.read_bytes())
    (submission/'model.pkl').write_bytes(artifact.read_bytes())
    (runs/'submitted.json').write_text(json.dumps(audit))
    marker = dict(**{key:copy.deepcopy(request[key]) for key in
        ('operation_id', 'candidate_id', 'artifact_ref', 'artifact_origin')}, audit=audit,
        published=dict(run_model=cov_delivery._generation(runs/'submitted_model.pkl'),
                       submission_model=cov_delivery._generation(submission/'model.pkl')))
    return request, runs, submission, artifact, marker


def _paths(runs, submission, artifact):
    targets = (runs/'submitted_model.pkl', submission/'model.pkl', runs/'submitted.json',
               runs/'rootless-delivery-operation.json')
    return (*targets, *(path.with_name(path.name+'.rootless-pending') for path in targets), artifact)


def _snapshot(paths):
    result = {}
    for path in paths:
        if not path.exists() and not path.is_symlink():
            result[path] = None
            continue
        info = path.lstat()
        result[path] = ((info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns),
            str(path.readlink()) if path.is_symlink() else path.read_bytes() if path.is_file() else None)
    return result


@pytest.mark.parametrize('pending', [False, True])
def test_complete_exact_marker_confirms_without_touching_any_target(tmp_path, pending):
    request, runs, submission, artifact, marker = _scene(tmp_path)
    path = runs/('rootless-delivery-operation.json'+('.rootless-pending' if pending else ''))
    path.write_text(json.dumps(marker))
    for other in (runs/'submitted_model.pkl.rootless-pending', submission/'model.pkl.rootless-pending',
                  runs/'submitted.json.rootless-pending'):
        other.write_bytes(b'unowned partial original')
    before = _snapshot(_paths(runs, submission, artifact))
    result = cov_delivery._confirm(request, runs, submission)
    assert result == dict(status='confirmed', operation_id=request['operation_id'],
        candidate_id=request['candidate_id'], artifact_ref=request['artifact_ref'],
        note='exact_public_submission_confirmed')
    assert _snapshot(before) == before


@pytest.mark.parametrize('damage', [
    'absent', 'truncated', 'non_object', 'missing_operation_id', 'missing_candidate_id',
    'missing_artifact_ref', 'missing_artifact_origin', 'missing_audit', 'missing_published',
    'missing_run_model', 'missing_submission_model', 'foreign_operation_id', 'foreign_candidate_id',
    'foreign_artifact_ref', 'foreign_artifact_origin', 'invalid_actual_audit', 'different_audit',
    'replaced_run_model', 'replaced_submission_model', 'missing_target_run_model',
    'missing_target_submission_model', 'missing_actual_audit', 'symlink_marker',
    'symlink_run_model', 'symlink_submission_model', 'symlink_actual_audit',
])
def test_incomplete_foreign_or_mismatched_pending_stays_unknown_and_unchanged(tmp_path, damage):
    request, runs, submission, artifact, marker = _scene(tmp_path)
    path = runs/'rootless-delivery-operation.json.rootless-pending'
    models = dict(run_model=runs/'submitted_model.pkl', submission_model=submission/'model.pkl')
    extra = []
    if damage.startswith('missing_') and damage[8:] in marker:
        marker.pop(damage[8:])
    elif damage in {'missing_run_model', 'missing_submission_model'}:
        marker['published'].pop(damage[8:])
    elif damage.startswith('foreign_'):
        key = damage[8:]
        marker[key] = 'foreign' if key in {'operation_id', 'candidate_id'} else dict(marker[key], path='foreign')
    elif damage in {'invalid_actual_audit', 'different_audit'}:
        actual = dict(marker['audit'])
        if damage == 'invalid_actual_audit':
            actual['audit_note'] = 'audit skipped'
            marker['audit'] = actual
        else:
            actual['audit_gap'] = .2
        (runs/'submitted.json').write_text(json.dumps(actual))
    elif damage.startswith('replaced_'):
        model = models[damage[9:]]
        replacement = model.with_name(model.name+'.replacement')
        replacement.write_bytes(b'x'*model.stat().st_size)
        replacement.replace(model)
    elif damage.startswith('missing_target_'):
        models[damage[15:]].unlink()
    elif damage == 'missing_actual_audit':
        (runs/'submitted.json').unlink()
    path.write_text(json.dumps(marker))
    if damage == 'absent':
        path.unlink()
        (runs/'submitted_model.pkl.rootless-pending').write_bytes(b'partial model only')
        (runs/'submitted.json.rootless-pending').write_bytes(b'partial receipt only')
    elif damage == 'truncated':
        path.write_bytes(b'{"operation_id":')
    elif damage == 'non_object':
        path.write_text('[]')
    elif damage.startswith('symlink_'):
        selected = dict(marker=path, actual_audit=runs/'submitted.json', **models)[damage[8:]]
        original = selected.with_name(selected.name+'.original')
        selected.replace(original);selected.symlink_to(original)
        extra.append(original)
    before = _snapshot((*_paths(runs, submission, artifact), *extra))
    assert cov_delivery._confirm(request, runs, submission)['status'] == 'unknown'
    assert _snapshot(before) == before


@pytest.mark.parametrize('matches', [False, True])
def test_foreign_canonical_marker_only_accepts_fully_matching_pending(tmp_path, matches):
    request, runs, submission, artifact, marker = _scene(tmp_path)
    (runs/'rootless-delivery-operation.json').write_text(json.dumps(dict(marker, operation_id='foreign')))
    pending = copy.deepcopy(marker)
    if not matches:pending['published']['submission_model'][1] += 1
    (runs/'rootless-delivery-operation.json.rootless-pending').write_text(json.dumps(pending))
    before = _snapshot(_paths(runs, submission, artifact))
    assert cov_delivery._confirm(request, runs, submission)['status'] == ('confirmed' if matches else 'unknown')
    assert _snapshot(before) == before


@pytest.mark.parametrize('target_index', range(4))
@pytest.mark.parametrize('kind', ['file', 'dangling_symlink', 'directory'])
def test_any_exact_pending_refuses_promotion_before_first_target_write(tmp_path, monkeypatch, target_index, kind):
    request, runs, submission, artifact, marker = _scene(tmp_path)
    (runs/'rootless-delivery-operation.json').write_text(json.dumps(marker))
    targets = (runs/'submitted_model.pkl', submission/'model.pkl', runs/'submitted.json',
               runs/'rootless-delivery-operation.json')
    pending = targets[target_index].with_name(targets[target_index].name+'.rootless-pending')
    if kind == 'file':pending.write_bytes(b'existing uncertain bytes')
    elif kind == 'dangling_symlink':pending.symlink_to(tmp_path/'missing-original')
    else:pending.mkdir()
    before = _snapshot(_paths(runs, submission, artifact))
    writes = []
    def unexpected_write(*args):
        writes.append(args)
        pytest.fail('Pending preflight allowed a target write')
    monkeypatch.setattr(cov_delivery, '_copy_selected', unexpected_write)
    monkeypatch.setattr(cov_delivery, '_replace', unexpected_write)
    monkeypatch.setattr(cov_delivery, '_write', unexpected_write)
    with pytest.raises(ValueError, match='Uncertain delivery target temporary exists'):
        cov_delivery._promote(request, runs, submission, artifact, marker['audit'])
    assert writes == []
    assert _snapshot(before) == before
