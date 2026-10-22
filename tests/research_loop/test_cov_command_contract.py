"""Cov single-source admission and public contracts; no executor is started."""
from copy import deepcopy
import hashlib
import json

import pytest

from scidiscovery.research_loop import experiments as e
from cov_execution import experiments as cov_experiments
from test_cov_lineage import wrapper
from execution_fixtures import execution_registry, remote_registry


@pytest.mark.parametrize('options', ['directory', 'arguments'])
def test_cov_unsupported_options_are_known_not_started_before_prepare(wrapper, monkeypatch, capsys, options):
    script = wrapper.script('candidate')

    def forbidden(*args, **kwargs):
        pytest.fail('Unsupported caller options reached executor preparation or submission')

    monkeypatch.setattr(cov_experiments.CovExecution, 'prepare', forbidden)
    monkeypatch.setattr(cov_experiments.CovExecution, 'submit', forbidden)
    args = ['--config', str(wrapper.config), 'run', '--script', str(script)]
    args += ['--experiment-dir', str(wrapper.root)] if options == 'directory' else ['--', '--seed', '7']
    assert e.main(args) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'known_not_started' and result['accepted'] is False
    assert result['error'] == 'caller_error' and '--experiment-dir or script arguments' in result['note']
    assert 'call_id' not in result and wrapper.accepted() == [] and wrapper.calls() == b''


def test_other_admission_errors_keep_unresolved_classification(wrapper, capsys):
    script = wrapper.script('candidate')
    assert e.main(['--config', str(wrapper.config), 'run', '--script', str(script),
        '--parent-plan', 'absent-candidate']) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'unresolved' and result['note'] == 'parent_plan_unresolved'
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
    assert all(set(item)==scientific_fields|{'sha256','size_bytes'} for item in frozen)
    assert listed == [{key:item[key] for key in scientific_fields} for item in frozen]
    summary = next(item['public_summary'] for item in listed if item['key'] == 'cov_single_source')
    assert summary['accepted_source'] == 'exact source.py snapshot'
    assert summary['experiment_directory'] is False and summary['script_arguments'] is False
    output = wrapper.root / 'single-source-contract.json'
    assert e.main(['--config', str(wrapper.config), 'describe', '--key', 'cov_single_source', '--output', str(output)]) == 0
    capsys.readouterr()
    assert json.loads(output.read_text()) == summary
    bound=next(item for item in frozen if item['key']=='cov_single_source')
    assert hashlib.sha256(output.read_bytes()).hexdigest()==bound['sha256']
    assert output.stat().st_size==bound['size_bytes']
    changed = deepcopy(config)
    changed['execution_interface']['public_capabilities'] = []
    with pytest.raises(e.IntegrityError, match='contract or configuration changed'):
        e.load(changed, path=wrapper.config)
    for field,value in (('sha256','0'*64),('size_bytes',bound['size_bytes']+1)):
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
        scope=dict(handoff_protocol=CURRENT, step=2, role='W', job_sha256='a' * 64,
            execution_key='b' * 64, mechanism_ref=None)), 'remote_execution:fixture', path=tmp_path / 'config.json')
    assert ('registered_implementation' in config) is registered
    contracts = config['execution_interface']['public_capabilities']
    assert [item['key'] for item in contracts] == ['opaque_input']
    assert contracts == e._public_capabilities(e.load(config, path=tmp_path / 'config.json'))
    changed = deepcopy(config)
    changed['execution_interface']['public_capabilities'][0]['sha256'] = '0' * 64
    with pytest.raises(e.IntegrityError, match='contract or configuration changed'):
        e.load(changed, path=tmp_path / 'config.json')


@pytest.mark.parametrize('registered', [False, True])
def test_empty_capability_adapter_preserves_public_contract_presence(tmp_path, remote_registry, monkeypatch, registered):
    from execution_fixtures import DifferentExternalIdentity
    from scidiscovery.research_loop.protocol import CURRENT
    monkeypatch.setattr(DifferentExternalIdentity, 'capabilities', lambda self: ())
    options = dict(registered_implementation=dict(factory='previous-installation:factory',plugin_version='old',code=[])) if registered else {}
    config = e.freeze(dict(options, record_dir=str(tmp_path / 'records'),
        scope=dict(handoff_protocol=CURRENT, step=2, role='W', job_sha256='a' * 64,
            execution_key='b' * 64, mechanism_ref=None)), 'remote_execution:fixture', path=tmp_path / 'config.json')
    interface = config['execution_interface']
    assert ('public_capabilities' in interface) is registered
    if registered:
        assert interface['public_capabilities'] == []
