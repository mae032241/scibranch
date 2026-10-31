"""Small sealed-reader checks; no HTTP, native subprocess, backend or model."""
import io
from pathlib import Path

import pytest

from scidiscovery.research_loop import budget, contracts as c, execution, instance_display as display, instance_science as science
from test_execution_authority import prepare
from test_history_handoff import finish


def scene(tmp_path):
    root,_=prepare(tmp_path,enabled=False)
    finish(root,1,'H',[],action='analyze',text='Exact scientific finding, with a known limitation.',
        limitations=['A bounded fixture, not scientific qualification.'],
        files={'report.html':'<script>must remain an attachment</script>',
               'figure.svg':'<svg onload="never execute"/>','values.csv':'x,y\n1,2\n',
               'z%20literal.txt':'A literal scientific filename, not URL input.'})
    return root


def read(original):
    with original:
        target=io.BytesIO();original.copy_to(target)
        return target.getvalue()


def test_science_and_complete_material_pages_are_read_only_without_budget_clock(tmp_path,monkeypatch):
    root=scene(tmp_path)
    before={path.relative_to(root):path.read_bytes() for path in root.rglob('*') if path.is_file()}
    def no_clock(*args,**kwargs):
        pytest.fail('Scientific original reading must not sample a live budget or enter broader continuation validation')
    monkeypatch.setattr(budget,'clock_stamp',no_clock)
    monkeypatch.setattr(execution,'_terminal',no_clock)
    assert read(science.science(root,1))==before[next(path for path in before if str(path)=='native-evidence/001/science-output.json')]
    rows=[];after=0
    while True:
        page=science.materials(root,1,after=after,limit=1);rows.extend(page['items'])
        if page['next_after'] is None:break
        after=page['next_after']
    assert [row['index'] for row in rows]==[1,2,3,4]
    assert {row['name'] for row in rows}=={'report.html','figure.svg','values.csv','z%20literal.txt'}
    original,name=science.artifact(root,1,rows[0]['index'])
    assert read(original)==(c.workspace(root,1)/'artifacts'/name).read_bytes()
    literal=next(row for row in rows if row['name']=='z%20literal.txt')
    assert read(science.artifact(root,1,literal['index'])[0])==b'A literal scientific filename, not URL input.'
    assert before=={path.relative_to(root):path.read_bytes() for path in root.rglob('*') if path.is_file()}


def directory_scene(tmp_path, names):
    """Reader-boundary fixture with explicit sealed logical membership."""
    root=scene(tmp_path)
    seal_path=root/'native-evidence/001/result-identity.json';seal=c.read(seal_path)
    rows=[]
    for name in names:
        source=c.workspace(root,1)/'artifacts'/name
        source.parent.mkdir(parents=True,exist_ok=True)
        content=f'Exact directory fixture: {name}'.encode()
        source.write_bytes(content)
        rows.append(dict(source=str(source),path=f'inputs/parents/001/artifacts/{name}',size_bytes=len(content)))
    seal['materials']=rows;c.write(seal_path,seal)
    return root


@pytest.mark.parametrize('reader',[science,display],ids=['science','display'])
def test_directory_navigation_groups_sealed_names_and_preserves_download_ordinals(tmp_path,reader):
    names=['root-note.txt','experiments/call-b/artifacts/curve.csv',
        'experiments/call-a/artifacts/curve.csv','reports/nested/finding.txt',
        'experiments/call-a/log.txt','experiments/call-a/artifacts/z%20literal.txt']
    root=directory_scene(tmp_path,names)
    flat=reader.materials(root,1)
    assert [row['name'] for row in flat['items']]==names
    assert 'directory' not in flat and 'total_files' not in flat
    page=reader.materials(root,1,directory='')
    assert page['directory']=='' and page['total']==3 and page['total_files']==6
    assert page['items'][:2]==[
        dict(kind='directory',name='experiments',path='experiments',file_count=4),
        dict(kind='directory',name='reports',path='reports',file_count=1)]
    assert page['items'][2]==dict(kind='file',index=1,name='root-note.txt',path='root-note.txt',
        size_bytes=len(b'Exact directory fixture: root-note.txt'))
    calls=reader.materials(root,1,directory='experiments')
    assert [(row['name'],row['file_count']) for row in calls['items']]==[('call-a',3),('call-b',1)]
    call=reader.materials(root,1,directory='experiments/call-a')
    assert [row['kind'] for row in call['items']]==['directory','file']
    assert call['items'][1]['index']==5
    for directory,index in [('experiments/call-a/artifacts',3),('experiments/call-b/artifacts',2)]:
        files=reader.materials(root,1,directory=directory)
        curve=next(row for row in files['items'] if row['name']=='curve.csv')
        assert curve['index']==index and curve['path']==names[index-1]
        original,name=reader.artifact(root,1,curve['index'])
        assert name==names[index-1] and read(original)==f'Exact directory fixture: {name}'.encode()
    # Derived folder metadata does not open each artifact, or expose host sources.
    source=c.workspace(root,1)/'artifacts'/names[3];source.unlink()
    nested=reader.materials(root,1,directory='reports/nested')
    assert nested['items'][0]['index']==4
    assert all('source' not in item for item in nested['items'])


@pytest.mark.parametrize('reader',[science,display],ids=['science','display'])
def test_directory_pagination_is_bounded_per_folder_with_mixed_children(tmp_path,reader):
    names=[]
    for number in range(23):
        names.extend([f'collection/item-{number:02d}.txt',f'collection/folder-{number:02d}/data.csv'])
    root=directory_scene(tmp_path,names)
    entries=[];after=0
    while True:
        page=reader.materials(root,1,directory='collection',after=after,limit=20)
        assert page['total']==46 and page['total_files']==46 and len(page['items'])<=20
        entries.extend(page['items'])
        if page['next_after'] is None:break
        after=page['next_after']
    assert len(entries)==46
    assert [row['name'] for row in entries[:23]]==[f'folder-{number:02d}' for number in range(23)]
    assert [row['index'] for row in entries[23:]]==list(range(1,46,2))
    assert reader.materials(root,1,directory='collection',after=46)['items']==[]


@pytest.mark.parametrize('directory',[False,1,'.','..','/','/experiments','experiments/',
    './experiments','experiments//call-a','experiments/../reports','experiments/./call-a',
    'experiments\\call-a'])
@pytest.mark.parametrize('reader',[science,display],ids=['science','display'])
def test_directory_navigation_rejects_noncanonical_paths(tmp_path,directory,reader):
    with pytest.raises(ValueError,match='directory required'):
        reader.materials(tmp_path,1,directory=directory)


@pytest.mark.parametrize('reader',[science,display],ids=['science','display'])
def test_directory_navigation_rejects_unsealed_or_file_names(tmp_path,reader):
    root=directory_scene(tmp_path,['experiments/call-a/artifacts/curve.csv'])
    private=c.workspace(root,1)/'artifacts/private';private.mkdir()
    for directory in ['private','experiments/call-b','experiments/call-a/artifacts/curve.csv']:
        with pytest.raises(ValueError,match='directory is unavailable'):
            reader.materials(root,1,directory=directory)


def test_unsealed_tampered_missing_and_linked_originals_are_unavailable(tmp_path):
    root=scene(tmp_path)
    c.build_job(root,2,'A',[1])
    with pytest.raises(ValueError,match='missing|unavailable'):
        science.science(root,2)
    source=Path(c.read(root/'native-evidence/001/result-identity.json')['materials'][0]['source']);original=source.read_bytes()
    source.chmod(0o600);source.write_bytes(original+b'changed size')
    with pytest.raises(ValueError,match='changed'):
        science.artifact(root,1,1)
    source.unlink()
    # Saved identity resolution can surface the missing original before the
    # download stream translates inaccessible paths into ValueError.
    with pytest.raises((ValueError,FileNotFoundError),match='missing|inaccessible|No such file'):
        science.artifact(root,1,1)
    outside=tmp_path/'private';outside.write_bytes(original);source.symlink_to(outside)
    with pytest.raises(ValueError,match='source escaped its exact output owner'):
        science.artifact(root,1,1)
    raw=root/'native-evidence/001/science-output.json';raw.chmod(0o600);raw.write_bytes(b'{}')
    with pytest.raises(ValueError,match='changed'):
        science.materials(root,1)


def test_verified_download_never_reopens_a_replaced_path(tmp_path):
    root=scene(tmp_path)
    original,name=science.artifact(root,1,1)
    path=c.workspace(root,1)/'artifacts'/name;expected=path.read_bytes()
    private=b'X'*len(expected)
    with original:
        path.unlink();path.write_bytes(private)
        output=io.BytesIO()
        try:original.copy_to(output)
        except ValueError:pass  # Mutation may be detected before sending any body.
        assert output.getvalue() in (b'',expected) and output.getvalue()!=private
    assert original.stream.closed


def test_seal_membership_cannot_turn_host_private_paths_into_deliverables(tmp_path):
    root=scene(tmp_path)
    private=root/'execution-authority/host-private.json';private.parent.mkdir(exist_ok=True)
    private.write_bytes(b'private fixture credential, never a scientific download')
    seal_path=root/'native-evidence/001/result-identity.json';seal=c.read(seal_path)
    # Rebind the public-facing seal row to bypass mere hash matching. The owner
    # boundary must still reject this independent of scientific JSON content.
    info=private.stat()
    seal['materials'][0].update(source=str(private),size_bytes=info.st_size,
        file_stat=[info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns])
    c.write(seal_path,seal)
    with pytest.raises(ValueError,match='output owner|output binding'):
        science.artifact(root,1,1)
    for value in (0,-1,True,100):
        with pytest.raises(ValueError,match='index'):
            science.artifact(root,1,value)


def test_managed_deliverable_reads_only_its_existing_sealed_output_snapshot(tmp_path):
    root,_=prepare(tmp_path,enabled=False)
    finish(root,1,'H',[],action='experiment');finish(root,2,'W',[1])
    seal_path=root/'native-evidence/002/result-identity.json';seal=c.read(seal_path)
    data=b'an existing declared managed scientific output'
    snapshot=root/'host-history/objects'/'fixture-managed-original';snapshot.parent.mkdir(parents=True,exist_ok=True);snapshot.write_bytes(data)
    source=c.workspace(root,2)/'work/lineage/calls/original-call/outputs/curve.csv'
    material=dict(source=str(snapshot),path='inputs/parents/002/artifacts/experiments/original-call/artifacts/curve.csv',
        size_bytes=len(data))
    output=dict(name='curve.csv',path=source.relative_to(root).as_posix(),snapshot=snapshot.relative_to(root).as_posix(),
        size_bytes=len(data),retention='archived')
    observation=root/'host-history/observations/002.json'
    c.write(observation,dict(binding=dict(source_ref=seal['raw_ref']),managed_calls=[dict(call_id='original-call',
        scope=dict(step=2,job_ref=seal['job_ref']),outputs=[output])]))
    seal.update(materials=[material],observations=dict(path=observation.relative_to(root).as_posix(),size_bytes=observation.stat().st_size))
    c.write(seal_path,seal)
    # A frozen-reader boundary fixture, not a backend/terminal execution verdict.
    original,name=science.artifact(root,2,1)
    assert name.endswith('curve.csv') and read(original)==data and not source.exists()
    snapshot.unlink()
    with pytest.raises(ValueError,match='missing|inaccessible'):
        science.artifact(root,2,1)


def test_readonly_download_survives_cross_root_link_changes_without_reopening(tmp_path):
    import os
    source = tmp_path / 'source'
    raw = b'opaque immutable download\n' * 50000
    source.write_bytes(raw); source.chmod(0o400)
    other_root = tmp_path / 'other-owner'; other_root.mkdir()
    retained = other_root / 'retained-original'
    proof = dict(size_bytes=len(raw))
    class LinkingOutput(io.BytesIO):
        def write(self, block):
            if not retained.exists():
                os.link(source, retained)
            else:
                retained.unlink()
            return super().write(block)
    with science.Original(source, proof) as original:
        output = LinkingOutput()
        original.copy_to(output)
        assert output.getvalue() == raw
        if retained.exists():
            retained.unlink()
        original.check()
    assert source.read_bytes() == raw and source.stat().st_mode & 0o777 == 0o400


@pytest.mark.parametrize('mutation', ['mode', 'mtime', 'size'])
def test_readonly_original_still_rejects_content_and_other_metadata_changes(tmp_path, mutation):
    import os
    source = tmp_path / 'source'
    raw = b'Exact scientific original bytes.'
    source.write_bytes(raw); source.chmod(0o400)
    proof = dict(size_bytes=len(raw))
    with science.Original(source, proof) as original:
        before = source.stat()
        if mutation == 'mode':
            source.chmod(0o600)
        elif mutation == 'mtime':
            os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns + 1000000000))
        else:
            source.chmod(0o600)
            source.write_bytes(raw + b'changed size' if mutation == 'size' else b'X' * len(raw))
            source.chmod(0o400)
            os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
        with pytest.raises(ValueError, match='changed during reading'):
            original.check()


def test_mutable_original_does_not_ignore_link_metadata_changes(tmp_path):
    import os
    source = tmp_path / 'source'; source.write_bytes(b'Mutable original')
    source.chmod(0o600)
    with science.Original(source) as original:
        os.link(source, tmp_path / 'alias')
        with pytest.raises(ValueError, match='changed during reading'):
            original.check()
