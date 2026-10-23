"""Child-local accounting and exact bootstrap for historical-task forks."""
import copy
from pathlib import Path


FROZEN_TASK_SETTINGS = (
    'search_policy', 'role_guidance', 'node_budget_policy', 'role_budget_policy', 'correction_policy',
    'portfolio_policy', 'worker_fragment_policy', 'candidate_nomination_policy', 'candidate_audit_policy',
    'selection_policy', 'decision_contract_version', 'handoff_authority_version', 'lineage_projection_version',
    'selection_protocol', 'node_budget_mode', 'experiment_mode', 'execution_config', 'history_observer',
    'delivery_policy', 'resume_policy', 'model', 'reasoning_effort', 'service_tier', 'role_profiles',
    'native_backend', 'native_options', 'native_launcher_argv', 'max_output_item_bytes', 'max_output_total_bytes',
    'final_a_completion_policy', 'live_feedback_version', 'experiment_interface_version',
    'managed_admission_version', 'worker_view_version', 'storage_policy',
    'skills', 'worker_profiles', 'role_profile_declarations',
)


def changed_task_settings(source, child):
    return [key for key in FROZEN_TASK_SETTINGS if source.get(key) != child.get(key)]


def is_fork(root):
    path = Path(root) / 'host-runtime/fork/manifest.json'
    return path.exists() or path.is_symlink()


def local_steps(root, *, include_native=False):
    """Enumerate local jobs, optionally retaining orphan native originals too."""
    from . import contracts as c
    steps = set()
    directory = Path(root) / 'jobs'
    for path in directory.iterdir() if directory.exists() else ():
        if path.suffix != '.json':
            continue
        if (not path.stem.isascii() or not path.stem.isdecimal()
                or path.name != f'{int(path.stem):03d}.json' or int(path.stem) <= 0):
            raise ValueError('Scientific job namespace changed')
        c.regular(path)
        steps.add(int(path.stem))
    if include_native:
        # Only this instance's physical records count. Fork inherited objects
        # live under host-runtime/fork and never enter local consumption/guards.
        directory = Path(root) / 'native-evidence'
        if directory.is_symlink():
            raise ValueError('Native original namespace changed')
        for path in directory.iterdir() if directory.exists() else ():
            intent = path / 'native-intent.json'
            if not intent.exists() and not intent.is_symlink():
                continue
            if (not path.name.isascii() or not path.name.isdecimal()
                    or path.name != f'{int(path.name):03d}' or int(path.name) <= 0
                    or path.is_symlink() or not path.is_dir()):
                raise ValueError('Native original namespace changed')
            c.regular(intent)
            steps.add(int(path.name))
    return sorted(steps)


def bootstrap(root):
    if not is_fork(root):
        return None
    from . import fork_sources
    fork_sources.validate_manifest(root)
    boot = copy.deepcopy(fork_sources.bootstrap(root))
    boot['route']['evidence_return'] = _bound_return(
        boot['route'].get('evidence_return'), boot['selected_task_ref']['origin_identity'])
    return boot


def _bound_ref(ref, origin):
    return dict(ref, origin_identity=ref.get('origin_identity', origin))


def _bound_return(value, origin):
    """Bind host proof refs without changing the frozen original route."""
    result = copy.deepcopy(value)
    if isinstance(result, dict):
        for field in ('request', 'unavailable', 'review'):
            if isinstance(result.get(field), dict) and 'path' in result[field]:
                result[field] = _bound_ref(result[field], origin)
    return result


def node_count(root, numbers):
    """Fork logical labels do not consume a new action allowance."""
    if is_fork(root):
        bootstrap(root)
        return len(numbers)
    return max(numbers, default=0)


def source_job(root, step):
    from . import originals
    return originals.source_job(originals.node(root, step))


def source_record(root, step, name):
    from . import originals
    return originals.source_record(originals.node(root, step), name)


def science_steps(root, before_step):
    from . import originals
    return [source.step for source in originals.source_sequence(root, before_step)
        if not originals.source_job(source).get('final_only')]


def _selected_identity(selected):
    return dict(job=selected['source_job_ref'], task=selected['source_task_ref'])


def ensure_phase(root, boot):
    """Only start creates a fresh child closeout journal; source events stay history."""
    from . import budget, contracts as c, delivery, fork_sources, originals
    if budget.closeout_state(root)['begin'] is not None:
        return
    selected = fork_sources.selected_task(root)
    job = selected['job']
    if not job.get('final_only') and job.get('purpose') != 'closeout':
        return
    boundary = selected.get('phase_original_ref')
    if boundary is None:
        raise ValueError('Selected final task lacks its exact original phase boundary')
    original = c.read(originals.resolve_ref(originals.source_for_ref(selected['source'], boundary), boundary))
    if original['kind'] != 'closeout_begin' or job.get('source_cutoff', job.get('final_report', {}).get('source_cutoff')) != original['source_cutoff']:
        raise ValueError('Selected task changed its exact original closeout cutoff')
    budget.begin_closeout(root, original['source_cutoff'], boot['next_step'], original['stop_reason'],
        delivery_sources=delivery.rebind_sources(root, original.get('delivery_sources')),
        preparation_seconds=original['preparation_seconds'],
        final_only=job.get('final_only', False), stopped_original=original.get('stopped_original'))


def _selected_base(root, selected, *, bind_inputs=False):
    from . import contracts as c, configuration_updates, execution_authority, qualification
    job = copy.deepcopy(selected['job'])
    revision = configuration_updates.for_new_job(root, job['step'])
    changed = changed_task_settings(selected['settings'], revision)
    if changed:
        raise ValueError('Fork selected task configuration changed: ' + ', '.join(changed))
    binding = configuration_updates._reservation(root, job['step'])
    job.pop('configuration_revision', None)
    if binding is not None and binding['configuration_revision'] is not None:
        job['configuration_revision'] = binding['configuration_revision']
    from . import skill_supply
    skill_supply.for_job(selected['settings'], selected['job'])
    if revision.get('skills') is not None:
        job['skill_binding'] = skill_supply.binding(revision, job)
    job['public_root'] = revision['public_root']
    if job['goal_text'] != revision['objective']:
        raise ValueError('Fork cannot replace the selected original scientific objective')
    job['fork_source'] = _selected_identity(selected)
    if job.get('delivery_sources') is not None:
        from . import delivery
        job['delivery_sources'] = delivery.rebind_sources(root, job['delivery_sources'], job=job)
    origin = selected['source'].origin_identity
    for field in ('source_provenance', 'formal_review', 'candidate_audit'):
        if isinstance(job.get(field), dict) and 'path' in job[field]:
            job[field] = _bound_ref(job[field], origin)
    if 'evidence_return' in job:
        job['evidence_return'] = _bound_return(job['evidence_return'], origin)
    if 'formal_review_choices' in job:
        job['formal_review_choices']['unavailable'] = [dict(ref, origin_identity=ref.get('origin_identity', origin))
            for ref in job['formal_review_choices']['unavailable']]
    job.pop('normal_resume', None)
    if job.get('purpose') == 'closeout' or job.get('final_only'):
        from . import budget
        begin = budget.closeout_state(root)['begin']
        if begin is None or begin['first_step'] != job['step']:
            raise ValueError('Fork closeout task lacks its new child boundary')
        job['closeout_begin'] = dict(seq=begin['seq'],
            **c.record_ref(Path(root) / 'control-ledger' / f'{begin["seq"]:06d}.json'))
    job.pop('execution_authority', None)
    job.pop('execution_qualifications', None)
    job['source_provenance'] = (qualification.bind_inputs(root, job) if bind_inputs else
        qualification._ref(root, c.host_record(c.workspace(root, job['step'])) / 'input-provenance.json'))
    qualification.validate_inputs(root, job)
    job['source_restrictions'] = qualification.status(root, job)
    execution_authority.bind_job(root, job)
    return job


def selected_science_view(root, job):
    from . import contracts as c, fork_sources, qualification, user_guidance
    selected = fork_sources.selected_task(root)
    if job.get('fork_source') != _selected_identity(selected):
        raise ValueError('Fork task lost its exact scientific original')
    view = copy.deepcopy(selected['science_task'])
    view['public_root'] = job['public_root']
    view.pop('user_guidance', None)
    view.update(user_guidance.public(job))
    view.pop('execution_authority', None)
    if job.get('execution_authority') is not None:
        view['execution_authority'] = dict(
            status='trusted_gate_required' if c.settings(Path(root), job).get('execution_resource_policy') else 'authority_only_production_gate_unavailable',
            qualified_materials=[qualification._read(root, ref)['subject']['material']['path']
                for ref in job.get('execution_qualifications', [])[:3]],
            qualified_materials_omitted_count=max(0, len(job.get('execution_qualifications', [])) - 3),
            instruction='Choose scientific materials through the existing selection. Control binds exact review and execution identities; ordinary pass text grants nothing. Decisions alone do not dispatch or add budget.')
    if 'source_restrictions' in job:
        view['source_restrictions'] = job['source_restrictions']
    return view


def _selected_files(root, selected):
    from . import fork_sources, originals
    manifest = fork_sources.validate_manifest(root)
    source = selected['source']
    prefix = f'project/nodes/{source.step:03d}/'
    for entry in manifest['entries']:
        if (entry['origin_identity'] == source.origin_identity
                and entry['visibility'] == 'scientific_input' and entry['path'].startswith(prefix)):
            relative = originals.relative(entry['path'][len(prefix):])
            if not relative.startswith('inputs/'):
                raise ValueError('Fork scientific input is outside its exact input namespace')
            yield relative, originals.resolve_ref(source, entry), entry['size_bytes']


def build_selected_job(root, step):
    """Prepare a new child task from the selected task's frozen input bytes."""
    from . import contracts as c, configuration_updates, fork_sources, user_guidance
    root = Path(root)
    selected = fork_sources.selected_task(root)
    if step != selected['job']['step']:
        raise ValueError('Fork bootstrap cannot select another logical task')
    configuration_updates.bind_new_job(root, step)
    path = root / 'jobs' / f'{step:03d}.json'
    if path.exists():
        job = c.read(c.regular(path))
        validate_selected_job(root, c.workspace(root, step), job)
        from . import skill_supply
        skill_supply.project(root, configuration_updates.for_job(root, job), job)
        return job
    # First admission verifies the complete immutable closure. Later reads
    # verify metadata plus the exact members they consume.
    selected = fork_sources.selected_task(root, full=True)
    work = c.workspace(root, step)
    work.mkdir(parents=True, exist_ok=True)
    (work / 'work').mkdir(exist_ok=True)
    (work / 'artifacts').mkdir(exist_ok=True)
    job = _selected_base(root, selected, bind_inputs=True)
    from . import storage_policy as storage
    frozen=configuration_updates.for_new_job(root,step)
    context=storage.Deadline(frozen['max_seconds'])
    inventory=storage.Inventory(frozen.get('storage_policy'),context=context)
    for relative, source, size_bytes in _selected_files(root, selected):
        inventory.entry();inventory.file(source.stat().st_size)
        c._bind_original(source, work/relative, root=root, policy=frozen.get('storage_policy'), context=context, size_bytes=(source).stat().st_size, source_kind='managed_frozen')
    # Review inputs remain neutral; general advice waits for the next legal task.
    if job.get('formal_review'):
        statements = selected['job'].get('user_guidance', [])
        if statements:
            job['user_guidance'] = copy.deepcopy(statements)
        _publish_selected(root, work, job)
    else:
        with user_guidance.binding(root, step, job['role'], None) as statements:
            previous = copy.deepcopy(selected['job'].get('user_guidance', []))
            combined = previous + [row for row in statements
                if row['guidance_id'] not in {item['guidance_id'] for item in previous}]
            if combined:
                job['user_guidance'] = combined
            _publish_selected(root, work, job)
    from . import skill_supply
    skill_supply.project(root, frozen, job)
    return job


def _publish_selected(root, work, job):
    from . import contracts as c
    for path, value in ((Path(root) / 'jobs' / f'{job["step"]:03d}.json', job),
            (work / 'science-task.json', selected_science_view(root, job)),
            (c.host_record(work) / 'science-result.schema.json', c.result_schema(job['role'],
                job['handoff_protocol'], 'selection' in job, **c.response_options(job)))):
        if path.exists():
            if c.read(c.regular(path)) != value:
                raise ValueError('Fork scientific task changed during preparation')
        else:
            c.write(path, value)


def validate_selected_job(root, work, job):
    """Validate exact inherited inputs rather than reconstructing a later context."""
    from . import contracts as c, fork_sources, execution_authority
    selected = fork_sources.selected_task(root)
    expected = _selected_base(root, selected)
    original_statements = expected.pop('user_guidance', [])
    actual = copy.deepcopy(job)
    statements = actual.pop('user_guidance', [])
    if actual != expected or statements[:len(original_statements)] != original_statements:
        raise ValueError('Fork task differs from its exact selected input contract')
    if job.get('formal_review') and statements != original_statements:
        raise ValueError('Formal review cannot inherit general fork advice')
    for statement in statements[len(original_statements):]:
        from . import user_guidance
        if (statement.get('applied_step') != job['step'] or statement.get('applied_role') != job['role']
                or c.read(c.regular(user_guidance._path(root, statement['guidance_id']))) != statement):
            raise ValueError('Fork advice differs from its exact applied user original')
    if c.read(c.regular(Path(work) / 'science-task.json')) != selected_science_view(root, job):
        raise ValueError('Fork scientific task projection changed')
    for relative, original, size_bytes in _selected_files(root, selected):
        if c.regular(Path(work) / relative).stat().st_ino != original.stat().st_ino or (Path(work) / relative).stat().st_dev != original.stat().st_dev:
            raise ValueError('Fork scientific input bytes changed')
    execution_authority.validate_job(root, job)
