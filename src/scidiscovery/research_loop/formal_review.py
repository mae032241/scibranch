"""Opt-in exact-subject review through the existing native A node lifecycle.

These host facts grant review qualification only. They do not grant execution,
renew budgets, or establish independence for unobserved native helpers.
"""
import json
from pathlib import Path
from typing import get_args

from . import configuration_updates
from ..operations import review_rules
from . import contracts as c, execution, qualification as q

QUESTION = ('Assess the exact supplied artifact against the original objective and constraints. '
            'Identify supported claims, counterevidence, missing evidence and limitations; no favorable verdict is presumed.')
REVIEW_EVIDENCE_GUIDANCE = (
    'Assess the exact requested claims within the original objective and authorized scope. '
    'Tie key findings to locatable sources or observations and their experiment conditions; distinguish measurements, inferences and assumptions. '
    'Separate target metrics from proxies and sensitivity checks, limiting generalization to supported conditions. '
    'Address conflicting evidence, uncertainty and missing inputs without inventing evidence or presuming a favorable verdict.')


def schema():
    fields = dict(verdict=dict(type='string', enum=list(get_args(review_rules.ReviewVerdict))),
        summary=dict(type='string'), scientific_result=dict(type='string'),
        limitations=dict(type='array', items=dict(type='string')))
    return dict(type='object', properties=fields, required=list(fields), additionalProperties=False)


def configuration(value):
    if value is not None and (not isinstance(value,dict) or value != {'version': 1} or type(value.get('version')) is not int):
        raise ValueError('Formal review requires the explicit version 1 operator policy')
    return value


def _contract():
    return dict(version=1, instruction=review_rules.REVIEW_INSTRUCTION+' '+REVIEW_EVIDENCE_GUIDANCE,
        scope='Review only the exact supplied material and scientific question. Do not edit the subject. '
              'Use revise, blocked or inconclusive for unsupported claims. Review grants no execution permission.',
        schema=schema(), authors_contract='native_participants_and_selected_implementations_v1',
        default_inputs='exact_subject_and_global_objective_constraints_v1')


def freeze_contract(root, directory=None):
    root = Path(root).resolve()
    path = Path(directory) / 'review-contract.json' if directory is not None else root / 'qualification/review-contract.json'
    return dict(version=1, contract=q._save(root, path, _contract()))


def contract(root, job=None):
    settings = configuration_updates.for_job(root, job) if job is not None else configuration_updates.current(root)
    policy = settings.get('formal_review_policy')
    if policy is None:
        return None
    current = _contract()
    original=q._read(root, policy['contract'])
    if ({key:value for key,value in original.items() if key!='implementation'} !=
            {key:value for key,value in current.items() if key!='implementation'}):
        raise q.IntegrityError('Current formal review contract differs from its frozen original')
    return policy['contract']


def capture_participants(root, job):
    """Retain actual native identity, never inferred from role or subject inputs."""
    root = Path(root).resolve()
    from . import continuation
    rows, participants, complete = [], set(), True
    for number, record in enumerate(execution.records(root, job['step'])):
        if not (record / 'native-outcome.json').exists():
            continuation.interruption(root, job, record)
            rows.append(dict(attempt=number, intent=q._ref(root, record / 'native-intent.json'),
                outcome=None, interruption=q._ref(root, record / 'interruption.json'),
                status='unsupported', reason='native_execution_interrupted'))
            complete = False
            continue
        outcome = c.read(c.regular(record / 'native-outcome.json'))
        row = dict(attempt=number, intent=q._ref(root, record / 'native-intent.json'),
                   outcome=q._ref(root, record / 'native-outcome.json'))
        binding = outcome.get('native_identity') or {}
        try:
            if binding.get('participant_scope') != 'codex_exec_single_agent_v1' or not binding.get('path',):
                raise ValueError('native_participant_identity_unsupported')
            original = continuation.session(root, job['step'], record, home=binding['home'],
                session_path=binding['path'], identity_proof=True)
            with c.regular(Path(original['path'])).open('rb') as stream:
                line = stream.readline()
            if json.loads(line) != original['header']:
                raise q.IntegrityError('Native session header changed before retention')
            path = c.host_record(c.workspace(root, job['step'])) / 'authorship' / f'{number:03d}.json'
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                if c.regular(path).read_bytes() != line:
                    raise q.IntegrityError('Retained native identity changed')
            else:
                path.write_bytes(line)
                path.chmod(0o400)
            identity = ('codex_exec', original['thread_id'])
            participants.add(identity)
            row.update(status='known', participant=list(identity), session=original, header=q._ref(root, path))
        except q.IntegrityError:
            raise
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            complete = False
            row.update(status='unsupported', reason=str(error))
        rows.append(row)
    return q._save(root, c.host_record(c.workspace(root, job['step'])) / 'native-authorship.json',
        dict(version=1, complete=complete and bool(rows), participants=[list(row) for row in sorted(participants)],
             attempts=rows, scope='Actual controlled native participants only; arbitrary shell work remains outside this proof.'))


def participants(root, job, seal, *, source=None):
    from . import originals
    source = originals.node(root, job['step']) if source is None else source
    if source.inherited:
        return _inherited_participants(source, job, seal)
    ref = seal.get('native_authorship')
    if ref is None:
        return dict(complete=False, participants=[], reason='native_identity_unavailable')
    proof = q._read(root, ref)
    chain = execution.records(root, job['step'])
    if len(proof['attempts']) != len(chain):
        raise q.IntegrityError('Authorship omits a native attempt or context')
    found, complete = set(), True
    from . import continuation
    for number, (row, record) in enumerate(zip(proof['attempts'], chain)):
        if row.get('outcome') is None:
            continuation.interruption(root, job, record)
            if (row.get('attempt') != number or row.get('status') != 'unsupported'
                    or row.get('intent') != q._ref(root, record / 'native-intent.json')
                    or row.get('interruption') != q._ref(root, record / 'interruption.json')
                    or (record / 'native-outcome.json').exists()):
                raise q.IntegrityError('Authorship changed its interrupted original')
            complete = False
            continue
        if (row['attempt'] != number or row['intent'] != q._ref(root, record / 'native-intent.json')
                or row['outcome'] != q._ref(root, record / 'native-outcome.json')):
            raise q.IntegrityError('Authorship differs from its original native attempt')
        if row['status'] != 'known':
            complete = False
            continue
        outcome = q._read(root, row['outcome'])
        if outcome.get('native_identity', {}).get('participant_scope') != 'codex_exec_single_agent_v1':
            raise q.IntegrityError('Authorship has no complete native participant scope')
        identity = ('codex_exec', outcome['thread_id'])
        header = root / row['header']['path']
        q._check({**row['header'], 'path': str(header)})
        continuation.validate_session_header(header.read_bytes(), identity[1], c.workspace(root, job['step']))
        if row['participant'] != list(identity) or row['session']['thread_id'] != identity[1]:
            raise q.IntegrityError('Authorship participant identity changed')
        found.add(identity)
    expected = [list(row) for row in sorted(found)]
    if proof['participants'] != expected or proof['complete'] != (complete and bool(chain)):
        raise q.IntegrityError('Authorship completeness differs from its native originals')
    return dict(complete=proof['complete'], participants=expected,
                reason=None if proof['complete'] else 'native_identity_unavailable')


def _inherited_participants(source, job, seal):
    """Verify retained headers in the original workspace identity, without a home."""
    from . import originals, continuation
    ref = seal.get('native_authorship')
    if ref is None:
        return dict(complete=False, participants=[], reason='native_identity_unavailable')
    proof = c.read(originals.resolve_ref(source, ref))
    found, complete = set(), True
    first_digest = previous = previous_interruption = None
    for number, row in enumerate(proof['attempts']):
        intent_path = originals.resolve_ref(source, row['intent'])
        intent = c.read(intent_path)
        if row['attempt'] != number or intent['task_ref'] != seal['job_ref']:
            raise q.IntegrityError('Inherited authorship changed its original task or attempt')
        if number == 0:
            first_digest = q.control_reference(source.original_root,row['intent'])
        elif (intent.get('attempt') != number or intent.get('root_intent_ref') != first_digest
                or (intent.get('previous') or {}).get('native-intent.json') != previous
                or previous_interruption is not None and (intent.get('previous') or {}).get('interruption.json') != previous_interruption):
            raise q.IntegrityError('Inherited authorship omitted its original attempt chain')
        previous = q.control_reference(source.original_root,row['intent'])
        previous_interruption = None
        if row.get('outcome') is None:
            saved = c.read(originals.resolve_ref(source, row['interruption']))
            if (row.get('status') != 'unsupported' or saved.get('task_ref') != seal['job_ref']
                    or saved.get('intent_ref') != previous):
                raise q.IntegrityError('Inherited interrupted authorship changed')
            previous_interruption = q.control_reference(source.original_root,row['interruption'])
            complete = False
            continue
        outcome = c.read(originals.resolve_ref(source, row['outcome']))
        if row['status'] != 'known':
            complete = False
            continue
        if outcome.get('native_identity', {}).get('participant_scope') != 'codex_exec_single_agent_v1':
            raise q.IntegrityError('Inherited native participant scope is incomplete')
        identity = ('codex_exec', outcome['thread_id'])
        header = originals.resolve_ref(source, row['header'])
        continuation.validate_session_header(header.read_bytes(), identity[1], source.logical_workspace)
        if row['participant'] != list(identity) or row['session']['thread_id'] != identity[1]:
            raise q.IntegrityError('Inherited native participant changed')
        found.add(identity)
    expected = [list(row) for row in sorted(found)]
    if proof['participants'] != expected or proof['complete'] != (complete and bool(proof['attempts'])):
        raise q.IntegrityError('Inherited authorship completeness changed')
    return dict(complete=proof['complete'], participants=expected,
        reason=None if proof['complete'] else 'native_identity_unavailable')


def validate_original(source, job, seal, payload, *, controls_only=False):
    """Retain an original review's historical meaning without granting a child call."""
    from . import originals
    if not job.get('formal_review'):
        if not controls_only:_inherited_participants(source, job, seal)
        return
    ref = seal.get('formal_review')
    if ref is None:
        raise q.IntegrityError('Original formal review lost its sealed qualification')
    context = originals.source_for_ref(source, job['formal_review'])
    request = c.read(originals.resolve_ref(context, job['formal_review']))
    proof = c.read(originals.resolve_ref(source, ref))
    subject = request['subject']
    producer = originals.dependency(context, subject['producer_step'],
        seal_ref=subject['producer_seal'])
    original_seal = c.read(originals.resolve_ref(producer, subject['producer_seal']))
    from . import execution_inputs
    execution_inputs._sealed_material(root=Path(producer.original_root), seal=original_seal,
        material=subject['material'], source=producer, controls_only=controls_only)
    if job.get('fork_source'):
        subject = dict(subject, producer_seal=dict(subject['producer_seal'], origin_identity=producer.origin_identity))
    if controls_only:
        expected = dict(version=1, review_step=job['step'], subject=subject, request=job['formal_review'],
            contract=request['contract'], authors=request['authors'], native_authorship=seal.get('native_authorship'),
            raw_ref=seal['raw_ref'], result_ref=seal['result_ref'], verdict=payload['verdict'])
        if any(proof.get(key) != value for key, value in expected.items()):
            raise q.IntegrityError('Original formal review changed its frozen subject or verdict')
        return
    reviewer = _inherited_participants(source, job, seal)
    host_root = source.storage_root.parents[1] if source.inherited else source.storage_root
    actual_authors = authors(host_root, subject['producer_step'], source=producer)
    independent = review_rules.independence(
        [tuple(row) for row in actual_authors['participants']], [tuple(row) for row in reviewer['participants']],
        authors_complete=actual_authors['complete'], reviewers_complete=reviewer['complete'])
    expected = dict(version=1, review_step=job['step'], subject=subject, request=job['formal_review'],
        contract=request['contract'], authors=actual_authors, reviewer=reviewer,
        native_authorship=seal.get('native_authorship'), raw_ref=seal['raw_ref'], result_ref=seal['result_ref'],
        verdict=payload['verdict'], independence=independent,
        status='passed' if independent == 'independent' and payload['verdict'] == 'pass' else 'not_qualified')
    if request['authors'] != actual_authors or proof != expected:
        raise q.IntegrityError('Original formal review changed its subject, authors, or verdict')


def authors(root, step, active=None, memo=None, *, source=None):
    """Only exact producer and declared implementation contributors are authors."""
    active, memo = set() if active is None else active, {} if memo is None else memo
    from . import originals
    owner = originals.node(root, step) if source is None else originals.at_step(source, step)
    key = (originals.identity_key(owner.origin_identity), step)
    if key in active:
        return dict(complete=False, participants=[], reason='authorship_cycle')
    if key in memo:
        return memo[key]
    context = owner if owner.inherited else None
    job = originals.source_job(owner)
    seal = c.read(originals.source_record(owner, 'result-identity.json'))
    own = participants(root, job, seal, source=owner)
    people = {tuple(row) for row in own['participants']}
    complete = own['complete']
    source = seal.get('source_provenance')
    coverage = None
    if source is not None:
        proof = c.read(originals.resolve_ref(owner, source))
        coverage = proof.get('managed_input_coverage')
        q.validate_outputs(root, job, seal, source=context)
        if coverage is not None:
            complete = complete and all(row['complete'] for row in coverage)
        elif proof.get('missing_reason') is not None:
            complete = False  # Legacy/opaque call inputs have no trusted coverage.
    if not job.get('formal_review'):
        contributions = list(job.get('selected_inputs', []))
        from . import execution_inputs
        execution_inputs.validate_seal(root, job, seal, source=context)
        compiled_originals = []
        for row in seal.get('compiled_implementations', []):
            compiler = execution_inputs.verify_compilation(root, row['compiler'], material=row['material'],
                client_binding=row.get('client_binding'), source=context, controls_only=True)
            compiled_originals.extend(item['original'] for item in compiler['inputs'])
            if compiler.get('author_input') is not None:
                compiled_originals.append(compiler['author_input'])
        # q.validate_outputs above verified this immutable accepted view. Only
        # actual input contributions join the existing traversal, not all parents.
        for value in coverage or []:
            if value['complete'] and 'input_sources' in value:
                declared = execution_inputs.managed_source_contributions(root, job,
                    q._read(root, value['accepted_ref'], source=context), source=context)
                contributions.extend(declared['contributions'])
        if originals.source_settings(owner).get('history_observer') is not None:
            from . import history
            observation = (history.validate_observation_original(owner, job, seal) if context is not None
                else history.read_observation(root, job, seal))
            declared = history.observer_input_sources(root, job, observation, source=context) if observation is not None else None
            complete = complete and declared is not None and declared['complete']
            if declared is not None:
                contributions.extend(declared['contributions'])
        for original in compiled_originals:
            producer = originals.dependency(owner, original['producer_step'], seal_ref=original['producer_seal'])
            origin_seal = c.read(originals.resolve_ref(producer, original['producer_seal']))
            contributions.append(dict(origin_step=original['producer_step'],
                origin_result_ref=origin_seal['result_ref'], material=execution_inputs.resolve_native_original(
                    root, original, compiler_job=job, source=context, controls_only=True), producer_seal=original['producer_seal']))
        for selected in contributions:
            origin = selected['origin_step']
            producer = originals.dependency(owner, origin,
                seal_ref=selected.get('producer_seal'))
            origin_seal = c.read(originals.source_record(producer, 'result-identity.json'))
            if (not any({key: value for key, value in row.items() if key != 'file_stat'} == selected['material']
                    for row in origin_seal['materials']) or selected['origin_result_ref'] != origin_seal['result_ref']):
                raise q.IntegrityError('Declared author contribution differs from its exact producer')
            contributor = authors(root, origin, {*active, key}, memo, source=producer)
            complete = complete and contributor['complete']
            people.update(tuple(row) for row in contributor['participants'])
    value = dict(complete=complete, participants=[list(row) for row in sorted(people)],
                 reason=None if complete else 'subject_authorship_unsupported')
    memo[key] = value
    return value


def _subject(root, selected):
    if len(selected) != 1:
        return None
    item = selected[0]
    step = item['origin_step']
    from . import originals
    source = originals.node(root, step)
    if not source.inherited:
        c.validate(c.workspace(root, step))
    path = originals.source_record(source, 'result-identity.json')
    seal = c.read(path)
    if item['origin_result_ref'] != seal['result_ref']:
        raise q.IntegrityError('Formal review subject differs from its exact producer')
    from . import execution_inputs
    execution_inputs._sealed_material(root, seal, item['material'], source=source if source.inherited else None)
    ref = (originals.original_ref(source, f'native-evidence/{step:03d}/result-identity.json')
        if source.inherited else q._ref(root, path))
    return dict(producer_step=step, producer_seal=ref, material=item['material'])


def _request_original(root, reference):
    """Carry the request's origin while reading its unchanged nested references."""
    from . import originals
    parts = Path(reference['path']).parts
    if len(parts) < 3 or parts[0] != 'native-evidence' or not parts[1].isdigit():
        raise q.IntegrityError('Formal review request has no exact original requester')
    step = int(parts[1])
    if reference.get('origin_identity') is not None:
        manifest = originals._manifest(root)
        if manifest is None:
            raise q.IntegrityError('Formal review request lost its original instance')
        source = originals._source(root, manifest, reference['origin_identity'], step)
    else:
        source = originals.node(root, step)
    return source, c.read(originals.resolve_ref(source, reference))


def _seal_original(root, step, reference, context=None):
    from . import originals
    source = originals.node(root, step)
    if reference.get('origin_identity') is not None:
        manifest = originals._manifest(root)
        source = originals._source(root, manifest, reference['origin_identity'], step)
    elif context is not None and source.origin_identity != context.origin_identity:
        # A nested request may refer to another origin only through an explicit
        # binding. Equal logical labels do not establish that relationship.
        raise q.IntegrityError('Formal review subject lacks its exact origin binding')
    plain = originals.original_ref(source, f'native-evidence/{step:03d}/result-identity.json')
    if reference.get('origin_identity') is None:
        plain.pop('origin_identity')
    if reference != plain:
        raise q.IntegrityError('Formal review seal differs from its exact original')
    return source, c.read(originals.resolve_ref(source, reference))


def _request_value(root, selected, job):
    current = contract(root, job)
    subject = _subject(root, selected)
    identity = authors(root, subject['producer_step']) if subject else dict(complete=False, participants=[])
    from . import backends
    backend = backends.selected(configuration_updates.for_job(root, job))
    reason = ('formal_review_subject_missing_or_ambiguous' if subject is None else
              'subject_authorship_unsupported' if not identity['complete'] else
              'reviewer_identity_unsupported' if not backend.complete_authorship else None)
    return dict(contract=current, question=QUESTION, subject=subject,
                authors=identity, unresolved_selection=selected if subject is None else None, reason=reason)


def _failure_key(value):
    return {key:value[key] for key in ('contract','subject','authors','unresolved_selection','reason')}


def _bound_failure_key(root, value, *, source=None):
    """Compare an unchanged subject across local and inherited ref spellings.

    A plain historical seal belongs to its failure receipt's original owner.
    Preserve that identity when comparing with a fork's explicit origin binding;
    identical bytes from another producer must remain a different subject.
    """
    from . import originals
    key = _failure_key(value)
    subject = key['subject']
    if subject is None:
        return key
    step = subject['producer_step']
    owner = originals.node(root, step) if source is None else originals.at_step(source, step)
    seal = subject['producer_seal']
    owner = originals.source_for_ref(owner, seal)
    bound = dict(seal, origin_identity=owner.origin_identity)
    originals.resolve_ref(owner, bound)
    return dict(key, subject=dict(subject, producer_seal=bound))


def request_route(root, job, payload, parents):
    root = Path(root).resolve()
    seal_path = c.host_record(c.workspace(root, job['step'])) / 'result-identity.json'
    seal = c.read(c.regular(seal_path))
    value = dict(_request_value(root,seal.get('selected_inputs', []), job), requester_rationale=payload['route_reason'])
    reason=value['reason']
    requester = dict(step=job['step'], seal=q._ref(root, seal_path))
    record = c.host_record(c.workspace(root, job['step']))
    if reason is not None:
        failure_key=_failure_key(value)
        ref = q._save(root, record / 'formal-review-unavailable.json', dict(requester=requester, failure=value,failure_key=failure_key))
        return ('A', sorted(set([*parents,job['step']])), 'Choose a scientific next action; formal review is unavailable: '+reason,
                dict(kind='formal_review_unavailable', unavailable=ref))
    ref = q._save(root, record / 'formal-review-request.json', dict(requester=requester, **value))
    return 'A', [value['subject']['producer_step']], value['question'], dict(kind='formal_review', request=ref)


def selected_compiler(root, reference, *, controls_only=False):
    """The exact review subject's compiler, without enumerating other code."""
    from . import execution_inputs
    context, request = _request_original(root, reference)
    subject = request['subject']
    producer, seal = _seal_original(root, subject['producer_step'], subject['producer_seal'], context)
    matches = [row for row in seal.get('compiled_implementations', []) if row['material'] == subject['material']]
    if not matches:return None
    if len(matches) != 1:
        raise q.IntegrityError('Reviewed compiler subject is ambiguous')
    execution_inputs._sealed_material(root, seal, subject['material'],
        source=producer if producer.inherited else None, controls_only=controls_only)
    row = matches[0]
    compiler = execution_inputs.verify_compilation(root, row['compiler'], material=subject['material'],
        client_binding=row.get('client_binding'), source=producer if producer.inherited else None,
        controls_only=controls_only)
    return row['compiler'], compiler


def input_binding(root, reference):
    """Exact material and producer metadata, without mounting authored conclusions."""
    from . import originals, history
    context, request = _request_original(root, reference)
    requester = originals.source_job(context)
    subject = request['subject']
    producer, seal = _seal_original(root, subject['producer_step'], subject['producer_seal'], context)
    expected_contract = originals.source_settings(context).get('formal_review_policy', {}).get('contract')
    if request['contract'] != expected_contract or request['question'] != QUESTION:
        raise q.IntegrityError('Formal review inputs differ from the exact requested subject')
    from . import execution_inputs
    source = producer if producer.inherited else None
    execution_inputs._sealed_material(root, seal, subject['material'], source=source)
    selected = dict(label='Exact reviewed artifact', origin_step=subject['producer_step'],
        origin_result_ref=seal['result_ref'], material=subject['material'])
    materials=[subject['material']];scientific=[];steps=[]
    compiled=[row for row in seal.get('compiled_implementations',[]) if row['material']==subject['material']]
    if compiled:
        if len(compiled)!=1:raise q.IntegrityError('Reviewed compiler subject is ambiguous')
        compiler=execution_inputs.verify_compilation(root,compiled[0]['compiler'],material=subject['material'],
            client_binding=compiled[0].get('client_binding'),source=source)
        if 'code_version' not in compiler and compiled[0].get('client_binding') is not None:
            author=compiler['author']
            originals.resolve_ref(originals.source_for_ref(producer,author),author)
            scientific=[dict(source=str(Path(producer.original_root)/author['path']),path='inputs/review/scientific-implementation.json',size_bytes=author['size_bytes'])]
            materials.extend(scientific)
        owner=q._read(root,compiler['job'],source=source)
        for item in compiler['inputs']:
            material=execution_inputs.resolve_native_original(root,item['original'],compiler_job=owner,source=source)
            if material not in materials:materials.append(material)
            steps.append(item['original']['producer_step'])
    return dict(materials=materials, selected=[selected],
        **(dict(compiler_scientific_materials=scientific,scientific_source_steps=sorted(set(steps))) if compiled else {}),
        history=dict(nodes=[dict(step=subject['producer_step'], seal_ref=history.source_seal_ref(producer))],
                     files=[], candidate_ids=[], view={}))


def unavailable(root, step):
    """Read a host failure against its exact sealed requester, never a model label."""
    from . import originals
    source = originals.node(root, step)
    relative = f'native-evidence/{step:03d}/formal-review-unavailable.json'
    if source.inherited:
        if not originals.has_original(source, relative):
            return None
        path = originals.resolve_ref(source, dict(path=relative))
    else:
        path = c.host_record(c.workspace(root, step)) / 'formal-review-unavailable.json'
        if not path.exists() and not path.is_symlink():
            return None
    value=c.read(c.regular(path))
    seal_ref = originals.original_ref(source, f'native-evidence/{step:03d}/result-identity.json')
    seal_ref.pop('origin_identity')
    if (value['requester']!=dict(step=step,seal=seal_ref)
            or value['failure_key']!={key:value['failure'][key] for key in
                ('contract','subject','authors','unresolved_selection','reason')}
            or value['failure']['reason'] is None):
        raise q.IntegrityError('Unavailable review differs from its sealed requester')
    return value


def unavailable_attempts(root, step, *, before_step=None):
    """Validate historical finite-correction originals; new routes never count these."""
    from . import originals
    source = originals.node(root, step)
    key = _bound_failure_key(root, unavailable(root, step)['failure_key'], source=source)
    stop = before_step if before_step is not None else step + 1
    return [prior.step for prior in originals.source_sequence(root, stop)
        if (value := unavailable(root, prior.step)) is not None
        and _bound_failure_key(root, value['failure_key'], source=prior) == key]


def choice_constraints(root, job, references=None):
    """Freeze existing failures, not a retry allowance or a second history store."""
    from . import backends
    backend=backends.selected(configuration_updates.for_job(root, job))
    if references is None:
        paths=sorted((Path(root)/'native-evidence').glob('*/formal-review-unavailable.json'),key=lambda path:int(path.parent.name))
        references=[q._ref(root,path) for path in paths if int(path.parent.name)<job['step']]
        from . import originals
        for source in originals.source_sequence(root, job['step']):
            if source.inherited and unavailable(root, source.step) is not None:
                references.append(originals.original_ref(source, f'native-evidence/{source.step:03d}/formal-review-unavailable.json'))
    for reference in references:
        value=q._read(root,reference)
        step=value['requester']['step']
        if (not 0<step<job['step'] or reference!=q._ref(root,c.host_record(c.workspace(root,step))/'formal-review-unavailable.json')
                or value!=unavailable(root,step)):
            raise q.IntegrityError('Review choice constraint lost its exact unavailable original')
    return dict(launcher_supported=backend.complete_authorship, unavailable=references)


def validate_choice(root, job, payload):
    """A known unavailable request is an output repair, never another A dispatch."""
    choices=job.get('formal_review_choices')
    if choices is None or payload.get('next_action')!='formal_review':
        return
    from .output_repair import OutputRepairRequired
    selected=c.selected_inputs(c.workspace(root,job['step']),payload,job)
    value=_request_value(root,selected,job)
    if value['subject'] is None:
        raise OutputRepairRequired('formal_review_unavailable',
            'Select exactly one resolvable scientific implementation for formal_review, or choose another scientific action. No review was dispatched; do not repeat experiments.')
    key = _bound_failure_key(root, value)
    for reference in choices['unavailable']:
        source, failure = _request_original(root, reference)
        if key == _bound_failure_key(root, failure['failure_key'], source=source):
            raise OutputRepairRequired('formal_review_unavailable',
                'Formal review of this exact subject and identity evidence is unavailable. Choose another scientific action or select a different exact subject in this same response. Rewording grants no qualification; do not repeat experiments.')


def public_choices(root, job):
    choices=job.get('formal_review_choices')
    if choices is None:
        return None
    rows=[];seen=set()
    for reference in choices['unavailable']:
        failure=q._read(root,reference)['failure'];subject=failure['subject']
        object_id=None
        if subject is not None:
            seal=q._read(root,subject['producer_seal'])
            materials=sorted(seal['materials'],key=lambda row:row['path'])
            if subject['material'] not in materials:
                raise q.IntegrityError('Unavailable subject differs from its producer original')
            from . import originals
            role=originals.source_job(originals.node(root, subject['producer_step']))['role']
            object_id=f'{role}{subject["producer_step"]:03d}-I{materials.index(subject["material"])+1:03d}'
        key=(object_id,failure['reason'])
        if key not in seen:
            seen.add(key);rows.append(dict(subject=object_id,reason=failure['reason']))
    return dict(launcher_supported=choices['launcher_supported'], unavailable=rows[:8],omitted_count=max(0,len(rows)-8),
        scope='Each restriction applies only while its exact subject, review contract and verified identity evidence remain unchanged.',
        enforcement='Control rejects unchanged unavailable requests before sealing, using same-session output repair. A different exact singleton may be selected in the same response; ordinary scientific actions retain their own checks.')


def bind_job(root, job):
    back = job.get('evidence_return') or {}
    if back.get('kind') != 'formal_review':
        return
    if job['role'] != 'A' or contract(root, job) is None:
        raise q.IntegrityError('Formal review requires its explicit frozen operator contract')
    from . import originals
    context, request = _request_original(root, back['request'])
    requester = originals.source_job(context)
    expected_contract = originals.source_settings(context).get('formal_review_policy', {}).get('contract')
    if request['contract'] != expected_contract or request['requester']['step'] >= job['step']:
        raise q.IntegrityError('Formal review request contract or requester changed')
    _seal_original(root, request['requester']['step'], request['requester']['seal'], context)
    _seal_original(root, request['subject']['producer_step'], request['subject']['producer_seal'], context)
    job.update(purpose='formal_review', formal_review=back['request'])


def validate_job(root, job):
    back = job.get('evidence_return') or {}
    if job.get('formal_review') or back.get('kind')=='formal_review':
        expected=dict(job)
        bind_job(root,expected)
        if (expected.get('formal_review')!=job.get('formal_review') or job.get('purpose')!='formal_review'):
            raise q.IntegrityError('Formal review task differs from its frozen request')
        binding=input_binding(root,job['formal_review'])
        if (job['instruction']!=QUESTION or job['goal_text']!=configuration_updates.for_job(root, job)['objective']
                or job['global_materials']!=c.global_materials(root) or job['direct_refs']
                or job['direct_materials']!=binding['materials'] or job.get('selected_inputs')!=binding['selected']
                or job['direct_entry_points'] or job['history']!=binding['history']
                or job.get('implementation_options')!=[] or job.get('implementation_options_omitted_count')!=0):
            raise q.IntegrityError('Formal review task differs from its exact subject and neutral input boundary')
        work=c.workspace(root,job['step'])
        if ((work/'inputs/history').exists() or (work/'inputs/history').is_symlink()
                or any((work/'inputs/parents').glob('*/conclusion.json'))):
            raise q.IntegrityError('Formal review retained default historical or requester conclusions')
    if 'formal_review_choices' in job and job['formal_review_choices']!=choice_constraints(root,job,job['formal_review_choices']['unavailable']):
        raise q.IntegrityError('Frozen formal review choice constraints changed')
    if job.get('formal_review_version')==1 and contract(root, job) is None:
        raise q.IntegrityError('Formal review task lost its operator contract')


def feedback(root, job):
    back=job.get('evidence_return') or {}
    if back.get('kind')=='formal_review_unavailable':
        failure=q._read(root,back['unavailable'])['failure']
        return dict(status='unavailable',reason=failure['reason'],
                    alternative_examples=['finish','wait'] if job.get('purpose')=='closeout' else ['revise_h','extract','continue_w','finish','wait'],
                    alternatives_scope='Examples only; each schema action retains its own capability, scientific-decision, budget and closeout checks.',
                    formal_review=dict(available=False,scope='Unchanged subject and identity evidence',
                        recovery='Select a different exact subject or supply the missing verifiable identity evidence.'),
                    instruction='Choose another scientific action. formal_review remains a conditional schema choice for a changed subject or identity; an unchanged request is rejected before sealing for same-session output repair, never another review or automatic A turn. Other scientific actions remain available under their own checks.')
    if back.get('kind')=='formal_review_return':
        proof=q._read(root,back['review'])
        return dict(status=proof['status'],verdict=proof['verdict'],independence=proof['independence'],
                    scope='This exact review only; no execution permission.')
    return None


def public(root, job):
    _, request = _request_original(root, job['formal_review'])
    material = request['subject']['material']
    binding=input_binding(root,job['formal_review'])
    scientific=binding.get('compiler_scientific_materials',[])
    implementation = scientific[0]['path'] if scientific else None
    if implementation is None:
        selected = selected_compiler(root, job['formal_review'], controls_only=True)
        if selected is not None and 'code_version' in selected[1]:
            implementation = 'inputs/review/scientific-implementation.json'
    return dict(role='A', question=request['question'],
        review=dict(subject=material['path'], instruction=_contract()['instruction'],
                    **(dict(scientific_implementation=implementation,
                        scientific_inputs=[item['path'] for item in binding['materials']
                            if item!=material and item not in scientific],
                        implementation_relation='The readable implementation and scientific inputs are exact compiler originals for this selected subject; control retains and verifies the native package privately.') if implementation is not None else {}),
                    scope=_contract()['scope'],
                    claims='Test claims in the exact artifact against the original global objective and constraints; absent claims or evidence remain unknown.',
                    requester_context='Requester rationale and conclusions are not default inputs. Host audit originals remain retained; any on-demand file reads are not completely observed. This is not blinded review or access isolation.'),
        global_inputs=[row['path'] for row in job['global_materials']],
        final_response=list(schema()['properties']), outputs='artifacts/')


def _qualification(root, job, payload, seal):
    context, request = _request_original(root, job['formal_review'])
    subject = request['subject']
    producer, _ = _seal_original(root, subject['producer_step'], subject['producer_seal'], context)
    if job.get('fork_source'):
        subject = dict(subject, producer_seal=dict(subject['producer_seal'], origin_identity=producer.origin_identity))
    current_authors = authors(root, subject['producer_step'], source=producer)
    input_binding(root, job['formal_review'])
    if request['authors'] != current_authors:
        raise q.IntegrityError('Formal review contract or subject authors changed')
    reviewer = participants(root, job, seal)
    independent = review_rules.independence(
        [tuple(row) for row in current_authors['participants']], [tuple(row) for row in reviewer['participants']],
        authors_complete=current_authors['complete'], reviewers_complete=reviewer['complete'])
    return dict(version=1, review_step=job['step'], subject=subject, request=job['formal_review'],
        contract=request['contract'], authors=current_authors, reviewer=reviewer,
        native_authorship=seal.get('native_authorship'), raw_ref=seal['raw_ref'], result_ref=seal['result_ref'],
        verdict=payload['verdict'], independence=independent,
        status='passed' if independent=='independent' and payload['verdict']=='pass' else 'not_qualified')


def seal_review(root, job, payload, seal):
    return q._save(root, c.host_record(c.workspace(root, job['step'])) / 'formal-review.json',
                   _qualification(root, job, payload, seal))


def validate_seal(root, job, payload, seal):
    if job.get('formal_review_version')==1 and 'native_authorship' not in seal:
        raise q.IntegrityError('Sealed task lost its native authorship original')
    if 'native_authorship' in seal:
        participants(root, job, seal)
    if job.get('formal_review'):
        ref = seal.get('formal_review')
        if ref is None or q._read(root, ref) != _qualification(root, job, payload, seal):
            raise q.IntegrityError('Formal review qualification differs from its sealed originals')


def return_route(root, job, parents):
    _, request = _request_original(root, job['formal_review'])
    seal = c.read(c.host_record(c.workspace(root, job['step'])) / 'result-identity.json')
    return 'A', sorted(set([*parents, request['requester']['step'], job['step']])), \
        'Use the exact formal review findings and limitations to choose the scientific next action.', \
        dict(kind='formal_review_return', review=seal['formal_review'])


def subject_for_receipt(root, receipt):
    """Host interpretation of a retained exact subject; original bytes stay intact."""
    import copy
    from . import originals
    proof = q._read(root, receipt)
    subject = copy.deepcopy(proof['subject'])
    if 'origin_identity' in receipt:
        context = originals.for_origin(root, receipt['origin_identity'], step=proof['review_step'])
        owner = originals.dependency(context, subject['producer_step'],
            seal_ref=subject['producer_seal'])
        subject['producer_seal'].setdefault('origin_identity', owner.origin_identity)
    return subject


def require_review(root, subject, receipt):
    """Future P3 gate consumes an explicit exact receipt; never a latest lookup."""
    root=Path(root).resolve()
    proof = q._read(root, receipt)
    from . import originals
    context = (originals.for_origin(root, receipt['origin_identity'], step=proof['review_step'])
        if 'origin_identity' in receipt else originals.node(root, proof['review_step']))
    job = originals.source_job(context)
    if context.inherited:
        c.validate_original(context)
        seal = c.read(originals.source_record(context, 'result-identity.json'))
        request = c.read(originals.resolve_ref(originals.source_for_ref(context, job['formal_review']), job['formal_review']))
        bound_receipt = dict(seal.get('formal_review') or {})
        bound_receipt.setdefault('origin_identity', context.origin_identity)
    else:
        c.validate(c.workspace(root, job['step']))
        seal = c.read(c.host_record(c.workspace(root, job['step'])) / 'result-identity.json')
        request = q._read(root, job['formal_review'])
        bound_receipt = seal.get('formal_review')
    if (bound_receipt != receipt or subject_for_receipt(root, receipt) != subject
            or proof['contract'] != request['contract'] or proof['status'] != 'passed'):
        raise ValueError('Exact subject has no compatible independent sealed pass')
    return receipt
