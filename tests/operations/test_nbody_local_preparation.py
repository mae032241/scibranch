"""Local prep's public-file and submission boundaries, without scientific execution."""
import json
from pathlib import Path

import pytest

from scripts.prepare_nbody_local import PUBLIC, REVISION, MAX_SUBMISSION, prepare, handoff, verify_public


def inputs(tmp_path):
    public=tmp_path/'original'; public.mkdir()
    for name in PUBLIC:
        path=public/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'public fixture\n')
    freeze=tmp_path/'freeze.json'
    freeze.write_text(json.dumps(dict(task=dict(commit=REVISION,id='nbody-accel-iterative',
        public_path=str(public),public_files=list(PUBLIC)),candidate=dict(source_commit='frozen-source',path='/unused-candidate'))))
    return public,freeze


def test_prepare_copies_only_frozen_public_files_and_no_control_or_scorer(tmp_path):
    public,freeze=inputs(tmp_path)
    (public/'hidden.txt').write_text('must not copy')
    root=tmp_path/'prepared';manifest=prepare(root,freeze)
    assert {str(p.relative_to(root/'public')) for p in (root/'public').rglob('*') if p.is_file()}==set(PUBLIC)
    assert not manifest['launch_ready'] and manifest['no_model']
    verify_public(root,manifest)
    result=handoff(root)
    assert (root/'submission/simulator.py').read_bytes()==(root/'workspace/simulator.py').read_bytes()
    assert result['size_bytes']==(root/'submission/simulator.py').stat().st_size
    assert 'sha256' not in result


@pytest.mark.parametrize('kind',['symlink','manifest_extra'])
def test_changed_or_unfrozen_public_material_rejected_before_output(tmp_path,kind):
    public,freeze=inputs(tmp_path)
    path=public/PUBLIC[0]
    if kind=='symlink':
        path.unlink();path.symlink_to(public/PUBLIC[1])
    else:
        value=json.loads(freeze.read_text());value['task']['public_files'].append('hidden.txt')
        freeze.write_text(json.dumps(value))
    root=tmp_path/'prepared'
    with pytest.raises(ValueError):prepare(root,freeze)
    assert not root.exists()


def test_added_material_after_prepare_rejected_before_namespace_mount(tmp_path):
    _,freeze=inputs(tmp_path);root=tmp_path/'prepared';manifest=prepare(root,freeze)
    (root/'public/extra.txt').write_text('unfrozen material')
    with pytest.raises(ValueError,match='whitelist_changed'):verify_public(root,manifest)


@pytest.mark.parametrize('kind',['symlink','oversized','empty'])
def test_submission_nonregular_or_unbounded_does_not_enter_handoff(tmp_path,kind):
    _,freeze=inputs(tmp_path);root=tmp_path/'prepared';prepare(root,freeze)
    path=root/'workspace/simulator.py'
    if kind=='symlink':path.unlink();path.symlink_to(root/'host-private/canary.txt')
    elif kind=='oversized':
        with path.open('wb') as stream:stream.truncate(MAX_SUBMISSION+1)
    else:path.write_bytes(b'')
    with pytest.raises((OSError,ValueError)):handoff(root)
    assert not (root/'submission/simulator.py').exists()


def test_public_verification_only_inspects_regular_file_sizes(tmp_path, monkeypatch):
    from scripts import prepare_nbody_local as local
    _, freeze=inputs(tmp_path);root=tmp_path/'prepared';manifest=prepare(root,freeze)
    monkeypatch.setattr(local, 'regular_bytes', lambda *args, **kwargs:pytest.fail('No payload read during validation'))
    verify_public(root,manifest)
    path=root/'public'/PUBLIC[0]
    path.write_bytes(b'changed size')
    with pytest.raises(ValueError,match='prepared_public_input_changed'):
        verify_public(root,manifest)
