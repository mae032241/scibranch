"""Selected Git contexts use original compiler controls, never old export files."""
from copy import deepcopy
from pathlib import Path

import pytest

pytestmark = pytest.mark.process_e2e

from scidiscovery.research_loop import (author_closeout, code_versions, contracts as c,
    execution_inputs as inputs, experiments as e, formal_review as review,
    qualification as q, shared_input_views)
from test_history_handoff import finish
from test_native_execution_inputs import chain


def compiled_scene(chain, *, another=False):
    root, _, _, config, _, original, aliases = chain
    work = c.workspace(root, 2)
    draft = work / 'code/implementation.json'
    draft.parent.mkdir(exist_ok=True)
    draft.write_bytes(original.read_bytes())
    output = work / 'artifacts/compiled.json'
    e.compile_implementation(config, e.load(config), draft, c.read(aliases), output)
    if another:
        extra = draft.with_name('unselected.json'); extra.write_bytes(draft.read_bytes())
        e.compile_implementation(config, e.load(config), extra, c.read(aliases), work / 'artifacts/unselected.json')
    finish(root, 2, 'W', [1])
    seal = c.read(c.host_record(work) / 'result-identity.json')
    binding = next(row for row in seal['compiled_implementations']
        if row['material']['source'] == str(output))
    compiler = inputs.verify_compilation(root, binding['compiler'], material=binding['material'],
        client_binding=binding['client_binding'])
    return root, draft, binding, compiler


def test_git_compiler_context_keeps_permanent_binding_after_export_cleanup(chain):
    root, draft, binding, compiler = compiled_scene(chain)
    identity = inputs._compiler_call_id(root, 2, binding['compiler'])
    version = deepcopy(compiler['code_version'])
    job = c.build_job(root, 3, 'W', [2])
    record = root / 'host-recovery/author-context/003/001'; record.mkdir(parents=True)
    proof = author_closeout.science_context(root, job, record)
    manifest = c.read(root / proof['path'])
    entry = next(row for row in manifest['entries'] if 'code_version' in row)
    assert entry['source'] == entry['compiler'] == binding['compiler']
    assert entry['code_version'] == version and len(version) == 7
    assert set(entry['display']) == {'path'} and 'author' not in compiler
    exported = code_versions.materialize(root, step=2, call_id=identity, code_version=version)
    assert (exported / version['entry']).read_bytes() == draft.read_bytes()
    code_versions.cleanup_export(root, step=2, call_id=identity)
    draft.unlink()
    assert not exported.exists()
    assert author_closeout.validate_science_context(root, job, proof) == record / 'author-science-context'
    retained = author_closeout._context_compiler(root, job, entry)
    assert retained['code_version'] == version
    assert inputs._compiler_call_id(root, 2, entry['compiler']) == identity
    assert str(exported) not in (root / proof['path']).read_text()
    assert not (root / entry['display']['path']).exists()


def test_selected_code_view_uses_original_compiler_uuid_and_pin(chain, monkeypatch):
    root, draft, binding, compiler = compiled_scene(chain, another=True)
    identity = inputs._compiler_call_id(root, 2, binding['compiler'])
    version = deepcopy(compiler['code_version'])
    verify = code_versions.verify; checked = []

    def selected_verify(root, *, step, call_id, code_version, **options):
        assert (step, call_id, code_version) == (2, identity, version)
        checked.append(call_id)
        return verify(root, step=step, call_id=call_id, code_version=code_version, **options)

    monkeypatch.setattr(code_versions, 'verify', selected_verify)
    requester, _, request_record = finish(root, 3, 'A', [2], action='formal_review', selection=['W002-I001'])
    role, parents, instruction, back = review.request_route(root, requester,
        c.read(request_record / 'result.json'), [3])
    job = c.build_job(root, 4, role, parents, instruction, back)
    draft.unlink()
    materialize = code_versions.materialize; selected = []

    def materialized(root, *, step, call_id, code_version, **options):
        selected.append((step, call_id, code_version))
        return materialize(root, step=step, call_id=call_id, code_version=code_version, **options)

    monkeypatch.setattr(code_versions, 'materialize', materialized)
    exports = []
    mappings = shared_input_views.selected_code_mounts(root, job, c.workspace(root, 4), exports=exports)
    assert selected == [(2, identity, version)] and checked
    assert exports == [(2, identity)]
    assert mappings[0][1] == c.workspace(root, 4) / 'inputs/review/scientific-implementation.json'
    assert mappings[0][0].read_bytes()
    arguments = shared_input_views.mount_arguments(root, job, c.workspace(root, 4), include_skills=False)
    readonly = [tuple(arguments[index:index + 3]) for index, flag in enumerate(arguments) if flag == '--ro-bind']
    assert ('--ro-bind', str(mappings[0][0]), str(mappings[0][1])) in readonly
    code_versions.cleanup_export(root, step=2, call_id=identity)

    def missing_pin(*args, **kwargs):
        raise FileNotFoundError('selected original pin is absent')

    monkeypatch.setattr(code_versions, 'verify', missing_pin)
    with pytest.raises(q.IntegrityError, match='original pin'):
        shared_input_views.selected_code_mounts(root, job, c.workspace(root, 4))


def test_bound_packet_and_catalogue_use_final_readonly_inputs_view(chain):
    from scidiscovery.research_loop import experiment_client
    root, _, binding, compiler = compiled_scene(chain)
    job = c.build_job(root, 3, 'W', [2]); work = c.workspace(root, 3)
    record = c.host_record(work)
    c.write(record / 'native-intent.json', dict(task_ref=c.record_ref(root / 'jobs/003.json')))
    config = e.configure(root, 3, 'tcad_execution:tcad', chain[2], work / 'work/execution.json')
    logical = work / binding['material']['path']
    packet = experiment_client.payload_path(config, logical)
    assert packet == Path(compiler['output']['path'])
    assert e.proof(packet) == compiler['output']
    catalogue = work / 'inputs/delivery-candidates.json'
    c.write(catalogue, dict(items=[], omitted_count=0))
    contents = catalogue.read_bytes()
    closeout_view = dict(job, delivery_sources=dict(catalogue=True))
    arguments = shared_input_views.mount_arguments(root, closeout_view, work,
        mounts=[(packet, logical)], include_skills=False)
    readonly = [tuple(arguments[index:index + 3]) for index, flag in enumerate(arguments) if flag == '--ro-bind']
    assert [row[1] for row in readonly if row[2] == str(logical)] == [str(packet)]
    assert ('--ro-bind', str(catalogue), str(catalogue)) in readonly
    assert catalogue.read_bytes() == contents
    ordinary = shared_input_views.mount_arguments(root, job, work, include_skills=False)
    assert str(catalogue) not in ordinary


def test_legacy_original_metadata_rejects_unrelated_material_and_client(chain):
    from scidiscovery.research_loop import originals
    root, _, binding, compiler = compiled_scene(chain)
    # Reuse the real compiler/control fixture with the retained legacy job
    # interpretation. This is a metadata read, with its old science absent.
    job_path = root / 'jobs/002.json'
    job = c.read(job_path); job.pop('worker_view_version')
    job_path.chmod(0o600); c.write(job_path, job)
    compiler_path = root / binding['compiler']['path']
    value = dict(compiler, job=q._ref(root, job_path))
    compiler_path.chmod(0o600); c.write(compiler_path, value)
    reference = q._ref(root, compiler_path)
    source = originals.node(root, 2)
    material = dict(source=value['output']['path'],
        path='inputs/parents/002/artifacts/legacy-package.json', size_bytes=value['output']['size_bytes'])
    Path(value['output']['path']).unlink()
    assert inputs.verify_original_compilation(source, reference, material=material, controls_only=True) == value
    for changed in (dict(material, source=str(c.workspace(root, 2) / 'artifacts/unrelated.json')),
            dict(material, size_bytes=material['size_bytes'] + 1)):
        with pytest.raises(q.IntegrityError, match='exact sealed material'):
            inputs.verify_original_compilation(source, reference, material=changed, controls_only=True)
    with pytest.raises(q.IntegrityError, match='Legacy compiler client'):
        inputs.verify_original_compilation(source, reference, material=binding['material'],
            client_binding=binding['client_binding'], controls_only=True)
