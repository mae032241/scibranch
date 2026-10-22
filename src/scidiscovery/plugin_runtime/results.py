"""Materialize mechanical copies in new drafts; never rewrite bound Artifacts."""
from __future__ import annotations

import json
from typing import Callable
from copy import deepcopy

from ..operations.workspace import WorkspaceFinalizationRequest, WorkspaceProtocolError
from ..artifact_agent.schema.common import canonical_json


# Concrete projections for fields already owned by these finalizers. The same
# declarations remove fields from the authoring contract and copy them at seal.
RESULT_COPIES = {
    'scidiscovery.hypothesis-proposal.v2': {},
    'scidiscovery.scientific-intake.v1': {
        '/payload/scientific_foundation/objective_contract/statement': '/payload/scientific_foundation/objective',
        '/payload/problem_frame/objective': '/payload/scientific_foundation/objective',
        '/handoff/summary': '/payload/scientific_foundation/summary',
        '/handoff/missing_inputs': '/payload/scientific_foundation/missing_inputs',
        '/handoff/assumptions': '/payload/problem_frame/assumptions',
    },
    'scidiscovery.layered-diagnosis.v1': {'/handoff/summary': '/payload/summary'},
    'scidiscovery.scientific-review.v1': {'/handoff/summary': '/payload/summary',
        '/handoff/next_actions': '/payload/next_actions'},
    'scidiscovery.experiment-report.v1': {'/handoff/summary': '/payload/summary'},
    'scidiscovery.experiment-review.v1': {'/handoff/summary': '/payload/summary'},
}
RESULT_PROJECTION_VERSION = '5'

VERDICT_PROJECTIONS = {
    'scidiscovery.layered-diagnosis.v1', 'scidiscovery.scientific-review.v1',
    'scidiscovery.critic-review.v2', 'scidiscovery.evidence-audit.v1',
    'scidiscovery.experiment-report.v1', 'scidiscovery.experiment-review.v1',
}


def _projection_error(path: str, message: str) -> None:
    from ..operation_contract import contract_diagnostic
    raise WorkspaceProtocolError(message, details=(contract_diagnostic(
        'output_invalid', phase='output_payload', affected_action='submit',
        path='$' + path.replace('/', '.'), message=message, repairable=True),))


def project_result_copies(value: dict, schema_id: str, *, copies=None) -> None:
    value['schema_version'] = 1
    for destination, source in (RESULT_COPIES.get(schema_id, {}) if copies is None else copies).items():
        current = value
        for part in source.strip('/').split('/'):
            current = current.get(part) if isinstance(current, dict) else None
        if current is None:
            if destination in {'/handoff/assumptions', '/handoff/missing_inputs', '/handoff/next_actions'}:
                current = []
            elif destination.endswith(('/summary', '/objective')):
                _projection_error(source, 'Supply this scientific field before submission.')
            else:
                continue
        if destination.endswith(('/summary', '/objective')) and (not isinstance(current, str) or not 1 <= len(current) <= 8192):
            _projection_error(source, 'Supply this scientific field as a nonempty string within its declared length bound.')
        if destination in {'/handoff/assumptions', '/handoff/missing_inputs', '/handoff/next_actions'} and (not isinstance(current, list) or any(not isinstance(item, str) for item in current) or len(current) > (32 if destination.endswith('next_actions') else 128)):
            _projection_error(source, 'Supply a string array within the declared scientific field bound.')
        parent = value
        parts = destination.strip('/').split('/')
        for part in parts[:-1]:
            parent = parent.setdefault(part, {}) if part == 'handoff' else parent.get(part)
            if not isinstance(parent, dict):
                break
        if isinstance(parent, dict):
            # Handoff is bounded transport; complete scientific text stays in payload.
            parent[parts[-1]] = deepcopy(current)


def result_draft_schema(envelope: dict, schema_id: str, input_source_ports=None, *, copies=None) -> dict:
    """Project the existing sealed schema; never create a second payload model."""
    envelope = deepcopy(envelope)
    if copies is None and schema_id not in RESULT_COPIES and schema_id not in VERDICT_PROJECTIONS:
        return envelope
    # Each payload resource has its own local $defs and is embedded as a resource.
    def remove(path):
        node, root = envelope, envelope
        parts = path.strip('/').split('/')
        for index, part in enumerate(parts):
            if not isinstance(node, dict):
                return
            if 'anyOf' in node:
                node = next((branch for branch in node['anyOf'] if '$ref' in branch), node)
            if '$ref' in node:
                ref = node['$ref']
                node = root
                for token in ref.removeprefix('#/').split('/'):
                    node = node[token]
            properties = node.get('properties', {})
            if part not in properties:
                return
            if index == len(parts) - 1:
                properties.pop(part)
                node['required'] = [name for name in node.get('required', ()) if name != part]
                return
            node = properties[part]
            if '$id' in node:
                root = node
    for path in (RESULT_COPIES.get(schema_id, {}) if copies is None else copies):
        remove(path)
    if schema_id in VERDICT_PROJECTIONS:
        remove('/handoff/verdict')
    if schema_id == 'scidiscovery.layered-diagnosis.v1':
        remove('/handoff/next_actions')
    if schema_id == 'scidiscovery.hypothesis-proposal.v2':
        remove('/payload/research_objective_key')
        envelope['properties']['boundary_declarations']['maxItems'] = 32
        envelope['properties']['boundary_declarations']['description'] = 'At most 32 authored declarations. Control retains unassociated hypothesis scope/parameter basis as attributed unverified prose within the shared sealed limit.'
    if schema_id == 'scidiscovery.layered-diagnosis.v1' and input_source_ports is not None and 'experiment_plan' not in input_source_ports.values():
        remove('/payload/plan_key')
        remove('/payload/experiment_key')
        remove('/payload/objective_assessment/objective_key')
    def versions(node):
        if isinstance(node, dict):
            props = node.get('properties', {})
            if 'const' in props.get('schema_version', {}):
                props.pop('schema_version')
                node['required'] = [name for name in node.get('required', ()) if name != 'schema_version']
            for child in node.values():
                versions(child)
        elif isinstance(node, list):
            for child in node:
                versions(child)
    versions(envelope)
    handoff = envelope['$defs']['RoleHandoff']
    if not handoff.get('required'):
        envelope['required'] = [name for name in envelope.get('required', ()) if name != 'handoff']
    return envelope


def validate_finalizer_payload(model, payload):
    """Preserve scientific repair locations when a finalizer needs typed content."""
    from pydantic import ValidationError
    from ..operation_contract import DeclaredDiagnostic, validation_diagnostics
    try:
        return model.model_validate_json(canonical_json(payload), strict=True)
    except ValidationError as error:
        details = validation_diagnostics(error, schema=model.model_json_schema(),
            phase='output_payload', action='submit')
        raise WorkspaceProtocolError('Scientific fields require correction.', details=tuple(
            DeclaredDiagnostic({**detail, 'path': '$.payload' + detail['path'].removeprefix('$')})
            for detail in details)) from error


def finalize_result(request: WorkspaceFinalizationRequest, project: Callable[[dict], None]) -> bytes:
    path = request.workspace / 'output' / 'result.json'
    # This runs before the regular output seal. Keep the same bounded file boundary.
    if path.is_symlink() or not path.is_file():
        raise WorkspaceProtocolError('output/result.json must be a regular file')
    with path.open('rb') as stream:
        raw = stream.read(request.output_limit_bytes + 1)
    if len(raw) > request.output_limit_bytes:
        raise WorkspaceProtocolError('output/result.json exceeds its byte limit')
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        # The existing envelope checker owns malformed JSON and its diagnostics.
        return raw
    if isinstance(value, dict) and not isinstance(value.get('payload'), dict):
        # Diagnose the author's missing content before the sealed envelope parser
        # could request control-owned schema_version or generated handoff fields.
        raise WorkspaceProtocolError('Supply the scientific payload as a JSON object.', details=({
            'path': '$.payload', 'message': 'Supply the scientific payload as a JSON object.',
            'type': 'object_type', 'rule_id': 'runtime.envelope'},))
    if isinstance(value, dict) and isinstance(value.get('payload'), dict):
        original = json.loads(raw)
        project(value)
        project_result_copies(value, request.output_schema_id)
        if value != original:
            return canonical_json(value)
    return raw


def materialize_intake(value: dict) -> None:
    foundation = value.get('scientific_foundation')
    if not isinstance(foundation, dict):
        return
    contract = foundation.get('objective_contract')
    # Author the objective once; the optional formal contract shares that statement.
    if isinstance(contract, dict) and isinstance(foundation.get('objective'), str):
        contract['statement'] = foundation['objective']
    if isinstance(value.get('problem_frame'), dict) and isinstance(foundation.get('objective'), str):
        value['problem_frame']['objective'] = foundation['objective']


def materialize_analysis_handoff(value: dict) -> None:
    """Project only duplicate transport fields from the analyst's formal report."""
    payload = value['payload']
    verdict = {'pass': 'pass', 'fail': 'blocked', 'invalid_study': 'blocked',
               'inconclusive': 'inconclusive'}.get(str(payload.get('overall_verdict')))
    if verdict is None:
        _projection_error('/payload/overall_verdict', 'Use a verdict declared by the scientific output contract.')
    if not isinstance(payload.get('summary'), str):
        _projection_error('/payload/summary', 'Supply the scientific summary as a string.')
    handoff = value.setdefault('handoff', {})
    if isinstance(handoff, dict):
        handoff['verdict'] = verdict
        handoff['summary'] = payload['summary']
        action = payload.get('next_action')
        handoff['next_actions'] = [action] if isinstance(action, str) else []


def materialize_summary_handoff(value: dict, verdict: str | None) -> None:
    """Fill omitted transport fields from a formal summary; preserve explicit notes."""
    # A draft may omit handoff. Name the actual source of an impossible copy,
    # before envelope validation mistakes absent generated fields for authored omissions.
    if verdict is None:
        raise WorkspaceProtocolError('Cannot derive handoff verdict from the formal result', details=({
            'path': '$.payload.verdict', 'message': 'Formal verdict must use a value allowed by the output Schema to generate handoff.verdict.',
            'type': 'field_projection_unavailable'},))
    if not isinstance(value['payload'].get('summary'), str):
        raise WorkspaceProtocolError('Cannot reference the formal summary', details=({
            'path': '$.payload.summary', 'message': 'Formal summary must be a string as declared by the output Schema.',
            'type': 'field_projection_unavailable'},))
    handoff = value.setdefault('handoff', {})
    if isinstance(handoff, dict):
        handoff['verdict'] = verdict
        handoff['summary'] = value['payload']['summary']


def materialize_general_result(value: dict, schema_id: str) -> None:
    payload = value['payload']
    verdict = None
    if schema_id == 'scidiscovery.layered-diagnosis.v1':
        materialize_analysis_handoff(value)
    elif schema_id == 'scidiscovery.scientific-intake.v1':
        materialize_intake(payload)
    elif schema_id == 'scidiscovery.experiment-portfolio.v1':
        for proposal in (payload['proposals'] if isinstance(payload.get('proposals'), list) else ()):
            if not isinstance(proposal, dict):
                continue
            cases, estimate = proposal.get('cases'), proposal.get('resource_estimate')
            if isinstance(cases, list) and isinstance(estimate, dict):
                estimate['case_count'] = len(cases)
            objectives = proposal.get('objectives', [])
            if isinstance(objectives, list) and all(isinstance(item, str) for item in objectives) and isinstance(payload.get('objective'), str):
                proposal['objectives'] = list(dict.fromkeys([payload['objective'], *objectives]))
    elif schema_id == 'scidiscovery.scientific-review.v1':
        verdict = {'pass': 'pass', 'revise': 'revise', 'reject': 'blocked', 'blocked': 'blocked', 'inconclusive': 'inconclusive'}.get(str(payload.get('verdict')))
        materialize_summary_handoff(value, verdict)
    elif schema_id == 'scidiscovery.critic-review.v2':
        verdict = {'ready_for_experiment': 'pass', 'revise_hypothesis': 'revise',
                   'revise_evidence': 'inconclusive', 'design_model_counterfactual': 'pass',
                   'inconclusive': 'inconclusive', 'reject': 'blocked'}.get(str(payload.get('disposition')))
        if verdict is None:
            _projection_error('/payload/disposition', 'Use a disposition declared by the scientific output contract.')
    elif schema_id == 'scidiscovery.evidence-audit.v1':
        checks = payload.get('checks')
        if not isinstance(checks, list) or not checks or not all(isinstance(item, dict) for item in checks):
            _projection_error('/payload/checks', 'At least one evidence check is required.')
        statuses = {str(item.get('status')) for item in checks}
        if not statuses <= {'pass', 'fail', 'unknown', 'not_applicable'}:
            _projection_error('/payload/checks', 'Each check status must be pass, fail, unknown or not_applicable.')
        verdict = 'blocked' if 'fail' in statuses else 'inconclusive' if 'unknown' in statuses else 'pass'
    if verdict is not None and isinstance(value.setdefault('handoff', {}), dict):
        value['handoff']['verdict'] = verdict
    project_result_copies(value, schema_id)


def materialize_hypothesis_boundaries(value):
    """Associate existing scientific identities; untagged prose stays attributed."""
    hypotheses = value['payload'].get('hypotheses', ())
    declarations = value.setdefault('boundary_declarations', [])
    if not isinstance(declarations, list) or not all(isinstance(item, dict) for item in declarations):
        return  # The common envelope validator owns malformed declarations.
    subjects = {item.get('hypothesis_key'): item for item in hypotheses if isinstance(item, dict)}
    for index, declaration in enumerate(declarations):
        key, parameter = declaration.get('hypothesis_key'), declaration.get('parameter_name')
        if key is None:
            continue
        if key not in subjects or parameter is not None and parameter not in {
                item.get('name') for item in subjects[key].get('parameters', ()) if isinstance(item, dict)}:
            _projection_error('/boundary_declarations/' + str(index),
                'Associate a declared boundary with an existing hypothesis_key and, if supplied, its parameter.name.')
    if len(declarations) > 128:
        return  # The common envelope validator reports the existing shared bound.
    keys = {item.get('boundary_key') for item in declarations}
    for hypothesis in subjects.values():
        key = hypothesis['hypothesis_key']
        fields = [(None, hypothesis.get('scope'))] + [(item.get('name'), item.get('basis'))
            for item in hypothesis.get('parameters', ()) if isinstance(item, dict)]
        for parameter, text in fields:
            if not isinstance(text, str) or not text:
                continue
            if any(item.get('hypothesis_key') == key and item.get('parameter_name') == parameter
                    for item in declarations):
                continue
            counter = len(declarations)
            boundary_key = 'attributed.' + str(counter)
            while boundary_key in keys:
                counter += 1
                boundary_key = 'attributed.' + str(counter)
            keys.add(boundary_key)
            # Copy the author's exact scope/basis without interpreting it as a
            # user restriction or making source absence a permission to relax.
            if len(declarations) == 128:
                continue  # Remaining original prose retains producer attribution; source proof is absent.
            declarations.append(dict(boundary_key=boundary_key, statement=text,
                interpretation=text, hypothesis_key=key, parameter_name=parameter))


def finalize_general_result(request: WorkspaceFinalizationRequest) -> bytes:
    def project(value):
        if request.output_schema_id == 'scidiscovery.hypothesis-proposal.v2':
            materialize_hypothesis_boundaries(value)
            originals = [request.input_contents[alias] for alias, descriptor in request.binding_descriptors.items()
                if descriptor.port_name == 'scientific_foundation' and alias in request.input_contents]
            if len(originals) == 1:
                contract = json.loads(originals[0]).get('objective_contract')
                if not isinstance(contract, dict) or not contract.get('objective_key'):
                    raise WorkspaceProtocolError('The bound foundation has no objective contract; repair the task inputs.')
                value['payload']['research_objective_key'] = contract['objective_key']
            elif not originals:
                from ..artifact_agent.schema.research_objective import is_original_text_objective, validate_objective_source
                subjects = [(descriptor, request.input_contents[alias]) for alias, descriptor in request.binding_descriptors.items()
                    if descriptor.port_name == 'subject' and alias in request.input_contents]
                if len(subjects) != 1 or not is_original_text_objective(subjects[0][0].artifact_ref,
                        subjects[0][0].media_type, subjects[0][0].labels):
                    raise WorkspaceProtocolError('The original task or exact scientific foundation binding is unavailable; repair the task inputs.')
                descriptor, raw = subjects[0]
                validate_objective_source(raw, ref=descriptor.artifact_ref, media_type=descriptor.media_type, labels=descriptor.labels)
            else:
                raise WorkspaceProtocolError('The exact scientific foundation binding is ambiguous; repair the task inputs.')
        if request.output_schema_id == 'scidiscovery.layered-diagnosis.v1':
            ports = {descriptor.port_name for descriptor in request.binding_descriptors.values()}
            if 'experiment_plan' not in ports:
                value['payload'].pop('plan_key', None)
                value['payload'].pop('experiment_key', None)
                assessment = value['payload'].get('objective_assessment')
                if isinstance(assessment, dict):
                    from ..artifact_agent.schema.cognitive import HypothesisProposal
                    portfolios = [request.input_contents[alias] for alias, descriptor in request.binding_descriptors.items()
                        if descriptor.port_name == 'hypothesis_portfolio' and alias in request.input_contents]
                    if len(portfolios) != 1:
                        raise WorkspaceProtocolError('The exact hypothesis portfolio binding is unavailable or ambiguous; repair the task inputs.')
                    proposal = HypothesisProposal.model_validate_json(portfolios[0], strict=True)
                    assessment['objective_key'] = proposal.research_objective_key
        materialize_general_result(value, request.output_schema_id)
    return finalize_result(request, project)
