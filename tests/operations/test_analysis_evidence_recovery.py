"""Bounded production evidence recovery paths; no solver is launched."""
from tests.operations.tcad_policy_fixtures import policy_fields
import json
from pathlib import Path
import pytest


def test_collection_keeps_later_products(tmp_path):
    from tcad_artifact.remote_runner_py36 import _collect_expected
    (tmp_path/'later.plx').write_bytes(b'raw data\n')
    expected=[dict(name=n,relative_path=p,required=True,max_bytes=1024,media_type='text/plain') for n,p in [('missing','missing.tdr'),('later','later.plx')]]
    records=[]; errors=[]
    _collect_expected(str(tmp_path),expected,{'max_output_bytes':4096,'transfer_chunk_bytes':1048576},records,errors)
    assert [x['name'] for x in records]==['later']
    assert len(errors)==1 and 'missing.tdr' in errors[0]


@pytest.mark.process_e2e
def test_command_ssh_remote_inspection_roundtrip(tmp_path):
    import os
    import sys
    from tcad_artifact.command_adapter import CommandAdapterConfig, CommandTCADExecutorAdapter
    run_id = 'run_' + 'a'*32
    directory = tmp_path / 'remote' / run_id
    (directory / 'work').mkdir(parents=True)
    (directory / 'done').touch()
    (directory / 'work/actual.plx').write_bytes(b'original bytes')
    script = tmp_path / 'transport.py'
    script.write_text('''import json,sys
from pathlib import Path
from tcad_artifact.ssh_transport import SSHTCADTransport
from tcad_artifact.remote_runner_py36 import _rpc
root=Path(sys.argv[1])
config=json.loads(sys.argv[2])
config['result_root']=str(root/'remote')
class Remote:
    def rpc(self,request): return _rpc(config,{'request':request})
    def get_to(self,path,destination,max_bytes):
        raw=Path(path).read_bytes()
        assert len(raw)<=max_bytes
        destination.write_bytes(raw)
transport=SSHTCADTransport(Remote(),local_result_root=root/'local')
request=json.load(sys.stdin)
value=transport.handle(request['operation'],request['payload'])
print(json.dumps(dict(schema_version=1,operation=request['operation'],ok=True,payload=value)))
''')
    adapter = CommandTCADExecutorAdapter(CommandAdapterConfig(executable=sys.executable,
        arguments=(str(script), str(tmp_path), json.dumps(policy_fields())), environment={'PYTHONPATH':os.pathsep.join(sys.path)}),
        local_result_root=tmp_path/'local')
    listed = adapter.inspect_outputs(run_id)
    assert listed['status'] == 'available' and listed['files'][0]['relative_path'] == 'actual.plx'
    value = adapter.inspect_outputs(run_id, 'actual.plx')
    assert value['status'] == 'available'
    assert Path(value['file']['local_path']).read_bytes() == b'original bytes'
    assert adapter.inspect_outputs(run_id, 'actual.plx', max_bytes=1)['status'] == 'limit_exceeded'
    assert (directory/'work/actual.plx').read_bytes() == b'original bytes'


@pytest.mark.process_e2e
def test_ssh_inspection_download_streams_through_remote_protocol(tmp_path, monkeypatch):
    import sys
    from tcad_artifact.ssh_transport import SSHRemoteClient, SSHTCADTransportConfig
    from tcad_artifact import remote_runner_py36
    raw = b'0123456789' * 220000
    source = tmp_path/'source'
    source.write_bytes(raw)
    client = SSHRemoteClient(SSHTCADTransportConfig(transfer_chunk_bytes=1048576, max_transfer_bytes=2100000000, ssh_executable=sys.executable,
        destination='a@fixture',remote_helper='/fixture/helper',remote_config='/fixture/config',remote_exchange_root='/fixture/exchange'))
    from scidiscovery.plugin_runtime.collection import run_bounded
    def run(command, **kwargs):
        assert callable(kwargs['sink'])
        code = "import runpy,sys,json; m=runpy.run_path(sys.argv[1]); m['_handle'](json.loads(sys.argv[2]),json.loads(sys.stdin.buffer.readline()),sys.stdin.buffer,sys.stdout.buffer)"
        return run_bounded([sys.executable,'-c',code,remote_runner_py36.__file__,
                               json.dumps({'result_root':str(tmp_path),'max_transfer_bytes':4*1024*1024,'runner':policy_fields()['runner']})], **kwargs)
    monkeypatch.setattr('tcad_artifact.ssh_transport.run_bounded', run)
    target = tmp_path/'download'
    client.get_to(str(source), target, len(raw))
    assert target.read_bytes() == raw


def test_missing_execution_binding_reports_exact_repair_without_discovery():
    from types import SimpleNamespace
    from tcad_artifact.output_recovery import _inspect
    def missing(name):
        raise ValueError('tool requested an undeclared Run input')
    context=SimpleNamespace(input_ref=missing)
    result=_inspect(context,'execution_result',None,run_deadline=1)
    assert result['reason']=='execution_result_not_bound'
    assert result['message']=='tool requested an undeclared Run input'
    assert 'result_artifact_name' in result['repair'] and 'Frozen inputs' in result['repair']
