"""Public implementation clients bind exact private packages; no backend execution."""
from pathlib import Path

import pytest

from scidiscovery.research_loop import contracts as c, execution_inputs as inputs, experiments as e
from test_history_handoff import finish
from test_native_execution_inputs import chain


@pytest.fixture
def compiled_client(chain):
    root, backend, _, config, _, author, aliases = chain
    work = c.workspace(root, 2)
    public = work / 'artifacts/implementation.json'
    result = e.compile_implementation(config, e.load(config), author, c.read(aliases), public)
    assert result['status'] == 'compiled' and not backend.prepared and not backend.submits
    job = c.read(root / 'jobs/002.json')
    materials = c.collect_materials(root, 2)
    rows = inputs.capture(root, job, materials)
    assert len(rows) == 1
    row = rows[0]
    return root, config, public, job, materials, row


def test_compiled_scientific_client_seals_separate_exact_private_package(compiled_client):
    root, config, public, _, _, row = compiled_client
    stub = c.read(public)
    assert stub == dict(worker_implementation_version=1, implementation_ref='I001')
    value = inputs.verify_compilation(root, row['compiler'], material=row['material'], client_binding=row['client_binding'])
    expected = Path(config['record_dir']).parent / 'implementations/I001/package.json'
    assert value['output'] == e.proof(expected)
    assert value['output']['path'] != row['material']['source']
    assert c.read(expected)['schema_version'] == 3
    finish(root, 2, 'W', [1])
    seal = c.read(c.host_record(c.workspace(root, 2)) / 'result-identity.json')
    assert seal['compiled_implementations'] == [row]
    assert inputs.compiled_payload(root, row['material']) == value['output']
    assert c.read(public) == stub


@pytest.mark.parametrize('subject', ['client_bytes', 'package_bytes', 'binding_package', 'binding_public_path', 'binding_client'])
def test_compiler_rejects_changed_client_package_or_private_binding(compiled_client, subject):
    root, _, public, job, materials, row = compiled_client
    value = inputs.verify_compilation(root, row['compiler'], material=row['material'], client_binding=row['client_binding'])
    binding_path = root / row['client_binding']['path']
    if subject == 'client_bytes':
        public.chmod(0o600)
        c.write(public, dict(worker_implementation_version=1, implementation_ref='I002'))
    elif subject == 'package_bytes':
        path = Path(value['output']['path'])
        path.chmod(0o600)
        path.write_bytes(path.read_bytes() + b' ')
    else:
        binding = c.read(binding_path)
        if subject == 'binding_package':
            binding['package']['path'] = str(public.with_name('unapproved-package.json'))
        elif subject == 'binding_public_path':
            binding['public_path'] = str(public.with_name('unrelated.json'))
        else:
            binding['client']['implementation_ref'] = 'I002'
        binding_path.chmod(0o600)
        c.write(binding_path, binding)
    with pytest.raises(inputs.q.IntegrityError):
        inputs.verify_compilation(root, row['compiler'], material=row['material'], client_binding=row['client_binding'])
    with pytest.raises(inputs.q.IntegrityError):
        inputs.capture(root, job, materials)


def test_client_binding_proof_is_mandatory_and_same_bytes_do_not_select_another_material(compiled_client):
    root, _, public, _, _, row = compiled_client
    with pytest.raises(inputs.q.IntegrityError, match='sealed original'):
        inputs.verify_compilation(root, row['compiler'], material=row['material'])
    copied = public.with_name('same-bytes.json')
    copied.write_bytes(public.read_bytes())
    wrong = dict(row['material'], source=str(copied))
    with pytest.raises(inputs.q.IntegrityError, match='exact scientific material'):
        inputs.verify_compilation(root, row['compiler'], material=wrong, client_binding=row['client_binding'])


def test_generic_payload_fallback_still_checks_exact_material_bytes(tmp_path):
    path = tmp_path / 'ordinary-package.json'
    path.write_bytes(b'{"schema_version":2,"scientific_payload":true}')
    identity = e.proof(path)
    material = dict(source=str(path), size_bytes=identity['size_bytes'])
    assert inputs.compiled_payload(tmp_path, material) == identity
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(inputs.q.IntegrityError, match='exact source'):
        inputs.compiled_payload(tmp_path, material)
