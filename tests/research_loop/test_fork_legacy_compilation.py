"""Retained legacy compiler/client originals; no native or backend execution."""
import shutil

import pytest

from scidiscovery.research_loop import contracts as c, execution_inputs as inputs, fork_sources, originals


def retained_legacy(tmp_path, *, attempt=1, changed=None, retain=True):
    root = tmp_path / 'parent'
    paths = []

    def write(path, value):
        path = root / path
        c.write(path, value)
        paths.append(path)
        return inputs.q._ref(root, path)

    settings = write('settings.json', dict(goal_id='legacy-compiler-fixture'))
    job = write('jobs/002.json', dict(step=2, role='W', direct_materials=[], selected_inputs=[]))
    intent = write('native-evidence/002/native-intent.json', dict(task_sha256=job['sha256']))
    interface = dict(public_capabilities=[dict(key='implementation_authoring', version=1)])
    configuration = write(f'host-runtime/experiment-control/{job["sha256"]}/interfaces/000.json',
        dict(native_attempt=dict(number=0), scope=dict(step=2, job_sha256=job['sha256']),
             execution_interface=interface))
    experiment = write('native-evidence/002/experiment-config.json', dict(execution_interface=interface))
    outcome = write('native-evidence/002/native-outcome.json', dict(stop_reason='token_limit'))
    usage = write('native-evidence/002/usage.json', dict(input_tokens=1, output_tokens=1))
    capability = write('native-evidence/002/compiled-implementations/z-original/capability.json', dict(version=1))
    author = write('native-evidence/002/compiled-implementations/z-original/author.json', dict(files=[]))
    author_source = write('project/nodes/002/work/author.json', dict(files=[]))
    package = write('project/nodes/002/artifacts/old-package.json', dict(schema_version=3))
    output = dict(package, path=str(root / package['path']))
    compiler_value = dict(version=1, job=job, native_intent=intent, configuration=configuration,
        implementation=interface, contract=interface['public_capabilities'][0], capability=capability,
        capability_source=dict(capability, path=str(root / capability['path'])), author=author,
        author_origin=dict(author_source, path=str(root / author_source['path'])), inputs=[], output=output)
    # The scientific handle is I002 because the original directory inventory
    # includes this earlier compiler, even when only the second client is used.
    first_compiler = write('native-evidence/002/compiled-implementations/a-original/compiler.json', compiler_value)
    compiler = write('native-evidence/002/compiled-implementations/z-original/compiler.json', compiler_value)
    original_material = dict(source=output['path'], path='inputs/parents/002/artifacts/old-package.json',
                             sha256=output['sha256'], size_bytes=output['size_bytes'])
    permit = write('host-recovery/author-closeout/002.json', dict(version=1, scope='author_closeout_only',
        context='new_context', step=2, source_attempt=0, job=job, settings=settings,
        original_intent=intent, original_outcome=outcome, original_usage=usage,
        original_experiment_config=experiment, calls=[]))
    first_intent = write('native-evidence/002/continuations/001/native-intent.json', dict(attempt=1,
        task_sha256=job['sha256'], author_closeout=permit, continuation_kind='author_closeout',
        context_mode='new_context', worker_view_version=1, root_intent_sha256=intent['sha256']))
    successor = upgrade = None
    if attempt == 2:
        source_originals = {'native-intent.json': first_intent}
        for name in ('native-outcome.json', 'usage.json', 'usage-observation.json', 'native.jsonl',
                     'runner-process.json', 'native-process.json'):
            source_originals[name] = write('native-evidence/002/continuations/001/' + name, dict(retained=name))
        successor = write('host-recovery/author-closeout/002-successor.json', dict(version=1,
            scope='author_closeout_successor_only', source_attempt=1, attempt=2, context='new_context',
            step=2, max_tokens=100, max_seconds=10., authorization_note='fixture operator grant',
            authorization_statement='fixture exact subject', original_permit=permit, source_originals=source_originals))
        upgrade = write('host-recovery/author-closeout/002-output-upgrade.json', dict(version=1,
            scope='completed_author_output_code_upgrade', step=2, attempt=2, successor=successor,
            authorization_note='fixture output grant', authorization_statement='fixture exact output'))
        permit = successor
        write('native-evidence/002/continuations/002/native-intent.json', dict(attempt=2,
            task_sha256=job['sha256'], author_closeout=permit, continuation_kind='author_closeout',
            context_mode='new_context', worker_view_version=1))
    client = dict(worker_implementation_version=1, implementation_ref='I002')
    public = write('project/nodes/002/artifacts/scientific-implementations/I002.json', client)
    material = dict(source=str(root / public['path']), path='inputs/parents/002/artifacts/scientific-implementations/I002.json',
                    sha256=public['sha256'], size_bytes=public['size_bytes'])
    binding_value = dict(client=client, public_path=material['source'], package=output,
        legacy_authorization=dict(permit=permit, compiler=compiler, material=original_material, attempt=attempt))
    if changed == 'compiler':
        binding_value['legacy_authorization']['compiler'] = first_compiler
    elif changed == 'public_path':
        binding_value['public_path'] = str(root / 'project/nodes/002/artifacts/copied.json')
    elif changed == 'package':
        binding_value['package'] = dict(output, sha256='0' * 64)
    elif changed == 'intent':
        path = root / f'native-evidence/002/continuations/{attempt:03d}/native-intent.json'
        c.write(path, dict(c.read(path), context_mode='resume'))
    binding = write(f'native-evidence/002/compiled-implementations/z-original/author-client-{attempt:03d}.json', binding_value)
    identity = dict(goal_id='legacy-compiler-fixture', settings_sha256=settings['sha256'])
    if not retain:
        return originals.NodeSource(identity, 2, str(root), root), compiler, binding, material
    key = originals.identity_key(identity)
    child = tmp_path / 'child'
    store = child / originals.FORK_DIRECTORY
    entries = []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        proof = inputs.q._ref(root, path)
        target = f'inherited/{key}/{relative}'
        (store / target).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, store / target)
        entries.append(dict(proof, origin_identity=identity, storage_path=target))
    metadata = dict(compiler_paths=[first_compiler['path'], compiler['path']],
                    author_successor=successor, output_upgrade=upgrade)
    manifest = dict(version=1, kind='scientific_fork', source=dict(origin_identity=identity, step=2),
        entries=entries, origins={key: dict(identity=identity, original_root=str(root))},
        legacy_compilations={f'{key}:2': metadata})
    c.write(child / originals.MANIFEST, manifest)
    original_bytes = {path.relative_to(root).as_posix(): path.read_bytes() for path in paths}
    shutil.rmtree(root)
    return child, compiler, binding, material, original_bytes


@pytest.mark.parametrize('attempt', [1, 2])
def test_retained_legacy_client_verifies_after_parent_removal(tmp_path, attempt):
    child, compiler, binding, material, original_bytes = retained_legacy(tmp_path, attempt=attempt)
    source = originals.selected_source(child)
    value = inputs.verify_original_compilation(source, dict(compiler, origin_identity=source.origin_identity),
        material=material, client_binding=dict(binding, origin_identity=source.origin_identity))
    assert value['output']['path'].endswith('/artifacts/old-package.json')
    assert material['source'].endswith('/scientific-implementations/I002.json')
    for path, expected in original_bytes.items():
        assert originals.resolve_ref(source, dict(path=path)).read_bytes() == expected


@pytest.mark.parametrize('changed', ['compiler', 'public_path', 'package', 'intent'])
def test_retained_legacy_client_rejects_wrong_original_relationships(tmp_path, changed):
    child, compiler, binding, material, _ = retained_legacy(tmp_path, changed=changed)
    with pytest.raises((ValueError, inputs.q.IntegrityError)):
        inputs.verify_original_compilation(originals.selected_source(child), compiler,
            material=material, client_binding=binding)


@pytest.mark.parametrize('missing', ['permit', 'intent', 'inventory', 'earlier_compiler', 'successor', 'upgrade'])
def test_retained_legacy_client_requires_exact_author_chain_and_inventory(tmp_path, missing):
    child, compiler, binding, material, _ = retained_legacy(tmp_path, attempt=2)
    manifest_path = child / originals.MANIFEST
    manifest = c.read(manifest_path)
    inventory = next(iter(manifest['legacy_compilations'].values()))
    if missing == 'inventory':
        manifest['legacy_compilations'] = {}
    else:
        path = dict(permit='host-recovery/author-closeout/002.json',
            intent='native-evidence/002/continuations/002/native-intent.json',
            earlier_compiler=inventory['compiler_paths'][0],
            successor='host-recovery/author-closeout/002-successor.json',
            upgrade='host-recovery/author-closeout/002-output-upgrade.json')[missing]
        manifest['entries'] = [row for row in manifest['entries'] if row['path'] != path]
    c.write(manifest_path, manifest)
    with pytest.raises((ValueError, inputs.q.IntegrityError)):
        inputs.verify_original_compilation(originals.selected_source(child), compiler,
            material=material, client_binding=binding)


@pytest.mark.parametrize('attempt', [1, 2])
def test_legacy_typed_closure_and_copy_preserve_author_chain(tmp_path, attempt):
    source, compiler, binding, material = retained_legacy(tmp_path, attempt=attempt, retain=False)
    closure = fork_sources._Closure()
    closure.compilation_inventory(source)
    closure.refs(source, dict(material=material, client_binding=binding), work_step=2)
    # The real sealed-node collector independently adds the accepted attempt
    # chain. These fixtures contain no scientific seal or admissible task cut;
    # keep the exact accepted intent as an explicit proof root instead.
    accepted_intent = f'native-evidence/002/continuations/{attempt:03d}/native-intent.json'
    closure.add(source, accepted_intent)
    paths = {entry['path'] for entry in closure.entries.values()}
    required = {'settings.json', 'jobs/002.json', binding['path'], compiler['path'],
        'host-recovery/author-closeout/002.json', 'native-evidence/002/native-intent.json',
        'native-evidence/002/experiment-config.json', accepted_intent,
        'project/nodes/002/artifacts/old-package.json',
        'project/nodes/002/artifacts/scientific-implementations/I002.json'}
    if attempt == 2:
        required |= {'host-recovery/author-closeout/002-successor.json',
            'host-recovery/author-closeout/002-output-upgrade.json'}
        required |= {'native-evidence/002/continuations/001/' + name for name in
            ('native-intent.json', 'native-outcome.json', 'usage.json', 'usage-observation.json',
             'native.jsonl', 'runner-process.json', 'native-process.json')}
    assert required <= paths
    # Exercise the production copier with an explicit retained-proof closure;
    # this fixture makes no inspect_source admission or scientific-cut claim.
    plan = dict(version=1, kind='scientific_fork',
        source=dict(origin_identity=source.origin_identity, step=2),
        entries=list(closure.entries.values()), origins=closure.origins,
        legacy_compilations=closure.legacy_compilations, bootstrap={},
        source_paths=closure.paths, blocked_reasons=closure.blocked)
    plan['source_fingerprint'] = fork_sources.fingerprint(plan)
    child = tmp_path / 'copied-child'
    manifest = fork_sources.copy_source(plan, child / originals.FORK_DIRECTORY)
    shutil.rmtree(source.storage_root)
    assert fork_sources.validate_manifest(child, full=True) == manifest
    retained = originals.selected_source(child)
    value = inputs.verify_original_compilation(retained, compiler, material=material, client_binding=binding)
    assert value['output']['path'].endswith('/artifacts/old-package.json')
    assert originals.legacy_compilation(retained)['compiler_paths'] == [
        'native-evidence/002/compiled-implementations/a-original/compiler.json', compiler['path']]
