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


def home_scene(tmp_path):
    home = tmp_path / 'home'; home.mkdir()
    guard = tmp_path / 'guard'; guard.mkdir()
    for name in ('sudo', 'id'):
        command = guard / name
        command.write_text('#!/bin/sh\nprintf "unexpected privilege command\\n" >&2\nexit 95\n')
        command.chmod(0o755)
    environment = {**os.environ, 'HOME': str(home), 'SCID_PYTHON': sys.executable,
                   'PATH': str(guard) + os.pathsep + os.environ['PATH']}
    for name in ('SCID_BIN_ROOT', 'SCID_INSTALL_ROOT', 'SCID_BACKUP_ROOT',
                 'SCID_DEPLOY_MODE', 'SCID_PLUGINS'):
        environment.pop(name, None)
    return environment, home


@pytest.mark.process_e2e
def test_default_home_dry_run_does_not_create_directories(tmp_path):
    environment, home = home_scene(tmp_path)
    result = shell(environment, '''validate_base_python() { :; }
validate_source() { :; }
main --dry-run
''')
    assert result.returncode == 0, result.stderr
    for relative in ('.local/bin', '.local/share/scidiscovery/site',
                     '.local/share/scidiscovery/skills', '.local/state/scidiscovery/install'):
        assert str(home / relative) in result.stdout
    assert not (home / '.local').exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize('mode', ['status', 'uninstall'])
def test_default_home_status_and_uninstall_without_install_are_read_only(tmp_path, mode):
    environment, home = home_scene(tmp_path)
    result = shell(environment, 'main ' + mode)
    assert result.returncode == 0, result.stderr
    assert not (home / '.local').exists()


@pytest.mark.process_e2e
def test_default_home_install_creates_bin_and_uninstalls_without_sudo(tmp_path):
    import json

    environment, home = home_scene(tmp_path)
    # Package construction is a fixture; path selection, lock, transaction,
    # publication, and uninstall all use the installer functions.
    result = shell(environment, '''validate_base_python() { :; }
validate_source() { :; }
install_packages() {
    PACKAGE_STAGE="$INSTALL_ROOT/.site.new.fixture"
    SKILL_STAGE="$INSTALL_ROOT/.skills.new.fixture"
    mkdir -p "$PACKAGE_STAGE" "$SKILL_STAGE"
    printf 'retained package\\n' > "$PACKAGE_STAGE/fixture"
    printf 'retained skill\\n' > "$SKILL_STAGE/SKILL.md"
}
probe_thin_package() { :; }
main install
''')
    assert result.returncode == 0, result.stderr
    binary = home / '.local/bin'
    prefix = home / '.local/share/scidiscovery'
    for name in ('scid-research', 'scid-experiment'):
        wrapper = (binary / name).read_text()
        assert 'PYTHONPATH=' + str(prefix / 'site') in wrapper
    transactions = home / '.local/state/scidiscovery/install/transactions'
    manifest = next(transactions.glob('*/manifest.json'))
    transaction = json.loads(manifest.read_text())
    assert transaction['state'] == 'complete'
    assert {entry['path'] for entry in transaction['entries']} == {
        str(binary / 'scid-research'), str(binary / 'scid-experiment')}
    result = shell(environment, 'main uninstall')
    assert result.returncode == 0, result.stderr
    assert not (binary / 'scid-research').exists()
    assert not (binary / 'scid-experiment').exists()
    assert (prefix / 'site/fixture').read_text() == 'retained package\n'
    assert (prefix / 'skills/SKILL.md').read_text() == 'retained skill\n'


@pytest.mark.process_e2e
@pytest.mark.parametrize('setting', ['SCID_BIN_ROOT', 'SCID_INSTALL_ROOT', 'SCID_BACKUP_ROOT'])
@pytest.mark.parametrize('kind', ['file', 'symlink'])
def test_thin_rejects_unsafe_root_before_mutation(tmp_path, setting, kind):
    environment, home = home_scene(tmp_path)
    target = tmp_path / 'unsafe'
    if kind == 'file':
        target.write_bytes(b'foreign file')
    else:
        target.symlink_to(home, target_is_directory=True)
    environment[setting] = str(target)
    result = shell(environment, 'require_sources')
    assert result.returncode != 0
    assert 'thin deployment paths' in result.stderr
    assert not (home / '.local').exists()
    if kind == 'symlink':
        assert target.is_symlink()
    else:
        assert target.read_bytes() == b'foreign file'


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
    skill_stage=tmp_path/'prepared-skills';skill_stage.mkdir();(skill_stage/'SKILL.md').write_text('fixture')
    environment['PREPARED_SKILLS']=str(skill_stage)
    # Only package supply/preflight checks are stand-ins. Actual
    # activation, transaction manifest, wrapper creation and rollback run.
    result=shell(environment,'''validate_source() { :; }
validate_base_python() { :; }
install_packages() { PACKAGE_STAGE="$PREPARED_SITE"; SKILL_STAGE="$PREPARED_SKILLS"; }
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
systemctl() { exit 91; }
status
uninstall_services
''')
    assert result.returncode!=0 and 'refusing to remove' in result.stderr
    assert 'owned by another configuration' in result.stdout
    assert (binary/'scid-research').exists() and (binary/'scid-experiment').exists()
    result=shell(environment,'''thin_cli_wrapper scidiscovery.research_loop.experiments > "$CLI_ROOT/scid-experiment"
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
@pytest.mark.parametrize('mode, forwarded', [(None, 'reinstall'), ('reinstall', 'reinstall'), ('install', 'install'),
                                            ('--dry-run', '--dry-run'), ('status', 'status')])
def test_reinstaller_forwards_settings_and_mode_without_sudo(tmp_path, mode, forwarded):
    environment, originals = scene(tmp_path)
    binary = Path(environment['SCID_BIN_ROOT'])
    for name in ('id', 'sudo'):
        command = binary / name
        command.write_text('#!/bin/sh\nprintf "unexpected privilege command\\n" >&2\nexit 95\n')
        command.chmod(0o755)
    environment['PATH'] = str(binary) + os.pathsep + environment['PATH']
    environment['SCID_PLUGINS'] = 'tcad_artifact'
    deployment = tmp_path / 'copied-source/deploy'; deployment.mkdir(parents=True)
    wrapper = deployment / 'reinstall.sh'
    wrapper.write_bytes(SCRIPT.with_name('reinstall.sh').read_bytes())
    installer = deployment / 'install.sh'
    installer.write_text('#!/bin/sh\nprintf "%s\\n" "$@" "$SCID_PYTHON" '
                         '"$SCID_PLUGINS" "$SCID_INSTALL_ROOT" "$SCID_BIN_ROOT" "$SCID_BACKUP_ROOT"\n')
    installer.chmod(0o755)
    result = subprocess.run(['bash', str(wrapper), *([] if mode is None else [mode])],
        env=environment, capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [forwarded, sys.executable, 'tcad_artifact',
        environment['SCID_INSTALL_ROOT'], environment['SCID_BIN_ROOT'], environment['SCID_BACKUP_ROOT']]
    assert_preserved(originals)


@pytest.mark.process_e2e
@pytest.mark.parametrize('observed', [None, '41.0', '47.0'])
def test_no_deps_preflight_rejects_missing_or_wrong_cryptography_before_publication(tmp_path, observed):
    environment, originals = scene(tmp_path)
    patch = tmp_path / 'dependency-patch'; patch.mkdir()
    (patch / 'sitecustomize.py').write_text(
        'import importlib.metadata as metadata\n'
        'original = metadata.version\n'
        'def version(name):\n'
        '    if name == "cryptography":\n'
        + ('        raise metadata.PackageNotFoundError(name)\n' if observed is None
           else f'        return {observed!r}\n')
        + '    return original(name)\nmetadata.version = version\n')
    environment['PYTHONPATH'] = str(patch)
    result = shell(environment, 'validate_base_python')
    assert result.returncode != 0
    assert 'base Python dependency' in result.stderr and 'cryptography' in result.stderr
    assert not Path(environment['SCID_INSTALL_ROOT']).exists()
    assert not (Path(environment['SCID_BIN_ROOT']) / 'scid-research').exists()
    assert_preserved(originals)


@pytest.mark.process_e2e
def test_fresh_prefix_publishes_persistent_skills_and_wrapper_default(tmp_path):
    environment, originals = scene(tmp_path)
    prefix = Path(environment['SCID_INSTALL_ROOT']); prefix.mkdir()
    source = tmp_path / 'source-skills'; (source / 'fixture').mkdir(parents=True)
    (source / 'fixture' / 'SKILL.md').write_text('frozen fixture bytes')
    site_stage = prefix / '.site.new.fixture'; site_stage.mkdir()
    skill_stage = prefix / '.skills.new.fixture'
    import shutil
    shutil.copytree(source, skill_stage)
    environment.update(PREPARED_SITE=str(site_stage), PREPARED_SKILLS=str(skill_stage))
    result = shell(environment, '''PACKAGE_STAGE="$PREPARED_SITE"
SKILL_STAGE="$PREPARED_SKILLS"
activate_packages
''')
    assert result.returncode == 0, result.stderr
    shutil.rmtree(source)
    assert (prefix / 'skills/fixture/SKILL.md').read_text() == 'frozen fixture bytes'
    wrapper = (Path(environment['SCID_BIN_ROOT']) / 'scid-research').read_text()
    assert 'PYTHONPATH=' + str(prefix / 'site') in wrapper
    assert_preserved(originals)
