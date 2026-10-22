"""Installer routing and filesystem publication, without building or installing packages."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / 'deploy/install.sh'


def scene(tmp_path):
    binary=tmp_path/'bin';binary.mkdir()
    state=tmp_path/'old-state';state.mkdir()
    (state/'database.sqlite3').write_bytes(b'Existing SQL must never be opened or changed.')
    project=tmp_path/'old-project';(project/'.codex').mkdir(parents=True)
    (project/'.codex/config.toml').write_text('Existing Root configuration stays unchanged.')
    (binary/'scid').write_text('Existing legacy CLI stays unchanged.')
    environment={**os.environ,'SCID_PYTHON':sys.executable,
        'SCID_BIN_ROOT':str(binary),'SCID_INSTALL_ROOT':str(tmp_path/'prefix'),
        'SCID_STATE_ROOT':str(state),'SCID_WORKSPACE':str(tmp_path/'missing-workspace'),
        'SCID_CODEX_LAUNCH_ROOT':str(tmp_path/'missing-launch-root'),
        'SCID_BACKUP_ROOT':str(tmp_path/'backups')}
    environment.pop('SCID_DEPLOY_MODE',None)
    environment.pop('SCID_PLUGINS',None)
    return environment, {path:path.read_bytes() for path in (state/'database.sqlite3',project/'.codex/config.toml',binary/'scid')}


def shell(environment, body):
    return subprocess.run(['bash','-c','source "$1"\n'+body,'bash',str(SCRIPT)],
        env=environment,capture_output=True,text=True,timeout=10,check=False)


def assert_preserved(originals):
    assert all(path.read_bytes()==raw for path,raw in originals.items())


@pytest.mark.process_e2e
def test_default_thin_preview_needs_no_legacy_workspace_or_service(tmp_path):
    environment, originals=scene(tmp_path)
    result=shell(environment,'''systemctl() { exit 91; }
configure_platform() { exit 92; }
probe_mcp() { exit 93; }
require_sources
preview
''')
    assert result.returncode==0,result.stderr
    assert 'Thin deployment:' in result.stdout
    assert not Path(environment['SCID_INSTALL_ROOT']).exists()
    assert not Path(environment['SCID_WORKSPACE']).exists()
    assert_preserved(originals)


@pytest.mark.process_e2e
def test_thin_refuses_existing_prefix_and_foreign_wrapper(tmp_path):
    environment, originals=scene(tmp_path)
    prefix=Path(environment['SCID_INSTALL_ROOT'])/'site';prefix.mkdir(parents=True)
    sentinel=prefix/'existing';sentinel.write_bytes(b'Existing package bytes.')
    refused=shell(environment,'validate_thin_targets')
    assert refused.returncode!=0 and 'target already exists' in refused.stderr
    assert sentinel.read_bytes()==b'Existing package bytes.'
    sentinel.unlink();prefix.rmdir()
    wrapper=Path(environment['SCID_BIN_ROOT'])/'scid-experiment'
    wrapper.write_bytes(b'Another installation owns this name.')
    refused=shell(environment,'validate_thin_targets')
    assert refused.returncode!=0 and 'scid-experiment' in refused.stderr
    assert wrapper.read_bytes()==b'Another installation owns this name.'
    assert_preserved(originals)


@pytest.mark.process_e2e
def test_thin_publication_failure_rolls_back_only_new_wrappers(tmp_path):
    environment, originals=scene(tmp_path)
    prepared=tmp_path/'prepared-site';prepared.mkdir();(prepared/'retained').write_bytes(b'Prepared fixture bytes, not a package.')
    Path(environment['SCID_INSTALL_ROOT']).mkdir()
    environment['PREPARED_SITE']=str(prepared)
    # Only package supply/preflight and privilege checks are stand-ins. Actual
    # activation, transaction manifest, wrapper creation and rollback run.
    result=shell(environment,'''require_root() { :; }
validate_source() { :; }
validate_base_python() { :; }
install_packages() { PACKAGE_STAGE="$PREPARED_SITE"; }
probe_thin_package() { return 43; }
systemctl() { exit 91; }
configure_platform() { exit 92; }
ensure_secret() { exit 93; }
install_all
''')
    assert result.returncode==43,result.stderr
    binary=Path(environment['SCID_BIN_ROOT'])
    assert not (binary/'scid-research').exists() and not (binary/'scid-experiment').exists()
    assert (Path(environment['SCID_INSTALL_ROOT'])/'site/retained').read_bytes()==b'Prepared fixture bytes, not a package.'
    import json
    manifest=next(Path(environment['SCID_BACKUP_ROOT']).glob('.transaction-*/manifest.json'))
    transaction=json.loads(manifest.read_text())
    assert transaction['state']=='rolled_back'
    assert {row['path'] for row in transaction['entries']}=={str(binary/'scid-research'),str(binary/'scid-experiment')}
    assert all(row['kind']=='path' for row in transaction['entries'])
    assert_preserved(originals)


@pytest.mark.process_e2e
def test_thin_status_and_uninstall_preserve_foreign_commands_and_package(tmp_path):
    environment, originals=scene(tmp_path)
    binary=Path(environment['SCID_BIN_ROOT'])
    prefix=Path(environment['SCID_INSTALL_ROOT'])/'site';prefix.mkdir(parents=True)
    (prefix/'retained').write_bytes(b'Do not remove code used by existing processes.')
    result=shell(environment,'''thin_cli_wrapper scidiscovery.research_loop.cli > "$CLI_ROOT/scid-research"
thin_cli_wrapper scidiscovery.research_loop.experiments > "$CLI_ROOT/scid-experiment"
printf 'foreign modification\n' >> "$CLI_ROOT/scid-experiment"
require_root() { :; }
systemctl() { exit 91; }
status
uninstall_services
''')
    assert result.returncode!=0 and 'refusing to remove' in result.stderr
    assert 'owned by another configuration' in result.stdout
    assert (binary/'scid-research').exists() and (binary/'scid-experiment').exists()
    result=shell(environment,'''thin_cli_wrapper scidiscovery.research_loop.experiments > "$CLI_ROOT/scid-experiment"
require_root() { :; }
systemctl() { exit 91; }
uninstall_services
''')
    assert result.returncode==0,result.stderr
    assert not (binary/'scid-research').exists() and not (binary/'scid-experiment').exists()
    assert (prefix/'retained').read_bytes()==b'Do not remove code used by existing processes.'
    assert_preserved(originals)


@pytest.mark.parametrize('script', ['install.sh', 'reinstall.sh'])
@pytest.mark.process_e2e
def test_retired_service_deployment_is_rejected_before_any_mutation(tmp_path, script):
    environment, originals = scene(tmp_path)
    environment['SCID_DEPLOY_MODE'] = 'legacy'
    result = subprocess.run(['bash', str(SCRIPT.with_name(script)), 'install'],
        env=environment, capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 2
    assert 'legacy service deployment is retired' in result.stderr
    assert not Path(environment['SCID_INSTALL_ROOT']).exists()
    assert not Path(environment['SCID_BACKUP_ROOT']).exists()
    assert_preserved(originals)


@pytest.mark.process_e2e
def test_reinstaller_passes_operator_python_and_selected_plugins_as_data(tmp_path):
    environment, originals = scene(tmp_path)
    binary = Path(environment['SCID_BIN_ROOT'])
    for name, body in {'id': 'printf "1000\\n"', 'sudo': 'printf "%s\\n" "$@"'}.items():
        command = binary / name
        command.write_text('#!/bin/sh\n' + body + '\n')
        command.chmod(0o755)
    environment['PATH'] = str(binary) + os.pathsep + environment['PATH']
    environment['SCID_PLUGINS'] = 'tcad_artifact,curve_score'
    result = subprocess.run(['bash', str(SCRIPT.with_name('reinstall.sh')), 'reinstall'],
        env=environment, capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr
    arguments = result.stdout.splitlines()
    assert arguments[0] == 'env'
    assert 'SCID_PYTHON=' + sys.executable in arguments
    assert 'SCID_PLUGINS=tcad_artifact,curve_score' in arguments
    assert not any(value.startswith(('SCID_WORKSPACE=', 'SCID_STATE_ROOT=')) for value in arguments)
    assert arguments[-2:] == [str(SCRIPT), 'install']
    assert_preserved(originals)
