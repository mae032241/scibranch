"""Small local contracts; no runtime/Root/installation or solver fixture."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from scidiscovery.artifact_agent.schema.common import canonical_json
from tcad_artifact.execution_control import SolverCapabilitySnapshot
from tests.operations.test_runtime_plugin_configuration import _execution_package
from tcad_artifact.native_implementation import compile_native_implementation
from tcad_artifact.project_packager import (
    DeckProjectDraft, NativeExecutionPackage, NativeResolvedProjectInput,
    ProjectInitializationAttestation,
    ScientificDeckImplementation,
)


def _document():
    return json.loads(_execution_package())


def _initialization_report():
    return dict(schema_version=1,profile='tcad.project-initialization.v1',
        mode='initialization',terminal_state='succeeded',exit_code=0,
        diagnostic_layer='complete',qualified=True,summary='Exact source-bound initialization fixture')


def test_initialization_attestation_accepts_exact_project_source():
    project=_document()['project']
    assert project['files']==[dict(relative_path='main.cmd',content='Solve {}\n')]
    report=ProjectInitializationAttestation.model_validate_json(canonical_json(_initialization_report()),strict=True)
    project['initialization_attestation']=report.model_dump(mode='json')
    validated=DeckProjectDraft.model_validate_json(canonical_json(project),strict=True)
    assert validated.initialization_attestation==report
    assert validated.initialization_attestation.qualified is True


def test_initialization_attestation_rejects_preflight_report():
    preflight=dict(_initialization_report(),profile='tcad.project-preflight.v1',mode='preflight')
    with pytest.raises(ValidationError,match='literal_error'):
        ProjectInitializationAttestation.model_validate_json(canonical_json(preflight),strict=True)


def test_native_compiler_preserves_exact_origin_and_control_capture():
    # Ref objects here are declarations only; producer membership and source
    # authorization are tested at the host loader, not asserted by this test.
    document = _document()
    capability = SolverCapabilitySnapshot.model_validate_json(canonical_json(document['capability']), strict=True)
    slot = dict(semantic_name='mesh', target_relative_path='inputs/mesh.tdr',
                media_type='application/octet-stream')
    authored = ScientificDeckImplementation.model_validate_json(canonical_json(dict(
        solver_kind='sdevice', files=[dict(relative_path='main.cmd', content='Solve {}\n')],
        input_slots=[slot], entrypoint='main.cmd', resource_limits=document['project']['resource_limits'])), strict=True)
    original = dict(instance_id='scid-hwa-native-test', producer_step=1,
        producer_seal=dict(path='native-evidence/001/result-identity.json', size_bytes=100),
        material_path='inputs/parents/001/artifacts/mesh.tdr',
        size_bytes=4)
    resolved = NativeResolvedProjectInput.model_validate_json(canonical_json(
        dict(slot, original=original, size_bytes=4)), strict=True)
    raw = compile_native_implementation(authored, capability=capability, resolved_inputs=(resolved,))
    package = NativeExecutionPackage.model_validate_json(raw, strict=True)
    assert package.schema_version == 3
    assert package.review is None
    assert package.project.preflight_attestation is None
    assert package.project.collect_generated_outputs is True
    assert package.project.tool_profile == capability.profile_id
    assert package.project.expected_outputs[0].model_dump(mode='json') == dict(
        name='process_log', capture='process_log', relative_path='.scid-capture/solver_stdout.log',
        media_type='text/plain', max_bytes=document['project']['resource_limits']['max_output_bytes'],
        required=True, experiment_key=None, case_key=None)
    assert package.resolved_inputs[0].original.model_dump(mode='json') == original
    assert 'artifact_ref' not in json.loads(raw)['resolved_inputs'][0]
    another = dict(original, producer_step=2,
        producer_seal=dict(original['producer_seal'], path='native-evidence/002/result-identity.json'),
        material_path='inputs/parents/002/artifacts/mesh.tdr')
    other = NativeResolvedProjectInput.model_validate_json(canonical_json(
        dict(slot, original=another, size_bytes=4)), strict=True)
    # Same scientific bytes do not erase the exact native producer identity.
    assert compile_native_implementation(authored, capability=capability, resolved_inputs=(other,)) != raw
    with pytest.raises(ValueError):
        NativeResolvedProjectInput.model_validate_json(canonical_json(
            dict(slot, original=original, size_bytes=5)), strict=True)
    with pytest.raises(ValueError):
        compile_native_implementation(authored, capability=capability, resolved_inputs=())
    limited = authored.model_copy(update={'resource_limits': authored.resource_limits.model_copy(
        update={'max_storage_bytes': len('Solve {}\n'.encode())+3})})
    with pytest.raises(ValueError, match='storage'):
        compile_native_implementation(limited, capability=capability, resolved_inputs=(resolved,))
