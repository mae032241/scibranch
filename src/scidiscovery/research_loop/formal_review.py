"""Opt-in exact-subject review through the existing native A node lifecycle.

These host facts grant review qualification only. They do not grant execution,
renew budgets, or establish independence for unobserved native helpers.
"""
import hashlib
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
        from . import startup_recovery
        if not startup_recovery.review_contract(root,policy,current):
            raise q.IntegrityError('Current formal review contract differs from its frozen original')
    return policy['contract']


def capture_participants(root, job):
    """Retain actual native identity, never inferred from role or subject inputs."""
    root = Path(root).resolve()
    from . import continuation
    rows, participants, complete = [], set(), True
    for number, record in enumerate(execution.records(root, job['step'])):
        outcome = c.read(c.regular(record / 'native-outcome.json'))
        row = dict(attempt=number, intent=q._ref(root, record / 'native-intent.json'),
                   outcome=q._ref(root, record / 'native-outcome.json'))
        binding = outcome.get('native_identity') or {}
        try:
            if binding.get('participant_scope') != 'codex_exec_single_agent_v1' or not binding.get('path'):
                raise ValueError('native_participant_identity_unsupported')
            original = continuation.session(root, job['step'], record, home=binding['home'],
                session_path=binding['path'], identity_proof=True)
            with c.regular(Path(original['path'])).open('rb') as stream:
                line = stream.readline()
            if hashlib.sha256(line).hexdigest() != original['header_sha256']:
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


def participants(root, job, seal):
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


def authors(root, step, active=None, memo=None):
    """Only exact producer and declared implementation contributors are authors."""
    active, memo = set() if active is None else active, {} if memo is None else memo
    if step in active:
        return dict(complete=False, participants=[], reason='authorship_cycle')
    if step in memo:
        return memo[step]
    job = c.read(c.regular(root / 'jobs' / f'{step:03d}.json'))
    seal = c.read(c.regular(c.host_record(c.workspace(root, step)) / 'result-identity.json'))
    own = participants(root, job, seal)
    people = {tuple(row) for row in own['participants']}
    complete = own['complete']
    source = seal.get('source_provenance')
    coverage = None
    if source is not None:
        proof = q._read(root, source)
        coverage = proof.get('managed_input_coverage')
        if coverage is not None:
            q.validate_outputs(root, job, seal)
            complete = complete and all(row['complete'] for row in coverage)
        elif proof.get('missing_reason') is not None:
            complete = False  # Legacy/opaque call inputs have no trusted coverage.
    if not job.get('formal_review'):
        contributions = list(job.get('selected_inputs', []))
        from . import execution_inputs
        execution_inputs.validate_seal(root, job, seal)
        originals = []
        for row in seal.get('compiled_implementations', []):
            compiler = execution_inputs.verify_compilation(root, row['compiler'], material=row['material'],
                client_binding=row.get('client_binding'))
            originals.extend(item['original'] for item in compiler['inputs'])
            if compiler.get('author_input') is not None:
                originals.append(compiler['author_input'])
        # q.validate_outputs above verified this immutable accepted view. Only
        # actual input contributions join the existing traversal, not all parents.
        for value in coverage or []:
            if value['complete'] and 'input_sources' in value:
                declared = execution_inputs.managed_source_contributions(root, job, q._read(root, value['accepted_ref']))
                contributions.extend(declared['contributions'])
        if configuration_updates.for_job(root, job).get('history_observer') is not None:
            from . import history
            observation = history.read_observation(root, job, seal)
            declared = history.observer_input_sources(root, job, observation) if observation is not None else None
            complete = complete and declared is not None and declared['complete']
            if declared is not None:
                contributions.extend(declared['contributions'])
        for original in originals:
            origin_seal = q._read(root, original['producer_seal'])
            contributions.append(dict(origin_step=original['producer_step'],
                origin_result_sha256=origin_seal['sha256'], material=execution_inputs.resolve_native_original(
                    root, original, compiler_job=job)))
        for selected in contributions:
            origin = selected['origin_step']
            origin_seal = c.read(c.regular(c.host_record(c.workspace(root, origin)) / 'result-identity.json'))
            if selected['material'] not in origin_seal['materials'] or selected['origin_result_sha256'] != origin_seal['sha256']:
                raise q.IntegrityError('Declared author contribution differs from its exact producer')
            contributor = authors(root, origin, {*active, step}, memo)
            complete = complete and contributor['complete']
            people.update(tuple(row) for row in contributor['participants'])
    value = dict(complete=complete, participants=[list(row) for row in sorted(people)],
                 reason=None if complete else 'subject_authorship_unsupported')
    memo[step] = value
    return value


def _subject(root, selected):
    if len(selected) != 1:
        return None
    item = selected[0]
    step = item['origin_step']
    c.validate(c.workspace(root, step))
    path = c.host_record(c.workspace(root, step)) / 'result-identity.json'
    seal = c.read(path)
    if item['material'] not in seal['materials'] or item['origin_result_sha256'] != seal['sha256']:
        raise q.IntegrityError('Formal review subject differs from its exact producer')
    return dict(producer_step=step, producer_seal=q._ref(root, path), material=item['material'])


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


def input_binding(root, reference):
    """Exact material and producer metadata, without mounting authored conclusions."""
    request = q._read(root, reference)
    requester = c.read(c.regular(Path(root) / 'jobs' / f'{request["requester"]["step"]:03d}.json'))
    subject = request['subject']; seal = q._read(root, subject['producer_seal'])
    if (request['contract'] != contract(root, requester) or request['question'] != QUESTION
            or subject['producer_seal'] != q._ref(root, c.host_record(c.workspace(root,subject['producer_step']))/'result-identity.json')
            or subject['material'] not in seal['materials']):
        raise q.IntegrityError('Formal review inputs differ from the exact requested subject')
    selected = dict(label='Exact reviewed artifact', origin_step=subject['producer_step'],
        origin_result_sha256=seal['sha256'], material=subject['material'])
    materials=[subject['material']];scientific=[];steps=[]
    compiled=[row for row in seal.get('compiled_implementations',[]) if row['material']==subject['material']]
    if compiled and compiled[0].get('client_binding') is not None:
        if len(compiled)!=1:raise q.IntegrityError('Reviewed compiler subject is ambiguous')
        from . import execution_inputs
        compiler=execution_inputs.verify_compilation(root,compiled[0]['compiler'],material=subject['material'],
            client_binding=compiled[0]['client_binding'])
        author=compiler['author']
        scientific=[dict(source=str(Path(root)/author['path']),path='inputs/review/scientific-implementation.json',
            sha256=author['sha256'],size_bytes=author['size_bytes'])]
        materials.extend(scientific)
        owner=q._read(root,compiler['job'])
        for item in compiler['inputs']:
            material=execution_inputs.resolve_native_original(root,item['original'],compiler_job=owner)
            if material not in materials:materials.append(material)
            steps.append(item['original']['producer_step'])
    return dict(materials=materials, selected=[selected],
        **(dict(compiler_scientific_materials=scientific,scientific_source_steps=sorted(set(steps))) if scientific else {}),
        history=dict(nodes=[dict(step=subject['producer_step'], seal_sha256=subject['producer_seal']['sha256'])],
                     files=[], candidate_ids=[], view={}))


def unavailable(root, step):
    """Read a host failure against its exact sealed requester, never a model label."""
    path=c.host_record(c.workspace(root,step))/'formal-review-unavailable.json'
    if not path.exists() and not path.is_symlink():
        return None
    value=c.read(c.regular(path))
    seal_path=c.host_record(c.workspace(root,step))/'result-identity.json'
    if (value['requester']!=dict(step=step,seal=q._ref(root,seal_path))
            or value['failure_key']!={key:value['failure'][key] for key in
                ('contract','subject','authors','unresolved_selection','reason')}
            or value['failure']['reason'] is None):
        raise q.IntegrityError('Unavailable review differs from its sealed requester')
    return value


def unavailable_attempts(root, step, *, before_step=None):
    """Validate historical finite-correction originals; new routes never count these."""
    key=unavailable(root,step)['failure_key']
    stop=before_step if before_step is not None else step+1
    steps=sorted(int(path.parent.name) for path in (Path(root)/'native-evidence').glob('*/formal-review-unavailable.json'))
    return [prior for prior in steps if prior<stop and unavailable(root,prior)['failure_key']==key]


def choice_constraints(root, job, references=None):
    """Freeze existing failures, not a retry allowance or a second history store."""
    from . import backends
    backend=backends.selected(configuration_updates.for_job(root, job))
    if references is None:
        paths=sorted((Path(root)/'native-evidence').glob('*/formal-review-unavailable.json'),key=lambda path:int(path.parent.name))
        references=[q._ref(root,path) for path in paths if int(path.parent.name)<job['step']]
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
    for reference in choices['unavailable']:
        failure=q._read(root,reference)
        if _failure_key(value)==failure['failure_key']:
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
            role=c.read(c.regular(Path(root)/'jobs'/f'{subject["producer_step"]:03d}.json'))['role']
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
    request = q._read(root, back['request'])
    requester = c.read(c.regular(Path(root) / 'jobs' / f'{request["requester"]["step"]:03d}.json'))
    if request['contract'] != contract(root, requester) or request['requester']['step'] >= job['step']:
        raise q.IntegrityError('Formal review request contract or requester changed')
    if request['requester']['seal'] != q._ref(root,c.host_record(c.workspace(root,request['requester']['step']))/'result-identity.json'):
        raise q.IntegrityError('Formal review requester differs from its exact original node')
    q._read(root, request['requester']['seal'])
    q._read(root, request['subject']['producer_seal'])
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
    request = q._read(root, job['formal_review'])
    material = request['subject']['material']
    binding=input_binding(root,job['formal_review'])
    scientific=binding.get('compiler_scientific_materials',[])
    return dict(role='A', question=request['question'],
        review=dict(subject=material['path'], instruction=_contract()['instruction'],
                    **(dict(scientific_implementation=scientific[0]['path'],
                        scientific_inputs=[item['path'] for item in binding['materials']
                            if item!=material and item not in scientific],
                        implementation_relation='The readable implementation and scientific inputs are exact compiler originals for this selected subject; control retains and verifies the native package privately.') if scientific else {}),
                    scope=_contract()['scope'],
                    claims='Test claims in the exact artifact against the original global objective and constraints; absent claims or evidence remain unknown.',
                    requester_context='Requester rationale and conclusions are not default inputs. Host audit originals remain retained; any on-demand file reads are not completely observed. This is not blinded review or access isolation.'),
        global_inputs=[row['path'] for row in job['global_materials']],
        final_response=list(schema()['properties']), outputs='artifacts/')


def _qualification(root, job, payload, seal):
    request = q._read(root, job['formal_review'])
    subject = request['subject']
    q._read(root, subject['producer_seal'])
    current_authors = authors(root, subject['producer_step'])
    input_binding(root, job['formal_review'])
    if request['authors'] != current_authors:
        raise q.IntegrityError('Formal review contract or subject authors changed')
    reviewer = participants(root, job, seal)
    independent = review_rules.independence(
        [tuple(row) for row in current_authors['participants']], [tuple(row) for row in reviewer['participants']],
        authors_complete=current_authors['complete'], reviewers_complete=reviewer['complete'])
    return dict(version=1, review_step=job['step'], subject=subject, request=job['formal_review'],
        contract=request['contract'], authors=current_authors, reviewer=reviewer,
        native_authorship=seal.get('native_authorship'), raw_sha256=seal['raw_sha256'], result_sha256=seal['sha256'],
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
    request = q._read(root, job['formal_review'])
    seal = c.read(c.host_record(c.workspace(root, job['step'])) / 'result-identity.json')
    return 'A', sorted(set([*parents, request['requester']['step'], job['step']])), \
        'Use the exact formal review findings and limitations to choose the scientific next action.', \
        dict(kind='formal_review_return', review=seal['formal_review'])


def require_review(root, subject, receipt):
    """Future P3 gate consumes an explicit exact receipt; never a latest lookup."""
    root=Path(root).resolve()
    proof = q._read(root, receipt)
    job = c.read(c.regular(root / 'jobs' / f'{proof["review_step"]:03d}.json'))
    c.validate(c.workspace(root, job['step']))
    seal = c.read(c.host_record(c.workspace(root, job['step'])) / 'result-identity.json')
    if (seal.get('formal_review') != receipt or proof['subject'] != subject
            or proof['contract'] != q._read(root, job['formal_review'])['contract'] or proof['status'] != 'passed'):
        raise ValueError('Exact subject has no compatible independent sealed pass')
    return receipt
