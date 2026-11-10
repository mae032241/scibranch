"""One preparation/defaulting entry for TOML, CLI and host interfaces.

The TOML is an input declaration for a new instance. Existing instances always
retain their identity-bound settings; operational edits append exact node revisions.
"""
from __future__ import annotations

import copy
from pathlib import Path
import re

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from . import contracts, controller, search, project_layout, backends

_PROFILE_DEFAULTS = backends.provider().profile_defaults()
DEFAULT_MODEL = _PROFILE_DEFAULTS['model']
DEFAULT_REASONING_EFFORT = _PROFILE_DEFAULTS['reasoning_effort']
DEFAULT_SERVICE_TIER = _PROFILE_DEFAULTS['service_tier']


def read_declaration(path):
    """Read a bounded TOML draft, including one whose required fields are pending."""
    path = contracts.regular(Path(path).expanduser().resolve(), limit=256_000)
    try:
        return tomllib.loads(path.read_text(encoding='utf-8'))
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f'Invalid research TOML: {error}') from error


def with_token_budget(text, max_tokens):
    """Edit only a plain budget field; verify every other parsed value is retained.

    Unusual TOML layouts remain editable through the full declaration editor.
    No policy or authorization default is supplied by this helper.
    """
    if type(max_tokens) is not int or max_tokens <= 0:
        raise ValueError('Token budget must be a positive integer')
    before = tomllib.loads(text)
    expected = copy.deepcopy(before)
    table = expected.setdefault('budget', {})
    if not isinstance(table, dict):
        raise ValueError('budget must be a table')
    table['max_tokens'] = max_tokens
    headers = list(re.finditer(r'(?m)^[ \t]*\[([^\n]+)\][ \t]*(?:#[^\n]*)?$', text))
    matches = [item for item in headers if item[1].strip() == 'budget']
    if len(matches) == 1:
        start = matches[0].end()
        end = next((item.start() for item in headers if item.start() > start), len(text))
        section = text[start:end]
        fields = list(re.finditer(r'(?m)^([ \t]*max_tokens[ \t]*=[ \t]*)[^\n]*$', section))
        if len(fields) > 1:
            raise ValueError('Use the advanced editor for this budget declaration')
        if fields:
            field = fields[0]
            section = section[:field.start()] + field[1] + str(max_tokens) + section[field.end():]
        else:
            section = '\nmax_tokens = ' + str(max_tokens) + section
        updated = text[:start] + section + text[end:]
    elif not matches and 'budget' not in before:
        updated = text + '\n[budget]\nmax_tokens = ' + str(max_tokens) + '\n'
    else:
        raise ValueError('Use the advanced editor for this budget declaration')
    if tomllib.loads(updated) != expected:
        raise ValueError('Use the advanced editor to preserve this TOML declaration')
    return updated


def _table(value, allowed, name):
    if not isinstance(value, dict) or set(value) - set(allowed):
        unknown = sorted(set(value) - set(allowed)) if isinstance(value, dict) else []
        raise ValueError(f'{name} must be a table with supported fields' +
                         (f': unknown {", ".join(unknown)}' if unknown else ''))
    return value


def _path(value, base, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be a nonempty path string')
    path = Path(value).expanduser()
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def _paths(value, base, name):
    if not isinstance(value, list):
        raise ValueError(f'{name} must be an array of paths')
    return [_path(item, base, name) for item in value]


def load(path):
    """Read a strict declaration, resolving explicit paths relative to its file."""
    path = contracts.regular(Path(path).expanduser().resolve(), limit=256_000)
    return from_document(read_declaration(path), base=path.parent)


def from_document(document, *, base):
    """Decode an in-memory declaration using the same semantics as a TOML file.

    This only resolves declarations; ``normalize`` remains the sole defaulting
    engine for CLI, file and structured host interfaces. Input is never mutated.
    """
    document = copy.deepcopy(document)
    base = Path(base).expanduser().resolve()
    _table(document, {'version', 'research', 'agent', 'permissions', 'budget',
                      'executor', 'delivery', 'ui', 'policies', 'skills', 'project'}, 'research configuration')
    if type(document.get('version', 1)) is not int or document.get('version', 1) != 1:
        raise ValueError('Unsupported research configuration version')
    research = _table(document.get('research', {}),
        {'root', 'objective', 'objective_text', 'public_root', 'user_updates', 'source_restrictions'}, 'research')
    missing = {'root', 'public_root'} - set(research)
    if missing:
        raise ValueError('research requires ' + ', '.join(sorted(missing)))
    if ('objective' in research) == ('objective_text' in research):
        raise ValueError('research requires exactly one objective or objective_text')
    result = {key: _path(research[key], base, f'research.{key}')
              for key in ('root', 'public_root')}
    if 'project' in document:
        result['project'] = project_layout.configuration(document['project'])
    if 'objective_text' in research:
        if not isinstance(research['objective_text'], str) or not research['objective_text'].strip():
            raise ValueError('research.objective_text must contain original user text')
        result['objective_text'] = research['objective_text']
        result['objective'] = result['root'] / 'project/global/objective.md'
    else:
        result['objective'] = _path(research['objective'], base, 'research.objective')
    result['user_updates'] = _paths(research.get('user_updates', []), base, 'research.user_updates')
    if 'source_restrictions' in research:
        rows = research['source_restrictions']
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('research.source_restrictions must be an array of tables')
        result['source_restrictions'] = [
            {**row, 'source': str(_path(row['source'], base, 'source_restrictions.source'))}
            if 'source' in row else row for row in rows]
    agent = _table(document.get('agent', {}),
        {'backend', 'launcher', 'model', 'reasoning_effort', 'service_tier', 'roles', 'workers', 'native'}, 'agent')
    for key in ('model', 'reasoning_effort', 'service_tier'):
        if key in agent:
            result[key] = agent[key]
    result['native_backend'] = agent.get('backend', backends.DEFAULT_BACKEND)
    launcher = agent.get('launcher')
    if launcher is not None:
        if not isinstance(launcher, list) or not launcher or any(not isinstance(arg, str) or not arg.strip() for arg in launcher):
            raise ValueError('agent.launcher must be a nonempty argv array')
        launcher = list(launcher)
        # Only a path-like executable is relative to the declaration. Other
        # argv elements are native arguments, not guessed filesystem paths.
        if '/' in launcher[0] or launcher[0].startswith('~'):
            # Preserve the invoked entry point; its resolved target is frozen
            # separately by the provider's launcher contract.
            launcher[0] = str((Path(base) / Path(launcher[0]).expanduser()).absolute())
    result['launcher'] = launcher
    if 'roles' in agent:
        result['role_profiles'] = agent['roles']
    if 'workers' in agent:
        result['worker_profiles'] = agent['workers']
    if 'skills' in document:
        result['skills'] = copy.deepcopy(_table(document['skills'], {'sets'}, 'skills'))
    options = backends.provider(result['native_backend']).declaration_options(
        agent.get('native', {}), base=base)
    permissions = _table(document.get('permissions', {}),
        {'workspace', 'network', 'web', 'subagents'}, 'permissions')
    result['native_options'] = {**options, **permissions}
    budgets = _table(document.get('budget', {}),
        {'max_tokens', 'max_seconds', 'max_steps', 'max_output_item_bytes',
         'max_output_total_bytes', 'node_targets', 'role_shares', 'breadth', 'correction',
         'candidate_extra_slots', 'portfolio_review_after_cycles', 'storage'}, 'budget')
    for key in ('max_tokens', 'max_seconds', 'max_steps'):
        if key not in budgets:
            raise ValueError(f'budget.{key} is required')
        result[key] = (None if key in {'max_tokens', 'max_seconds'}
                       and budgets[key] == 'unlimited' else budgets[key])
    for key in ('max_output_item_bytes', 'max_output_total_bytes', 'candidate_extra_slots'):
        # Preserve legacy byte fields for exact declaration roundtrips. They no
        # longer impose file-size quotas on scientific originals or outputs.
        if key in budgets:
            result[key] = budgets[key]
    if 'storage' in budgets:
        result['storage_policy'] = budgets['storage']
    for source, target in (('node_targets', 'node_budget_policy'), ('role_shares', 'role_budget_policy'),
                           ('breadth', 'search_policy'), ('correction', 'correction_policy')):
        if source in budgets:
            result[target] = budgets[source]
    if 'portfolio_review_after_cycles' in budgets:
        result['portfolio_policy'] = dict(review_after_cycles=budgets['portfolio_review_after_cycles'])
    executor = _table(document.get('executor', {}), {'mode', 'config', 'resource_policy'}, 'executor')
    for source, target in (('mode', 'experiment_mode'), ('config', 'execution_config'),
                           ('resource_policy', 'execution_resource_policy')):
        if source in executor:
            result[target] = executor[source]
    if isinstance(result.get('execution_config'), dict) and 'config_file' in result['execution_config']:
        from scidiscovery.plugin_configuration import configuration_path
        result['execution_config']['config_file'] = str(configuration_path(
            result['execution_config']['config_file'], base=base))
    if 'delivery' in document:
        delivery = copy.deepcopy(document['delivery'])
        if isinstance(delivery, dict) and isinstance(delivery.get('adapter'), dict):
            adapter = delivery['adapter']
            for name in ('adapter',):
                if name in adapter:
                    adapter[name] = str(_path(adapter[name], base, f'delivery.adapter.{name}'))
            if 'source_roots' in adapter:
                adapter['source_roots'] = [str(item) for item in _paths(adapter['source_roots'], base, 'delivery.adapter.source_roots')]
            if isinstance(adapter.get('launcher_argv'), list) and adapter['launcher_argv']:
                if '/' in adapter['launcher_argv'][0]:
                    adapter['launcher_argv'][0] = str(_path(adapter['launcher_argv'][0], base, 'delivery.adapter.launcher_argv[0]'))
        result['delivery_policy'] = delivery
    policies = _table(document.get('policies', {}),
        {'formal_review', 'execution_authority', 'candidate_audit', 'decision_contract',
         'selection', 'worker_context', 'worker_context_windows', 'worker_context_handoff_version', 'resume', 'history_observer', 'supervision'}, 'policies')
    for source, target in (('formal_review', 'formal_review_policy'),
                           ('execution_authority', 'execution_authority_policy'),
                           ('candidate_audit', 'candidate_audit_policy')):
        if source in policies:
            if type(policies[source]) is not bool:
                raise ValueError(f'policies.{source} must be boolean')
            result[target] = dict(version=1) if policies[source] else None
    if 'decision_contract' in policies:
        if type(policies['decision_contract']) is not bool:
            raise ValueError('policies.decision_contract must be boolean')
        result['decision_contract_version'] = 1 if policies['decision_contract'] else None
    for source, target in (('selection', 'selection_policy'), ('resume', 'resume_policy'),
                           ('history_observer', 'history_observer'),('supervision','supervision_policy')):
        if source in policies:
            result[target] = policies[source]
    if 'worker_context' in policies:
        result['worker_fragment_policy'] = dict(context=policies['worker_context'])
    if 'worker_context_windows' in policies:
        result.setdefault('worker_fragment_policy', dict(context='same_session'))['max_context_windows'] = policies['worker_context_windows']
    if 'worker_context_handoff_version' in policies:
        result.setdefault('worker_fragment_policy',dict(context='same_session'))['context_handoff_version']=policies['worker_context_handoff_version']
    result['ui'] = copy.deepcopy(_table(document.get('ui', {}),
        {'port', 'open_browser', 'approval_secret_file'}, 'ui'))
    if 'approval_secret_file' in result['ui']:
        result['ui']['approval_secret_file'] = str(_path(result['ui']['approval_secret_file'], base, 'ui.approval_secret_file'))
    return result


def normalize(options, *, frozen_skill_selection=None):
    """Apply defaults; an exact fork retains its already expanded Skill names."""
    result = copy.deepcopy(options)
    from .protocol import require_preparation_behavior
    require_preparation_behavior(result.get('research_strategy_version',3),
        supervision_policy=result.get('supervision_policy'),
        worker_fragment_policy=result.get('worker_fragment_policy'))
    if result.get('native_options') is None:
        result['native_options'] = {}
    backend_name=result.get('native_backend', backends.DEFAULT_BACKEND)
    provider=backends.provider(backend_name)
    for name,default in provider.profile_defaults().items():
        if result.get(name) is None:result[name]=copy.deepcopy(default)
    if result.get('model') is None:
        raise ValueError('The selected backend requires an explicit model')
    launcher = result.get('launcher')
    if launcher is None:
        launcher = provider.default_launcher()
        result['launcher'] = launcher
    # Provider admission sees every explicit policy/profile before plugin or
    # filesystem preparation. Never discard a policy to fit a narrower backend.
    provider.validate_declaration(result)
    backend = backends.selected(dict(result, native_backend=backend_name,
        native_launcher_argv=launcher))
    result.setdefault('search_policy', copy.deepcopy(search.DEFAULT_POLICY))
    for name in ('root', 'objective', 'public_root'):
        if name not in result:
            raise ValueError(f'{name} is required for preparation')
        result[name] = Path(result[name]).expanduser().resolve()
    ui = _table(result.get('ui', {}), {'port', 'open_browser', 'approval_secret_file'}, 'ui')
    ui = {'port': 0, 'open_browser': False, **ui}
    if type(ui['port']) is not int or not 0 <= ui['port'] <= 65535:
        raise ValueError('ui.port must be an integer between 0 and 65535')
    if type(ui['open_browser']) is not bool:
        raise ValueError('ui.open_browser must be boolean')
    if 'approval_secret_file' in ui:
        # A host credential source reference only. Never read or copy its bytes.
        ui['approval_secret_file'] = str(Path(ui['approval_secret_file']).expanduser().resolve())
    result['ui'] = ui
    if frozen_skill_selection is not None:
        from . import skill_supply
        if not isinstance(frozen_skill_selection, dict):
            raise ValueError('Frozen Skill selection must be retained settings data')
        frozen_sets=skill_supply.configuration(dict(sets=frozen_skill_selection.get('sets', {})))['sets']
        if skill_supply.configuration(result.get('skills'))['sets']!=frozen_sets:
            raise ValueError('Frozen Skill selection changed during fork preparation')
    selected=result.get('execution_config')
    if isinstance(selected,dict) and 'executor' in selected:
        from . import experiments
        declared,_=experiments.registration(selected['executor'])
        if declared.research_defaults is not None:
            prepared=experiments.operator_configuration(selected)
            defaults=declared.research_defaults(prepared['configuration'],root=result['root'])
            if not isinstance(defaults,dict) or set(defaults)-{'history_observer','delivery_adapter','optional_skill_sets'}:
                raise ValueError('Execution provider defaults exceed observation/delivery/optional Skill scope')
            optional_sets=defaults.get('optional_skill_sets')
            if optional_sets is not None and frozen_skill_selection is None:
                from . import skill_supply
                offered=skill_supply.configuration(dict(sets=optional_sets))['sets']
                configured=skill_supply.configuration(result.get('skills'))['sets']
                for name,row in offered.items():
                    if name in configured and configured[name]!=row:
                        raise ValueError('Explicit Skill set conflicts with the selected execution provider')
                    configured.setdefault(name,copy.deepcopy(row))
                result['skills']=dict(sets=configured)
            observer=defaults.get('history_observer')
            if observer is not None:
                selected_observer=result.get('history_observer')
                if selected_observer is None:result['history_observer']=copy.deepcopy(observer)
                elif any(selected_observer.get(key)!=value for key,value in observer.items()):
                    raise ValueError('Explicit history observer conflicts with the selected execution provider')
            adapter_defaults=defaults.get('delivery_adapter')
            policy=result.get('delivery_policy')
            if adapter_defaults is not None and isinstance(policy,dict) and isinstance(policy.get('adapter'),dict):
                selected_adapter=policy['adapter']
                for key,value in adapter_defaults.items():
                    if key in selected_adapter and selected_adapter[key]!=value:
                        raise ValueError('Explicit delivery adapter conflicts with the selected execution provider')
                    selected_adapter.setdefault(key,copy.deepcopy(value))
    cadence=backend.supports_resume and backend.default_experiment_mode()=='local'
    if (cadence and result.get('node_budget_policy') is None
            and any(key in result and result[key] is None for key in ('max_tokens', 'max_seconds'))):
        raise ValueError('Unlimited global budgets require explicit finite all-role budget.node_targets')
    if result.get('resume_policy') is None:
        defaults=backend.default_resume_policy()
        if defaults is not None:
            result['launcher']=defaults['launcher']
            result['resume_policy']=defaults['policy']
    if cadence:
        # Ordinary W fragments have their own full-task authorization. JSON
        # repairs keep the narrower output-only policy, including after a
        # caller explicitly selects a different fragment context strategy.
        result.setdefault('worker_fragment_policy', dict(context='same_session'))
        if result['worker_fragment_policy'] is not None:
            result['worker_fragment_policy'].setdefault('max_context_windows', 1)
        if result.get('resume_policy') is not None and result.get('node_budget_policy') is None:
            result['node_budget_policy'] = {role: dict(max_observed_tokens=result['max_tokens'],
                max_seconds=result['max_seconds']) for role in 'HWAE'}
    if result.get('worker_fragment_policy') is not None:
        result['worker_fragment_policy'].setdefault('context_handoff_version',2)
    return result


def prepare(config=None, *, validate_only=False, **options):
    """Prepare through one defaulting path; a TOML declaration cannot mix flags."""
    if config is not None:
        if options:
            raise ValueError('A research config cannot be combined with preparation overrides')
        options = load(config)
    return controller.prepare(**normalize(options), validate_only=validate_only)


def inspect(*, config=None, root=None, view='frozen'):
    """Read declarations and operational revisions without binding a new node."""
    if view not in {'frozen', 'current'}:
        raise ValueError('Configuration view must be frozen or current')
    if view == 'current' and root is None:
        raise ValueError('The current configuration view requires an existing instance root')
    if (config is None) == (root is None):
        raise ValueError('Select exactly one config file or existing instance root')
    if root is not None:
        from .protocol import require_settings_format
        root = Path(root).expanduser().resolve()
        settings = require_settings_format(contracts.settings(root))
        if view == 'current':
            from . import configuration_updates
            state = configuration_updates.status(root)
            from . import budget
            active = copy.deepcopy(state['active_settings'])
            active.update(budget.ceilings(root))
            if active.get('execution_resource_policy') is not None:
                grant = budget.execution_accounting(root)['grant']
                if grant is not None:
                    active['execution_resource_policy']['allowance'] = dict(grant['allowance'])
            return dict(status='config_inspected', source='current_instance', instance=str(root),
                base_identity=dict(goal_id=settings['goal_id'], settings_ref=contracts.record_ref(root / 'settings.json')),
                active_revision=state['active_revision'],
                pending_revision=state['revision'] if state['pending'] else None,
                effective_from=state['effective_from'],
                active=to_document(root, active),
                pending=state['configuration'] if state['pending'] else None)
        return dict(status='config_inspected', source='frozen_instance',
                    instance=str(root), settings=settings)
    settings = prepare(config, validate_only=True)
    settings.pop('goal_id', None)
    return dict(status='config_checked', source='research_config', settings=settings)


def to_document(root, settings=None):
    """Reconstruct an editable declaration without changing frozen provenance."""
    from .protocol import require_settings_format
    root = Path(root).resolve()
    value = require_settings_format(contracts.settings(root) if settings is None else settings)
    research = dict(root=str(root), public_root=value['public_root'], objective_text=value['objective'])
    updates = [str(root / row['source']) for row in value.get('global_materials', [])
               if row['source'] != 'project/global/objective.md']
    if updates:
        research['user_updates'] = updates
    agent = dict(backend=value.get('native_backend', backends.DEFAULT_BACKEND), launcher=value['native_launcher_argv'],
                 model=value['model'], reasoning_effort=value.get('reasoning_effort'),
                 service_tier=value.get('service_tier'))
    agent = {key: copy.deepcopy(item) for key, item in agent.items() if item is not None}
    if 'role_profiles' in value:
        agent['roles'] = copy.deepcopy(value.get('role_profile_declarations', value['role_profiles']))
    if 'worker_profiles' in value:
        agent['workers'] = copy.deepcopy(value['worker_profiles'])
    native = value.get('native_options') or {}
    permissions = {key: copy.deepcopy(native[key]) for key in ('workspace', 'network', 'web', 'subagents') if key in native}
    extra = {key: copy.deepcopy(item) for key,item in native.items() if key not in permissions}
    if extra:
        agent['native'] = extra
    budgets = {key: ('unlimited' if value[key] is None else value[key])
               for key in ('max_tokens', 'max_seconds')}
    budgets['max_steps'] = value['max_steps']
    budgets.update({key: value[key] for key in ('max_output_item_bytes', 'max_output_total_bytes')
                    if value.get(key) is not None})
    if value.get('storage_policy') is not None:
        # TOML has no null: omitted dimensions retain their disabled defaults.
        budgets['storage'] = {key: item for key, item in value['storage_policy'].items() if item is not None}
    for source, target in (('node_budget_policy', 'node_targets'), ('role_budget_policy', 'role_shares'),
                           ('search_policy', 'breadth'), ('correction_policy', 'correction')):
        if value.get(source) is not None:
            budgets[target] = copy.deepcopy(value[source])
    if value.get('candidate_nomination_policy') is not None:
        budgets['candidate_extra_slots'] = value['candidate_nomination_policy']['extra_slots']
    if value.get('portfolio_policy') is not None:
        budgets['portfolio_review_after_cycles'] = value['portfolio_policy']['review_after_cycles']
    executor = {target: copy.deepcopy(value[source]) for source, target in
                (('experiment_mode', 'mode'), ('execution_config', 'config'), ('execution_resource_policy', 'resource_policy'))
                if value.get(source) is not None}
    if isinstance(executor.get('config'), dict):
        executor['config'].pop('implementation', None)
    policies = {target: value.get(source) is not None for source, target in
                (('formal_review_policy', 'formal_review'), ('execution_authority_policy', 'execution_authority'),
                 ('candidate_audit_policy', 'candidate_audit'), ('decision_contract_version', 'decision_contract'))}
    for source, target in (('selection_policy', 'selection'), ('resume_policy', 'resume'), ('history_observer', 'history_observer'),('supervision_policy','supervision')):
        if value.get(source) is not None:
            policies[target] = copy.deepcopy(value[source])
    supervision=policies.get('supervision')
    if (isinstance(supervision,dict) and supervision.get('version')==4
            and supervision.get('max_automatic_reviews') is None):
        supervision.pop('max_automatic_reviews',None)
    if isinstance(policies.get('resume'), dict):
        policies['resume'].pop('launcher_files', None)
    observer = policies.get('history_observer')
    if isinstance(observer, dict) and isinstance(observer.get('source_contract'), dict):
        from . import execution_inputs
        observer['source_contract'] = copy.deepcopy(execution_inputs.SOURCE_CONTRACT)
    if value.get('worker_fragment_policy') is not None:
        policies['worker_context'] = copy.deepcopy(value['worker_fragment_policy']['context'])
        if 'max_context_windows' in value['worker_fragment_policy']:
            policies['worker_context_windows'] = value['worker_fragment_policy']['max_context_windows']
            policies['worker_context_handoff_version'] = value['worker_fragment_policy'].get('context_handoff_version',1)
    result = dict(version=1, research=research, agent=agent, permissions=permissions,
                  budget=budgets, executor=executor, policies=policies, ui=copy.deepcopy(value.get('ui', {})))
    if value.get('skills') is not None:
        result['skills'] = dict(sets=copy.deepcopy(value['skills']['sets']))
    if value.get('project') is not None:
        result['project'] = {key: copy.deepcopy(value['project'][key])
                             for key in ('base_root', 'editable', 'scratch', 'outputs')}
    if value.get('delivery_policy') is not None:
        result['delivery'] = copy.deepcopy(value['delivery_policy'])
        adapter = result['delivery'].get('adapter')
        if isinstance(adapter, dict):
            adapter.pop('launcher_files', None)
            adapter['inputs'] = [{key: row[key] for key in ('label', 'path')} for row in adapter['inputs']]
    return result


def validate_update(root, document, *, base=None):
    """Validate a full operational declaration against an existing identity."""
    root = Path(root).resolve()
    original = contracts.settings(root)
    from . import configuration_updates
    previous = configuration_updates.editable(root)
    baseline = to_document(root, previous)
    for key in ('version', 'research', 'ui', 'project'):
        if document.get(key, baseline.get(key)) != baseline.get(key):
            raise ValueError(f'{key} is immutable for an existing research instance')
    candidate = copy.deepcopy(document)
    for key in ('version', 'research', 'ui', 'project'):
        if key in baseline:
            candidate[key] = baseline[key]
    if candidate == baseline:
        return previous
    options = from_document(candidate, base=root if base is None else base)
    # Existing configuration has already been defaulted. Absence disables an
    # optional policy instead of silently recreating standard-launcher defaults.
    options.setdefault('search_policy', None)
    if previous.get('native_options') is None and candidate.get('permissions', {}) == baseline.get('permissions', {}) and candidate.get('agent', {}).get('native', {}) == baseline.get('agent', {}).get('native', {}):
        options['native_options'] = None
    for key in ('model', 'reasoning_effort', 'service_tier'):
        options.setdefault(key, previous.get(key))
    if (candidate.get('executor', {}).get('config') == baseline.get('executor', {}).get('config')
            and options.get('experiment_mode') == previous.get('experiment_mode')):
        # Normalized plugin settings are already frozen; config_file-only
        # plugins must not require their source declaration for a model edit.
        options['execution_config'] = copy.deepcopy(previous.get('execution_config'))
    settings = controller.prepare(**options, validate_only=True, _existing_settings=previous)
    if (candidate.get('permissions', {}) == baseline.get('permissions', {})
            and candidate.get('agent', {}).get('native', {}) == baseline.get('agent', {}).get('native', {})
            and settings.get('native_backend') == previous.get('native_backend')
            and settings.get('native_launcher_argv') == previous.get('native_launcher_argv')):
        for key in ('native_options', 'native_permissions', 'native_capabilities'):
            if key in previous:
                settings[key] = copy.deepcopy(previous[key])
            else:
                settings.pop(key, None)
    if 'ui' not in original:
        settings.pop('ui', None)
    for key in ('goal_id', 'objective', 'public_root', 'global_materials', 'source_provenance',
                'time_budget_basis', 'handoff_protocol', 'final_a_completion_policy'):
        if key in original:
            settings[key] = copy.deepcopy(original[key])
    # Preserve historical options which have no public declaration field.
    for key in ('role_guidance', 'node_budget_mode', 'handoff_authority_version',
                'lineage_projection_version', 'selection_protocol', 'live_feedback_version',
                'experiment_interface_version', 'managed_admission_version', 'worker_view_version',
                'research_strategy_version'):
        if key in previous:
            settings[key] = copy.deepcopy(previous[key])
        else:
            settings.pop(key, None)
    # Contract originals are frozen separately when the revision is committed.
    return settings
