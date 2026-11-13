"""Selected Git contexts use original compiler controls, never old export files."""
from copy import deepcopy
from pathlib import Path

import pytest

pytestmark = pytest.mark.process_e2e

from scidiscovery.research_loop import author_closeout, code_versions, contracts as c, accepted_budget, execution_inputs as inputs, experiments as e, shared_input_views
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
    finish(root, 2, 'W', [1], preserve_native_intent=True)
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


def test_bound_packet_and_catalogue_use_final_readonly_inputs_view(chain):
    from scidiscovery.research_loop import experiment_client
    root, _, binding, compiler = compiled_scene(chain)
    job = c.build_job(root, 3, 'W', [2]); work = c.workspace(root, 3)
    record = c.host_record(work)
    accepted_budget.accept_native(root, 3, record, dict(task_ref=c.record_ref(root / 'jobs/003.json')))
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
