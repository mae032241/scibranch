"""Bounded local control over immutable scientific role handoffs."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import fcntl
import math
import os
import stat
import time
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
import uuid

from . import budget, contracts, history, native, runtime, search, correction, semantic, execution, continuation, guidance, checkpoint, role_share, shortlist, engineering, worker_delivery
from .output_repair import OutputRepairRequired
from . import managed_wait, configuration_updates, handoff_state


def prepare(root: Path, objective: Path, public_root: Path, launcher: list[str], *,
            max_tokens: int, max_seconds: float, max_steps: int,
            model: str, reasoning_effort: str, service_tier: str = "default",
            max_output_item_bytes: int | None = None,
            max_output_total_bytes: int | None = None,
            storage_policy: dict | None = None,
            user_updates: list[Path] | None = None,
            history_observer: dict | None = None,
            search_policy: dict | None = search.DEFAULT_POLICY,
            correction_policy: dict | None = None,
            node_budget_policy: dict | None = None,
            delivery_policy: dict | None = None,
            resume_policy: dict | None = None,
            lineage_projection_version: int | None = 1,
            role_guidance: dict | None = guidance.DEFAULT,
            selection_protocol: int | None = 1,
            node_budget_mode: str = 'soft', candidate_audit_policy: dict | None = None,
            handoff_authority_version: int | None = 1, decision_contract_version: int | None = None,
            portfolio_policy: dict | None = None, exploration_order_version: int | None = None,
            worker_fragment_policy: dict | None = None, role_budget_policy: dict | None = None,
            selection_policy: dict | None = None, role_profiles: dict | None = None,
            worker_profiles: dict | None = None, skills: dict | None = None,
            project: dict | None = None,
            experiment_mode: str | None = None, execution_config: dict | None = None,
            source_restrictions: list[dict] | None = None, formal_review_policy: dict | None = None,
            execution_authority_policy: dict | None = None,
            execution_resource_policy: dict | None = None,
            candidate_extra_slots: int = 2,
            native_backend: str = 'codex', native_options: dict | None = None,
            ui: dict | None = None, validate_only: bool = False,
            objective_text: str | None = None, _existing_settings: dict | None = None,
            _identity_root: Path | None = None,
            _public_identity_root: Path | None = None,
            _user_update_texts: list[str] | None = None,
            _source_facts_factory=None, _on_created=None, _fork_search_seed=None) -> dict:
    """Freeze an explicit goal and finite launch budgets in a fresh directory."""
    from . import candidate_nominations
    from . import storage_policy as storage
    storage_limits = storage.configuration(storage_policy)
    nomination_limits=candidate_nominations.configuration(candidate_extra_slots)
    portfolio_limits = search.portfolio_policy(portfolio_policy) if portfolio_policy is not None else None
    if exploration_order_version is not None:
        raise ValueError('Author priority-table strategy is retired; current science declares intent directly')
    if portfolio_limits is not None:
        if search_policy is None or lineage_projection_version != 1:
            raise ValueError('Portfolio review requires H commitment search and unified lineage projection')
        decision_contract_version = 1
    if decision_contract_version is not None and (type(decision_contract_version) is not int or decision_contract_version != 1):
        raise ValueError('Unsupported scientific decision contract')
    if handoff_authority_version is not None and (type(handoff_authority_version) is not int or handoff_authority_version != 1):
        raise ValueError('Unsupported scientific handoff authority version')
    if lineage_projection_version is not None and (type(lineage_projection_version) is not int or lineage_projection_version != 1):
        raise ValueError('Unsupported lineage projection version')
    if selection_protocol is not None and (type(selection_protocol) is not int or selection_protocol != 1):
        raise ValueError('Unsupported public selection protocol')
    if node_budget_mode != 'soft':
        raise ValueError('All H/W/A/E node token targets must use soft mode; hard mode is unsupported')
    root, objective, public_root = map(Path, (root, objective, public_root))
    identity_root = root.resolve() if _identity_root is None else Path(_identity_root).absolute()
    if identity_root.resolve() != identity_root or identity_root.is_symlink():
        raise ValueError('Preparation identity requires an exact canonical final root')
    if _identity_root is not None and identity_root != root.resolve() and (identity_root.exists() or identity_root.is_symlink()):
        raise ValueError('Preparation identity requires a fresh final root')
    public_identity = public_root.resolve() if _public_identity_root is None else Path(_public_identity_root).absolute()
    if public_identity.resolve() != public_identity or public_identity.is_symlink():
        raise ValueError('Preparation public identity requires an exact canonical root')
    for name, value in (("max_tokens", max_tokens), ("max_steps", max_steps)):
        if type(value) is not int or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    if (type(max_seconds) not in (int, float) or not 0 < max_seconds < float("inf")):
        raise ValueError("max_seconds must be positive and finite")
    storage_context = storage.Deadline(max_seconds)
    from . import project_layout
    if (_existing_settings is not None and _existing_settings.get('project') is None
            and project is not None):
        raise ValueError('Project declaration is immutable for an existing instance')
    native_home = Path((resume_policy or {}).get('native_home',
        os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))).absolute()
    frozen_project = project_layout.prepare(project,
        protected=[root.absolute(), identity_root, public_root.absolute(), public_identity,
                   native_home, *[Path(path) for path in
                   ('/usr', '/bin', '/sbin', '/lib', '/lib64', '/etc', '/dev', '/proc', '/sys', '/run',
                    '/task-native', '/task-tmp')]],
        existing=(_existing_settings or {}).get('project'), context=storage_context)
    if frozen_project is not None:
        # Scientific originals can be readonly base context, but cannot be
        # shadowed by a declared writable/code mount at their original path.
        originals = [objective, *[Path(path) for path in (user_updates or [])],
                     *[Path(row['source']) for row in (source_restrictions or [])
                       if isinstance(row, dict) and isinstance(row.get('source'), str)]]
        for relative in frozen_project['editable'] + frozen_project['scratch'] + frozen_project['outputs']:
            target = Path(frozen_project['base_root']) / relative
            if any(project_layout._overlap(target, path.absolute()) for path in originals):
                raise ValueError('Project declared directory overlaps an exact scientific original: ' + relative)
    for name,value in (("max_output_item_bytes",max_output_item_bytes),("max_output_total_bytes",max_output_total_bytes)):
        if value is not None and (type(value) is not int or value<=0):
            raise ValueError(f"{name} must be a positive integer or null")
    if max_output_item_bytes is not None and max_output_total_bytes is not None and max_output_item_bytes > max_output_total_bytes:
        raise ValueError("Output item capacity exceeds total capacity")
    from . import backends as agent_backends
    backend_settings = agent_backends.configuration(native_backend, launcher,
        {} if native_options is None else native_options, existing=_existing_settings)
    if frozen_project is not None:
        from . import native_execution
        native_execution.project_policy(backend_settings)
    launcher = backend_settings['native_launcher_argv']
    if not isinstance(model, str) or not model.strip():
        raise ValueError('An explicit model is required')
    if any(value is not None and (not isinstance(value, str) or not value.strip())
           for value in (reasoning_effort, service_tier)):
        raise ValueError('Reasoning effort and service tier must be nonempty strings or null')
    if native_backend != 'codex' and resume_policy is not None:
        raise ValueError('The selected backend does not support Codex continuation policies')
    from . import profiles, skill_supply
    frozen_skills = skill_supply.prepare(skills, backend=native_backend,
        launcher=launcher, existing=_existing_settings,
        role_profiles=role_profiles, worker_profiles=worker_profiles)
    frozen_profiles = profiles.normalize(role_profiles if role_profiles is not None else {}, dict(model=model, reasoning_effort=reasoning_effort,
        service_tier=service_tier), worker_profiles, (frozen_skills or {}).get('sets', {})) if role_profiles is not None or worker_profiles is not None else None
    if (not isinstance(launcher, list) or not launcher
            or not all(isinstance(item, str) and item.strip() for item in launcher)):
        raise ValueError("An explicit native launcher argument list is required")
    if execution_config is not None and experiment_mode=='external':
        raise ValueError('Execution config conflicts with external launcher-owned execution')
    if execution_config is not None and experiment_mode is None:
        experiment_mode='local'
    if experiment_mode is None:
        experiment_mode = 'local' if len(launcher) == 1 and Path(launcher[0]).name == 'codex' else 'external'
    if experiment_mode not in {'local', 'external'}:
        raise ValueError('Experiment mode must be local or external')
    frozen_execution=None
    if experiment_mode=='local':
        from . import experiments
        import sys
        if (_existing_settings is not None and _existing_settings.get('experiment_mode') == 'local'
                and execution_config is not None and execution_config == _existing_settings.get('execution_config')):
            frozen_execution = dict(execution_config)
        else:
            frozen_execution=experiments.operator_configuration(execution_config if execution_config is not None else
                dict(executor='local_execution:command',configuration=dict(run_argv=[sys.executable],max_seconds=min(65,max_seconds))))
    if (root.exists() or root.is_symlink()) and not (validate_only and _existing_settings is not None):
        raise ValueError("Preparation requires a fresh directory")
    if not public_root.is_dir() or (objective_text is None and not objective.is_file()):
        raise ValueError("Explicit objective file and public directory are required")
    if objective_text is not None:
        if not isinstance(objective_text, str):
            raise ValueError("The objective must contain original user text")
        # An unchanged frozen file objective may exceed the new inline input cap.
        if (len(objective_text.encode('utf-8')) > 256_000
                and (_existing_settings is None or objective_text != _existing_settings.get('objective'))):
            raise ValueError("The objective text exceeds the 256000-byte limit")
        objective = root.resolve() / "project/global/objective.md"
    text = objective.read_text(encoding="utf-8") if objective_text is None else objective_text
    if not text.strip():
        raise ValueError("The objective must contain original user text")
    updates = [contracts.regular(Path(path)).read_text(encoding='utf-8')
               for path in (user_updates or [])]
    if _user_update_texts is not None:
        if user_updates or not isinstance(_user_update_texts, list) or any(not isinstance(text, str) for text in _user_update_texts):
            raise ValueError('Generated updates require explicit original texts without external update paths')
        updates = list(_user_update_texts)
    if any(not update.strip() for update in updates):
        raise ValueError('Global user updates must contain original user text')
    from . import qualification
    original_globals = [objective, *[Path(path) for path in (user_updates or [])]]
    if _user_update_texts is not None:
        original_globals = [objective, *[root.resolve() / 'project/global/updates' / f'{index:03d}.md'
            for index in range(1, len(updates) + 1)]]
    source_facts = (qualification.source_configuration(public_root, source_restrictions, original_globals)
                    if _existing_settings is None else None)
    from . import formal_review
    review_policy=formal_review.configuration(formal_review_policy)
    from . import execution_authority
    authority_policy=execution_authority.configuration(execution_authority_policy)
    if authority_policy is not None and review_policy is None:
        raise ValueError('Execution authority requires the explicit formal review contract')
    resource_policy = budget.execution_resource_policy(execution_resource_policy)
    if resource_policy is not None and (authority_policy is None or frozen_execution is None):
        raise ValueError('Execution resources require explicit authority and registered host execution')
    observer = history.observer_config(history_observer) if history_observer is not None else None
    literal_guidance = (guidance.OPPORTUNITY if portfolio_limits is not None and portfolio_limits.get('version', 1) == 2
                        and role_guidance is guidance.DEFAULT else role_guidance)
    advice = guidance.policy(literal_guidance) if literal_guidance is not None else None
    search_limits = search.policy(search_policy) if search_policy is not None else None
    correction_limits = correction.policy(correction_policy) if correction_policy is not None else None
    if correction_limits is not None and search_limits is None:
        raise ValueError('Correction requires the frozen search policy')
    node_limits = budget.node_policy(node_budget_policy) if node_budget_policy is not None else None
    resume_limits = continuation.policy(resume_policy, launcher) if resume_policy is not None else None
    fragment_limits = checkpoint.policy(worker_fragment_policy) if worker_fragment_policy is not None else None
    if fragment_limits is not None:
        if resume_limits is None or node_limits is None:
            raise ValueError('W fragments require frozen turn-scoped launcher and original node budgets')
        if 'stage_intent' not in contracts.result_schema('W', contracts.HANDOFF_PROTOCOL)['properties']:
            raise ValueError('W fragments require the complete stage-intent schema; protocol13 is not enabled')
    if resume_limits is not None and node_limits is None:
        raise ValueError('Continuation requires explicit original node budgets')
    share_limits = role_share.policy(role_budget_policy) if role_budget_policy is not None else None
    if share_limits is not None and node_limits is None:
        raise ValueError('Role budget shares require explicit original node budgets')
    delivery_limits = None
    if delivery_policy is not None:
        from . import delivery
        delivery_limits = delivery.policy(delivery_policy)
        if node_limits is None:
            raise ValueError('Delivery requires explicitly frozen all-role node budgets')
    if candidate_audit_policy is not None:
        if candidate_audit_policy != {'version': 1} or type(candidate_audit_policy.get('version')) is not int:
            raise ValueError('Unsupported candidate audit policy')
        if delivery_limits is None or delivery_limits['adapter'] is None:
            raise ValueError('Candidate audit requires an explicit isolated delivery adapter')
        if 'audit' not in delivery_limits['adapter'].get('capabilities', ['audit']):
            raise ValueError('Candidate audit was required but the adapter declares no audit capability')
    production = delivery_limits is not None and delivery_limits['adapter'] is not None and (observer is not None or experiment_mode == 'local')
    selection_limits = shortlist.policy(selection_policy) if selection_policy is not None else None
    if selection_limits is not None and not production:
        raise ValueError('Control selection requires public observations and an isolated delivery adapter')
    settings = dict(goal_id="scid-hwa-" + uuid.uuid4().hex[:12], objective=text,
        public_root=str(public_identity), native_launcher_argv=list(launcher),
        max_tokens=max_tokens, max_seconds=max_seconds, max_steps=max_steps, time_budget_basis="active",
        model=model, reasoning_effort=reasoning_effort, service_tier=service_tier,
        max_output_item_bytes=max_output_item_bytes,
        max_output_total_bytes=max_output_total_bytes,
        storage_policy=storage_limits)
    settings.update(backend_settings)
    if frozen_project is not None:
        settings['project'] = frozen_project
    if ui is not None:
        settings['ui'] = dict(ui)
    from .protocol import CURRENT
    if frozen_profiles is not None:
        settings['role_profiles'] = frozen_profiles
    if worker_profiles is not None:
        settings['worker_profiles'] = worker_profiles
        settings['role_profile_declarations'] = role_profiles or {}
    if frozen_skills is not None:
        settings['skills'] = frozen_skills
    settings['handoff_protocol']=CURRENT
    settings['handoff_authority_version']=settings['lineage_projection_version']=settings['selection_protocol']=1
    settings['node_budget_mode'] = node_budget_mode
    settings['final_a_completion_policy'] = dict(version=1)
    settings['live_feedback_version'] = 1
    settings['experiment_interface_version'] = 1
    settings['managed_admission_version'] = 1
    settings['worker_view_version'] = 1
    settings['experiment_mode'] = experiment_mode
    if frozen_execution is not None:
        settings['execution_config']=frozen_execution
    if resource_policy is not None:
        settings['execution_resource_policy']=resource_policy
    if handoff_authority_version is not None:
        settings['handoff_authority_version'] = handoff_authority_version
    if decision_contract_version is not None:
        settings['decision_contract_version'] = decision_contract_version
    if portfolio_limits is not None:
        settings['portfolio_policy'] = portfolio_limits
    if fragment_limits is not None:
        settings['worker_fragment_policy'] = fragment_limits
    if candidate_audit_policy is not None:
        settings['candidate_audit_policy'] = dict(candidate_audit_policy)
    if selection_protocol is not None and lineage_projection_version == 1:
        settings['selection_protocol'] = selection_protocol
    if advice is not None:
        settings['role_guidance'] = advice
    if lineage_projection_version is not None:
        settings['lineage_projection_version'] = lineage_projection_version
    if delivery_limits is not None:
        settings['delivery_policy'] = delivery_limits
        reserve = budget.delivery_reserve(settings)
        if reserve['tokens'] > max_tokens or reserve['seconds'] > max_seconds or reserve['steps'] > max_steps:
            raise ValueError('Delivery reservation exceeds original total authorization')
    if resume_limits is not None:
        settings['resume_policy'] = resume_limits
    if node_limits is not None:
        settings['node_budget_policy'] = node_limits
    if share_limits is not None:
        settings['role_budget_policy'] = share_limits
    if selection_limits is not None:
        settings['selection_policy'] = selection_limits
    if observer is not None:
        settings['history_observer'] = observer
    if search_limits is not None:
        settings['search_policy'] = search_limits
        settings['candidate_nomination_policy'] = nomination_limits
    if correction_limits is not None:
        settings['correction_policy'] = correction_limits
    if review_policy is not None:
        settings['formal_review_policy'] = dict(review_policy)
    if authority_policy is not None:
        settings['execution_authority_policy'] = dict(authority_policy)
    if validate_only:
        if _existing_settings is not None:
            skill_supply.validate(root, settings)
        return settings
    from .instance_owner import creation
    with creation(root):
        root.mkdir(parents=True)
        if _on_created is not None:
            _on_created(root)
        # Include newly created parents and this root before writing originals.
        with creation(root):
            globals_ = root / 'project/global'
            (globals_ / 'updates').mkdir(parents=True)
            sources = [(globals_ / 'objective.md', text), *[
                (globals_ / 'updates' / f'{index:03d}.md', update)
                for index, update in enumerate(updates, 1)]]
            settings['global_materials'] = []
            for index, (source, contents) in enumerate(sources):
                generated = objective_text is not None if index == 0 else _user_update_texts is not None
                if generated:
                    contracts.atomic_files.publish_bytes(source, contents.encode('utf-8'))
                    source.chmod(0o400)
                else:
                    source = Path(original_globals[index]).absolute()
                    contracts.record_path(source)
                    if source.read_text(encoding='utf-8') != contents:
                        raise ValueError('Original global text changed before preparation')
                logical = 'objective.md' if index == 0 else f'updates/{index:03d}.md'
                settings['global_materials'].append(dict(
                    source=source.relative_to(root).as_posix() if source.is_relative_to(root) else str(source),
                    size_bytes=source.stat().st_size, path='inputs/global/' + logical))
            if _source_facts_factory is not None:
                if source_restrictions:
                    raise ValueError('Staged source facts cannot replace external source declarations')
                source_facts = _source_facts_factory(root, settings['global_materials'])
                if not isinstance(source_facts, list):
                    raise ValueError('Staged source preparation must return exact configured declarations')
            settings['source_provenance'] = qualification.freeze_sources(root, source_facts, original_globals, settings['global_materials'],
                policy=storage_limits,context=storage_context,
                source_kind='managed_frozen' if _source_facts_factory is not None else 'mutable')
            if identity_root != root.resolve() and (objective_text is not None or _user_update_texts is not None or _source_facts_factory is not None):
                # This original is created by prepare itself. Freeze its final logical
                # location before sealing; supplied external originals keep their identity.
                provenance_path = root / settings['source_provenance']['path']
                provenance = contracts.read(provenance_path)
                for index, mapping in enumerate(provenance['globals']):
                    if index == 0 and objective_text is None or index > 0 and _user_update_texts is None:
                        continue
                    relative = 'project/global/objective.md' if index == 0 else f'project/global/updates/{index:03d}.md'
                    generated = mapping['origin']
                    if generated['path'] != str((root / relative).resolve()):
                        raise ValueError('Generated global lost its exact preparation original')
                    generated['path'] = str(identity_root / relative)
                if _source_facts_factory is not None:
                    for declaration in provenance['declarations']:
                        source_path = Path(declaration['origin']['path'])
                        if source_path.is_relative_to(root.resolve()):
                            declaration['origin']['path'] = str(identity_root / source_path.relative_to(root.resolve()))
                contracts.write(provenance_path, provenance)
                settings['source_provenance'] = qualification._ref(root, provenance_path)
            if review_policy is not None:
                settings['formal_review_policy']=formal_review.freeze_contract(root)
            if authority_policy is not None:
                settings['execution_authority_policy']=execution_authority.freeze_configuration(root)
            skill_supply.validate(root, settings)
            contracts.write(root / "settings.json", settings)
            contracts.write(root / "settings-identity.json",
                            dict(path="settings.json"))
            from . import accepted_budget, accepted_prefix
            from . import accepted_search
            search_seed=accepted_search.empty() if _fork_search_seed is None else _fork_search_seed
            if _fork_search_seed is None:
                prefix_seed,search_rows=None,None
            else:
                prefix_seed,search_rows,search_seed=accepted_prefix.fork_seed(
                    root,settings,search_seed=search_seed,identity_root=identity_root)
            search_ref=accepted_prefix.initialize(root,search_seed=search_seed,identity_root=identity_root)
            accepted_budget.initialize(root,settings,identity_root=identity_root,search_seed=search_seed,
                search_ref=search_ref,prefix_seed=prefix_seed,search_rows=search_rows)
            with storage.mutation(root,0,policy=storage_limits,context=storage.Deadline(max_seconds)):
                pass
    return settings


@contextmanager
def _writer(root):
    from .instance_owner import lease
    with lease(root), _local_writer(root):
        yield


_controller_writers = ContextVar('controller_writers', default=frozenset())


def require_writer(root):
    if (os.getpid(), str(Path(root).resolve())) not in _controller_writers.get():
        raise RuntimeError('Original controller writer is required for audit recovery')


@contextmanager
def _local_writer(root, *, descriptor=None):
    from .protocol import require_current
    require_current(root)
    owned = descriptor is None
    if owned:
        descriptor = os.open(root / "host-run.lock",
            os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    else:
        opened=os.fstat(descriptor);current=os.stat(root/'host-run.lock',follow_symlinks=False)
        if (opened.st_dev,opened.st_ino)!=(current.st_dev,current.st_ino):
            raise ValueError('Detached controller lost the original writer lock')
    try:
        opened=os.fstat(descriptor);current=os.stat(root/'host-run.lock',follow_symlinks=False)
        if (not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(current.st_mode)
                or opened.st_nlink!=1 or current.st_nlink!=1
                or (opened.st_dev,opened.st_ino)!=(current.st_dev,current.st_ino)):
            raise ValueError('Original controller writer lock changed')
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("This research loop already has a running controller") from error
        token = _controller_writers.set(_controller_writers.get() | {(os.getpid(), str(Path(root).resolve()))})
        try:
            with handoff_state.scope(root):
                yield
        finally:
            _controller_writers.reset(token)
    finally:
        if owned:os.close(descriptor)


@contextmanager
def _inactive_on_error(root, reason='controller_failed'):
    """Reconcile failed control while its caller still owns the writer."""
    try:
        yield
    except BaseException as error:
        try:
            budget.inactive(root, reason)
        except BaseException as stop_error:
            try:
                note = getattr(error, 'add_note', None)
                if callable(note):
                    note(f'Budget reconciliation failed ({type(stop_error).__name__}): {stop_error}')
            except BaseException:
                pass  # Optional diagnostics cannot replace the original failure.
        raise


def run(root: Path) -> dict:
    """Reconstruct routing from sealed nodes; never replay an uncertain launch."""
    from .protocol import require_current
    require_current(root)
    root = Path(root).resolve()
    with _writer(root), _inactive_on_error(root):
        result = _run(root)
        from . import control_operations
        control_operations.apply_pending_budget(root)
        return result


def _record(root, step):
    return root / "native-evidence" / f"{step:03d}"


class _AutomaticRecoveryUnavailable(continuation.ResumeUnavailable):
    def __init__(self, reason, *, paused=False):
        self.status = 'paused' if paused else 'automatic_recovery_stopped'
        super().__init__(reason)


def _recovery_status(error):
    return getattr(error, 'status', 'output_repair_unavailable')


def _recovery_state(root, step, decision):
    """Bind mutable scheduling facts to this exact accepted source attempt."""
    record = Path(decision['source_record'])
    if record != execution.record(root, step) or record.resolve() != record:
        raise ValueError('Automatic recovery source attempt changed')
    path = record / 'automatic-recovery.json'
    counters = decision['counters']
    binding = dict(version=1, task_ref=contracts.record_ref(root / 'jobs' / f'{step:03d}.json'),
        source_intent_ref=contracts.record_ref(record / 'native-intent.json'),
        source_attempt=decision['source_attempt'], action=decision['action'], reason=decision['reason'])
    saved = contracts.read(contracts.regular(path)) if path.exists() else None
    if saved is not None:
        if any(saved.get(key) != value for key, value in binding.items() if key not in {'action', 'reason'}):
            raise ValueError('Automatic recovery schedule lost its original task')
        for key in ('local_wait_seconds', 'service_wait_seconds'):
            if type(saved.get(key)) not in (int, float) or not math.isfinite(saved[key]) or saved[key] < 0:
                raise ValueError('Automatic recovery waiting consumption changed')
        if (saved.get('retry_count') != counters['retries'] or saved.get('continue_count') != counters['continues']
                or not math.isclose(saved['service_wait_seconds'], counters['wait_seconds'] + saved['local_wait_seconds'], abs_tol=1e-8)):
            raise ValueError('Automatic recovery schedule changed its consumed counters')
    if saved is None:
        delay = max(0., float(decision.get('delay_seconds') or 0.))
        now = time.time()
        saved = dict(binding, status='waiting', scheduled_at=_recovery_utc(now),
            next_attempt_at=_recovery_utc(now + delay), delay_seconds=delay,
            local_wait_seconds=0., service_wait_seconds=float(counters['wait_seconds']),
            retry_count=counters['retries'], continue_count=counters['continues'])
    else:
        saved.update(binding)
    return path, saved


def _recovery_utc(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def _recovery_remaining(saved, policy):
    """UTC grants re-arbitration only; clock anomalies get one finite delay."""
    cap = float(policy['max_backoff_seconds'])
    try:
        stamp = datetime.fromisoformat(saved['next_attempt_at'].replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError('UTC recovery deadline required')
        remaining = stamp.timestamp() - time.time()
        if not math.isfinite(remaining) or remaining > cap:
            raise ValueError('Recovery deadline is outside its bounded window')
        return max(0., remaining)
    except (ValueError, TypeError, KeyError, OverflowError, AttributeError):
        remaining = min(float(policy['initial_backoff_seconds']), cap)
        saved.update(next_attempt_at=_recovery_utc(time.time() + remaining),
            limitation='Saved UTC deadline was not comparable; a bounded interval precedes fresh arbitration')
        return remaining


def _wait_automatic_recovery(root, step, decision, *, inventory):
    require_writer(root)
    path, saved = _recovery_state(root, step, decision)
    policy = configuration_updates.for_step(root, step)['resume_policy']['automatic_recovery']
    # A positively verified format rejection needs no capacity backoff. Keep
    # any consumed wait from an older stopped schedule without inventing more.
    remaining = 0. if decision.get('format_fallback') is True and decision.get('delay_seconds') == 0 else _recovery_remaining(saved, policy)
    if saved['service_wait_seconds'] + remaining > policy['max_wait_seconds']:
        saved.update(status='stopped', next_attempt_at=None, limitation='automatic_recovery_wait_limit')
        contracts.write(path, saved)
        raise _AutomaticRecoveryUnavailable('automatic_recovery_wait_limit')
    saved['status'] = 'waiting'
    contracts.write(path, saved)
    budget.transition(root, 'waiting', 'automatic_service_backoff')
    until = time.monotonic() + remaining
    try:
        # The writer excludes concurrent budget amendments during this wait.
        # Observe the full ledger once, at the same clock sample used to anchor
        # its local deadline. Fresh acceptance still belongs to automatic_start.
        stamp = budget.clock_stamp()
        limits = budget.snapshot(root, sample_stamp=stamp)
        final_transport=False
        if decision.get('format_fallback') is True and remaining <= 0:
            # Only negotiation of this already accepted final return can pass
            # its consumed shared clock; the original per-call clock remains.
            current=continuation.automatic_decision(root,step,inventory=inventory)
            original=contracts.read(contracts.regular(execution.origin(root,step)/'native-intent.json'))
            if current == decision and (original.get('budget_slice') or {}).get('final_return') is not None:
                job=contracts.read(contracts.regular(root/'jobs'/f'{step:03d}.json'))
                final_transport=budget.final_a_return(root,job,original) is not None
        active = limits['time_budget_basis'] == 'active'
        shared_until = stamp['at_boottime' if active else 'at_unix'] + limits['seconds_remaining']
        while True:
            if (root / 'pause.request').exists():
                saved.update(status='paused', limitation='User pause prevents automatic continuation')
                contracts.write(path, saved)
                budget.transition(root, 'paused', 'automatic_recovery_paused')
                raise _AutomaticRecoveryUnavailable('automatic_recovery_paused', paused=True)
            # BOOTTIME includes same-boot suspend; service wait below deliberately
            # uses MONOTONIC and only counts this controller's actual waiting.
            shared_now = time.clock_gettime(time.CLOCK_BOOTTIME) if active else time.time()
            shared_left = max(0., shared_until - shared_now)
            if shared_left <= 0 and not final_transport:
                saved.update(status='stopped', next_attempt_at=None, limitation='wall_limit')
                contracts.write(path, saved)
                raise budget.NodeBudgetUnavailable('wall_limit')
            left = max(0., until - time.monotonic())
            if left <= 0:
                break
            allowance = policy['max_wait_seconds'] - saved['service_wait_seconds']
            if allowance <= 0:
                saved.update(status='stopped', next_attempt_at=None, limitation='automatic_recovery_wait_limit')
                contracts.write(path, saved)
                raise _AutomaticRecoveryUnavailable('automatic_recovery_wait_limit')
            before = time.monotonic()
            try:
                time.sleep(min(1., left, allowance, shared_left))
            finally:
                # Only this live controller's monotonic wait is added. Offline
                # gaps and unknown crash tails are never inferred from UTC.
                elapsed = max(0., time.monotonic() - before)
                saved['local_wait_seconds'] += elapsed
                saved['service_wait_seconds'] += elapsed
                contracts.write(path, saved)
        saved['status'] = 'ready'
        contracts.write(path, saved)
        current = continuation.automatic_decision(root, step,inventory=inventory)
        if current['action'] not in {'retry', 'continue'}:
            paused = (root / 'pause.request').exists()
            saved.update(status='paused' if paused else 'stopped', next_attempt_at=None, limitation=current['reason'])
            contracts.write(path, saved)
            if paused:
                budget.transition(root, 'paused', 'automatic_recovery_paused')
            raise _AutomaticRecoveryUnavailable(current['reason'], paused=paused)
        try:
            continuation.automatic_start(root, step, current,inventory=inventory,
                recovery_wait=dict(waited_seconds=saved['local_wait_seconds'], record_ref=contracts.record_ref(path)))
        except continuation.ResumeUnavailable as error:
            paused = (root / 'pause.request').exists()
            saved.update(status='paused' if paused else 'stopped', next_attempt_at=None, limitation=str(error))
            contracts.write(path, saved)
            if paused:
                budget.transition(root, 'paused', 'automatic_recovery_paused')
            raise _AutomaticRecoveryUnavailable(str(error), paused=paused) from error
        except budget.NodeBudgetUnavailable as error:
            saved.update(status='stopped', next_attempt_at=None, limitation=error.reason)
            contracts.write(path, saved)
            raise
        saved['status'] = 'started'
        contracts.write(path, saved)
    finally:
        if saved['status'] != 'paused':
            budget.transition(root, 'advance', 'automatic_recovery_arbitration')


def _native_call(root, step, *, launch=False, operation=None):
    """One controller consumer owns all automatic recovery scheduling."""
    call = operation if operation is not None else native.execute_native if launch else native.wait_native
    inventory=None
    while True:
        try:
            return call(root, step)
        except continuation.AutomaticRecoveryRequired as error:
            inventory=error.inventory
            try:
                _wait_automatic_recovery(root, step, error.decision,inventory=error.inventory)
            except budget.NodeBudgetUnavailable as stopped:
                if error.decision.get('work_repair') is not None:
                    return continuation.finish_automatic_work(root, step, error.decision, stopped.reason,inventory=error.inventory)
                raise
            # automatic_start has already spawned this accepted attempt. Its
            # receipts can still be unpublished; collection must not spawn it
            # again in that window. Ordinary reentry keeps the default flag.
            call = partial(native.wait_native, _reentry_started=True)
        except (OutputRepairRequired, budget.NodeBudgetUnavailable):
            raise
        except Exception as error:
            # Permanent/unknown failures remain failures. Save only the exact
            # arbiter's stop reason so the UI can explain why no retry occurs.
            stopped = None
            try:
                decision = continuation.automatic_decision(root, step,inventory=None)
                if decision.get('action') == 'stop' and decision.get('source_record'):
                    path, saved = _recovery_state(root, step, decision)
                    paused = (root / 'pause.request').exists()
                    saved.update(status='paused' if paused else 'stopped', next_attempt_at=None, limitation=decision['reason'])
                    contracts.write(path, saved)
                    stopped = _AutomaticRecoveryUnavailable(decision['reason'], paused=paused)
            except (OSError, ValueError, KeyError, TypeError):
                pass
            # Integrity and exact-original failures retain their original typed
            # exception even when a separate recovery decision also says stop.
            if stopped is not None and not isinstance(error, ValueError):
                raise stopped
            raise


def _parents(*steps):
    return list(dict.fromkeys(step for step in steps if step is not None))


def _launch_limit(root, settings):
    limits = budget.snapshot(root, settings)
    if limits['seconds_remaining'] <= 0:
        return "wall_limit"
    observed = native.usage(root)
    if observed.get('blocking_unknown_steps',observed['unknown_steps']):
        return "usage_unknown"
    if observed.get('accounted_tokens',observed['observed_tokens']) >= limits["max_tokens"]:
        return "token_limit"
    return None


def _finish(root, settings, status, completed, last, *, reason=None, next_role=None, correction_trigger=None):
    budget.inactive(root, status)
    result = dict(status=status, goal_id=settings["goal_id"], completed_steps=completed,
        last_step=completed or None, usage=native.usage(root),
        goal_completion="not_asserted", unmet_goal=settings["objective"])
    state = budget.closeout_state(root)
    actions = state['actions']
    confirmed = [row['outcome']['result'] for row in actions if row['outcome'] is not None
        and row['intent']['action_kind'] == 'confirm' and row['outcome']['result']['status'] == 'confirmed']
    if actions:
        last_action = actions[-1]
        pending = last_action['outcome'] is None or last_action['outcome']['result']['status'] == 'unknown' or (
            last_action['intent']['action_kind'] == 'submit' and last_action['outcome']['result']['status'] == 'confirmed')
        result['current_actual'] = 'unknown' if state['unresolved_operations'] or pending else confirmed[-1] if confirmed else None
    if last is not None:
        result.update(role=last["role"], scientific_route=last.get("next_action", ""),
            route_reason=last.get("route_reason", ""), summary=last["summary"],
            limitations=last["limitations"],
            scientific_output_path=str(execution.raw(root, completed)))
        control = contracts.read(_record(root, completed) / 'result-identity.json').get('control_outcome')
        if control is not None:
            result['control_outcome'] = control
        completion = contracts.read(execution.record(root, completed) / 'native-outcome.json').get('final_a_completion')
        if completion is not None:
            result['final_overrun'] = completion['final_overrun']
    if reason is not None:
        result["reason"] = reason
    if next_role is not None:
        result["next_role"] = next_role
    if correction_trigger is not None:
        result['correction_trigger'] = correction_trigger
    return result


def _check_job(root, settings, step, role, parents, instruction, evidence_return, correction_request=None, author_correction=None):
    """A retained node must be the exact job selected by its sealed predecessor."""
    job = contracts.read(root / "jobs" / f"{step:03d}.json")
    if job.get('fork_source'):
        from . import fork_runtime
        fork_runtime.validate_selected_job(root, contracts.workspace(root, step), job)
        if (job['role'], job['instruction'], job.get('evidence_return'), job.get('correction_request'), job.get('author_correction')) != (
                role, instruction, evidence_return, correction_request, author_correction):
            raise ValueError('Fork task differs from its exact bootstrap route')
        return job
    expected_refs = [dict(path=f"inputs/parents/{parent:03d}/conclusion.json",size_bytes=execution.raw(root,parent).stat().st_size)
        for parent in parents]
    if (evidence_return or {}).get('kind')=='formal_review':
        from . import formal_review
        formal_review.validate_job(root,job)
        expected_refs=[]
    if (job.get("step") != step or job.get("role") != role
            or job.get("goal_text") != settings["objective"]
            or job.get("public_root") != settings["public_root"]
            or job.get("direct_originals",job.get("direct_refs")) != expected_refs
            or job.get("instruction", "") != instruction
            or job.get("evidence_return") != evidence_return
            or job.get('correction_request') != correction_request
            or job.get('author_correction') != author_correction):
        raise ValueError("Retained job differs from the sealed scientific route")
    return job


def _route(root, step, role, result, job, current_h, current_w):
    instruction = result.get("route_reason", "")
    if role=='A' and (job.get('formal_review') or result.get('next_action')=='formal_review'):
        from . import formal_review
        parents=_parents(current_h,current_w,step)
        route=(formal_review.return_route(root,job,parents) if job.get('formal_review') else
               formal_review.request_route(root,job,result,parents))
        return (*route,current_h,current_w)
    if role == "H":
        current_h = step
        action = result["next_action"]
        if action == "experiment":
            route = ("W", [step], instruction, None)
        elif action == "analyze":
            route = ("A", _parents(current_h, current_w), instruction, None)
        elif action == "extract":
            route = ("E", _parents(current_h, current_w), instruction,
                     dict(role="H", step=step))
        elif action == "wait":
            route = (None, [], "", None)
        else:
            raise ValueError("Invalid sealed H route")
    elif role == "W":
        current_w = step
        record = execution.record(root, step)
        ended = checkpoint.stage_end(root, job, record) if (record / 'stage-end.json').exists() else None
        question = 'Review the scientific contribution and original goal limits; control ended this stage for ' + ended['reason'] + '.' if ended else ''
        outcome = contracts.read(_record(root,step)/'result-identity.json')['control_outcome']
        delivery = outcome.get('worker_delivery') or {}
        if delivery.get('status') == 'partial':
            question = 'Review the retained partial scientific contribution and original goal limits; assigned work remains incomplete for ' + delivery['reason'] + '.'
        elif not ended and outcome.get('stage_end_reason')=='continuation_unavailable':
            question='Frozen context continuation was unavailable; judge the original retained contribution and its limitations.'
        route = ("A", _parents(current_h, step), question, None)
    elif role == "A":
        action = result["next_action"]
        parents = _parents(current_h, current_w, step)
        if action == "continue_w":
            route = ("W", parents, instruction, None)
        elif action == "revise_h":
            route = ("H", parents, instruction, None)
        elif action == "audit":
            from . import engineering
            repair=engineering.request(root,step)
            if repair is not None:
                engineering.admit(root,repair,step+1)
                route=('W',sorted(set([*parents,repair['source']['step']])),engineering.public(repair)['scope'],None)
            else:
                route = ("A", parents, instruction, None)
        elif action == "extract":
            route = ("E", parents, instruction, dict(role="A", step=step))
        elif action in {"finish", "deliver", "wait"}:
            route = (None, [], "", None)
        else:
            raise ValueError("Invalid sealed A route")
    elif role == "E":
        target = job["evidence_return"]
        if (not isinstance(target, dict) or target.get("role") not in {"H", "A"}
                or type(target.get("step")) is not int or not 0 < target["step"] < step):
            raise ValueError("Extraction has no exact scientific requester")
        requester = handoff_state.read_sealed(root, target["step"])
        if requester["role"] != target["role"] or requester["next_action"] != "extract":
            raise ValueError("Extraction requester identity differs")
        route = (target["role"], _parents(current_h, current_w, target["step"], step),
                 requester["route_reason"], None)
    else:
        raise ValueError("Unknown scientific role")
    return (*route, current_h, current_w)


def _terminal_success(root, record):
    """Known terminal turn, including diagnosed missing output, never unknown work."""
    try:
        outcome = contracts.native_outcome(record)
        final = None
        if outcome.get('final_a_completion') is not None:
            step = int(record.parents[1].name) if record.parent.name == 'continuations' else int(record.name)
            job = contracts.read(root / 'jobs' / f'{step:03d}.json')
            intent = contracts.read(record / 'native-intent.json')
            final = budget.validate_final_a_receipt(root, job, intent, outcome)
        if (type(outcome.get('returncode')) is not int or outcome['returncode'] != 0
                or outcome.get('stop_reason')
                or budget.final_a_usage_unknown(final, outcome['usage']) and not budget.native_completion_known(outcome, contracts.settings(root))):
            return False
        raw, pending = record / 'science-output.json', record / 'result.pending.json'
        if outcome.get('output_status') == 'missing':
            return (outcome.get('raw_ref') is None and outcome.get('cleanup') == 'observed_group_cleaned'
                    and not raw.exists() and not raw.is_symlink() and not pending.exists() and not pending.is_symlink())
        candidate = raw if raw.exists() else pending
        return outcome.get('raw_ref') == dict(path=str(raw),
            size_bytes=contracts.regular(candidate).stat().st_size)
    except (KeyError, ValueError, OSError, TypeError):
        return False


def _seal_reason(error):
    return str(error) if isinstance(error, ValueError) else type(error).__name__


def _step_numbers(root, first_step):
    """Re-read amended ceilings only at the boundary before a new task."""
    from . import control_operations
    step = first_step
    while True:
        if (not (root / 'jobs' / f'{step:03d}.json').exists()
                and not (root / 'pause.request').exists() and not budget.execution_blocked(root)):
            control_operations.apply_pending_budget(root)
            configuration_updates.bind_new_job(root, step)
        from . import fork_runtime
        if not fork_runtime.is_fork(root) and step > budget.historical_step_limit(root):
            return
        yield step
        step += 1


def _wait_continuation_target(root):
    """Capture the last exact wait while the caller owns the controller writer."""
    paths = list((root / 'jobs').glob('[0-9]*.json'))
    if not paths:
        return None
    path = max(paths, key=lambda path: int(path.stem))
    job = contracts.read(contracts.regular(path))
    step = job['step']
    record = _record(root, step)
    identity = record / 'result-identity.json'
    if not identity.exists() or job['role'] != 'A' or job.get('purpose') or job.get('formal_review'):
        return None
    result = contracts.read(contracts.regular(record / 'result.json'))
    if result.get('next_action') != 'wait':
        return None
    return dict(source_step=step, seal_ref=contracts.record_ref(contracts.regular(identity)))


def _continue_wait(root, step, job, result, next_role, target):
    """Retain a single explicit boundary; ordinary reconstruction never adds one."""
    if (next_role is not None or job['role'] != 'A' or result.get('next_action') != 'wait'
            or job.get('purpose') or job.get('formal_review')):
        return False
    path = root / 'host-control' / 'wait-continuations' / f'{step:03d}.json'
    expected = dict(source_step=step,
        seal_ref=contracts.record_ref(contracts.regular(_record(root, step) / 'result-identity.json')))
    if path.exists():
        if contracts.read(contracts.regular(path)) != expected:
            raise ValueError('Wait continuation differs from its exact sealed source')
        return True
    if target != expected or (root / 'jobs' / f'{step + 1:03d}.json').exists():
        return False
    observed = native.usage(root)
    if observed.get('blocking_unknown_steps', observed['unknown_steps']) or budget.execution_blocked(root):
        return False
    contracts.write(path, expected)
    return True


def _save_route(root, completed, route, current_h, current_w, *, resumed):
    """Publish accepted routing facts once; cold loading never renews the gates."""
    contracts.write(Path(root) / 'host-history/controller-route.json', dict(
        version=1, goal_id=contracts.settings(root)['goal_id'],
        source=dict(step=completed, seal_ref=dict(path=str(Path(root)/'native-evidence'/f'{completed:03d}'/'result-identity.json'))),
        completed=completed, current_h=current_h, current_w=current_w,
        resume_seq=(resumed or {}).get('seq'),
        route=dict(zip(('role', 'parents', 'instruction', 'evidence_return',
            'correction_request', 'author_correction'), route))))


def _saved_route(root, *, first_step, resumed):
    path=Path(root) / 'host-history/controller-route.json'
    if not path.exists():return None
    value=contracts.read(contracts.regular(path))
    if value.get('resume_seq')!=(resumed or {}).get('seq'):
        return None
    completed=value.get('completed')
    if (value.get('version')!=1 or type(completed) is not int or completed<1
            or value.get('goal_id')!=contracts.settings(root)['goal_id']
            or value.get('source')!=dict(step=completed,seal_ref=dict(path=str(
                Path(root)/'native-evidence'/f'{completed:03d}'/'result-identity.json')))):
        raise ValueError('Saved controller route lost its exact instance or source seal')
    if completed<first_step:return None
    # Only the latest accepted seal is read. Its predecessor's originals and
    # scientific gates remain closed, including when source files are absent.
    handoff_state.read_sealed(root,value['completed'])
    return value


def _terminal_route(root, settings, step, result, role):
    if role == 'H' and result.get('next_action') == 'wait' and not budget.execution_blocked(root):
        return _start_budget_closeout(root, step, step + 1, 'H', 'scientific_wait')
    if settings.get('delivery_policy') is not None and result.get('next_action') == 'deliver':
        return _start_closeout(root, step, step + 1, 'scientific_deliver', authorization_step=step)
    action = 'finish' if result.get('next_action') == 'deliver' else result['next_action']
    return _finish(root, settings, action, step, result,
        reason='Scientific role suggestion; overall goal success is not asserted')


def _run(root, *, continue_wait=None):
    from .protocol import require_current
    require_current(root)
    from . import accepted_prefix
    accepted_prefix.reconcile(root)
    from . import candidate_audit
    candidate_audit.settle_preflights(root)
    from . import fork_runtime
    fork = fork_runtime.bootstrap(root)
    if fork is not None:
        fork_runtime.ensure_phase(root, fork)
    settings = configuration_updates.current(root)
    started_path = root / "host-started.json"
    if not started_path.exists():
        contracts.write(started_path, budget.clock_stamp())
    state = budget.closeout_state(root)
    if state['begin'] is not None:
        if _resume_final(root,state['begin']):
            return _run(root)
        result = _run_closeout(root)
        if _resume_final(root,state['begin']):
            return _run(root)
        return result
    final = budget.final_a_barrier(root)
    if final is not None:
        return _collect_final_a(root, final)
    role, parents, instruction, evidence_return, correction_request = "H", [], "", None, None
    author_correction = None
    current_h = current_w = last = None
    completed, first_step = 0, 1
    if fork is not None:
        first_step = fork['next_step']
        route = fork['route']
        role, parents, instruction, evidence_return, correction_request, author_correction = (
            route[key] for key in ('role', 'parents', 'instruction', 'evidence_return', 'correction_request', 'author_correction'))
        def source_number(ref):
            return int(Path(ref['path']).stem) if ref is not None else None
        current_h, current_w = source_number(fork['current_h']), source_number(fork['current_w'])
        completed = max((source_number(ref) for ref in fork['completed_source_refs']), default=0)
    resumed = budget.normal_resume(root)
    if resumed is not None:
        cursor = budget.resume_cursor(root,resumed)
        role,parents,instruction,evidence_return,correction_request,author_correction = (
            cursor[key] for key in ('role','parents','instruction','evidence_return','correction_request','author_correction'))
        source_steps = [ref['step'] for ref in cursor['source_refs']]
        completed = max(source_steps,default=0)
        last = handoff_state.read_sealed(root, completed) if completed else None
        for number in source_steps:
            source_role = fork_runtime.source_job(root, number)['role']
            if source_role == 'H': current_h = number
            if source_role == 'W': current_w = number
        first_step = resumed['first_step']
    saved = _saved_route(root, first_step=first_step, resumed=resumed)
    if saved is None:
        from . import accepted_budget
        head=accepted_budget.read(root)
        slot=head['current_job']
        if slot is not None and slot['step']>=first_step:
            job=contracts.read(contracts.regular(root/'jobs'/f'{slot["step"]:03d}.json'))
            role=job['role']
            parents=[int(Path(ref['path']).parts[2]) for ref in job['direct_refs']]
            instruction=job.get('instruction','')
            evidence_return=job.get('evidence_return')
            correction_request=job.get('correction_request')
            author_correction=job.get('author_correction')
            current_h=head['search']['last_h']
            current_w=head['search']['last_w']
            completed=head['prefix']['tip']['step'] if head['prefix']['tip'] is not None else 0
            last=handoff_state.read_sealed(root,completed) if completed else None
            first_step=slot['step']
    if saved is not None:
        completed=saved['completed'];current_h=saved['current_h'];current_w=saved['current_w']
        last=handoff_state.read_sealed(root,completed)
        cursor=saved['route']
        role,parents,instruction,evidence_return,correction_request,author_correction = (
            cursor[key] for key in ('role','parents','instruction','evidence_return','correction_request','author_correction'))
        first_step=completed+1
        if role is None:
            job=contracts.read(root/'jobs'/f'{completed:03d}.json')
            if _continue_wait(root,completed,job,last,None,continue_wait):
                role='A';parents=_parents(current_h,current_w,completed)
                instruction='The operator explicitly requested continuation from the retained wait. Reassess the original goal, evidence and remaining limitations before choosing the next scientific route.'
                # This explicit continuation is a new admission boundary. The
                # saved terminal route stays closed; only its proposed new A
                # spends today's role allowance, with the repeat waiver.
                admitted=role_share.effective_route(root,completed,last,job,
                    (role,parents,instruction,evidence_return,current_h,current_w,correction_request,author_correction),
                    allow_repeat=True)
                route,share_stop=admitted[:-1],admitted[-1]
                if share_stop is not None:
                    if budget.execution_blocked(root):
                        return _finish(root,settings,'waiting_execution',completed,last,
                            reason='Original execution remains live or unknown; optional admission refused: '+share_stop,
                            next_role='H')
                    route=role_share.reconsider(root,completed,job,route,share_stop)
                role,parents,instruction,evidence_return,current_h,current_w,correction_request,author_correction=route
                _save_route(root,completed,(role,parents,instruction,evidence_return,correction_request,author_correction),
                    current_h,current_w,resumed=resumed)
            else:
                return _terminal_route(root,settings,completed,last,job['role'])
    def start_final(cutoff, step, blocked_role, reason, *, pending=None):
        route = pending if pending is not None else (role,parents,instruction,evidence_return,correction_request,author_correction)
        cursor = sources = None
        if route[0] is not None:
            sources = history.source_sequence(root,cutoff+1)
            cursor = dict(zip(('role','parents','instruction','evidence_return','correction_request','author_correction'),route))
            cursor['source_refs'] = [dict(step=job['step'],seal_ref=seal_sha) for job,_,_,seal_sha in sources]
        return _start_budget_closeout(root,cutoff,step,blocked_role,reason,resume_cursor=cursor,sources=sources)
    for step in _step_numbers(root, first_step):
        # Reserve pending edits once at a new-node boundary, before admission.
        settings = configuration_updates.for_new_job(root, step)
        record = _record(root, step)
        work = contracts.workspace(root, step)
        identity = record / "result-identity.json"
        successor_path=root/'jobs'/f'{step+1:03d}.json'
        if identity.exists() and successor_path.exists():
            # The frozen successor records the routing decision already made
            # for this seal, including correction/refusal outcomes. Recover that
            # saved fact without replaying the author's historical gates.
            job=contracts.read(root/'jobs'/f'{step:03d}.json')
            successor=contracts.read(contracts.regular(successor_path))
            if job['role']=='H':current_h=step
            if job['role']=='W':current_w=step
            completed=step;last=handoff_state.read_sealed(root,step)
            role=successor['role']
            parents=[int(Path(ref['path']).parts[2]) for ref in successor['direct_refs']]
            instruction=successor.get('instruction','')
            evidence_return=successor.get('evidence_return')
            correction_request=successor.get('correction_request')
            author_correction=successor.get('author_correction')
            _save_route(root,completed,(role,parents,instruction,evidence_return,correction_request,author_correction),
                current_h,current_w,resumed=resumed)
            continue
        if not identity.exists():
            retained = any(path.exists() for path in (record / "native-intent.json",
                record / "native-outcome.json", record / "science-output.json", record / "result.json"))
            if retained:
                budget.transition(root, 'advance', 'recover_original')
                job_path = root / 'jobs' / f'{step:03d}.json'
                if not job_path.exists():
                    return _finish(root, settings, "native_outcome_unsealed", completed, last,
                        reason="The original scientific job is missing; retained work is preserved without replay", next_role=role)
                retained_job = contracts.read(job_path)
                if (runtime.node_state(root, step) in {"running","waiting","terminal","interrupted","unstarted"} or checkpoint.enabled(root, retained_job)
                        or worker_delivery.applicable(retained_job)):
                    try:
                        _native_call(root, step)
                    except managed_wait.ExecutionWaiting as error:
                        return _finish(root,settings,'waiting_execution',completed,last,reason=error.reason,next_role=role)
                    except continuation.ResumeUnavailable as error:
                        return _finish(root, settings, _recovery_status(error), completed, last, reason=str(error), next_role=role)
                    except OutputRepairRequired as error:
                        return _finish(root, settings, 'output_repair_required', completed, last, reason=error.code, next_role=role)
                    except budget.NodeBudgetUnavailable as error:
                        if _budget_denied(error.reason):
                            return start_final(completed, step, role, error.reason)
                        return _finish(root, settings, error.reason, completed, last, next_role=role)
                    except Exception as error:
                        if not _terminal_success(root, execution.record(root, step)):
                            return _finish(root, settings, "native_outcome_unsealed", completed, last,
                                reason="Original execution lost its verified completion; preserved without replay",
                                next_role=role)
                        return _finish(root, settings, "seal_pending", completed, last,
                            reason=f"Native turn completed; host sealing requires repair: {_seal_reason(error)}",
                            next_role=role)
                if not _terminal_success(root, execution.record(root, step)):
                    return _finish(root, settings, "native_outcome_unsealed", completed, last,
                        reason="Retain the original native work; its terminal scientific output is unverified",
                        next_role=role)
                try:
                    _check_job(root, settings, step, role, parents, instruction, evidence_return, correction_request, author_correction)
                    if not (execution.origin(root,step)/'result-identity.json').exists():
                        continuation.seal_or_repair(root,step,inventory=contracts.accept_current_inventory(root,step))
                except managed_wait.ExecutionWaiting as error:
                    return _finish(root,settings,'waiting_execution',completed,last,reason=error.reason,next_role=role)
                except continuation.ResumeUnavailable as error:
                    return _finish(root, settings, _recovery_status(error), completed, last, reason=str(error), next_role=role)
                except OutputRepairRequired as error:
                    return _finish(root, settings, 'output_repair_required', completed, last, reason=error.code, next_role=role)
                except budget.NodeBudgetUnavailable as error:
                    if _budget_denied(error.reason):
                        return start_final(completed, step, role, error.reason)
                    return _finish(root, settings, error.reason, completed, last, next_role=role)
                except Exception as error:
                    return _finish(root, settings, "seal_pending", completed, last,
                        reason=f"Native turn completed; host sealing requires repair: {_seal_reason(error)}",
                        next_role=role)
        if not identity.exists():
            # Accepted originals above a later reduced ceiling were collected
            # above; no new native turn may consume that historical allowance.
            if not budget.task_step_available(root, step):
                return start_final(completed, step, role, 'bounded_steps_exhausted')
            stopped = correction.gate(root, completed)
            if stopped is not None:
                return _finish(root, settings, 'correction_paused', completed, last,
                    reason=stopped['reason'], next_role=role, correction_trigger=stopped)
            if (root / "pause.request").exists():
                return _finish(root, settings, "paused", completed, last,
                    reason="User pause.request prevents further native launches", next_role=role)
            if (author_correction is not None and author_correction['reason']!='formal_review_unavailable'
                    and role in {'H', 'A'} and semantic.accepted(root, author_correction, step) >= semantic.MAX_AUTHOR_CORRECTIONS):
                return _finish(root, settings, 'semantic_correction_required', completed, last,
                    reason='semantic_correction_limit', next_role=role)
            if role=='W' and parents:
                pending_repair=engineering.request(root,max(parents))
                if pending_repair is not None and not _repair_funds(root):
                    return start_final(completed, step, role, 'engineering_repair_budget_unavailable')
            reserve_stop = budget.exploration_admission(root, dict(step=step, role=role))
            if reserve_stop:
                return start_final(completed, step, role, reserve_stop)
            budget.transition(root, 'advance', 'controller_launch_boundary')
            limit = _launch_limit(root, settings)
            if limit:
                return start_final(completed, step, role, limit)
            node_limit = budget.node_admission(root, dict(step=step, role=role))
            if node_limit:
                if _budget_denied(node_limit):
                    return start_final(completed, step, role, node_limit)
                return _finish(root, settings, node_limit, completed, last, next_role=role)
            if (root / 'jobs' / f'{step:03d}.json').exists():
                try:
                    _check_job(root, settings, step, role, parents, instruction, evidence_return,
                               correction_request, author_correction)
                except ValueError:
                    return _finish(root, settings, 'retained_job_conflict', completed, last,
                        reason='A prepared task differs from the exact retained scientific route', next_role=role)
            try:
                if fork is not None and step == fork['next_step']:
                    job = fork_runtime.build_selected_job(root, step)
                else:
                    job = contracts.build_job(root, step, role, parents, instruction=instruction,
                        evidence_return=evidence_return, correction_request=correction_request,
                        **(dict(author_correction=author_correction) if author_correction is not None else {}))
            except engineering.RepairUnavailable as error:
                return _finish(root,settings,str(error),completed,last,next_role=role)
            search_limit = search.launch_reason(root, job)
            if search_limit:
                return _finish(root, settings, 'search_pending', completed, last,
                    reason=search_limit, next_role=role)
            # Job creation is reversible; check again at the actual launch boundary.
            if (root / "pause.request").exists():
                return _finish(root, settings, "paused", completed, last, next_role=role)
            limit = _launch_limit(root, settings)
            if limit:
                return start_final(completed, step, role, limit)
            try:
                _native_call(root, step, launch=True)
            except managed_wait.ExecutionWaiting as error:
                return _finish(root,settings,'waiting_execution',completed,last,reason=error.reason,next_role=role)
            except budget.NodeBudgetUnavailable as error:
                if _budget_denied(error.reason):
                    return start_final(completed, step, role, error.reason)
                return _finish(root, settings, error.reason, completed, last, next_role=role)
            except continuation.ResumeUnavailable as error:
                return _finish(root, settings, _recovery_status(error), completed, last, reason=str(error), next_role=role)
            except OutputRepairRequired as error:
                return _finish(root, settings, 'output_repair_required', completed, last, reason=error.code, next_role=role)
            except Exception as error:
                if _terminal_success(root, execution.record(root, step)):
                    return _finish(root, settings, "seal_pending", completed, last,
                        reason=f"Native turn completed; host sealing requires repair: {_seal_reason(error)}",
                        next_role=role)
                if (record / 'native-outcome.json').exists():
                    stopped = contracts.read(record / 'native-outcome.json')
                    delivery = settings.get('delivery_policy')
                    known = not native.usage(root).get('blocking_unknown_steps', native.usage(root)['unknown_steps'])
                    reason = stopped.get('stop_reason', '')
                    token_report = reason in {'closeout_protected_token_limit', 'token_limit'}
                    known_budget_stop = (known and stopped.get('cleanup') == 'observed_group_cleaned'
                        and reason in {'closeout_protected_time_limit', 'wall_limit', 'token_limit', 'closeout_protected_token_limit'})
                    if budget.final_a_barrier(root) is None:
                        if token_report or known_budget_stop:
                            return start_final(completed, step, role, reason)
                        if delivery is not None and delivery['adapter'] is not None and (str(reason).startswith('closeout_protected_') or known_budget_stop):
                            if known_budget_stop:
                                budget.stopped_report_original(root, step)
                            return _start_closeout(root, completed, step + 1, reason)
                return _finish(root, settings, "native_stopped", completed, last,
                    reason=f"Native work did not seal ({type(error).__name__}); original intent is retained",
                    next_role=role)
            if not identity.exists():
                return _finish(root, settings, "native_outcome_unsealed", completed, last,
                    reason="Native work returned without a complete seal", next_role=role)
        try:
            # seal_result already closed terminal, task and current-input
            # verification. Loading the saved seal never renews those checks.
            job = contracts.read(root / 'jobs' / f'{step:03d}.json')
            result = handoff_state.read_sealed(root, step)
            if result["role"] != role:
                raise ValueError("Sealed result role differs from its job")
            final = _final_a_result(root, job, result)
            if final is not None:
                return final
            if role == 'A' and result.get('next_action') == 'audit':
                from . import candidate_audit
                audit_status = candidate_audit.execute(root, job, result)
                if audit_status is not None:
                    detail=(candidate_audit._outcome(root,step)['result'].get('diagnostic')
                        if audit_status=='candidate_audit_originals_unavailable' else None)
                    return _finish(root,settings,audit_status,step,result,reason=detail,next_role='A')
                receipt=candidate_audit._outcome(root,step)['result']
                if receipt['note'] == 'candidate_audit_reserve_unavailable':
                    return start_final(step, step+1, 'A', receipt['note'])
                repair=engineering.request(root,step)
                if repair is not None or receipt['status'] in {'engineering_error','unclassified'}:
                    if repair is None:
                        return _finish(root,settings,'candidate_audit_'+receipt['status'],step,result,reason=receipt['note'])
                    try:engineering.admit(root,repair,step+1)
                    except engineering.RepairUnavailable as error:
                        return _finish(root,settings,str(error),step,result)
            correction.publish(root, step)
            next_role, next_parents, next_instruction, next_return, current_h, current_w, next_correction = correction.effective_route(
                root, step, result, job, _route(root, step, role, result, job, current_h, current_w))
            next_role, next_parents, next_instruction, next_return, current_h, current_w, next_correction, next_author = semantic.effective_route(
                root, step, result, job, (next_role, next_parents, next_instruction, next_return, current_h, current_w, next_correction))
            wait_continued = _continue_wait(root, step, job, result, next_role, continue_wait)
            if wait_continued:
                next_role, next_parents = 'A', _parents(current_h, current_w, step)
                next_instruction = 'The operator explicitly requested continuation from the retained wait. Reassess the original goal, evidence and remaining limitations before choosing the next scientific route.'
            (next_role, next_parents, next_instruction, next_return, current_h, current_w, next_correction, next_author,
             share_stop) = role_share.effective_route(root, step, result, job,
                (next_role, next_parents, next_instruction, next_return, current_h, current_w, next_correction, next_author),
                allow_repeat=wait_continued)
        except (KeyError, ValueError, OSError, TypeError) as error:
            return _finish(root, settings, "sealed_node_invalid", completed, last,
                reason=f"Retained node is not eligible for routing ({type(error).__name__})", next_role=role)
        completed, last = step, result
        admission_refusal = share_stop or ((next_author or {}).get('reason') if (next_author or {}).get('reason') in role_share.REFUSALS else None)
        if admission_refusal is not None and budget.execution_blocked(root):
            return _finish(root, settings, 'waiting_execution', completed, last,
                reason='Original execution remains live or unknown; optional admission refused: ' + admission_refusal,
                next_role='H')
        if share_stop is not None:
            (next_role, next_parents, next_instruction, next_return, current_h, current_w,
             next_correction, next_author) = role_share.reconsider(root, step, job,
                (next_role,next_parents,next_instruction,next_return,current_h,current_w,next_correction,next_author),
                share_stop)
        _save_route(root,completed,(next_role,next_parents,next_instruction,next_return,next_correction,next_author),
            current_h,current_w,resumed=resumed)
        if next_role is None:
            return _terminal_route(root,settings,completed,last,role)
        role, parents, instruction, evidence_return, correction_request = next_role, next_parents, next_instruction, next_return, next_correction
        author_correction = next_author
    return start_final(completed, completed + 1, role, 'bounded_steps_exhausted')


def _closeout_accepted_slots(root, step):
    """Read the two saved candidate slots at one exact accepted cut."""
    if not step:
        return dict(actual_submission=None, a_recommended=None, latest_a_step=None)
    from . import candidate_refs, originals
    source = originals.node(root, step)
    seal = contracts.read(contracts.regular(originals.source_record(source, 'result-identity.json')))
    reference = seal['breadth_snapshot']
    owner = originals.source_for_ref(source, reference)
    summary = contracts.read(contracts.regular(originals.resolve_ref(owner, reference)))
    def candidate(identity):
        return candidate_refs.origin_candidate(owner.origin_identity, identity) if owner.inherited else identity
    actual = summary['actual_submission']
    if actual is not None:
        actual = dict(candidate_id=candidate(actual['candidate_id']), observed_at=actual['observed_at'],
            retention=(actual.get('artifact') or {}).get('retention', 'unknown'))
    recommendation = summary['a_recommended']
    if recommendation is not None:
        recommendation = dict(recommendation, candidate_id=candidate(recommendation['candidate_id']))
    return dict(actual_submission=actual, a_recommended=recommendation, latest_a_step=summary['latest_a_step'])


def _closeout_result(root, status, *, reason=None, step=None, read_only=False):
    state = budget.closeout_state(root)
    query_limits = budget.status_snapshot(root) if read_only else None
    begin = state['begin']
    all_actions = state['actions']
    if begin is not None and begin['mode'] == 'final':
        state = dict(state,actions=[row for row in all_actions if row['intent']['seq'] > begin['seq']])
    closed, pending = [], []
    last_science = None
    if begin is not None:
        from . import accepted_budget
        head = accepted_budget.read(root)
        # The phase owns this known closeout range. Earlier nodes already
        # belong to its cutoff; status never discovers the research history.
        for number in range(begin['first_step'], head['max_local_step'] + 1):
            path = root / 'jobs' / f'{number:03d}.json'
            if not path.exists():continue
            job = contracts.read(contracts.regular(path))
            if job.get('purpose') != 'closeout' and not job.get('final_only'):continue
            if (_record(root, number) / 'result-identity.json').exists():
                last_science = handoff_state.read_sealed(root, number)
                closed.append(number)
            elif job.get('purpose') == 'closeout':
                pending.append(number)
    cutoff = begin['source_cutoff'] if begin else 0
    previous = _closeout_accepted_slots(root, cutoff)
    latest_recommendation = previous['a_recommended']
    for number in closed:
        accepted = _closeout_accepted_slots(root, number)
        # A FINAL resume may exclude earlier FINAL nodes after this cutoff.
        # Consume only a slot actually updated by this selected closed node.
        if accepted['latest_a_step'] == number:
            latest_recommendation = accepted['a_recommended']
    screened = shortlist.summary(root) if configuration_updates.current(root).get('selection_policy') else None
    last = max(closed, default=cutoff) or None
    confirmed = [row['outcome']['result'] for row in all_actions if row['outcome'] is not None
                 and row['intent']['action_kind'] == 'confirm' and row['outcome']['result']['status'] == 'confirmed']
    last_action=state['actions'][-1] if state['actions'] else None
    last_outcome=last_action['outcome']['result'] if last_action and last_action['outcome'] is not None else None
    pending_confirmation=bool(last_action and (last_outcome is None or last_outcome['status']=='unknown'
        or last_action['intent']['action_kind']=='submit' and last_outcome['status']=='confirmed'))
    if not read_only:
        budget.inactive(root, status)
    if last_science is None and last is not None:
        last_science = handoff_state.read_sealed(root, last)
    if status == 'closeout_status':
        final = budget.final_a_barrier(root)
        if final is not None and last == final['step'] and (_record(root, last) / 'result-identity.json').exists():
            if state['unresolved_operations']:
                status = 'delivery_unknown'
            elif last_outcome is not None:
                status = ('delivery_confirmed' if last_action['intent']['action_kind'] == 'confirm' and last_outcome['status'] == 'confirmed' else
                          'delivery_confirmation_pending' if last_outcome['status'] == 'confirmed' else 'delivery_' + last_outcome['status'])
            else:
                status = 'closeout_unresolved' if last_science.get('next_action') == 'deliver' else 'final_a_completed'
    if status == 'closeout_status':
        from . import candidate_audit, delivery
        if begin['mode'] == 'export_only':
            status = 'exported_unsubmitted'
        elif state['unresolved_operations']:
            status = 'delivery_unknown'
        elif candidate_audit.blocked(root):
            status = 'candidate_audit_unknown'
        elif pending:
            step = pending[-1]
            status = ('closeout_running' if runtime.node_state(root, step) == 'running' else
                'closeout_native_unsealed' if (_record(root, step) / 'native-intent.json').exists() else 'closeout_pending')
        elif state['actions'] and state['actions'][-1]['intent']['decision']['step'] == last:
            row = state['actions'][-1]
            outcome = row['outcome']['result'] if row['outcome'] else None
            status = ('delivery_unknown' if outcome is None else
                'delivery_reselect' if outcome['status'] == 'rejected' and outcome.get('failure_phase') == 'audit' else
                'delivery_confirmation_pending' if outcome['status'] == 'confirmed' and row['intent']['action_kind'] == 'submit' else
                'delivery_' + outcome['status'])
        elif last in closed and last_science['role']=='W':
            status='engineering_repair_review_pending'
        elif last_science is not None and (last in closed or last == begin.get('authorization_step')):
            status = {'wait':'closeout_waiting', 'finish':'exported_unsubmitted', 'audit':'candidate_audit_pending',
                'deliver':'closeout_preparation_pending'}.get(last_science['next_action'], 'closeout_correction_pending')
            if last == begin.get('authorization_step') and configuration_updates.current(root).get('selection_policy') is not None:
                status = 'selection_comparison_pending'
        else:
            status = 'closeout_pending'
        if last_science is not None and last_science['role']=='A' and not pending and not state['unresolved_operations'] and not candidate_audit.blocked(root):
            from . import engineering
            repair=engineering.request(root,last)
            if repair is not None:
                try:
                    engineering.admit(root,repair,last+1)
                    status='engineering_repair_pending' if _repair_funds(root, limits=query_limits) else 'engineering_repair_budget_unavailable'
                except engineering.RepairUnavailable as error:status=str(error)
            elif last_science.get('next_action')=='audit':
                receipt=candidate_audit._outcome(root,last)
                if receipt is not None:
                    kind=receipt['result']['status']
                    status=('candidate_audit_originals_unavailable' if receipt['result']['note']=='candidate_audit_unchanged_originals_unavailable'
                        else 'candidate_audit_'+kind if kind in {'engineering_error','unclassified'} else 'candidate_audit_review_pending')
        if status in {'closeout_correction_pending', 'closeout_preparation_pending'}:
            errors = semantic.closeout_errors(root, (last or 0) + 1)
            if errors > semantic.MAX_AUTHOR_CORRECTIONS:
                status, reason = 'closeout_unresolved', 'semantic_correction_limit'
            elif status == 'closeout_preparation_pending':
                preparation = delivery.preparation_status(root)
                if preparation is not None and preparation['candidate_id'] == last_science.get('candidate_recommendation'):
                    status, reason = 'closeout_preparation_unavailable', preparation['last_failure']
        if status in {'closeout_pending', 'closeout_correction_pending', 'candidate_audit_pending', 'delivery_reselect', 'selection_comparison_pending','engineering_repair_review_pending','candidate_audit_review_pending'} and not _closeout_funds(
                root, preparation_seconds=begin['preparation_seconds'], step=step, limits=query_limits):
            status = 'closeout_budget_unavailable'
    scientific_output_ref = str(execution.raw(root,last)) if last is not None else None
    limitations = last_science['limitations'] if last_science is not None else []
    shown_limitations, _ = contracts._whole_limitations(limitations)
    shown_limitations = shown_limitations[:4]
    result = dict(status=status, goal_completion='not_asserted', objective_ref=str(root / 'project/global/objective.md'),
        scientific_output_ref=scientific_output_ref, scientific_limitations=shown_limitations,
        limitations_omitted_count=len(limitations)-len(shown_limitations),
        limitations_ref=dict(path=scientific_output_ref,field='limitations') if scientific_output_ref is not None else None,
        source_cutoff=cutoff, last_step=last, completed_scientific_nodes=cutoff + len(closed),
        used_steps=budget.used_steps(root), usage=native.usage(root),
        budget=query_limits if read_only else budget.snapshot(root),
        previous_submission_observation=previous['actual_submission'], existing_A_recommendation=latest_recommendation,
        last_confirmed=confirmed[-1] if confirmed else None,
        current_actual='unknown' if state['unresolved_operations'] or pending_confirmation else (confirmed[-1] if confirmed else None))
    if last is not None and (root / 'jobs' / f'{last:03d}.json').exists():
        # Inherited science supplies evidence, not a child completion/overrun.
        completion = contracts.read(execution.record(root, last) / 'native-outcome.json').get('final_a_completion')
        if completion is not None:
            result['final_overrun'] = completion['final_overrun']
    if begin is not None and begin['mode'] == 'final':
        result['submission_result'] = (last_outcome if last_outcome is not None else
            dict(status='unknown', note='Original submission requires same-operation reconciliation') if state['actions'] else
            dict(status='unsubmitted', note=reason or 'final_submission_not_accepted'))
    if screened is not None:
        result['control_selection'] = screened
    if reason is not None:
        result['reason'] = reason
    if step is not None:
        result['attempt_step'] = step
    return result


def _closeout_native_reason(root, step, fallback):
    """Explain a retained native failure without exposing private diagnostics."""
    record = execution.record(root, step)
    outcome = contracts.read(record / 'native-outcome.json') if (record / 'native-outcome.json').exists() else {}
    if type(outcome.get('returncode')) is int and outcome['returncode'] != 0:
        return 'Original closeout native failed; explicit worker-resume required for the same original task'
    return fallback


def _closeout_funds(root, *, preparation_seconds=0., step=None, author_required=True, limits=None):
    settings = configuration_updates.current(root)
    policy = settings.get('delivery_policy')
    if policy is None:
        return False
    meter = native.usage(root)
    limits = budget.snapshot(root) if limits is None else limits
    floor = budget.closeout_floor(settings, preparation_seconds=preparation_seconds)
    additional = int(author_required and not (step is not None and (root / 'jobs' / f'{step:03d}.json').exists()))
    author = policy['closeout_a'] if author_required else dict(max_observed_tokens=0,max_seconds=0)
    return (not meter.get('blocking_unknown_steps',meter['unknown_steps'])
        and limits['max_tokens'] - meter.get('accounted_tokens',meter['observed_tokens']) > 0
        and limits['seconds_remaining'] >= author['max_seconds'] + floor['seconds']
        and budget.effective_max_steps(root) - budget.used_steps(root) >= additional + floor['steps'])


def _final_a_result(root, job, result):
    """Collect one final author's science; only its reserved delivery may follow."""
    record = execution.record(root,job['step'])
    intent = contracts.read(contracts.regular(record / 'native-intent.json'))
    accepted = budget.final_a_return(root, job, intent)
    if accepted is None:
        return None
    settings = contracts.settings(root, job)
    begin = budget.closeout_state(root)['begin']
    policy = settings.get('delivery_policy')
    if result.get('next_action') != 'deliver' or policy is None or policy['adapter'] is None:
        from . import originals
        frozen=contracts.read(originals.resolve_root_ref(root,job['history']['breadth_snapshot']))
        has_worker = frozen['last_w'] is not None
        reason = ('accepted_worker_evidence_unavailable' if not has_worker else
                  'delivery_unavailable' if policy is None or policy['adapter'] is None else 'candidate_originals_unavailable')
        if begin is not None:
            return _closeout_result(root, 'final_a_completed', reason=reason, step=job['step'])
        return _finish(root, settings, 'final_a_completed', job['step'], result, reason=reason)
    if begin is None:
        return _start_closeout(root, job['step'], job['step'] + 1, 'final_a_delivery', authorization_step=job['step'])
    from . import delivery
    try:
        delivery.prepare_binding(root, job, result)
    except (delivery.PreparationUnavailable, delivery.DeliveryUnresolved) as error:
        return _closeout_result(root, 'closeout_unresolved', reason=error.code, step=job['step'])
    return _run_delivery_actions(root, job, result)


def _resume_final(root, begin):
    if begin['mode'] != 'final' or begin.get('resume_cursor') is None:
        return False
    events,_ = budget._events(root)
    raised = budget._threshold_raised(events,begin['seq'])
    if not raised or budget.execution_blocked(root) or _launch_limit(root,contracts.settings(root)) is not None:
        return False
    if (root/'pause.request').exists() or correction.gate(root,begin['source_cutoff']) is not None:
        return False
    record = _record(root,begin['first_step'])
    if (record/'native-intent.json').exists() and not (record/'result-identity.json').exists():
        return False  # Finish/collect this exact original A first.
    state = budget.closeout_state(root)
    actions = [row for row in state['actions'] if row['intent']['seq'] > begin['seq']]
    if state['unresolved_operations'] or actions and (actions[-1]['outcome'] is None or
            actions[-1]['intent']['action_kind'] == 'submit' and actions[-1]['outcome']['result']['status'] == 'confirmed'):
        return False
    from . import accepted_budget
    first = max(begin['first_step'],accepted_budget.read(root)['max_local_step'])+1
    cursor = begin['resume_cursor']
    if cursor['source_refs'] and cursor['author_correction'] is None:
        reference = cursor['source_refs'][-1]
        source_step = reference['step']
        source_job, source_result, _, seal_ref = history._source(root, source_step)
        if seal_ref != reference['seal_ref']:
            raise ValueError('Final resume source seal identity changed')
        if role_share.reason(root, source_step, source_job, source_result, cursor['role'],
                cursor['correction_request'], allow_repeat=source_result.get('next_action') == 'wait') is not None:
            return False
    if not budget.task_step_available(root, first) or budget.node_admission(root,
            dict(step=first,role=cursor['role']),normal_resume_check=True) is not None:
        return False
    budget.resume_final(root,begin,first)
    return True


def _budget_denied(reason):
    return (reason in {'step_limit', 'closeout_budget_unavailable', 'bounded_steps_exhausted',
                      'engineering_repair_budget_unavailable', 'token_limit', 'wall_limit', 'usage_unknown'}
            or reason.startswith(('node_budget_unavailable_', 'closeout_reserve_', 'closeout_protected_')))


def _start_budget_closeout(root, cutoff, step, blocked_role, reason, *, resume_cursor=None, sources=None):
    """One durable FINAL phase, followed only by its exact A and host delivery."""
    from .protocol import require_settings_format
    require_settings_format(configuration_updates.current(root))
    begin = budget.closeout_state(root)['begin']
    if begin is not None and begin['mode'] == 'final':
        return _run_final(root, begin)
    if budget.final_a_barrier(root) is not None:
        return _collect_final_a(root, budget.final_a_barrier(root))
    stopped = None
    path = root / 'jobs' / f'{step:03d}.json'
    if path.exists():
        # Preserve every prepared or cleaned stopped original, including an A.
        # A FINAL assignment is explicit and never relabels its original job.
        try:
            stopped = budget.stopped_report_original(root, step)
        except (ValueError, OSError):
            return _closeout_result(root, 'exported_unsubmitted', reason='original_execution_unresolved', step=step)
        step += 1
    begin = budget.begin_closeout(root, cutoff, step, reason, final_only=True, stopped_original=stopped,resume_cursor=resume_cursor)
    return _run_final(root, begin, sources=sources)


def _start_final_report(root, cutoff, step, blocked_role, reason, *, stopped_step=None):
    return _start_budget_closeout(root, cutoff, stopped_step if stopped_step is not None else step, blocked_role, reason)


def _run_final(root, begin, *, sources=None):
    """FINAL cannot dispatch W/E, scientific audit, correction or another A."""
    from . import delivery
    settings = configuration_updates.current(root)
    final = budget.final_a_barrier(root)
    if final is not None:
        return _collect_final_a(root, final)
    if budget.execution_blocked(root):
        return _closeout_result(root, 'exported_unsubmitted', reason='original_execution_unresolved')
    if (root / 'pause.request').exists() or correction.gate(root, begin['source_cutoff']) is not None:
        return _closeout_result(root, 'exported_unsubmitted', reason='original_pause_requires_release')
    step = begin['first_step']
    from . import fork_runtime
    path = root / 'jobs' / f'{step:03d}.json'
    fork = fork_runtime.bootstrap(root)
    if fork is not None and step == fork['next_step']:
        fork_runtime.build_selected_job(root, step)
    if path.exists():
        job = contracts.read(path)
        if not job.get('final_only'):
            return _closeout_result(root, 'retained_job_conflict', reason='final_assignment_changed', step=step)
    else:
        from . import control_operations
        control_operations.apply_pending_budget(root)
        revision = configuration_updates.bind_new_job(root, step)
        prospective_job = dict(step=step, configuration_revision=revision)
        settings = configuration_updates.for_new_job(root, step)
        cursor = begin.get('resume_cursor')
        last_roles = {}
        after_cutoff = set()
        nodes = []
        if sources is None and cursor is None:
            # First construction without an accepted list owns this traversal.
            # A crash before publishing its job may repeat this attempt.
            sources = history.source_sequence(root, begin['source_cutoff'] + 1)
            # A retained stopped original may lie before first_step without
            # a seal. Only accepted closeout members extend the frozen cut.
            for number in range(begin['source_cutoff'] + 1, step):
                if (_record(root, number) / 'result-identity.json').exists():
                    sources.append(history._source(root, number))
        if sources is not None:
            members = ((job['step'],job['role'],seal_ref) for job,_,_,seal_ref in sources)
        else:
            from . import accepted_budget, accepted_search
            head = accepted_budget.read(root)
            def known_members():
                for reference in cursor['source_refs']:
                    number = reference['step']
                    metadata = accepted_budget.search_row(root, accepted_search.key('node',number), head=head)
                    if metadata is None:
                        raise ValueError('Final source has no accepted role metadata')
                    yield number, metadata['role'], reference['seal_ref']
            members = known_members()
        for number, source_role, seal_ref in members:
            last_roles[source_role] = number
            if number > begin['source_cutoff']:
                after_cutoff.add(number)
            nodes.append(dict(step=number, seal_ref=seal_ref))
        has_worker = 'W' in last_roles
        # Keep all accepted closeout science in the exact final history.
        parents = sorted(set(last_roles.values()) | after_cutoff)
        instruction = ('This is FINAL. Give the final scientific conclusion from accepted existing W evidence, '
            'make your explicit selection, and explain all unmet objectives and missing work in scientific_result and limitations. '
            'No new scientific role, experiment, audit, wait or correction is authorized. '
            'When existing candidate originals and delivery permission are available, use deliver and select an exact candidate; '
            'control performs the original host submission and supplies its actual confirmed, failed or unknown receipt. '
            'Never manufacture submission success. If originals or delivery permission are absent, return finish with the explicit unsubmitted reason.')
        policy = settings.get('delivery_policy')
        sources = None
        if not has_worker:
            instruction += ' No accepted W evidence exists: give the explicit missing-input conclusion and unsubmitted reason without inventing scientific results.'
        # Execution safety was checked above. Billing uncertainty cannot remove
        # accepted W originals or the final author's submission choice.
        if has_worker and policy is not None and policy['adapter'] is not None:
            try:
                sources = delivery.catalogue_sources(root, nodes, job=prospective_job)
            except delivery.DeliveryUnresolved as error:
                instruction += ' Candidate originals are unavailable: ' + error.code + '.'
        try:
            if sources is not None:
                if settings.get('selection_policy') is not None:
                    stopped = shortlist.screen(root, begin, before_step=step)
                    if stopped is not None:
                        return _closeout_result(root, 'exported_unsubmitted', reason=stopped, step=step)
                job = contracts.build_job(root, step, 'A', parents, instruction=instruction, purpose='closeout',
                    source_cutoff=begin['source_cutoff'], delivery_sources=sources)
            else:
                job = contracts.build_job(root, step, 'A', parents, instruction=instruction,
                    final_report=dict(source_cutoff=begin['source_cutoff'], stopped_original=begin.get('stopped_original')))
        except (ValueError, OSError) as error:
            return _closeout_result(root, 'exported_unsubmitted', reason=str(error), step=step)
    budget.transition(root, 'advance', 'final_author')
    try:
        record = _record(root, step)
        if (record / 'native-intent.json').exists():
            _native_call(root, step)
        else:
            _native_call(root, step, launch=True)
    except (budget.NodeBudgetUnavailable, continuation.ResumeUnavailable, OutputRepairRequired, RuntimeError, ValueError) as error:
        return _closeout_result(root, 'closeout_native_unsealed', reason=str(error), step=step)
    result=_final_a_result(root,job,handoff_state.read_sealed(root, step))
    if result is None:
        return _closeout_result(root,'closeout_native_unsealed',reason='final_return_not_authorized',step=step)
    return result


def _collect_final_a(root, accepted):
    """Restore only the accepted original, bypassing earlier routing decisions."""
    step = accepted['step']
    record = _record(root, step)
    job = contracts.read(contracts.regular(root / 'jobs' / f'{step:03d}.json'))
    if not (record / 'result-identity.json').exists():
        try:
            if (record / 'native-intent.json').exists():
                # The ordinary collector owns exact reentry qualification as
                # well as terminal checks; an outer state gate loses accepted
                # interrupted and not-yet-started continuation originals.
                _native_call(root, step)
            else:
                _native_call(root, step, launch=True)
        except (budget.NodeBudgetUnavailable, continuation.ResumeUnavailable, OutputRepairRequired, RuntimeError, ValueError) as error:
            if budget.closeout_state(root)['begin'] is not None:
                return _closeout_result(root, 'closeout_native_unsealed', reason=str(error), step=step)
            return _finish(root, contracts.settings(root), 'native_outcome_unsealed', step-1, None,
                           reason=str(error), next_role='A')
    return _final_a_result(root, job, handoff_state.read_sealed(root, step))


def _start_closeout(root, cutoff, first_step, reason, *, export_only=False, authorization_step=None):
    from . import delivery
    if budget.closeout_state(root)['begin'] is not None:
        return _run_closeout(root)
    if not export_only and authorization_step is None:
        return _start_budget_closeout(root, cutoff, first_step, 'A', reason)
    if (root / 'jobs' / f'{first_step:03d}.json').exists():
        return _closeout_result(root, 'retained_job_conflict', reason='Closeout cannot replace a prepared scientific task', step=first_step)
    if budget.execution_blocked(root):
        return _closeout_result(root, 'closeout_blocked', reason='Original execution remains live or unknown')
    if (root / 'pause.request').exists() or correction.gate(root, cutoff) is not None:
        return _closeout_result(root, 'closeout_blocked', reason='Original pause requires its existing release or exact ack')
    # The cutoff consists of accepted seals; admission consumes their saved facts.
    settings = configuration_updates.current(root)
    policy = settings.get('delivery_policy')
    configured = not export_only and policy is not None and policy['adapter'] is not None
    use_model = configured
    sources, preparation = None, 0.
    if use_model:
        budget.transition(root, 'advance', 'prepare_closeout_inputs')
        at = budget.snapshot(root)['advance_seconds']
        nodes = [dict(step=step, seal_ref=contracts.record_ref(_record(root, step) / 'result-identity.json'))
                 for step in range(1, cutoff + 1)]
        try:
            sources = delivery.catalogue_sources(root, nodes)
        except delivery.DeliveryUnresolved as error:
            # No accepted external effect or immutable boundary exists yet.
            # Retain the failure without converting a transient preparation
            # problem into permanent operator export authorization.
            delivery.preparation_failure(root, error.code, nodes)
            return _closeout_result(root, 'closeout_preparation_unavailable', reason=error.code)
        preparation = budget.snapshot(root)['advance_seconds'] - at
        use_model = (authorization_step is not None
                     or _closeout_funds(root, preparation_seconds=preparation, author_required=authorization_step is None))
    budget.begin_closeout(root, cutoff, first_step, reason, export_only=not configured,
                          delivery_sources=sources, preparation_seconds=preparation, authorization_step=authorization_step)
    if not use_model:
        return _closeout_result(root, 'closeout_budget_unavailable' if configured else 'exported_unsubmitted',
            reason='closeout_budget_unavailable' if configured else 'export_only')
    return _run_closeout(root)


def _run_delivery_actions(root, job, result):
    from . import delivery
    state = budget.closeout_state(root)
    policy = contracts.settings(root, job)['delivery_policy']
    if policy['adapter'] is None:
        return _closeout_result(root, 'exported_unsubmitted', reason='No external delivery adapter was configured')
    actions = [row for row in state['actions'] if row['intent']['decision']['step'] == job['step']]
    if actions:
        original = actions[0]['intent']
        completed = actions[-1]['outcome']
        if completed is not None and completed['result']['status'] == 'confirmed' and actions[-1]['intent']['action_kind'] == 'confirm':
            return _closeout_result(root, 'delivery_confirmed')
        if completed is not None and completed['result']['status'] in {'failed', 'rejected', 'engineering_error', 'unclassified'} and completed['result']['cleanup_confirmed']:
            return _closeout_result(root, 'delivery_reselect' if budget.final_a_barrier(root) is None
                and completed['result']['status']=='rejected' else 'delivery_' + completed['result']['status'], reason=completed['result']['note'])
        kind, binding = 'confirm', original['binding']
        if budget.execution_blocked(root, ignore_operation=binding['operation_id']):
            return _closeout_result(root, 'delivery_unknown', reason='Original operation remains live or unverified; never resubmit')
    else:
        try:
            binding = delivery.binding(root, job, result)
        except delivery.DeliveryUnresolved as error:
            return _closeout_result(root, 'closeout_unresolved', reason=error.code)
        kind = 'submit'
    successor = root / 'jobs' / f'{job["step"]+1:03d}.json'
    if (not actions and budget.final_a_barrier(root) is None
            and successor.exists() and contracts.read(successor).get('role') == 'A'
            and contracts.read(successor).get('purpose') == 'closeout'):
        return _closeout_result(root, 'closeout_budget_unavailable', reason='final_author_required')
    while True:
        try:
            intent = budget.accept_action(root, kind, binding['operation_id'], binding['decision'], binding,
                                          policy['submit_seconds' if kind == 'submit' else 'confirm_seconds'])
        except budget.NodeBudgetUnavailable:
            meter = native.usage(root)
            accepted_actions = [row for row in budget.closeout_state(root)['actions']
                                if row['intent']['decision'] == binding['decision']]
            if (not accepted_actions and budget.final_a_barrier(root) is None
                    and not meter.get('blocking_unknown_steps', meter['unknown_steps'])
                    and not budget.execution_blocked(root)):
                return _closeout_result(root, 'closeout_budget_unavailable', reason='final_author_required')
            if budget.final_a_barrier(root) is not None and accepted_actions:
                return _closeout_result(root, 'delivery_unknown', reason='Original operation is unverified; final reconciliation allowance exhausted')
            return _closeout_result(root, 'closeout_budget_unavailable')
        budget.transition(root, 'advance', 'delivery_' + kind)
        remaining = (intent['max_seconds'] if intent.get('final_return') is not None else
                     min(intent['max_seconds'], budget.snapshot(root)['seconds_remaining']))
        reply = delivery.invoke(root, intent, remaining,
            on_process=lambda phase, identity: budget.action_process(root, intent, phase, identity))
        budget.action_outcome(root, intent, reply)
        if reply['status'] != 'confirmed':
            status = ('delivery_reselect' if budget.final_a_barrier(root) is None and reply['status'] == 'rejected'
                and reply.get('failure_phase') == 'audit' and reply['cleanup_confirmed'] else 'delivery_' + reply['status'])
            return _closeout_result(root, status, reason=reply['note'])
        if kind == 'confirm':
            return _closeout_result(root, 'delivery_confirmed')
        kind = 'confirm'  # First confirmation is explicitly reserved and accepted once.


def _repair_funds(root, *, limits=None):
    settings=configuration_updates.current(root)
    limits=budget.snapshot(root) if limits is None else limits
    meter=native.usage(root)
    if budget.execution_blocked(root) or meter.get('blocking_unknown_steps',meter['unknown_steps']):return False
    worker=(settings.get('node_budget_policy') or {}).get('W')
    if worker is None:return False
    available=budget.execution_available(root,dict(role='W'),limits,meter)
    budget.node_mode(root)
    return (available['tokens']>0 and available['seconds']>0
        and budget.effective_max_steps(root)-budget.used_steps(root)>=1)


def _run_direct_delivery(root, begin):
    """Consume only the ordinary A's exact choice; failure grants no new science."""
    from . import delivery
    step=begin['authorization_step']
    job=contracts.read(root/'jobs'/f'{step:03d}.json')
    result=handoff_state.read_sealed(root, step)
    if job['role']!='A' or job.get('purpose') is not None or result['next_action']!='deliver' or step!=begin['source_cutoff']:
        raise ValueError('Original direct delivery authorization changed')
    try:
        delivery.prepare_binding(root,job,result)
    except delivery.PreparationUnavailable as error:
        return _closeout_result(root,'closeout_preparation_unavailable',reason=error.code,step=step)
    except delivery.DeliveryUnresolved as error:
        return _closeout_result(root,'closeout_unresolved',reason=error.code,step=step)
    delivered=_run_delivery_actions(root,job,result)
    if delivered['status']=='delivery_reselect':
        return _closeout_result(root,'delivery_rejected',reason=delivered.get('reason'),step=step)
    if delivered.get('reason')=='final_author_required':
        return _start_budget_closeout(root,begin['source_cutoff'],begin['first_step'],'A',
                                      'original_delivery_budget_unavailable')
    return delivered


def _run_closeout(root):
    from . import delivery,engineering
    begin = budget.closeout_state(root)['begin']
    if begin is None:
        raise ValueError('Closeout continuation has no immutable boundary')
    if begin['mode'] == 'final':
        return _run_final(root, begin)
    if begin['mode'] == 'export_only':
        return _closeout_result(root, 'exported_unsubmitted', reason=begin['stop_reason'])
    if (root / 'pause.request').exists() or correction.gate(root, begin['source_cutoff']) is not None:
        return _closeout_result(root, 'closeout_blocked', reason='Original pause remains effective')
    final = budget.final_a_barrier(root)
    if final is not None:
        return _collect_final_a(root, final)
    if begin.get('authorization_step') is not None:
        return _run_direct_delivery(root, begin)
    initial_repair=None
    first_job=root/'jobs'/f'{begin["first_step"]:03d}.json'
    from . import fork_runtime
    fork = fork_runtime.bootstrap(root)
    selected_closeout = fork is not None and fork['next_step'] == begin['first_step']
    if selected_closeout:
        fork_runtime.build_selected_job(root, begin['first_step'])
    if first_job.exists():initial_repair=contracts.read(first_job).get('engineering_repair')
    closeout_settings = configuration_updates.for_new_job(root, begin['first_step'])
    if closeout_settings.get('selection_policy') and not first_job.exists():
        try:
            stopped = shortlist.screen(root, begin)  # Freeze candidate facts before the first closeout A.
        except delivery.DeliveryUnresolved as error:
            return _closeout_result(root, 'closeout_preparation_unavailable', reason=error.code)
        if stopped is not None:return _closeout_result(root,stopped)
    parents = []
    source_numbers = budget.scientific_steps(root, begin['source_cutoff'] + 1)
    for role in ('H', 'W', 'A'):
        matching = [step for step in source_numbers if fork_runtime.source_job(root, step)['role'] == role]
        if matching:
            parents.append(matching[-1])
    instruction = ('Choose a scientifically justified frozen candidate to retain or replace for delivery within the remaining authorization. '
        'Candidate availability is not scientific qualification. Declared required adapter audits gate delivery; absent optional audit/smoke capabilities provide no qualification. '
        'Do not perform or manufacture mechanical audit qualification. '
        'Use next_action=deliver to explicitly authorize adapter delivery and candidate_recommendation for its exact candidate. '
        'Use finish to decline delivery or wait to pause. Neither delivery nor finish asserts global goal completion. '
        'Explain science, limitations and unmet objectives. E only extracts scientific evidence; control resolves, packages and submits originals. '
        'Exploration is closed; continue_w/revise_h/extract require bounded clarification, never automatic dispatch.')
    if closeout_settings.get('selection_policy') and shortlist.marker(root) is not None:
        instruction = ('Consider the complete closeout.comparison catalogue and compare relevant alternatives by the same goal criteria in scientific_result. '
            'Explicitly name candidate_recommendation and explain its scientific evidence; null supplies no delivery choice. '
            'Declared required adapter audits gate delivery; absent optional audit/smoke capabilities provide no qualification. '
            'Use next_action=deliver to explicitly authorize adapter delivery, finish to decline, or wait to pause. '
            'Explain science, limitations and unmet objectives; global completion remains unasserted. '
            'E extracts scientific evidence only. Exploration is closed; new H/W/E requests require bounded clarification.')
    if begin['stop_reason'] == 'engineering_repair_budget_unavailable':
        instruction += ' The proposed W repair was not executed at its budget boundary. Judge only accepted originals and retain this missing repair as a limitation.'
    from . import engineering
    repair=initial_repair
    if repair:parents=sorted({repair['decision_step'],repair['source']['step']})
    author_request, corrections = None, 0
    if selected_closeout:
        original_route = fork['route']
        parents, instruction = original_route['parents'], original_route['instruction']
        author_request = original_route['author_correction']
    # A preserved unused repair may occupy the last granted slot.
    last_step = max(begin['first_step'], budget.historical_step_limit(root)) + 1
    if fork_runtime.is_fork(root):
        # Include a denial boundary so exhaustion can journal FINAL. Logical
        # labels cannot determine how many new closeout actions fit.
        last_step = max(last_step, begin['first_step'] + budget.effective_max_steps(root) + 1)
    for step in range(begin['first_step'], last_step + 1):
        if not (root / 'jobs' / f'{step:03d}.json').exists():
            configuration_updates.bind_new_job(root, step)
        closeout_settings = configuration_updates.for_new_job(root, step)
        if not (root / 'jobs' / f'{step:03d}.json').exists():
            from . import control_operations
            control_operations.apply_pending_budget(root)
        record = _record(root, step)
        path = root / 'jobs' / f'{step:03d}.json'
        if not (record / 'result-identity.json').exists():
            if (root / 'pause.request').exists() or correction.gate(root, begin['source_cutoff']) is not None:
                return _closeout_result(root, 'closeout_blocked', reason='Original pause remains effective', step=step)
            if author_request is not None and corrections > semantic.MAX_AUTHOR_CORRECTIONS:
                return _closeout_result(root, 'closeout_unresolved', reason='semantic_correction_limit', step=step)
            if (record / 'native-intent.json').exists():
                try:
                    _native_call(root, step)
                except budget.NodeBudgetUnavailable as error:
                    if _budget_denied(error.reason):
                        return _start_budget_closeout(root, begin['source_cutoff'], step, 'W' if repair else 'A', error.reason)
                    return _closeout_result(root, 'closeout_budget_unavailable', reason=error.reason, step=step)
                except continuation.ResumeUnavailable as error:
                    status = ('output_repair_unavailable' if getattr(error, 'status', None) == 'output_repair_unavailable'
                        else 'closeout_native_unsealed')
                    reason = (_closeout_native_reason(root, step, str(error))
                        if str(error) == 'automatic_recovery_final_boundary' else str(error))
                    return _closeout_result(root, status, step=step, reason=reason)
                except OutputRepairRequired as error:
                    return _closeout_result(root, 'output_repair_required', step=step, reason=error.code)
                except ValueError:
                    raise
                except Exception:
                    return _closeout_result(root, 'closeout_native_unsealed', step=step,
                        reason=_closeout_native_reason(root, step,
                            'Original closeout native outcome remains unsealed; reconcile the same original without replay'))
            else:
                if repair is not None:
                    successor = root / 'jobs' / f'{step+1:03d}.json'
                    prepared_author = (successor.exists() and contracts.read(successor).get('role') == 'A'
                        and contracts.read(successor).get('purpose') == 'closeout'
                        and contracts.read(successor).get('source_cutoff') == begin['source_cutoff'])
                    if not _repair_funds(root) or prepared_author:
                        return _start_budget_closeout(root, begin['source_cutoff'], step, 'W', 'engineering_repair_budget_unavailable')

                if not budget.task_step_available(root, step):
                    return _start_budget_closeout(root, begin['source_cutoff'], step, 'W' if repair else 'A', 'bounded_steps_exhausted')
                if budget.execution_blocked(root) or repair is None and not _closeout_funds(root, preparation_seconds=begin['preparation_seconds'], step=step):
                    return _start_budget_closeout(root, begin['source_cutoff'], step, 'W' if repair else 'A', 'closeout_budget_unavailable')
                budget.transition(root, 'advance', 'closeout_engineering_repair_admission' if repair else 'closeout_author_admission')
                if path.exists():
                    prior = contracts.read(path)
                    if prior.get('purpose') != 'closeout' or prior.get('source_cutoff') != begin['source_cutoff'] or prior.get('direct_originals',prior['direct_refs']) != [dict(path=f'inputs/parents/{parent:03d}/conclusion.json',size_bytes=execution.raw(root,parent).stat().st_size) for parent in parents]:
                        return _closeout_result(root, 'retained_job_conflict', step=step)
                if repair is None and closeout_settings.get('selection_policy') is not None:
                    stopped=shortlist.screen(root,begin,before_step=step)
                    if stopped is not None:return _closeout_result(root,stopped,step=step)
                try:
                    if selected_closeout and step == fork['next_step']:
                        fork_runtime.build_selected_job(root, step)
                    else:
                        contracts.build_job(root, step, 'W' if repair else 'A', parents,
                            instruction=engineering.public(repair)['scope'] if repair else instruction, purpose='closeout',
                            source_cutoff=begin['source_cutoff'], delivery_sources=engineering.sources(root,step),
                            author_correction=author_request if repair is None else None, engineering_repair=repair)
                except engineering.RepairUnavailable as error:
                    return _closeout_result(root,str(error),step=step)
                try:
                    _native_call(root, step, launch=True)
                except budget.NodeBudgetUnavailable as error:
                    if _budget_denied(error.reason):
                        return _start_budget_closeout(root, begin['source_cutoff'], step, 'W' if repair else 'A', error.reason)
                    return _closeout_result(root, 'closeout_budget_unavailable', reason=error.reason, step=step)
                except continuation.ResumeUnavailable as error:
                    status = ('output_repair_unavailable' if getattr(error, 'status', None) == 'output_repair_unavailable'
                        else 'closeout_native_unsealed')
                    reason = (_closeout_native_reason(root, step, str(error))
                        if str(error) == 'automatic_recovery_final_boundary' else str(error))
                    return _closeout_result(root, status, step=step, reason=reason)
                except OutputRepairRequired as error:
                    return _closeout_result(root, 'output_repair_required', step=step, reason=error.code)
                except ValueError:
                    raise
                except Exception:
                    return _closeout_result(root, 'closeout_native_unsealed', step=step,
                        reason=_closeout_native_reason(root, step,
                            'Original closeout native outcome remains unsealed; reconcile the same original without replay'))
        job = contracts.read(path)
        result = handoff_state.read_sealed(root, step)
        final = _final_a_result(root, job, result)
        if final is not None:
            return final
        if job['role']=='W':
            repair=None
            parents=sorted({step,*engineering.steps(root,step),*parents})
            instruction='Judge the bounded implementation repair and new candidate from exact evidence. Old delivery authorization does not cover new bytes. Use deliver only for an explicit new choice, or report unresolved science.'
            author_request=None
            continue
        if result['next_action'] == 'audit':
            from . import candidate_audit
            audit_status = candidate_audit.execute(root, job, result)
            if audit_status is not None:
                detail=(candidate_audit._outcome(root,step)['result'].get('diagnostic')
                    if audit_status=='candidate_audit_originals_unavailable' else None)
                return _closeout_result(root,audit_status,reason=detail,step=step)
            receipt=candidate_audit._outcome(root,step)['result']
            if receipt['note'] == 'candidate_audit_reserve_unavailable':
                return _start_budget_closeout(root, begin['source_cutoff'], step+1, 'A', receipt['note'])
            repair=engineering.request(root,step)
            if repair is not None or receipt['status'] in {'engineering_error','unclassified'}:
                if repair is None:return _closeout_result(root,'candidate_audit_'+receipt['status'],reason=receipt['note'],step=step)
                try:engineering.admit(root,repair,step+1)
                except engineering.RepairUnavailable as error:return _closeout_result(root,str(error),step=step)
            parents, author_request = sorted({step,*engineering.steps(root,step),*([repair['source']['step']] if repair else [])}), None
            instruction = 'Reassess the requested candidate using the bound audit receipt; exploration remains closed.'
            continue
        if result['next_action'] in {'finish', 'wait'}:
            return _closeout_result(root, 'exported_unsubmitted' if result['next_action'] == 'finish' else 'closeout_waiting',
                reason='scientific_delivery_declined' if result['next_action'] == 'finish' else 'scientific_wait', step=step)
        # There is no implicit replacement or retention on missing choice/qualification.
        try:
            delivery.prepare_binding(root, job, result)
        except delivery.PreparationUnavailable as error:
            repair=engineering.request(root,step)
            if repair is None:return _closeout_result(root,'closeout_preparation_unavailable',reason=error.code,step=step)
            try:engineering.admit(root,repair,step+1)
            except engineering.RepairUnavailable as blocked:return _closeout_result(root,str(blocked),step=step)
            parents=sorted({step,repair['source']['step'],*engineering.steps(root,step)})
            author_request=None
            continue
        except delivery.DeliveryUnresolved as error:
            corrections += 1
            source = semantic._source(root, step)
            author_request = dict(root=author_request['root'] if author_request else source, source=source,
                                  reason=author_request['reason'] if author_request else error.code)
            parents = [step]
            instruction = ('Correct only this unresolved delivery choice: ' + error.code + '. '
                'Use deliver to explicitly authorize adapter delivery of an exact candidate, audit for qualification, finish to decline or wait. '
                'extract means scientific evidence extraction; it never authorizes copying or submission. Exploration is closed.')
            continue
        delivered = _run_delivery_actions(root, job, result)
        if delivered['status']=='delivery_engineering_error':
            repair=engineering.request(root,step)
            if repair is None:return delivered
            try:engineering.admit(root,repair,step+1)
            except engineering.RepairUnavailable as error:return _closeout_result(root,str(error),step=step)
        elif delivered.get('reason') == 'final_author_required':
            parents, author_request = [step], None
            instruction = 'No original submission was accepted before the host budget boundary. Return the final scientific decision and retain missing work and limitations.'
            continue
        elif delivered['status'] != 'delivery_reselect':
            return delivered
        parents, author_request = sorted({step,*engineering.steps(root,step),*([repair['source']['step']] if repair else [])}), None
        from . import candidate_refs
        instruction = ('The completed public audit rejected candidate '
            + str(candidate_refs.public_reference(root,job,result.get('candidate_recommendation'))) + ': ' + delivered.get('reason', '')
            + '. Select an alternative candidate with deliver, request audit, decline with finish or wait. '
            'No unknown external operation may be repeated; original science and budget remain binding.')
    return _start_budget_closeout(root, begin['source_cutoff'], last_step + 1, 'A', 'bounded_steps_exhausted')


def closeout(root, *, export_only=False):
    from .protocol import require_current
    require_current(root)
    root = Path(root).resolve()
    with _writer(root), _inactive_on_error(root):
        from . import candidate_audit
        candidate_audit.settle_preflights(root)
        if budget.closeout_state(root)['begin'] is not None:
            return _closeout_result(root, 'exported_unsubmitted', reason='operator_export_only') if export_only else _run_closeout(root)
        from . import fork_runtime, originals
        fork = fork_runtime.bootstrap(root)
        if fork is None:
            cutoff = 0
            steps = range(1, budget.historical_step_limit(root) + 1)
            first = max((int(path.stem) for path in (root / 'jobs').glob('*.json')), default=0) + 1
        else:
            steps = fork_runtime.local_steps(root)
            # The frozen selection remains reserved even if operator closeout
            # precedes its first preparation. A new FINAL cannot replace it.
            first = max([fork['next_step'], *steps]) + 1
            inherited = [source for source in originals.source_sequence(root, first) if source.inherited]
            for source in inherited:
                handoff_state.read_sealed(root, source.step)
            cutoff = max((source.step for source in inherited
                if not originals.source_job(source).get('final_only')), default=0)
            resumed = budget.normal_resume(root)
            if resumed is not None:
                visible = set(budget.scientific_steps(root, first))
                steps = [number for number in steps if number in visible or number >= resumed['first_step']]
        for step in steps:
            record = _record(root, step)
            if (record / 'result-identity.json').exists():
                handoff_state.read_sealed(root, step)
                if fork is None or not fork_runtime.source_job(root, step).get('final_only'):
                    cutoff = step
            elif not export_only and (record / 'native-intent.json').exists() and _terminal_success(root, execution.record(root, step)):
                continuation.seal_or_repair(root, step);cutoff = step
            else:
                break
        if not (root / 'host-started.json').exists():
            contracts.write(root / 'host-started.json', budget.clock_stamp())
        return _start_closeout(root, cutoff, first, 'operator_closeout', export_only=export_only)


def worker_closeout(root: Path, step: int, *, max_tokens, max_seconds, authorization_note,
                    successor=False, authorization_statement=None):
    """One explicit author-only continuation, followed by the existing A closeout."""
    from . import author_closeout
    root=Path(root).resolve()
    with _writer(root),_inactive_on_error(root,'author_closeout_failed'):
        if budget.closeout_state(root)['begin'] is not None:return _run_closeout(root)
        if type(step) is not int or not 0<step<=budget.historical_step_limit(root):
            raise ValueError('Exact original W step required')
        if successor:
            author_closeout.start_successor(root,step,max_tokens=max_tokens,max_seconds=max_seconds,
                authorization_note=authorization_note,authorization_statement=authorization_statement)
            return _start_closeout(root,step,step+1,'operator_author_closeout_successor')
        if authorization_statement is not None:
            raise ValueError('Successor authorization fields require explicit --successor')
        paths=execution.records(root,step)
        if len(paths)>1:
            intent=contracts.read(paths[-1]/'native-intent.json')
            author_closeout.validate_intent(root,contracts.read(root/'jobs'/f'{step:03d}.json'),intent)
            _native_call(root,step)  # Collect this accepted turn; never submit another.
        else:
            author_closeout.start(root,step,max_tokens=max_tokens,max_seconds=max_seconds,authorization_note=authorization_note)
        return _start_closeout(root,step,step+1,'operator_author_closeout')


def worker_resume(root: Path, step: int):
    """Explicitly accept one continuation, or collect its already accepted work."""
    from .protocol import require_current
    require_current(root)
    root = Path(root).resolve()
    with _writer(root), _inactive_on_error(root, 'worker_resume_failed'):
        return _worker_resume_locked(root, step)


def _worker_resume_locked(root, step):
    """Original resume gates under an already-held controller writer."""
    try:
        if type(step) is not int or not 0 < step <= budget.historical_step_limit(root):
            raise ValueError('An exact prepared node is required')
        _native_call(root, step, operation=continuation.start)
        return _run(root)
    except (continuation.ResumeUnavailable, budget.NodeBudgetUnavailable) as error:
        if isinstance(error, budget.NodeBudgetUnavailable) and error.reason == 'worker_resume_budget_unavailable':
            return _run(root)
        budget.inactive(root, 'worker_resume_unavailable')
        return dict(status='worker_resume_unavailable', reason=getattr(error, 'reason', str(error)), usage=native.usage(root))


def execution_recover(root: Path, step: int):
    """Append verified same-call terminal evidence without changing old seals or jobs."""
    from . import execution_recovery
    root=Path(root).resolve()
    with _writer(root),_inactive_on_error(root,'execution_recovery_failed'):
        result=execution_recovery.create(root,step)
        budget.inactive(root,'execution_recovery_complete')
        return result


def execution_resume(root: Path, step: int, call_id: str, *, max_seconds,
                     authorization_note, request_valid_seconds=None, maintenance=None):
    """Finite operator progress of the same call; never restart its scientific author."""
    from . import execution_resume as recovery
    root=Path(root).resolve()
    if maintenance is not None:
        if request_valid_seconds is not None:
            raise ValueError('Maintenance cannot renew or dispatch an execution request')
        with _writer(root):
            return recovery.maintain(root,step,call_id,action=maintenance,max_seconds=max_seconds,
                authorization_note=authorization_note)
    with _writer(root),_inactive_on_error(root,'execution_resume_failed'):
        result=recovery.resume(root,step,call_id,max_seconds=max_seconds,
            authorization_note=authorization_note,request_valid_seconds=request_valid_seconds)
        budget.inactive(root,'execution_resume_complete')
        return result
