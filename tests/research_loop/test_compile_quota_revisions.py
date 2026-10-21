"""Real compilation follows each job's saved quota; native scheduling is synthetic."""
from pathlib import Path

import pytest

from scidiscovery.research_loop import (
    configuration_updates as updates, contracts as c, experiments as e,
)
from test_history_handoff import finish
from test_native_execution_inputs import chain


def save_quota(root, field, limit):
    state = updates.status(root)
    document = state['configuration']
    document['budget'][field] = limit
    saved = updates.submit(root, document, expected_revision=state['revision'])
    assert saved['status'] == 'configuration_saved' and saved['pending']
    return saved['revision']


def compilation(root, step, options, implementation, aliases):
    job = c.build_job(root, step, 'W', [1])
    work = c.workspace(root, step)
    record = c.host_record(work)
    c.write(record / 'native-intent.json', dict(task_sha256=c.digest(root / f'jobs/{step:03d}.json')))
    config = e.configure(root, step, 'tcad_execution:tcad', options, work / 'work/execution.json')
    source = work / 'work/implementation.json'
    c.write(source, implementation)

    def compile(name):
        output = work / 'artifacts' / name
        result = e.compile_implementation(config, e.load(config), source, aliases, output)
        assert result['status'] == 'compiled'
        reference = c.read(output)['implementation_ref']
        package = Path(config['record_dir']).parent / 'implementations' / reference / 'package.json'
        assert package.is_file()
        return package.stat().st_size

    return job, compile


@pytest.mark.parametrize('check,field', [
    ('inputs', 'max_output_total_bytes'),
    ('package', 'max_output_item_bytes'),
    ('package', 'max_output_total_bytes'),
])
def test_compile_uses_stricter_and_looser_job_revisions(chain, check, field):
    root, backend, options, _, _, author, alias_path = chain
    implementation = c.read(author)
    aliases = c.read(alias_path)
    if check == 'inputs':
        # One bound original passes build_job's quota, while two declared slots
        # count that original twice in the compiler's resolved input inventory.
        implementation['input_slots'].append(dict(implementation['input_slots'][0],
            semantic_name='second_mesh', target_relative_path='inputs/second-mesh.tdr'))
        aliases['second_mesh'] = aliases['mesh']
    old_job, old_compile = compilation(root, 2, options, implementation, aliases)
    old_bytes = (root / 'jobs/002.json').read_bytes()
    package_size = old_compile('baseline.json')
    input_size = old_job['direct_materials'][0]['size_bytes']
    strict_limit = input_size + 1 if check == 'inputs' else package_size - 1
    assert strict_limit >= input_size
    strict_revision = save_quota(root, field, strict_limit)
    assert c.build_job(root, 2, 'W', [1]) == old_job
    assert (root / 'jobs/002.json').read_bytes() == old_bytes
    assert c.settings(root, old_job).get(field) is None
    assert old_compile('original-after-save.json') == package_size
    finish(root, 2, 'W', [1])

    strict_job, strict_compile = compilation(root, 3, options, implementation, aliases)
    strict_bytes = (root / 'jobs/003.json').read_bytes()
    assert strict_job['configuration_revision']['revision'] == strict_revision
    assert c.settings(root, strict_job)[field] == strict_limit
    message = 'Scientific inputs exceed' if check == 'inputs' else 'Compiled package exceeds'
    with pytest.raises(ValueError, match=message):
        strict_compile('rejected.json')
    assert not (c.workspace(root, 3) / 'artifacts/rejected.json').exists()

    loose_limit = package_size * 2
    loose_revision = save_quota(root, field, loose_limit)
    assert c.build_job(root, 3, 'W', [1]) == strict_job
    assert (root / 'jobs/003.json').read_bytes() == strict_bytes
    assert c.settings(root, strict_job)[field] == strict_limit
    with pytest.raises(ValueError, match=message):
        strict_compile('still-rejected.json')
    finish(root, 3, 'W', [1])

    loose_job, loose_compile = compilation(root, 4, options, implementation, aliases)
    assert loose_job['configuration_revision']['revision'] == loose_revision
    assert c.settings(root, loose_job)[field] == loose_limit
    assert loose_compile('accepted.json') == package_size
    assert not backend.prepared and not backend.submits and not backend.policy_reads
