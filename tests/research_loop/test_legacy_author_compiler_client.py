"""Synthetic legacy compiler and grant ports; no rewritten task or native execution."""
from pathlib import Path

import pytest

from scidiscovery.research_loop import author_closeout, contracts as c, execution_inputs as inputs


@pytest.fixture
def legacy_compiler(tmp_path, monkeypatch):
    root = tmp_path / 'instance'
    job = dict(step=2, role='W')  # A separately constructed historical protocol.
    c.write(root / 'jobs/002.json', job)
    job_ref = inputs.q._ref(root, root / 'jobs/002.json')
    origin = root / 'native-evidence/002'
    c.write(origin / 'native-intent.json', dict(task_ref=c.record_ref(root/job_ref['path'])))
    original_intent = inputs.q._ref(root, origin / 'native-intent.json')
    package = c.workspace(root, 2) / 'artifacts/old-package.json'
    c.write(package, dict(schema_version=3, capability_ref='a' * 64))
    original_bytes = package.read_bytes()
    identity = inputs.q._identity(package)
    material = dict(source=str(package), path='inputs/parents/002/artifacts/old-package.json',
                    size_bytes=identity['size_bytes'])
    compiler_path = inputs._directory(root, 2) / 'original-compiler/compiler.json'
    compiler = dict(job=job_ref, native_intent=original_intent, output=identity)
    compiler_ref = inputs.q._save(root, compiler_path, compiler)
    permit = dict(version=1, scope='author_closeout_only', context='new_context', step=2,
                  job=job_ref, original_intent=original_intent)
    permit_ref = inputs.q._save(root, root / 'host-recovery/author-closeout/002.json', permit)
    # The original compiler and full grant implementations have separate real
    # integration coverage. These ports assert the exact old subject and grant
    # passed by the new client protocol, without inventing a historical job seal.
    original_verify = inputs.verify_compilation

    def verify(root_arg, ref, *, material=None, client_binding=None):
        if ref == compiler_ref and client_binding is None:
            assert material == legacy_compiler_material
            for proof in (compiler['output'],):
                inputs.q._check(proof)
            return compiler
        return original_verify(root_arg, ref, material=material, client_binding=client_binding)

    legacy_compiler_material = material
    monkeypatch.setattr(inputs, 'verify_compilation', verify)
    monkeypatch.setattr(author_closeout, 'validate', lambda root_arg, value: value if value == permit else pytest.fail('Different grant'))
    monkeypatch.setattr(author_closeout, 'require_original_eligibility',
                        lambda root_arg, value: value if value == permit else pytest.fail('Different eligibility subject'))
    public = c.workspace(root, 2) / 'artifacts/scientific-implementations/I001.json'
    c.write(origin / 'continuations/001/native-intent.json', dict(attempt=1, task_ref=c.record_ref(root/job_ref['path']),
        author_closeout=permit_ref, continuation_kind='author_closeout', context_mode='new_context', worker_view_version=1))
    return root, job, compiler_ref, material, permit_ref, public, original_bytes


def create(selected):
    root, job, compiler, material, permit, public, _ = selected
    return inputs.create_legacy_author_client(root, job, compiler, material, permit, 1, public)


def test_legacy_author_client_has_actual_new_path_and_preserves_raw_package(legacy_compiler):
    root, job, compiler, material, permit, public, original_bytes = legacy_compiler
    ref = create(legacy_compiler)
    binding, original = inputs.verify_legacy_author_client(root, job, ref)
    assert binding['public_path'] == str(public) != material['source']
    assert binding['legacy_authorization'] == dict(permit=permit, compiler=compiler, material=material, attempt=1)
    assert c.read(public) == dict(worker_implementation_version=1, implementation_ref='I001')
    assert binding['package'] == original['output']
    assert Path(material['source']).read_bytes() == original_bytes
    assert c.read(root / 'jobs/002.json') == job and 'worker_view_version' not in job


def test_legacy_client_cannot_masquerade_at_old_package_path(legacy_compiler):
    root, job, compiler, material, permit, _, original_bytes = legacy_compiler
    with pytest.raises(inputs.q.IntegrityError, match='actual new scientific path'):
        inputs.create_legacy_author_client(root, job, compiler, material, permit, 1, material['source'])
    assert Path(material['source']).read_bytes() == original_bytes


def test_legacy_client_requires_original_authorization_and_accepted_intent(legacy_compiler):
    root, job, compiler, material, permit, public, _ = legacy_compiler
    with pytest.raises(inputs.q.IntegrityError, match='exact author authorization'):
        inputs.create_legacy_author_client(root, job, compiler, material, dict(permit, path='host-recovery/unapproved.json'), 1, public)
    assert not public.exists()
    ref = create(legacy_compiler)
    intent_path = root / 'native-evidence/002/continuations/001/native-intent.json'
    intent = c.read(intent_path)
    intent['author_closeout'] = dict(permit, path='host-recovery/unapproved.json')
    c.write(intent_path, intent)
    with pytest.raises(inputs.q.IntegrityError, match='accepted author context'):
        inputs.verify_legacy_author_client(root, job, ref)


@pytest.mark.parametrize('changed', ['binding', 'package'])
def test_legacy_client_rejects_changed_private_binding_and_original_package(legacy_compiler, changed):
    root, job, _, material, _, _, _ = legacy_compiler
    ref = create(legacy_compiler)
    if changed == 'binding':
        path = root / ref['path']
        binding = c.read(path)
        binding['package']['path'] = str(root/'unapproved-package.json')
        path.chmod(0o600)
        c.write(path, binding)
    else:
        package = Path(material['source'])
        package.write_bytes(package.read_bytes() + b' ')
    with pytest.raises(inputs.q.IntegrityError):
        inputs.verify_legacy_author_client(root, job, ref)


def test_legacy_capture_rejects_unbound_self_authored_client(legacy_compiler):
    root, job, _, _, _, public, _ = legacy_compiler
    c.write(public, dict(worker_implementation_version=1, implementation_ref='I001'))
    identity = inputs.q._identity(public)
    material = dict(source=str(public), path='inputs/parents/002/artifacts/scientific-implementations/I001.json',
                    size_bytes=identity['size_bytes'])
    with pytest.raises(inputs.q.IntegrityError, match='exact sealed compiler provenance'):
        inputs.capture(root, job, [material])
