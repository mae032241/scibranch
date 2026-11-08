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
    'skills', 'worker_profiles', 'role_profile_declarations', 'project',
    'supervision_policy', 'supervision_budget_basis', 'research_strategy_version',
)


def changed_task_settings(source, child):
    return [key for key in FROZEN_TASK_SETTINGS if source.get(key) != child.get(key)]


def _validate_selected_settings(selected, settings):
    changed = changed_task_settings(selected['settings'], settings)
    if changed:
        raise ValueError('Fork selected task configuration changed: ' + ', '.join(changed))


def preflight_selected_settings(root, settings, *, step=None):
    """Check a proposed snapshot only while the selected task is unbound."""
    if not is_fork(root):
        return
    from . import accepted_budget, configuration_updates, fork_sources, originals
    manifest = originals._manifest(root)
    if manifest is None:
        raise ValueError('Fork manifest identity changed')
    selected_step = manifest['source']['step']
    if step is not None and step != selected_step:
        return
    if (Path(root) / 'jobs' / f'{selected_step:03d}.json').exists():
        return
    head = accepted_budget.read(root)
    reserved = configuration_updates._reservation(root, selected_step, head=head)
    if reserved is not None:
        configuration_updates.policy_for_job(root, reserved, base=head.base, head=head)
        return
    selected = fork_sources.selected_task(root)
    _validate_selected_settings(selected, settings)


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
    from . import originals
    # Fork creation validates and freezes this manifest. Resuming its saved
    # route does not reopen the inherited closure or its original files.
    boot = copy.deepcopy(originals._manifest(root)['bootstrap'])
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


def _load_selected_base_facts(root, selected, *, base, policy, head, bind_inputs=False):
    """Own selected-input reads and acceptance using the entrypoint's policy."""
    from . import contracts as c, configuration_updates, execution_authority, qualification, originals
    job = copy.deepcopy(selected['job'])
    revision = policy
    _validate_selected_settings(selected, revision)
    binding = configuration_updates._reservation(root, job['step'], head=head)
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
    admitted=head.row(f'job/{job["step"]:03d}')
    # Formal review keeps the exact request's neutral input boundary. The
    # child's accepted host prefix must not rewrite that frozen review context.
    if not job.get('formal_review'):
        job['history']['prefix']=copy.deepcopy(head['prefix'] if admitted is None else admitted['prefix'])
        job['history']['breadth_snapshot']=copy.deepcopy(head['search_ref'] if admitted is None else admitted['input_search_ref'])
    if (job.get('search') or {}).get('breadth'):
        job['search']['breadth']['catalog']['prefix']=copy.deepcopy(job['history']['prefix'])
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
    source, original = selected['source'], selected['job']
    document = (qualification._source_read(source, original['source_provenance'])
        if original.get('source_provenance') is not None else
        dict(version=1, step=original['step'], edges=[], scope=qualification.SCOPE))
    producers = {edge['step']: originals.dependency(source, edge['step'], seal_ref=edge['seal'])
        for edge in document['edges'] if edge['kind']=='producer'}
    inputs = qualification.fork_input_facts(source,original,document=document,
        producer_origins={step:owner.origin_identity for step,owner in producers.items()})
    if bind_inputs:
        job['source_provenance'], document = qualification.bind_inputs_from_facts(root,job,facts=inputs)
    else:
        job['source_provenance'] = qualification._ref(root,
            c.host_record(c.workspace(root, job['step'])) / 'input-provenance.json')
        document = qualification.validate_inputs_from_facts(root,job,facts=inputs)
    job['source_restrictions'] = qualification.status_from_facts(job,document,facts=inputs)
    sources = dict(selected['material_sources'])
    from . import delivery_review, handoff_state
    scoped=delivery_review.binding(job)
    if scoped is not None:
        step=scoped['step']
        known=sources.get(step)
        owner=(known or {}).get('owner') or producers.get(step)
        if owner is None:
            owner=originals.dependency(source,step)
        facts=handoff_state.sealed_facts(root,step,base=base,owner=owner,known=known)
        sources[step]=handoff_state.source_facts_from_sealed(root,facts,resolve_choices=False)
        delivery_review.validate_parent(job,sources)
    if policy.get('execution_authority_policy') is not None:
        for reference in job['direct_refs']:
            step = int(Path(reference['path']).parts[2])
            owner = producers[step] if step in producers else originals.dependency(source, step)
            if step not in sources:
                sources[step] = dict(owner=owner)
    authority = execution_authority.job_facts(root,job,base=base,policy=policy,sources=sources)
    execution_authority.bind_job(root,job,facts=authority)
    return job, dict(inputs=inputs, authority=authority, sources=sources)


def selected_science_view(root, job):
    from . import contracts as c, fork_sources, qualification, user_guidance
    selected = fork_sources.selected_task(root)
    if job.get('fork_source') != _selected_identity(selected):
        raise ValueError('Fork task lost its exact scientific original')
    policy = c.settings(Path(root), job) if job.get('execution_authority') is not None else {}
    qualified = [qualification._read(root, ref)['subject']['material']['path']
        for ref in job.get('execution_qualifications', [])[:3]]
    return _selected_science_view(job,selected=selected,policy=policy,qualified=qualified)


def _selected_science_view(job, *, selected, policy, qualified):
    from . import user_guidance
    view = copy.deepcopy(selected['science_task'])
    view['public_root'] = job['public_root']
    view.pop('user_guidance', None)
    view.update(user_guidance.public(job))
    view.pop('execution_authority', None)
    if job.get('execution_authority') is not None:
        view['execution_authority'] = dict(
            status='trusted_gate_required' if policy.get('execution_resource_policy') else 'authority_only_production_gate_unavailable',
            qualified_materials=qualified[:3],
            qualified_materials_omitted_count=max(0, len(job.get('execution_qualifications', [])) - 3),
            instruction='Choose scientific materials through the existing selection. Control binds exact review and execution identities; ordinary pass text grants nothing. Decisions alone do not dispatch or add budget.')
    if 'source_restrictions' in job:
        view['source_restrictions'] = job['source_restrictions']
    return view


def _selected_files(root, selected):
    from . import contracts as c, fork_sources, originals, project_layout
    manifest = originals._manifest(root)
    source = selected['source']
    layout=project_layout.code_roots(selected['job'].get('project'),c.workspace(Path(source.original_root),source.step))
    prefixes=[row['source'].relative_to(source.original_root).as_posix()+'/' for row in layout['editable']]
    for entry in manifest['entries']:
        if (entry['origin_identity'] == source.origin_identity
                and entry['visibility'] == 'private_snapshot'):
            path = originals.relative(entry['path'])
            # Native handoff drafts stay inert in the existing private entry.
            # A child starts its own accepted native chain in a clean namespace.
            if any(path.startswith(prefix) for prefix in prefixes):
                yield path, originals.resolve_ref(source, entry), entry


def build_selected_job(root, step):
    """Prepare the frozen task and restore its inert, writable current drafts."""
    from . import accepted_budget, contracts as c, configuration_updates, fork_sources, user_guidance
    root = Path(root)
    selected = fork_sources.selected_task(root)
    if step != selected['job']['step']:
        raise ValueError('Fork bootstrap cannot select another logical task')
    base=c.settings(root)
    _, frozen, head=configuration_updates.bind_new_job_facts(root,step,base=base)
    path = root / 'jobs' / f'{step:03d}.json'
    if path.exists():
        job = c.read_job(path, sources=selected['material_sources'], base=base)
        _load_selected_job_facts(root,c.workspace(root,step),job,
            base=base,policy=frozen,head=head,selected=selected)
        with user_guidance.binding_from_base(root,step,job['role'],job,base=base) as (_,guidance_current):
            acceptance=accepted_budget.job_acceptance_facts(root,step,base=base)
            accepted_budget.accept_job_from_facts(root,step,job,facts=acceptance,
                guidance_current=guidance_current)
        from . import skill_supply
        skill_supply.project(root,frozen,job)
        return job
    # First admission verifies copied controls/drafts. Scientific inputs are
    # checked through their original bindings by their actual consumers.
    selected = fork_sources.selected_task(root, full=True, material_sources=selected['material_sources'])
    work = c.workspace(root, step)
    work.mkdir(parents=True, exist_ok=True)
    (work / 'work').mkdir(exist_ok=True)
    (work / 'artifacts').mkdir(exist_ok=True)
    if selected['job'].get('project') is not None:
        from . import project_layout
        for row in project_layout.code_roots(selected['job']['project'],work)['editable']:
            row['source'].mkdir(parents=True,exist_ok=True)
    from . import storage_policy as storage
    from scidiscovery import archive_io as files
    context=storage.Deadline(65 if frozen['max_seconds'] is None else frozen['max_seconds'])
    inventory=storage.Inventory(frozen.get('storage_policy'),context=context)
    for relative, source, entry in _selected_files(root, selected):
        inventory.entry();inventory.file(entry['size_bytes'])
        files.copy_original(source,root/relative,dict(entry,kind='file'),context=context)
    job, facts = _load_selected_base_facts(root,selected,base=base,policy=frozen,head=head,bind_inputs=True)
    # Review inputs remain neutral; general advice waits for the next legal task.
    if job.get('formal_review'):
        statements = selected['job'].get('user_guidance', [])
        if statements:
            job['user_guidance'] = copy.deepcopy(statements)
    with user_guidance.binding_from_base(root,step,job['role'],job if job.get('formal_review') else None,
            base=base) as (statements,guidance_current):
        if not job.get('formal_review'):
            previous = copy.deepcopy(selected['job'].get('user_guidance', []))
            combined = previous + [row for row in statements
                if row['guidance_id'] not in {item['guidance_id'] for item in previous}]
            if combined:
                job['user_guidance'] = combined
        _publish_selected(root,work,job,selected=selected,policy=frozen,facts=facts)
        acceptance=accepted_budget.job_acceptance_facts(root,step,base=base)
        accepted_budget.accept_job_from_facts(root,step,job,facts=acceptance,
            guidance_current=guidance_current)
    from . import skill_supply
    skill_supply.project(root, frozen, job)
    return job


def _publish_selected(root, work, job, *, selected, policy, facts):
    from . import contracts as c
    for path, value in ((Path(root) / 'jobs' / f'{job["step"]:03d}.json', c.job_record(job)),
            (work / 'science-task.json', _selected_science_view(job,selected=selected,policy=policy,
                qualified=facts['authority']['qualified_materials'])),
            (c.host_record(work) / 'science-result.schema.json', c.result_schema(job['role'],
                job['handoff_protocol'], 'selection' in job, **c.response_options(job)))):
        if path.exists():
            if c.read(c.regular(path)) != value:
                raise ValueError('Fork scientific task changed during preparation')
        else:
            c.write(path, value)


def validate_selected_job(root, work, job):
    """Validate exact inherited inputs rather than reconstructing a later context."""
    from . import accepted_budget, contracts as c, fork_sources
    base=c.settings(root)
    head=accepted_budget.load(root,base=base)
    policy=c.settings_for_job(root,job,base=base,head=head)
    selected=fork_sources.selected_task(root)
    _load_selected_job_facts(root,work,job,base=base,policy=policy,head=head,selected=selected)


def _load_selected_job_facts(root, work, job, *, base, policy, head, selected):
    """Own selected-job reads and validation, then return accepted facts."""
    from . import contracts as c, execution_authority, handoff_state, originals
    expected, facts = _load_selected_base_facts(root,selected,base=base,policy=policy,head=head)
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
    if c.read(c.regular(Path(work) / 'science-task.json')) != _selected_science_view(job,
            selected=selected,policy=policy,qualified=facts['authority']['qualified_materials']):
        raise ValueError('Fork scientific task projection changed')
    source=selected['source']
    for ref in [*job.get('global_materials',[]),*job.get('direct_refs',[]),
            *job.get('history',{}).get('execution_recovery_materials',[])]:
        originals.resolve_ref(source,dict(ref,path=f'project/nodes/{source.step:03d}/'+ref['path']))
    handoff_state.material_paths(root, job, sources=facts['sources'])
    for ref in job.get('direct_originals',job.get('direct_refs',[])):
        step=int(Path(ref['path']).parts[2])
        parent=facts['sources'].get(step)
        producer=parent['owner'] if parent is not None else originals.dependency(source,step)
        if parent is not None and 'seal' in parent:
            seal=parent['seal']
        else:
            seal=c.read(originals.source_record(producer,'result-identity.json'))
            if parent is not None:parent['seal']=seal
        if ref['size_bytes']!=seal['raw_ref']['size_bytes']:
            raise ValueError('Fork direct original changed its exact producer')
        originals.resolve_ref(producer,seal['raw_ref'])
    execution_authority.validate_job(root,job,facts=facts['authority'])
    return facts
