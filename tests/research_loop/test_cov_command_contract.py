"""Cov single-source admission and public contracts; no executor is started."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import experiments as e
from cov_execution import experiments as cov_experiments
from test_cov_lineage import wrapper
from execution_fixtures import execution_registry, remote_registry


@pytest.mark.parametrize('options', ['directory', 'arguments'])
def test_cov_unsupported_options_are_caller_errors_before_prepare(wrapper, monkeypatch, capsys, options):
    script = wrapper.script('candidate')

    def forbidden(*args, **kwargs):
        pytest.fail('Unsupported caller options reached executor preparation or submission')

    monkeypatch.setattr(cov_experiments.CovExecution, 'prepare', forbidden)
    monkeypatch.setattr(cov_experiments.CovExecution, 'submit', forbidden)
    args = ['--config', str(wrapper.config), 'run', '--script', str(script)]
    args += ['--experiment-dir', str(wrapper.root)] if options == 'directory' else ['--', '--seed', '7']
    assert e.main(args) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'caller_error' and result['accepted'] is False
    assert result['error'] == 'invalid_arguments'
    assert result['note'].startswith('Requested command options are unavailable:')
    assert 'does not support --experiment-dir or script arguments' in result['note']
    assert 'call_id' not in result and wrapper.accepted() == [] and wrapper.calls() == b''


def test_other_admission_errors_keep_unresolved_classification(wrapper, capsys):
    script = wrapper.script('candidate')
    assert e.main(['--config', str(wrapper.config), 'run', '--script', str(script),
        '--parent-plan', 'absent-candidate']) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'unresolved'
    assert result['note'] == 'Execution request could not be resolved; check declared inputs and preserve any original call'
    assert 'accepted' not in result and wrapper.accepted() == [] and wrapper.calls() == b''


def test_cov_single_source_contract_is_frozen_discoverable_and_exact(wrapper, capsys):
    original_config=wrapper.config.read_bytes()
    config = json.loads(wrapper.config.read_text())
    frozen = config['execution_interface']['public_capabilities']
    assert e.INPUT_SOURCES_KEY not in {item['key'] for item in frozen}
    assert e.main(['--config', str(wrapper.config), 'capabilities']) == 0
    listed = json.loads(capsys.readouterr().out)['contracts']
    scientific_fields={'key','kind','schema_id','payload_schema_version','media_type','public_summary'}
    assert all(set(item)==scientific_fields for item in listed)
    assert all(set(item)==scientific_fields|{'size_bytes'} for item in frozen)
    assert listed == [{key:item[key] for key in scientific_fields} for item in frozen]
    summary = next(item['public_summary'] for item in listed if item['key'] == 'cov_single_source')
    assert summary['accepted_source'] == 'exact accepted source original'
    assert summary['experiment_directory'] is False and summary['script_arguments'] is False
    output = wrapper.root / 'single-source-contract.json'
    assert e.main(['--config', str(wrapper.config), 'describe', '--key', 'cov_single_source', '--output', str(output)]) == 0
    capsys.readouterr()
    assert json.loads(output.read_text()) == summary
    bound=next(item for item in frozen if item['key']=='cov_single_source')
    assert output.stat().st_size==bound['size_bytes']
    changed = deepcopy(config)
    changed['execution_interface']['public_capabilities'] = []
    with pytest.raises(e.IntegrityError, match='contract or configuration changed'):
        e.load(changed, path=wrapper.config)
    for field,value in (('public_summary',dict(unapproved=True)),('size_bytes',bound['size_bytes']+1)):
        changed=deepcopy(config)
        next(item for item in changed['execution_interface']['public_capabilities'] if item['key']=='cov_single_source')[field]=value
        with pytest.raises(e.IntegrityError,match='contract or configuration changed'):
            e.load(changed,path=wrapper.config)
    assert wrapper.config.read_bytes()==original_config


@pytest.mark.parametrize('registered', [False, True])
def test_public_contracts_freeze_without_managed_source_key(tmp_path, remote_registry, registered):
    from scidiscovery.research_loop.protocol import CURRENT
    options = dict(registered_implementation=dict(factory='previous-installation:factory',plugin_version='old',code=[])) if registered else {}
    config = e.freeze(dict(options, record_dir=str(tmp_path / 'records'),
        scope=dict(handoff_protocol=CURRENT, step=2, role='W', job_ref=dict(path='/fixture/jobs/002.json'),
            execution_key=dict(path='/fixture/native-evidence/002/native-intent.json'), mechanism_ref=None)), 'remote_execution:fixture', path=tmp_path / 'config.json')
    assert ('registered_implementation' in config) is registered
    contracts = config['execution_interface']['public_capabilities']
    assert [item['key'] for item in contracts] == ['opaque_input']
    assert contracts == e._public_capabilities(e.load(config, path=tmp_path / 'config.json'))
    changed = deepcopy(config)
    changed['execution_interface']['public_capabilities'][0]['size_bytes'] += 1
    with pytest.raises(e.IntegrityError, match='contract or configuration changed'):
        e.load(changed, path=tmp_path / 'config.json')


@pytest.mark.parametrize('registered', [False, True])
def test_empty_capability_adapter_preserves_public_contract_presence(tmp_path, remote_registry, monkeypatch, registered):
    from execution_fixtures import DifferentExternalIdentity
    from scidiscovery.research_loop.protocol import CURRENT
    monkeypatch.setattr(DifferentExternalIdentity, 'capabilities', lambda self: ())
    options = dict(registered_implementation=dict(factory='previous-installation:factory',plugin_version='old',code=[])) if registered else {}
    config = e.freeze(dict(options, record_dir=str(tmp_path / 'records'),
        scope=dict(handoff_protocol=CURRENT, step=2, role='W', job_ref=dict(path='/fixture/jobs/002.json'),
            execution_key=dict(path='/fixture/native-evidence/002/native-intent.json'), mechanism_ref=None)), 'remote_execution:fixture', path=tmp_path / 'config.json')
    interface = config['execution_interface']
    assert ('public_capabilities' in interface) is registered
    if registered:
        assert interface['public_capabilities'] == []


def _fixed_cov_call(tmp_path,monkeypatch):
    """Retained Cov command facts only; no namespace or external work starts."""
    from cov_execution import receipt
    from scidiscovery.research_loop import code_versions, contracts as c
    root=tmp_path/'instance';root.mkdir(parents=True)
    work=c.workspace(root,1);work.mkdir(parents=True)
    c.write(root/'jobs/001.json',dict(step=1,role='W',worker_view_version=1,
        global_materials=[],direct_refs=[],direct_materials=[]))
    code=work/'code';code.mkdir()
    source=code/'run.py';source.write_text("print('fixed source')\n")
    call_id='0001-command-contract'
    version=dict(version=1,object_format='sha1',commit='a'*40,tree_path='.',entry='run.py',
        selection='file',logical_root='code')
    historical=root/'historical-export/run.py';historical.parent.mkdir()
    historical.write_bytes(source.read_bytes());script=e.proof(historical)
    public=tmp_path/'public';harness=public/'environment/harness';harness.mkdir(parents=True)
    runner=harness/'runner.py'
    runner.write_text("def main():\n with open(RESULTS_LOG, 'a') as f:\n  f.write(json.dumps(rec) + '\\n')\n print(json.dumps(rec, indent=2))\n sys.exit(0 if rec.get('ok') else 1)\n")
    config=dict(run_argv=[sys.executable],max_seconds=30,scope=dict(step=1,role='W'),
        record_dir=str(root/'host-runtime/experiment-control/001/records'),
        execution_interface=dict(executor='cov_execution:experiment'))
    accepted=dict(call_id=call_id,scope=config['scope'],parent=dict(status='unknown',candidate_id=None,source='none'),
        code_version=version,script=script,source_script=str(source),snapshot_files=[],
        command=[sys.executable,'run','--script',str(source)],script_index=3,cwd=None,
        prepared=dict(local_path=str(historical),size_bytes=script['size_bytes']),
        receipt_transport=dict(version=1,channel='public_runner_main',runner=receipt.runner_profile(runner)))
    record=Path(config['record_dir'])/'calls'/call_id;c.write(record/'accepted.json',accepted)
    historical.unlink()  # A command read must not reopen a removed historical export.
    def forbidden(*args,**kwargs):
        pytest.fail('A retained command contract read must not materialize code or start a process')
    for name in ('Popen','run','call'):monkeypatch.setattr(receipt.subprocess,name,forbidden)
    monkeypatch.setattr(code_versions,'materialize',forbidden)
    monkeypatch.setattr(code_versions,'verify',forbidden)
    adapter=object.__new__(cov_experiments.CovExecution)
    adapter.config=config;adapter.records=e.Records(config)
    return SimpleNamespace(root=root,work=work,source=source,config=config,
        accepted=accepted,record=record,harness=harness,adapter=adapter)


@pytest.mark.parametrize('draft',['missing','changed','symlink'])
def test_cov_code_command_ignores_draft_and_historical_export(tmp_path,monkeypatch,draft):
    from scidiscovery.research_loop import experiment_client
    from cov_execution import receipt
    case=_fixed_cov_call(tmp_path,monkeypatch)
    case.source.unlink()
    if draft=='changed':case.source.write_text("raise AssertionError('mutable draft')\n")
    elif draft=='symlink':
        private=case.root/'private.txt';private.write_text('host private fixture')
        case.source.symlink_to(private)
    def no_draft_reference(*args,**kwargs):
        pytest.fail('Fixed code must not consult the current logical source')
    monkeypatch.setattr(experiment_client,'input_reference',no_draft_reference)
    assert case.adapter.command(case.accepted)==[sys.executable,'-B','-I',
        str(Path(receipt.__file__).resolve()),str(case.record/'accepted.json')]
    assert not (case.record/'process.json').exists()


@pytest.mark.parametrize('changed',['prepared-path','prepared-size','logical-source','code-version','legacy-entry'])
def test_cov_code_command_refuses_changed_descriptor_or_host_binding(tmp_path,monkeypatch,changed):
    case=_fixed_cov_call(tmp_path,monkeypatch)
    assert case.adapter.command(case.accepted)[-1]==str(case.record/'accepted.json')
    if changed=='prepared-path':case.accepted['prepared']['local_path']+='-other'
    elif changed=='prepared-size':case.accepted['prepared']['size_bytes']+=1
    elif changed=='logical-source':case.accepted['source_script']=str(case.work/'code/other.py')
    elif changed=='code-version':case.accepted['code_version']['commit']='invalid'
    else:case.accepted['entry']='run.py'
    with pytest.raises(e.IntegrityError):case.adapter.command(case.accepted)
    assert not (case.record/'process.json').exists()


@pytest.mark.parametrize('missing',['protocol','runtime'])
def test_cov_optional_replay_input_failure_preserves_science_bindings_without_dispatch(tmp_path,monkeypatch,missing):
    from cov_execution.experiments import CovExecution, _replay_roles
    from scidiscovery.research_loop import contracts as c,delivery,protocol
    from test_cov_receipt import CLI
    case=_fixed_cov_call(tmp_path,monkeypatch)
    cli=case.harness/'exp_cli.py';cli.write_text(CLI)
    for name in ('train.csv','val.csv','meta.json'):(case.harness/name).write_text('bound '+name)
    for key,name in (('EXP_RUNNER','runner.py'),('EXP_TRAIN','train.csv'),('EXP_VAL','val.csv'),('EXP_META','meta.json')):
        monkeypatch.setenv(key,str(case.harness/name))
    invoked=case.root/'missing-python' if missing=='runtime' else Path(sys.executable)
    if missing=='protocol':monkeypatch.setattr(protocol,'__file__',str(case.root/'missing-protocol.py'))
    case.config.update(receipt_channel='public_runner_main',run_argv=[str(invoked),'-B','-I',str(cli)],
        runs_root=str(case.root/'runs'),evaluation_key='fixture')
    metadata=case.adapter.acceptance_metadata(SimpleNamespace(local_path=str(case.source)),new=True)
    assert metadata['snapshot_files']==[] and metadata['replay_inputs_unavailable'] is True
    inputs=metadata['replay_runtime']['inputs']
    data=[item for item in inputs if item['name'].startswith('replay/harness/')]
    assert [item['name'] for item in data]==['replay/harness/train.csv','replay/harness/val.csv','replay/harness/meta.json']
    assert all({key:item[key] for key in ('path','size_bytes','file_stat')}==e.proof(item['path']) for item in data)
    case.accepted.update(metadata);c.write(case.record/'accepted.json',case.accepted)
    assert {item['name'] for item in data}<={item['name'] for item in _replay_roles(case.accepted)}
    saved=(case.record/'accepted.json').read_bytes()
    def forbidden(*args,**options):pytest.fail('Unavailable optional replay input started a delivery leaf')
    monkeypatch.setattr(delivery,'_call',forbidden)
    assert CovExecution.replay_check.__wrapped__(case.adapter,case.accepted['call_id'],deadline_boottime=110.,
        on_process=forbidden)==dict(status='unsupported',note='exact_public_replay_inputs_unavailable',cleanup_confirmed=True)
    assert (case.record/'accepted.json').read_bytes()==saved
    assert not any((case.record/name).exists() for name in ('engineering-replay','receipt.json','process.json'))
