"""Saved form settings reach real local consumers while old originals stay frozen.

No provider, solver or external submission runs here. Native argv and protocol
requests are constructed by the production adapters; observer fixtures only emit
deterministic JSON. Creation-only research/UI/version controls are tested in
test_configuration_form and rejected for operational edits below.
"""
import copy
import json
from pathlib import Path
import sys

import pytest

from scidiscovery.research_loop import (
    accepted_budget, backends, budget, candidate_audit, checkpoint, configuration_form as form,
    configuration_updates as updates, continuation, contracts as c, controller,
    delivery, execution_authority as authority, experiments,
    formal_review as review, profiles, shortlist,
)
# Registers source-lane execution interfaces; codex instances default to local execution.
from execution_fixtures import execution_registry  # noqa: F401
from test_configuration_form import Controls
from test_delivery import delivery_policy
from test_history_handoff import finish


def instance(tmp_path, **options):
    public = tmp_path / 'public'; public.mkdir()
    objective = public / 'objective.md'; objective.write_text('Retain exact settings originals.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, options.pop('launcher', ['never-launched']),
        model='original-model', reasoning_effort='high', service_tier='original-tier',
        max_tokens=20000, max_seconds=300, max_steps=24, search_policy=None, **options)
    return root


def save_form(root, changes=None, *, document=None):
    state = updates.status(root)
    declaration = copy.deepcopy(state['configuration'] if document is None else document)
    draft = form.imported(declaration, base=root)
    fields = Controls(form.render(draft, edit=True)).fields
    advanced = form.tomllib.loads(fields['advanced_toml'])
    for path, value in (changes or {}).items():
        if 'cfg.' + path in fields:
            fields['present.' + path] = 'yes'
            fields['cfg.' + path] = str(value).lower() if type(value) is bool else str(value)
        else:
            # Settings outside the everyday form are edited as TOML.
            target = advanced
            *parents, leaf = path.split('.')
            for key in parents:
                target = target.setdefault(key, {})
            target[leaf] = value
    fields['advanced_toml'] = form.advanced_toml(advanced)[0]
    parsed = form.parse_edit(fields, base=root, configuration=state['configuration'])
    result = updates.submit(root, parsed['document'], expected_revision=state['revision'],
        authorization_note='Bounded settings regression adjustment')
    assert result['status'] == 'configuration_saved' and result['pending']
    return result


def launch(root, job):
    settings = c.settings(root, job)
    adapter = backends.selected(settings)
    argv = adapter.launch(profiles.for_job(settings, job), c.workspace(root, job['step']),
        root / 'pending.json', root / 'schema.json', 'Frozen role instruction')
    return adapter, argv


@pytest.mark.parametrize('role', ['global', *'HWAE'])
@pytest.mark.parametrize('key', ['model', 'reasoning_effort', 'service_tier'])
def test_profile_controls_change_native_argv_only_for_new_nodes(tmp_path, role, key):
    root = instance(tmp_path)
    chosen = 'H' if role == 'global' else role
    old, _, _ = finish(root, 1, chosen, [])
    old_bytes = (root / 'jobs/001.json').read_bytes()
    _, original = launch(root, old)
    path = f'agent.{key}' if role == 'global' else f'agent.roles.{role}.{key}'
    save_form(root, {path: 'revised-value'})
    new = c.build_job(root, 2, chosen, [])
    _, revised = launch(root, new)
    if key == 'model':
        assert revised[revised.index('--model') + 1] == 'revised-value'
    else:
        native_key = 'model_reasoning_effort' if key == 'reasoning_effort' else key
        assert native_key + '="revised-value"' in revised
    assert revised != original
    assert launch(root, old)[1] == original
    assert c.build_job(root, 1, chosen, []) == old
    assert (root / 'jobs/001.json').read_bytes() == old_bytes
    if role != 'global':
        other = next(item for item in 'HWAE' if item != role)
        assert profiles.selected(updates.current(root), other)[key] == profiles.selected(c.settings(root), other)[key]


@pytest.mark.parametrize('path,value,expected', [
    ('permissions.workspace', 'read-only', '--sandbox'),
    ('permissions.network', True, 'sandbox_workspace_write='),
    ('permissions.web', 'live', 'web_search="live"'),
    ('permissions.subagents', True, 'features.multi_agent=true'),
    ('agent.native.features.fast_mode', True, 'features.fast_mode=true'),
    ('agent.native.sandbox_workspace_write.exclude_tmpdir_env_var', True, 'exclude_tmpdir_env_var'),
    ('agent.native.sandbox_workspace_write.exclude_slash_tmp', True, 'exclude_slash_tmp'),
])
def test_native_permissions_and_features_reach_codex_argv(tmp_path, path, value, expected):
    root = instance(tmp_path, launcher=['codex'], native_options={})
    old, _, _ = finish(root, 1, 'H', [])
    original = launch(root, old)[1]
    save_form(root, {path: value})
    new = c.build_job(root, 2, 'H', [])
    adapter, args = launch(root, new)
    assert args != original and launch(root, old)[1] == original
    if path == 'permissions.workspace':
        assert args[args.index(expected) + 1] == value
    elif path.startswith('agent.native.sandbox'):
        sandbox = next(item for item in args if item.startswith('sandbox_workspace_write='))
        assert '"' + expected + '"=true' in sandbox
    else:
        assert any(item.startswith(expected) for item in args)
    summary = c.settings(root, new)['native_permissions']
    if path.startswith('permissions.'):
        assert summary['requested'][path.split('.')[-1]] == value
        assert summary['effective'][path.split('.')[-1]] == value
    if path == 'permissions.subagents':
        assert adapter.complete_authorship is False


def test_native_writable_roots_and_network_option_bind_exact_new_argv(tmp_path):
    root = instance(tmp_path, launcher=['codex'], native_options={})
    old, _, _ = finish(root, 1, 'H', [])
    document = updates.status(root)['configuration']
    document['agent']['native'] = dict(sandbox_workspace_write=dict(
        writable_roots=[str(tmp_path / 'extra')], network_access=True))
    document['permissions']['network'] = True
    save_form(root, document=document)
    new = c.build_job(root, 2, 'H', [])
    sandbox = next(item for item in launch(root, new)[1] if item.startswith('sandbox_workspace_write='))
    assert str(tmp_path / 'extra') in sandbox and '"network_access"=true' in sandbox
    assert str(tmp_path / 'extra') not in ' '.join(launch(root, old)[1])


def test_backend_and_exact_launcher_change_protocol_request_and_capabilities(tmp_path):
    root = instance(tmp_path)
    old, _, _ = finish(root, 1, 'H', [])
    document = updates.status(root)['configuration']
    document['agent'].update(backend='protocol', launcher=[sys.executable, 'literal-argument'])
    save_form(root, document=document)
    new = c.build_job(root, 2, 'H', [])
    adapter, args = launch(root, new)
    schema = root / 'request.schema.json'; c.write(schema, dict(type='object'))
    request = json.loads(adapter.request('Exact prompt', profile=profiles.selected(c.settings(root, new), 'H'),
        work=c.workspace(root, 2), schema=schema, response_schema=dict(type='object')))
    assert args == [sys.executable, 'literal-argument']
    assert request['protocol'] == 'scidiscovery-agent-v1'
    assert request['response_schema'] == dict(type='object')
    assert request['permissions']['enforcement'] == 'hint_only'
    assert c.settings(root, new)['native_capabilities']['session_resume'] is False
    assert launch(root, old)[0].name == 'codex'


@pytest.mark.parametrize('source', ['configuration', 'config_file'])
def test_executor_form_source_reaches_registered_adapter_bound_to_each_job(tmp_path, source):
    original = dict(executor='local_execution:command',
        configuration=dict(run_argv=[sys.executable], max_seconds=10))
    root = instance(tmp_path, experiment_mode='local', execution_config=original)
    old, _, _ = finish(root, 1, 'W', [])
    document = updates.status(root)['configuration']
    revised = dict(run_argv=[sys.executable, '-I'], max_seconds=17)
    if source == 'config_file':
        config_file = tmp_path / 'execution.toml'
        config_file.write_text('run_argv = ' + json.dumps(revised['run_argv']) + '\nmax_seconds = 17\n')
        document['executor']['config'] = dict(executor='local_execution:command', config_file=str(config_file))
    else:
        document['executor']['config'] = dict(executor='local_execution:command', configuration=revised)
    save_form(root, document=document)
    new = c.build_job(root, 2, 'W', [])
    for job, expected in [(old, original['configuration']), (new, revised)]:
        work = c.workspace(root, job['step'])
        record = c.host_record(work)
        if job is new:
            intent = dict(task_ref=c.record_ref(root / 'jobs/002.json'))
            c.write(record / 'native-intent.json', intent)
            accepted_budget.accept_native(root, 2, record, intent)
        config = experiments.configure(root, job['step'], 'local_execution:command', expected, work / 'work/execution.json')
        adapter = experiments.load(config)
        assert adapter.config['run_argv'] == expected['run_argv']
        assert adapter.config['max_seconds'] == expected['max_seconds']
        assert config['scope']['job_ref'] == c.record_ref(root / f'jobs/{job["step"]:03d}.json')
    with pytest.raises(ValueError, match='frozen operator selection'):
        experiments.configure(root, 1, 'local_execution:command', revised,
            c.workspace(root, 1) / 'work/wrong.json')


def test_executor_mode_control_changes_worker_execution_contract(tmp_path):
    root = instance(tmp_path)
    old, _, _ = finish(root, 1, 'W', [])
    save_form(root, {'executor.mode': 'local'})
    new = c.build_job(root, 2, 'W', [])
    assert old['experiment_mode'] == 'external' and new['experiment_mode'] == 'local'
    assert 'execution_config_record' not in old and 'execution_config_record' in new
    task = c.read(c.workspace(root, 2) / 'science-task.json')
    assert task['experiment_execution'].startswith('Host-configured registered W execution')
    assert c.settings(root, old).get('execution_config') is None


def test_execution_resource_form_adjustment_retains_cumulative_consumption(tmp_path):
    from scidiscovery.research_loop import qualification as q
    original = dict(version=1, allowance=dict(wall_time_seconds=10, max_storage_bytes=100))
    root = instance(tmp_path, experiment_mode='local', formal_review_policy=dict(version=1),
        execution_authority_policy=dict(version=1), execution_resource_policy=original)
    old, _, _ = finish(root, 1, 'H', [])
    grant = budget.execution_grant(root)
    request = q._save(root, root / 'host-runtime/settings-fixture/request.json', dict(scope='ledger only'))
    decision_ref = q._save(root, root / 'host-runtime/settings-fixture/decision.json', dict(scope='ledger only'))
    binding = dict(call_id='0001-' + '1' * 32, scope=dict(fixture='same cumulative owner'),
        native_attempt=dict(number=0), interface=dict(executor='local_execution:command'), authority=dict(fixture=1))
    budget.reserve_execution(root, binding=binding, request_ref=request, decision_ref=decision_ref,
        budget=dict(wall_time_seconds=6, max_storage_bytes=60), grant_revision=grant['ref'],
        effective_allowance=original['allowance'])
    document = updates.status(root)['configuration']
    document['executor']['resource_policy']['allowance'] = dict(wall_time_seconds=15, max_storage_bytes=150)
    save_form(root, document=document)
    assert budget.execution_grant(root) == grant
    new = c.build_job(root, 2, 'H', [])
    accounting = budget.execution_accounting(root)
    assert accounting['remaining'] == dict(wall_time_seconds=9, max_storage_bytes=90)
    assert c.settings(root, old)['execution_resource_policy'] == original
    assert c.settings(root, new)['execution_resource_policy']['allowance'] == dict(wall_time_seconds=15, max_storage_bytes=150)
    assert accounting['owner'] == grant['owner']


@pytest.mark.parametrize('key,value', [('max_tokens', 24000), ('max_seconds', 360.5), ('max_steps', 26)])
def test_total_ceiling_form_applies_once_at_next_node_without_refunding(tmp_path, key, value):
    root = instance(tmp_path)
    old, _, _ = finish(root, 1, 'H', [])
    before = budget.snapshot(root)
    save_form(root, {'budget.' + key: value})
    assert budget.snapshot(root)[key] == before[key]
    new = c.build_job(root, 2, 'H', [])
    after = budget.snapshot(root)
    assert after[key] == value and c.settings(root, old)[key] == before[key]
    from scidiscovery.research_loop import native
    assert native.usage(root)['observed_tokens'] == 10
    assert after['consumed_seconds'] == before['consumed_seconds']
    assert c.settings(root, new)[key] == value
    assert budget.step_limit(root) == (value if key == 'max_steps' else before['max_steps'])
    c.build_job(root, 2, 'H', [])
    events = budget._events(root)[0]
    assert len([row for row in events if row.get('operation_id') == 'configuration-1']) == 1


@pytest.mark.parametrize('role', list('HWAE'))
def test_each_role_node_target_reaches_admission_and_keeps_old_target(tmp_path, role):
    targets = {item: dict(max_observed_tokens=1000, max_seconds=30) for item in 'HWAE'}
    root = instance(tmp_path, node_budget_policy=targets)
    old, _, _ = finish(root, 1, role, [])
    save_form(root, {f'budget.node_targets.{role}.max_observed_tokens': 1700,
                     f'budget.node_targets.{role}.max_seconds': 40.5})
    new = c.build_job(root, 2, role, [])
    anchor = budget.accept_node(root, new)
    assert anchor['limits'] == dict(max_observed_tokens=1700, max_seconds=40.5)
    assert budget.node_limits(root, old) == targets[role]


def test_formal_review_and_authority_form_controls_freeze_contracts_for_new_jobs(tmp_path):
    root = instance(tmp_path)
    old, _, _ = finish(root, 1, 'H', [])
    save_form(root, {'policies.formal_review': True, 'policies.execution_authority': True})
    new, _, _ = finish(root, 2, 'H', [])
    review_contract, execution_contract = review.contract(root, new), authority.contract(root, new)
    assert review_contract and execution_contract
    assert review.contract(root, old) is None
    with pytest.raises(ValueError, match='Execution authority is unavailable'):
        authority.contract(root, old)
    save_form(root, {'policies.formal_review': False, 'policies.execution_authority': False})
    latest = c.build_job(root, 3, 'H', [])
    assert review.contract(root, latest) is None
    with pytest.raises(ValueError, match='Execution authority is unavailable'):
        authority.contract(root, latest)
    assert review.contract(root, new) == review_contract
    assert authority.contract(root, new) == execution_contract


def test_resume_and_worker_context_controls_use_exact_old_and_new_policies(tmp_path):
    targets = {role: dict(max_observed_tokens=1000, max_seconds=30) for role in 'HWAE'}
    root = instance(tmp_path, launcher=[sys.executable], node_budget_policy=targets)
    old, _, _ = finish(root, 1, 'W', [])
    document = updates.status(root)['configuration']
    document['policies'].update(worker_context='same_session', resume=dict(max_attempts=2,
        protocol='codex_exec_resume', usage_scope='turn', output_repair=True,
        output_only=True, native_home=str(tmp_path / 'native-home')))
    save_form(root, document=document)
    first, _, _ = finish(root, 2, 'W', [])
    assert checkpoint.enabled(root, first) and not checkpoint.enabled(root, old)
    limits = continuation.validate_launcher(root, 2)
    assert limits['max_attempts'] == 2 and limits['native_home'] == str(tmp_path / 'native-home')
    assert limits['launcher_files'][0]['invoked_path'] == str(Path(sys.executable).absolute())
    assert limits['launcher_files'][0]['path'] == str(Path(sys.executable).resolve())
    document = updates.status(root)['configuration']
    document['policies']['resume'].pop('native_home')
    save_form(root, {'policies.worker_context': 'new_context', 'policies.resume.max_attempts': 1,
        'policies.resume.output_only': False}, document=document)
    latest = c.build_job(root, 3, 'W', [])
    assert continuation.validate_launcher(root, 3)['max_attempts'] == 1
    assert continuation.validate_launcher(root, 2) == limits
    with pytest.raises(continuation.ResumeUnavailable, match='unsupported'):
        continuation.validate_launcher(root, 1)
    assert 'Continuation:new_context;' in c.read(c.workspace(root, 3) / 'science-task.json')['stage_response']
    assert 'Continuation:same_session;' in c.read(c.workspace(root, 2) / 'science-task.json')['stage_response']
    assert checkpoint.enabled(root, latest)


@pytest.mark.process_e2e
def test_history_observer_form_config_reaches_real_capture_for_bound_node(tmp_path):
    adapter = tmp_path / 'observer.py'
    adapter.write_text('import json,sys\nr=json.load(sys.stdin)\nprint(json.dumps(dict(candidates=[], '
        'current_candidate=None, actual_submission=None, coverage=dict(status="partial", note=r["config"]["note"]))))\n')
    root = instance(tmp_path, history_observer=dict(adapter=str(adapter), config=dict(note='original observer')))
    old, _, old_record = finish(root, 1, 'H', [])
    original = c.read(old_record / 'result-identity.json')['observations']
    original_bytes = (root / original['path']).read_bytes()
    document = updates.status(root)['configuration']
    document['policies']['history_observer']['config']['note'] = 'revised observer'
    save_form(root, document=document)
    new, _, record = finish(root, 2, 'H', [])
    observation = c.read(record / 'result-identity.json')['observations']
    assert c.read(root / observation['path'])['coverage']['note'] == 'revised observer'
    assert c.read(root / original['path'])['coverage']['note'] == 'original observer'
    assert (root / original['path']).read_bytes() == original_bytes
    assert c.settings(root, old)['history_observer'] != c.settings(root, new)['history_observer']


def delivery_instance(tmp_path):
    adapter = tmp_path / 'delivery.py'; adapter.write_text('# Frozen adapter; never invoked.\n')
    spec = dict(adapter=str(adapter), launcher_argv=[sys.executable, str(adapter)], config={},
        target=dict(directory=str(tmp_path / 'target')), inputs=[], source_roots=[str(tmp_path)],readonly_inputs_version=1)
    return instance(tmp_path, launcher=['codex'], node_budget_policy={role: dict(max_observed_tokens=1000,
        max_seconds=30) for role in 'HWAE'}, delivery_policy=delivery_policy(spec))


def test_delivery_audit_selection_forms_reach_new_contracts_and_runtime_enablement(tmp_path):
    root = delivery_instance(tmp_path)
    old, _, _ = finish(root, 1, 'H', [])
    document = updates.status(root)['configuration']
    document['delivery']['adapter']['target']['directory'] = str(tmp_path / 'new-target')
    document['policies'].update(candidate_audit=True, selection=dict(version=2))
    save_form(root, document=document)
    new, _, _ = finish(root, 2, 'A', [])
    assert delivery._adapter(root, new)['target'] == dict(directory=str(tmp_path / 'new-target'))
    assert delivery._adapter(root, old)['target'] == dict(directory=str(tmp_path / 'target'))
    assert candidate_audit.enabled(c.settings(root, new))
    assert not candidate_audit.enabled(c.settings(root, old))
    assert shortlist.configured(root) == dict(version=2)
    task = c.read(c.workspace(root, 2) / 'science-task.json')
    assert task['audit_capability']['available'] is True
    document = updates.status(root)['configuration']
    document['policies'].pop('selection'); document['policies']['candidate_audit'] = False
    save_form(root, document=document)
    latest, _, latest_record = finish(root, 3, 'A', [])
    assert shortlist.configured(root) is None
    assert not candidate_audit.enabled(c.settings(root, latest))
    assert c.settings(root, new)['selection_policy'] == dict(version=2)
    task = c.read(c.workspace(root, 3) / 'science-task.json')
    assert task['audit_capability']['available'] is False
    # A retained enabled task still reaches candidate eligibility after disable;
    # the new disabled task stops before any adapter or external action.
    assert candidate_audit.execute(root, new, dict(candidate_recommendation=None)) is None
    enabled_outcome = c.read(c.host_record(c.workspace(root, 2)) / 'candidate-audit/outcome.json')
    assert enabled_outcome['result']['note'] == 'candidate_unavailable'
    assert candidate_audit.execute(root, latest, dict(candidate_recommendation=None)) is None
    disabled_outcome = c.read(latest_record / 'candidate-audit/outcome.json')
    assert disabled_outcome['result']['note'] == 'candidate_audit_disabled'


@pytest.mark.parametrize('path,value,error', [
    ('budget.max_tokens', 0, 'positive integer'),
    ('budget.max_seconds', 0, 'positive and finite'),
    ('budget.max_steps', 0, 'positive integer'),
    ('budget.max_output_item_bytes', 0, 'positive integer'),
    ('budget.max_output_total_bytes', 0, 'positive integer'),
    ('agent.model', '', 'explicit model'),
    ('agent.roles.W.model', '', 'nonempty'),
    ('policies.execution_authority', True, 'formal review'),
    ('policies.candidate_audit', True, 'isolated delivery adapter'),
    ('policies.worker_context', 'new_context', 'frozen turn-scoped'),
])
def test_invalid_form_settings_do_not_publish_or_replace_old_job(tmp_path, path, value, error):
    root = instance(tmp_path)
    finish(root, 1, 'H', [])
    original = (root / 'jobs/001.json').read_bytes()
    with pytest.raises(ValueError, match=error):
        save_form(root, {path: value})
    assert updates.revisions(root) == []
    assert (root / 'jobs/001.json').read_bytes() == original


@pytest.mark.parametrize('key,value', [('version', 2), ('research', {}), ('ui', dict(port=9))])
def test_creation_only_declarations_are_immutable_during_operational_edit(tmp_path, key, value):
    root = instance(tmp_path)
    document = updates.status(root)['configuration']
    document[key] = value
    with pytest.raises(ValueError, match='immutable'):
        updates.submit(root, document)
    assert updates.revisions(root) == []
