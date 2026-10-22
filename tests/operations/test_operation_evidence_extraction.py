"""Retained Operation callbacks preserve exact proofs without an old Run host."""
from dataclasses import replace
import hashlib
import json

import pytest

from scidiscovery.artifact_agent.operation_tool_context import OperationToolContext
from scidiscovery.artifact_agent.schema.common import canonical_json
from scidiscovery.artifact_agent.schema.refs import ArtifactRef
from scidiscovery.artifact_agent.schema.tool_evidence import ToolEvidenceManifest, ToolSourceBinding
from scidiscovery.general_science_resources import TOOL_EVIDENCE_SCHEMA
from scidiscovery.operation_contract import SemanticRuleViolation
from scidiscovery.operation_declaration import schema_resource
from scidiscovery.operations import catalog as compiler
from scidiscovery.operations.calculation_proof import (
    ControlledCalculationRecord, calculation_record_content, calculation_sources,
    controlled_calculation, scientific_calculation,
)
from scidiscovery.operations.input_validation import InputBindingDescriptor, ValidationSources
from scidiscovery.plugin_runtime.calculations import CalculationResult


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _source(alias='data', *, identity='artifact_source', port='solver_outputs'):
    raw = b'exact original bytes'
    ref = ArtifactRef(artifact_id=identity, kind='evidence', schema_id='opaque', sha256=_digest(raw))
    return raw, InputBindingDescriptor(alias, port, ref, 'text/plain', len(raw), ref.sha256)


def _completed():
    raw, descriptor = _source()
    record = ControlledCalculationRecord(record_key='calculation', algorithm_version='fixture-v1',
        request={'metric': 'value'}, status='computed', result={'value': 1},
        input_digests={'data': descriptor.sha256},
        attempt={'manifest_alias': 'tool_recovery_manifest', 'attempt_key': 'attempt_one'})
    bindings = {'data': ToolSourceBinding(artifact_ref=descriptor.artifact_ref,
        port_name=descriptor.port_name).model_dump(mode='json')}
    attempt = dict(attempt_key='attempt_one', tool_name='fixture', operation_digest='0' * 64,
        request_digest=_digest(canonical_json(record.request)), state='completed',
        result_status='computed', read_sources=['data'],
        sources_digest=_digest(canonical_json(bindings)),
        result_digest=_digest(canonical_json(calculation_record_content(record))))
    manifest = canonical_json(dict(bindings=bindings, attempts=[attempt]))
    return record, ValidationSources({'data': raw}, {'data': descriptor}, tool_snapshot=manifest)


def test_completed_receipt_preserves_exact_scientific_bytes():
    record, sources = _completed()
    assert calculation_sources(record, sources) is sources
    receipt = {'metadata': {'calculation_proof': record.model_dump(mode='json')}}
    raw = canonical_json(scientific_calculation(record))
    assert controlled_calculation(raw, receipt) == record
    altered = dict(json.loads(raw), result={'value': 2})
    with pytest.raises(ValueError, match='differs from its sealed proof'):
        controlled_calculation(canonical_json(altered), receipt)


def _projected_calculation():
    record, sources = _completed()
    record = record.model_copy(update={'result': {'value': 1,
        'metric_report': {'values': [1, 2], 'source_identity': 'private identity'}}})
    record = ControlledCalculationRecord.model_validate_json(canonical_json(record.model_dump(mode='json')))
    proof = json.loads(sources.tool_snapshot)
    proof['attempts'][0]['result_digest'] = _digest(canonical_json(calculation_record_content(record)))
    sources = ValidationSources(sources, sources.binding_descriptors, tool_snapshot=canonical_json(proof))
    public = {'value': 1, 'metric_report': {'values': [1, 2]}}
    raw = canonical_json(scientific_calculation(record, public_result=public))
    return record, sources, public, raw


@pytest.mark.parametrize('algorithm', ['fixture-v1', 'analysis-curve-score.v1', 'tcad-score.v1'])
def test_shared_calculation_projection_does_not_interpret_algorithm_names(algorithm):
    record, _ = _completed()
    result = {'value': 1, 'curve_bundle_sha256': 'domain value',
        'metric_report': {'validation_plan_sha256': 'another domain value'}}
    record = record.model_copy(update={'algorithm_version': algorithm, 'result': result})
    assert scientific_calculation(record)['result'] == result


@pytest.mark.parametrize('legacy', [False, True])
def test_public_projection_retains_the_complete_original_proof(legacy):
    record, sources, _, raw = _projected_calculation()
    metadata = {'calculation_proof': record.model_dump(mode='json')}
    if not legacy:
        metadata['calculation_public_sha256'] = _digest(raw)
    receipt = {'metadata': metadata, 'artifact_ref': {'sha256': _digest(raw)}}
    restored = controlled_calculation(raw, receipt)
    assert restored == record
    assert restored.result['metric_report']['source_identity'] == 'private identity'
    assert calculation_sources(restored, sources) is sources
    receipt['metadata'].pop('calculation_public_sha256', None)
    receipt['artifact_ref']['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='sealed public identity'):
        controlled_calculation(raw, receipt)


@pytest.mark.parametrize('changed', [
    {'value': 2}, {'metric_report': {'values': [1]}},
    {'metric_report': {'values': [2, 1]}}, {'invented': 1},
])
def test_public_calculation_projection_cannot_change_numbers_or_invent_fields(changed):
    record, _, public, _ = _projected_calculation()
    with pytest.raises(ValueError, match='only omit fields'):
        scientific_calculation(record, public_result={**public, **changed})


@pytest.mark.parametrize('change', ['number', 'request', 'proof', 'unsealed_projection'])
def test_arbitrary_public_identity_cannot_pair_unrelated_calculation_proof(change):
    record, sources, _, raw = _projected_calculation()
    value = json.loads(raw)
    if change == 'number':
        value['result']['value'] = 9
    elif change == 'request':
        value['request'] = {'other': 'request'}
    elif change == 'proof':
        record = record.model_copy(update={'result': {'value': 99}})
    raw = canonical_json(value)
    receipt = {'metadata': {'calculation_proof': record.model_dump(mode='json')}}
    if change != 'unsealed_projection':
        receipt['artifact_ref'] = {'sha256': _digest(raw)}
    with pytest.raises(ValueError, match='sealed'):
        controlled_calculation(raw, receipt)
    # The retained attempt independently binds all source/result/request bytes.
    altered = record.model_copy(update={'request': {'other': 'request'}})
    with pytest.raises(SemanticRuleViolation, match='recorded attempt'):
        calculation_sources(altered, sources)


def test_complete_calculation_seals_public_bytes_and_full_attempt_separately(tmp_path):
    from types import SimpleNamespace
    from scidiscovery.operations.analysis_artifacts import complete_calculation
    record, sources, public, expected_raw = _projected_calculation()
    saved = {}
    def accept_evidence(**kwargs):
        saved.update(kwargs)
        return {'alias': 'published_calculation'}
    def finish_attempt(**kwargs):
        assert kwargs['response'] == calculation_record_content(record)
        return record.attempt.model_dump(mode='json'), ()
    context = SimpleNamespace(workspace=tmp_path, accept_evidence=accept_evidence,
        finish_attempt=finish_attempt, input_names_for_port=lambda port: ())
    response = complete_calculation(context, calculation_record_content(record), public_result=public)
    assert saved['raw'] == expected_raw
    assert saved['metadata']['calculation_public_sha256'] == _digest(expected_raw)
    assert response['result'] == public
    assert response['calculation_ref'] == 'published_calculation'
    restored = controlled_calculation(saved['raw'], {'metadata': saved['metadata']})
    assert calculation_sources(restored, sources) is sources


def test_plugin_evidence_view_excludes_public_digest_control_metadata():
    from scidiscovery.plugin_runtime.evidence import read_evidence_records
    raw = canonical_json({'records': [{'alias': 'calculation', 'metadata': {
        'record_key': 'science', 'calculation_proof': {'private': 'proof'},
        'calculation_public_sha256': '0' * 64, 'checkpoint_proof': {'private': 'checkpoint'}}}]})
    assert read_evidence_records(raw)[0]['metadata'] == {'record_key': 'science'}


@pytest.mark.parametrize(('defect', 'reason'), [
    ('public_record', 'Numerical evidence must cite a registered calculation'),
    ('request', 'calculation request differs from its recorded attempt'),
    ('result', 'calculation record differs from the actual tool response'),
    ('attempt', 'calculation attempt has no terminal control evidence'),
    ('source_identity', 'calculation attempt source is not exactly bound for replay'),
])
def test_unproven_or_changed_calculation_never_inherits_a_receipt(defect, reason):
    record, sources = _completed()
    assert calculation_sources(record, sources) is sources
    if defect == 'public_record':
        record = CalculationResult.model_validate_json(canonical_json(calculation_record_content(record)))
    elif defect == 'request':
        record = record.model_copy(update={'request': {'metric': 'other'}})
    elif defect == 'result':
        record = record.model_copy(update={'result': {'value': 2}})
    elif defect == 'attempt':
        record = record.model_copy(update={'attempt': record.attempt.model_copy(update={'attempt_key': 'absent'})})
    else:
        raw, descriptor = _source(identity='artifact_same_bytes_other_identity')
        sources = ValidationSources({'data': raw}, {'data': descriptor}, tool_snapshot=sources.tool_snapshot)
    with pytest.raises(SemanticRuleViolation, match=reason):
        calculation_sources(record, sources)


def test_recovery_uses_exact_original_and_port_instead_of_alias_order():
    raw, descriptor = _source('current_data')
    record = ControlledCalculationRecord(record_key='interrupted', algorithm_version='fixture-v1',
        request={}, status='error', reason_code='interrupted', input_digests={'old_data': descriptor.sha256},
        attempt={'manifest_alias': 'tool_recovery_manifest', 'attempt_key': 'attempt_one', 'proof_kind': 'recovery'})
    proof = dict(recovery=dict(source_request_digest='0' * 64, operation_digest='0' * 64,
        draft_digest='0' * 64, bindings={'old_data': dict(
            artifact_ref=descriptor.artifact_ref.model_dump(mode='json'), port_name='solver_outputs')},
        attempts=[dict(attempt_key='attempt_one', tool_name='fixture', operation_digest='0' * 64,
            request_digest=_digest(canonical_json({})), state='interrupted', read_sources=['old_data'])]))
    sources = ValidationSources({'context': raw, 'current_data': raw},
        {'context': replace(descriptor, source_name='context', port_name='user_context'),
         'current_data': descriptor}, tool_snapshot=canonical_json(proof))
    recovered = calculation_sources(record, sources)
    assert recovered['old_data'] == raw
    assert recovered.binding_descriptors['old_data'].port_name == 'solver_outputs'
    proof['recovery']['bindings']['old_data']['artifact_ref']['artifact_id'] = 'artifact_other'
    forged = ValidationSources(sources, sources.binding_descriptors, tool_snapshot=canonical_json(proof))
    with pytest.raises(SemanticRuleViolation, match='not exactly bound'):
        calculation_sources(record, forged)


def test_checkpoint_callbacks_bind_exact_source_identity(tmp_path):
    raw, descriptor = _source()
    receipts, contents, descriptors = [], {}, {'data': descriptor}

    def accept_evidence(*, raw, media_type, derived_from, metadata):
        alias = 'evidence_checkpoint'
        ref = ArtifactRef(artifact_id='artifact_checkpoint', kind='evidence', schema_id='opaque', sha256=_digest(raw))
        receipts.append(dict(alias=alias, artifact_ref=ref.model_dump(mode='json'),
            metadata=metadata, tool_name='fixture'))
        contents[alias] = raw
        return {'alias': alias}

    context = OperationToolContext(services={}, state={}, workspace=tmp_path,
        output_directory=tmp_path / 'output', output_collections=(), remaining_seconds=10,
        _read_input=lambda name: raw, _input_path=lambda name: tmp_path / name,
        _input_media_type=lambda name: 'text/plain', _input_ref=lambda name: descriptors[name].artifact_ref,
        _validate_outputs=lambda: None, _record_activity=lambda name: None, _candidate_snapshot=lambda: (),
        _accept_evidence=accept_evidence, _source_descriptor=lambda name: descriptors[name],
        _list_evidence=lambda: receipts, _read_evidence=lambda alias: contents[alias])
    published = context.publish_calculation_checkpoint(b'{"iteration":1}',
        algorithm_version='fixture-v1', record_key='checkpoint', sources=('data',), numerical_identity=['one'])
    kwargs = dict(algorithm_version='fixture-v1', tool_names=('fixture',), sources=('data',), max_bytes=1024)
    assert context.read_calculation_checkpoint(published['evidence_alias'], **kwargs) == (b'{"iteration":1}', ['one'])
    _, other = _source(identity='artifact_other_same_digest')
    descriptors['data'] = other
    with pytest.raises(ValueError, match='scope_mismatch'):
        context.read_calculation_checkpoint(published['evidence_alias'], **kwargs)


def test_resource_relocation_preserves_schema_bytes_and_old_specs(monkeypatch):
    from scidiscovery.builtin_plugin import CORE_PLUGIN
    from scidiscovery.general_science_plugin import PLUGIN

    old_path = 'scidiscovery.artifact_agent.service.tool_evidence:TOOL_EVIDENCE_SCHEMA'
    new_path = 'scidiscovery.general_science_resources:TOOL_EVIDENCE_SCHEMA'
    # Resolve the pre-extraction declaration from its unchanged wire resource,
    # never importing or reinstating the retired service module in the test.
    original_loader = compiler._load_implementation
    monkeypatch.setattr(compiler, '_load_implementation', lambda spec, plugin:
        TOOL_EVIDENCE_SCHEMA if spec.implementation == old_path else original_loader(spec, plugin))
    old_plugin = PLUGIN.model_copy(update={'components': tuple(
        component.model_copy(update={'implementation': old_path})
        if component.implementation == new_path else component for component in PLUGIN.components)})
    before = compiler.compile_catalog((CORE_PLUGIN, old_plugin))
    after = compiler.compile_catalog((CORE_PLUGIN, PLUGIN))
    assert TOOL_EVIDENCE_SCHEMA == schema_resource(ToolEvidenceManifest, 'scidiscovery.tool-evidence-manifest.v1')
    key, changed = 'general_science:tool_evidence_schema', []
    for name in before.operation_ids():
        previous = before.operation(name)
        if key not in previous.component_specs:
            continue
        current = after.operation(name)
        assert current.digest != previous.digest
        assert current.component_specs[key].implementation == new_path
        assert current.implementations[key] == previous.implementations[key] == TOOL_EVIDENCE_SCHEMA
        originals = [previous, *(compiled for (operation, _), compiled in before._historical_operations.items()
                                 if operation == name)]
        for original in originals:
            restored = after.recorded_operation(name, version=original.spec.version, digest=original.digest)
            assert restored.component_specs == original.component_specs
            assert restored.component_specs[key].implementation == old_path
            assert restored.digest == original.digest
            assert after.recorded_contract_status(name, version=original.spec.version, digest=original.digest) == 'supported_historical'
        with pytest.raises(KeyError):
            after.recorded_operation(name, version=previous.spec.version, digest='f' * 64)
        assert not after.can_recover(name, current.digest, previous.digest)
        changed.append(name)
    assert changed
