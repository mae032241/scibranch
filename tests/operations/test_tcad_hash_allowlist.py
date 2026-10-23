"""TCAD scientific transport has explicit owners and sizes, with no checksums."""
import hashlib
import io
import json
from pathlib import Path
import tarfile

import pytest

from tcad_artifact import execution_control as control, project_packager as packager
from tcad_artifact import remote_runner_py36 as runner, ssh_transport as transport


def forbid_hash(*args,**kwargs):
    pytest.fail('Scientific TCAD IO computed a content hash')


def test_descriptors_stat_scientific_original_without_reading(tmp_path,monkeypatch):
    source=tmp_path/'model.tdr';source.write_bytes(b'scientific binary')
    monkeypatch.setattr(hashlib,'sha256',forbid_hash)
    monkeypatch.setattr(Path,'read_bytes',lambda path:pytest.fail('Descriptor read scientific original'))
    assert runner._descriptor('model',str(source),'application/octet-stream')['size_bytes']==17
    assert control._descriptor(name='model',path=source,media_type='application/octet-stream')['size_bytes']==17
    assert transport._local_descriptor('model',source,'application/octet-stream').size_bytes==17
    assert 'sha256' not in control.FileDescriptor.model_fields
    assert 'sha256' not in control.ArchiveEntry.model_fields
    assert 'sha256' not in packager.NativeOriginalRef.model_fields
    assert 'sha256' not in packager.RuntimeOutputRecord.model_fields


def test_archive_manifest_and_extraction_use_exact_members_and_sizes(tmp_path,monkeypatch):
    source=tmp_path/'input.tdr';source.write_bytes(b'opaque input')
    archive=tmp_path/'project.tar'
    monkeypatch.setattr(hashlib,'sha256',forbid_hash)
    entries=packager._write_archive((packager.DeckFile(relative_path='deck.cmd',content='go'),),
        {'input.tdr':source},archive)
    assert {item.relative_path:item.size_bytes for item in entries}=={'deck.cmd':2,'input.tdr':12}
    output=tmp_path/'work'
    runner._extract_archive(str(archive),[item.model_dump(mode='json') for item in entries],str(output),3)
    assert (output/'input.tdr').read_bytes()==b'opaque input'
    wrong=[dict(item.model_dump(mode='json'),size_bytes=item.size_bytes+1) for item in entries]
    with pytest.raises(ValueError,match='manifest'):
        runner._extract_archive(str(archive),wrong,str(tmp_path/'wrong'),3)


def test_upload_and_tar_download_have_no_checksum_trailer(tmp_path,monkeypatch):
    exchange=tmp_path/'exchange';results=tmp_path/'results';exchange.mkdir();results.mkdir()
    config=dict(exchange_root=str(exchange),result_root=str(results),max_transfer_bytes=1024,
        runner=dict(transfer_chunk_bytes=3))
    monkeypatch.setattr(hashlib,'sha256',forbid_hash)
    runner._put(config,dict(relative_path='original/source',size_bytes=7),io.BytesIO(b'payload'))
    assert (exchange/'original/source').read_bytes()==b'payload'
    source=results/'model.tdr';source.write_bytes(b'payload')
    response=io.BytesIO()
    runner._stream_get_archive(config,dict(files=[dict(local_path=str(source),size_bytes=7)]),response)
    header,raw=response.getvalue().split(b'\n',1)
    assert json.loads(header)['schema_version']==2
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as archive:
        assert archive.extractfile('files/0').read()==b'payload'
    assert b'sha256:' not in raw
    link=results/'linked';link.symlink_to(source)
    with pytest.raises((OSError,ValueError)):
        runner._stream_get_archive(config,dict(files=[dict(local_path=str(link),size_bytes=7)]),io.BytesIO())


def test_retirement_fences_exact_original_request_id_without_hash(tmp_path,monkeypatch):
    config=dict(state_root=str(tmp_path/'state'),result_root=str(tmp_path/'runs'))
    Path(config['result_root']).mkdir()
    monkeypatch.setattr(hashlib,'sha256',forbid_hash)
    request='a'*32
    first=runner._retire_submission(config,request)
    assert first==runner._retire_submission(config,request)
    assert first==dict(found=False,retired=True,submission_id=request)
    assert (Path(config['state_root'])/'retired_submissions'/request).read_text()==request+'\n'
    with pytest.raises(ValueError):runner._lookup_submission(config,'a'*64)


def test_old_wire_and_job_schema_are_rejected_without_migration(tmp_path):
    with pytest.raises(ValueError,match='envelope'):
        runner._handle({},dict(schema_version=1,operation='rpc',payload={}),io.BytesIO(),io.BytesIO())
    assert control.TCADJobSpec.model_fields['schema_version'].default==5
    assert 'request_id' in control.TCADJobSpec.model_fields


def test_generated_capture_skips_large_unchanged_input_before_output_quota(tmp_path,monkeypatch):
    source=tmp_path/'input.tdr';source.write_bytes(b'x'*8192)
    entries=[dict(relative_path='input.tdr',size_bytes=8192)]
    monkeypatch.setattr(hashlib,'sha256',forbid_hash)
    before=runner._input_generations(str(tmp_path),entries)
    limits=dict(max_output_bytes=4,transfer_chunk_bytes=3)
    with pytest.raises(RuntimeError,match='no generated output files'):
        runner._collect_expected(str(tmp_path),[],limits,archive_entries=entries,
            input_generations=before,collect_generated_outputs=True)
    (tmp_path/'result.plx').write_bytes(b'new')
    outputs=runner._collect_expected(str(tmp_path),[],limits,archive_entries=entries,
        input_generations=before,collect_generated_outputs=True)
    assert [(item['relative_path'],item['size_bytes']) for item in outputs]==[('result.plx',3)]


def test_generated_capture_retains_same_path_same_size_modified_input(tmp_path,monkeypatch):
    import os
    source=tmp_path/'main.cmd';source.write_bytes(b'before')
    entries=[dict(relative_path='main.cmd',size_bytes=6)]
    monkeypatch.setattr(hashlib,'sha256',forbid_hash)
    before=runner._input_generations(str(tmp_path),entries)
    metadata=source.stat()
    source.write_bytes(b'after!')
    os.utime(source,ns=(metadata.st_atime_ns,metadata.st_mtime_ns+1))
    outputs=runner._collect_expected(str(tmp_path),[],dict(max_output_bytes=6),
        archive_entries=entries,input_generations=before,collect_generated_outputs=True)
    assert [(item['relative_path'],item['size_bytes']) for item in outputs]==[('main.cmd',6)]


@pytest.mark.parametrize('target_key',['config','runner'])
@pytest.mark.parametrize('after_replace',[False,True])
def test_deployment_rollback_recognizes_owned_inode_at_publication_crash(tmp_path,monkeypatch,target_key,after_replace):
    import base64
    import importlib.util
    project=Path(__file__).resolve().parents[2]
    script=project/'plugins/tcad_artifact/deploy/remote_deployment.py'
    spec=importlib.util.spec_from_file_location('hash_free_deployment_crash',script)
    deployment=importlib.util.module_from_spec(spec);spec.loader.exec_module(deployment)
    root=tmp_path/'remote';runner_path=root/'bin/scidiscovery-tcad-ssh-runner'
    config=root/'config/runner.json';runner_path.parent.mkdir(parents=True);config.parent.mkdir()
    runner_path.write_bytes(b'old runner');runner_path.chmod(0o750)
    config.write_text(json.dumps(dict(result_root=str(root/'runs'))));config.chmod(0o640)
    originals=(runner_path.read_bytes(),config.read_bytes())
    monkeypatch.setattr(deployment,'probe',lambda *args:dict(policy_available=True))
    monkeypatch.setattr(hashlib,'sha256',forbid_hash)
    request=dict(phase='prepare',transaction='d'*32,runner=str(runner_path),config=str(config),
        source=base64.b64encode(b'new runner').decode(),configuration=None)
    assert deployment.deploy(request)['state']=='prepared'
    real_replace=deployment.os.replace
    selected=runner_path if target_key=='runner' else config
    fired=[]
    class Crash(BaseException):pass
    def interrupted(source,target):
        is_publication=(Path(target)==selected and '.deploy-' in Path(source).name and not fired)
        if is_publication:
            fired.append(True)
            if after_replace:real_replace(source,target)
            raise Crash('power loss at new inode publication')
        return real_replace(source,target)
    monkeypatch.setattr(deployment.os,'replace',interrupted)
    with pytest.raises(Crash):deployment.deploy(dict(request,phase='activate'))
    manifest_path=root/'.deployments'/request['transaction']/'manifest.json'
    manifest=json.loads(manifest_path.read_bytes())
    staged=Path(manifest['publication_paths'][target_key])
    assert deployment.binding(selected if after_replace else staged)==manifest['published'][target_key]
    assert deployment.deploy(dict(request,phase='rollback'))['state']=='rolled_back'
    assert (runner_path.read_bytes(),config.read_bytes())==originals
    assert not staged.exists()


def test_command_adapter_and_bundled_transport_use_same_wire_version(tmp_path,monkeypatch):
    import sys
    from types import SimpleNamespace
    from tcad_artifact import command_adapter as command
    adapter=command.CommandTCADExecutorAdapter(command.CommandAdapterConfig(executable=sys.executable),local_result_root=tmp_path)
    def reply(argv,*,input,**kwargs):
        request=json.loads(input)
        assert request==dict(schema_version=2,operation='capabilities',payload={})
        stream=io.BytesIO()
        runner._write_response(stream,'capabilities',dict(capabilities=[]))
        return SimpleNamespace(returncode=0,stdout=stream.getvalue(),stderr=b'')
    monkeypatch.setattr(command,'run_bounded',reply)
    assert adapter._call('capabilities',{})==dict(capabilities=[])
