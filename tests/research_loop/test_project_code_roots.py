import pytest


@pytest.fixture
def selection(tmp_path, monkeypatch):
    from scidiscovery.research_loop import contracts as c, experiment_client, project_layout
    root = tmp_path / 'instance'
    work = root / 'project/nodes/001/work'
    base = tmp_path / 'base'
    for directory in (work / 'project/src', work / 'project/tools', base / 'src'):
        directory.mkdir(parents=True)
    (work / 'project/src/main.py').write_bytes(b'current draft\n')
    (base / 'src/main.py').write_bytes(b'old base\n')
    project = dict(base_root=str(base), editable=['src', 'tools'])
    job = dict(project=project)
    config = dict(scope=dict(role='W', step=1), execution_interface=dict(executor='local_execution:command'))
    monkeypatch.setattr(experiment_client, 'root_for_configuration', lambda *args, **kwargs: root)
    monkeypatch.setattr(c, 'workspace', lambda *args: work)
    monkeypatch.setattr(c, 'regular', lambda path: path)
    monkeypatch.setattr(c, 'read', lambda path: job)

    def layout(value, workspace):
        assert value is project and workspace == work
        return dict(root=work / 'project', logical_root='project', editable=[
            dict(relative=name, source=work / 'project' / name, target=base / name)
            for name in project['editable']])

    monkeypatch.setattr(project_layout, 'code_roots', layout)
    return experiment_client, config, work, base, job


def test_project_code_selection_maps_base_and_draft_to_same_declared_capture(selection):
    client, config, work, base, _ = selection
    expected = dict(code_root=work / 'project', entry='src/main.py', selection='directory',
                    logical_root='project', roots=['src', 'tools'])
    for entry in (base / 'src/main.py', work / 'project/src/main.py'):
        assert client.code_path(config, entry) == work / 'project/src/main.py'
        assert client.code_selection(config, entry, resolved=entry) == expected
    assert client.code_path(config, base / 'src/main.py').read_bytes() == b'current draft\n'


@pytest.mark.parametrize('name', ['README.md', 'src', 'tools', 'src/../README.md'])
def test_project_code_selection_rejects_outside_editable_entry(selection, name):
    from scidiscovery.research_loop.experiments import CallerError
    client, config, work, base, _ = selection
    for alias in (base, work / 'project'):
        with pytest.raises(CallerError, match='declared project editable'):
            client.code_selection(config, alias / name)


def test_project_code_selection_rejects_draft_symlink(selection):
    from scidiscovery.research_loop.experiments import IntegrityError
    client, config, work, base, _ = selection
    (work / 'project/src/link.py').symlink_to(base / 'src/main.py')
    with pytest.raises(IntegrityError, match='exact ordinary project draft'):
        client.code_selection(config, base / 'src/link.py')


def test_project_code_selection_keeps_bound_scientific_inputs_direct(selection):
    client, config, work, _, job = selection
    bound = work / 'inputs/data.json'
    job['direct_materials'] = [dict(path='inputs/data.json', source=str(bound))]
    assert client.code_selection(config, bound, resolved=bound) is None


def test_project_code_selection_preserves_bound_noneditable_base_original(selection):
    from scidiscovery.research_loop.experiments import CallerError
    client, config, work, base, job = selection
    original = base / 'data/input.json'
    logical = work / 'inputs/data.json'
    job['direct_materials'] = [dict(path='inputs/data.json', source=str(original))]
    assert client.code_path(config, original) == original
    assert client.code_selection(config, original, resolved=logical) is None
    with pytest.raises(CallerError, match='bound scientific inputs'):
        client.code_selection(config, original, experiment_directory=work / 'experiments/run')
    job['direct_materials'] = []
    with pytest.raises(CallerError, match='declared project editable'):
        client.code_path(config, original)


def test_project_code_selection_preserves_bound_noneditable_project_logical_path(selection):
    from scidiscovery.research_loop.experiments import CallerError
    client, config, work, base, job = selection
    logical = work / 'project/data/input.json'
    original = base / 'data/input.json'
    job['direct_materials'] = [dict(path='project/data/input.json', source=str(original))]
    assert client.code_path(config, logical) == logical
    assert client.code_selection(config, logical, resolved=original) is None
    job['direct_materials'] = []
    with pytest.raises(CallerError, match='declared project editable'):
        client.code_path(config, logical)


def test_project_code_selection_preserves_exact_bound_editable_original(selection):
    client, config, _, base, job = selection
    original = base / 'src/main.py'
    job['direct_materials'] = [dict(path='inputs/bound-main.py', source=str(original))]
    assert client.code_path(config, original) == original
    assert client.code_selection(config, original, resolved=original) is None
    assert original.read_bytes() == b'old base\n'


def test_project_code_selection_checks_each_editable_against_bound_material(selection):
    from scidiscovery.research_loop.experiments import CallerError
    client, config, work, base, job = selection
    job['direct_materials'] = [dict(path='project/tools/data.json', source=str(work / 'project/tools/data.json'))]
    with pytest.raises(CallerError, match='overlaps a bound scientific input'):
        client.code_selection(config, base / 'src/main.py')


@pytest.fixture
def code_metadata(tmp_path, monkeypatch):
    from scidiscovery.research_loop import contracts as c, experiment_client, project_layout
    root = tmp_path / 'instance'
    project = dict(base_root=str(tmp_path / 'deleted-base'), editable=['src', 'tools'],
        scratch=['build'], outputs=['out'], base_identity=dict(object_format='sha1', commit='a' * 40))
    job = dict(step=2, role='W', project=project)
    c.write(root / 'jobs/002.json', job)
    monkeypatch.setattr(experiment_client, 'root_for_configuration', lambda *args, **kwargs: root)
    monkeypatch.setattr(project_layout, 'files', lambda *args, **kwargs: pytest.fail('metadata query opened current base'))
    version = dict(version=1, object_format='sha1', commit='b' * 40, tree_path='.',
        selection='directory', entry='src/main.py', logical_root='project')
    config = dict(scope=dict(role='W', step=2), execution_interface=dict(executor='local_execution:command'))
    accepted = dict(scope=config['scope'], call_id='original', code_version=version,
        base_identity=dict(project['base_identity']), source_script=str(c.workspace(root, 2) / 'project/src/main.py'))
    return root, project, job, config, accepted


def test_project_original_code_metadata_survives_missing_base_draft_and_export(code_metadata):
    from scidiscovery.research_loop import contracts as c, experiments as e
    root, _, _, config, accepted = code_metadata
    assert not c.workspace(root, 2).exists()
    assert e._code_version_binding(config, accepted) == root


@pytest.mark.parametrize('change', ['base_identity', 'missing_base_identity', 'entry', 'source', 'file_selection'])
def test_project_original_code_metadata_rejects_wrong_binding(code_metadata, change):
    from copy import deepcopy
    from scidiscovery.research_loop import experiments as e
    _, _, _, config, accepted = code_metadata
    accepted = deepcopy(accepted)
    if change == 'base_identity':accepted['base_identity']['commit'] = 'c' * 40
    elif change == 'missing_base_identity':accepted.pop('base_identity')
    elif change == 'entry':accepted['code_version']['entry'] = 'data/private.py'
    elif change == 'source':accepted['source_script'] = '/another/project/src/main.py'
    else:accepted['code_version']['selection'] = 'file'
    with pytest.raises(e.IntegrityError):e._code_version_binding(config, accepted)


def test_project_input_verification_checks_all_exact_commit_paths(code_metadata, monkeypatch):
    from scidiscovery.research_loop import code_versions, experiments as e
    _, _, _, config, accepted = code_metadata
    monkeypatch.setattr(code_versions, 'verify', lambda *args, **kwargs: ('src/main.py', 'data/undeclared.bin'))
    with pytest.raises(e.IntegrityError, match='original changed'):
        e.verify_scientific_inputs(accepted, config=config)
    monkeypatch.setattr(code_versions, 'verify', lambda *args, **kwargs: ('src/main.py', 'tools/helper.py'))
    assert e.verify_scientific_inputs(accepted, config=config) is accepted


def test_project_compiler_metadata_keeps_full_relative_single_file_entry(code_metadata, monkeypatch):
    from copy import deepcopy
    from scidiscovery.research_loop import code_versions, execution_inputs, experiments as e
    root, project, job, _, accepted = code_metadata
    version = dict(accepted['code_version'], selection='file', entry='src/implementation.json')
    reference = dict(path='native-evidence/002/compiled-implementations/' + 'd' * 32 + '/compiler.json')
    compiler = dict(version=1, job=dict(path='jobs/002.json'), code_version=version,
        base_identity=dict(project['base_identity']), author_input=None)
    assert e._compiler_source_version(compiler, reference, dict(producer_step=2), project=project) == version
    execution_inputs._verify_compiler_code(root, job, reference, compiler, verify_pin=False)
    monkeypatch.setattr(code_versions, 'verify', lambda *args, **kwargs: ('src/implementation.json',))
    execution_inputs._verify_compiler_code(root, job, reference, compiler)
    changed = deepcopy(compiler); changed['base_identity']['commit'] = 'e' * 40
    with pytest.raises(ValueError):execution_inputs._verify_compiler_code(root, job, reference, changed, verify_pin=False)
    changed = deepcopy(compiler); changed['code_version']['entry'] = 'data/implementation.json'
    with pytest.raises(ValueError):execution_inputs._verify_compiler_code(root, job, reference, changed, verify_pin=False)


def test_project_directory_run_rejects_remote_provider_before_preparation(code_metadata, monkeypatch):
    from types import SimpleNamespace
    from scidiscovery.operations.execution import LocalFileDescriptor
    from scidiscovery.research_loop import code_versions, experiments as e
    _, project, _, config, _ = code_metadata
    config = dict(config, max_seconds=1, execution_interface=dict(executor='tcad_execution:tcad'))
    monkeypatch.setattr(e, '_pending_dispatch', lambda *args: None)
    monkeypatch.setattr(e, '_require_new_execution', lambda *args: None)
    monkeypatch.setattr(code_versions, 'capture', lambda *args, **kwargs: pytest.fail('unsupported project run captured code'))
    records = SimpleNamespace(calls=lambda: [], preflight=lambda **kwargs: pytest.fail('unsupported project run entered admission'))
    adapter = SimpleNamespace(prepare=lambda *args, **kwargs: pytest.fail('unsupported project run called provider prepare'))
    payload = LocalFileDescriptor(name='input', local_path=project['base_root'] + '/src/main.py',
        size_bytes=1, media_type='application/octet-stream')
    with pytest.raises(e.UnsupportedExecution, match='managed Local runtime'):
        e._prepare_descriptor(config, payload, adapter, records, [])


@pytest.mark.parametrize('wrong_identity', [False, True])
def test_project_terminal_cleanup_uses_original_metadata_without_base(code_metadata, monkeypatch, wrong_identity):
    from types import SimpleNamespace
    from scidiscovery.research_loop import code_versions, contracts as c, experiments as e, replay_check
    root, _, _, config, accepted = code_metadata
    directory = root / 'original-call'
    observation = dict(status='known_success', interface=dict(terminal_evidence='local_process'))
    c.write(directory / 'execution-observation.json', observation)
    if wrong_identity:accepted['base_identity']['commit'] = 'f' * 40
    monkeypatch.setattr(e, 'Records', lambda config: SimpleNamespace(directory=lambda call: directory,
        accepted=lambda call: accepted))
    monkeypatch.setattr(e, 'verify_observation', lambda *args, **kwargs: None)
    monkeypatch.setattr(replay_check, 'cleanup_confirmed', lambda *args, **kwargs: True)
    cleanups = []
    monkeypatch.setattr(code_versions, 'cleanup_export', lambda *args, **kwargs: cleanups.append((args, kwargs)))
    e._cleanup_terminal_code_export(config, 'original', observation)
    assert len(cleanups) == (0 if wrong_identity else 1)
    if cleanups:
        assert cleanups[0][0] == (root,)
        assert cleanups[0][1]['step'] == 2 and cleanups[0][1]['call_id'] == 'original'


@pytest.mark.parametrize('chosen,damage', [([],None), (['older'],None), (['older','newer'],None),
    (['older'],'binding'), (['older'],'owner_path')],
    ids=['unselected','selected-older','ambiguous','wrong-binding','wrong-owner-path'])
def test_project_seed_uses_only_uniquely_selected_original_call(code_metadata, monkeypatch, chosen, damage):
    from scidiscovery.research_loop import contracts as c, execution_inputs, history, originals
    root, project, _, _, template = code_metadata
    owner = originals.NodeSource(dict(goal_id='fixture-owner',settings_ref=dict(path=str(root/'settings.json'))),
        step=1,storage_root=root,original_root=str(root))
    parent = dict(step=1, role='W', project=project)
    c.write(root / 'jobs/001.json', parent)
    materials = {}
    calls = []
    for call, commit in (('older', 'c' * 40), ('newer', 'd' * 40)):
        scope = dict(step=1, role='W')
        config = dict(scope=scope, record_dir=str(root / 'host-runtime/experiment-control/001/records'))
        version = dict(template['code_version'], commit=commit)
        accepted = dict(call_id=call, scope=scope, interface=dict(executor='local_execution:command', config_record=config),
            code_version=version, base_identity=dict(project['base_identity']),
            source_script=str(c.workspace(root, 1) / 'project/src/main.py'))
        path = root / (call + '-accepted.json');c.write(path, accepted)
        calls.append(dict(call_id=call, outputs=[dict(name='result.txt', size_bytes=1)],
            accepted=dict(snapshot=path.name)))
        materials[call] = dict(path=f'inputs/parents/001/artifacts/experiments/{call}/artifacts/result.txt',
            source=str(root / (call + '-output')), size_bytes=1)
    observer_config = dict(adapter=str(root/'observer.py'),config={})
    (root/'observer.py').write_text('# Frozen observer fixture identity; never executed.\n')
    raw_ref = dict(path=str(root/'native-evidence/001/missing-scientific-raw.json'),size_bytes=7)
    observation_binding = history._observer_binding(raw_ref,observer_config)
    if damage=='binding':observation_binding=dict(unrelated_source=True)
    observed = root / 'host-history/observations/001.json'
    c.write(observed, dict(binding=observation_binding,managed_calls=calls))
    receipt = dict(path=observed.relative_to(root).as_posix())
    if damage=='owner_path':receipt['path']='host-history/observations/002.json'
    seal = root / 'seal.json';c.write(seal, dict(observations=receipt,raw_ref=raw_ref))
    monkeypatch.setattr(execution_inputs, '_producer', lambda *args, **kwargs: dict(producer_step=1))
    monkeypatch.setattr(originals, 'node', lambda *args: owner)
    monkeypatch.setattr(originals, 'source_job', lambda source: parent)
    monkeypatch.setattr(originals, 'source_settings', lambda source: dict(history_observer=observer_config))
    monkeypatch.setattr(originals, 'source_record', lambda *args: seal)
    monkeypatch.setattr(originals, 'resolve_ref', lambda source, reference: root / reference['path'])
    monkeypatch.setattr(originals, 'original_ref', lambda source, path: dict(path=path))
    job = dict(project=project,selected_inputs=[dict(material=materials[call]) for call in chosen])
    if damage:
        with pytest.raises(history.ObserverIntegrityError,match='binding|producer'):
            c._selected_project_code(root,job)
        return
    holder, binding = c._selected_project_code(root,job)
    if chosen == ['older']:
        assert holder == root and binding['call_id'] == 'older'
        assert binding['code_version']['commit'] == 'c' * 40
    else:
        assert holder is None and binding['source'] == 'frozen_base'


def test_build_job_retirement_fence_precedes_task_building(tmp_path, monkeypatch):
    import sys
    from scidiscovery.research_loop import contracts as c, controller, instance_archive as archive
    from scidiscovery.research_loop import instance_delete as deletion, instance_owner as owner, protocol
    monkeypatch.setattr(owner, '_gate_directory', lambda: tmp_path / 'private-gates')
    public = tmp_path / 'public';public.mkdir()
    objective = public / 'objective.md';objective.write_text('Owner fence fixture; no native execution.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, [sys.executable], max_tokens=100, max_seconds=10,
        max_steps=2, model='fixture-never-called', reasoning_effort='high', search_policy=None,
        experiment_mode='external')

    def interrupted(*args):
        raise RuntimeError('fixture interruption after retirement marker')

    monkeypatch.setattr(deletion, '_publish_retirement', interrupted)
    with pytest.raises(RuntimeError, match='fixture interruption'):
        deletion.delete_instance(root, instance_id=c.settings(root)['goal_id'],
            settings_ref=c.record_ref(root / 'settings.json'), operation_id='build-owner-fence')
    assert (root / owner.RETIREMENT).exists()
    monkeypatch.setattr(protocol, 'require_current', lambda *args: pytest.fail('Retired build entered task semantics'))
    with pytest.raises(archive.files.ArchiveError, match='delet'):
        c.build_job(root, 1, 'H', [])
    assert not (root / 'jobs/001.json').exists()
